"""Small deterministic arena for trusted prototype bots. NOT a sandbox.

No user-submitted Python should ever be imported into this process. A WASM adapter
will replace the direct strategy calls before public submissions are accepted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import random
from typing import Callable

from .mapgen import GameMap, generate_map

VERSION = "prototype-0.4"
MOVES = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
HATS = ("RED", "BLUE", "GREEN", "YELLOW")
WEAPON_KINDS = ("bow", "spear", "sword")
MELEE_DAMAGE = {"unarmed": 8, "sword": 25}
SPEAR_DAMAGE = 16
BOW_DAMAGE = 14


@dataclass(frozen=True)
class Config:
    size: int = 25
    max_ticks: int = 160
    shrink_start: int = 20
    shrink_every: int = 10
    storm_base: int = 10
    storm_ramp: int = 5  # extra storm damage per completed shrink step
    storm_cap: int = 40
    max_health: int = 100
    guard_fatigue: int = 2  # guard halves damage only this many consecutive ticks
    elim_heal: int = 10  # provisional; balance-tested at 0/10/25
    vision_radius: int = 8  # Chebyshev; rocks occlude
    rock_density: float = 0.08
    medkit_heal: int = 30
    medkit_cap: int = 2
    spear_range: int = 2
    bow_min_range: int = 2
    bow_max_range: int = 6


@dataclass
class Fighter:
    id: str
    x: int
    y: int
    hp: int = 100
    weapon: str = "unarmed"
    arrows: int = 0
    medkits: int = 0
    hat: str = "RED"
    alive: bool = True
    survival_tick: int = 0
    damage_done: int = 0
    eliminations: int = 0


Strategy = Callable[[dict, dict], tuple[dict, dict]]
Loot = dict[tuple[int, int], dict[str, int]]


def _radius(tick: int, cfg: Config) -> int:
    initial = cfg.size // 2
    if tick < cfg.shrink_start:
        return initial
    # Radius 0 (single safe tile) makes endgame standoffs terminal.
    return max(0, initial - 1 - (tick - cfg.shrink_start) // cfg.shrink_every)


def _storm_damage(tick: int, cfg: Config) -> int:
    if tick < cfg.shrink_start:
        return cfg.storm_base
    steps = (tick - cfg.shrink_start) // cfg.shrink_every + 1
    return min(cfg.storm_base + cfg.storm_ramp * steps, cfg.storm_cap)


def occluded(ax: int, ay: int, bx: int, by: int, rocks: frozenset) -> bool:
    """Integer supercover line between tile centers; endpoints never block.

    Exact corner crossings step diagonally and do not block, so perfect diagonals
    only test the diagonal cells. Symmetric in its endpoints.
    """
    dx, dy = bx - ax, by - ay
    sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
    dx, dy = abs(dx), abs(dy)
    x, y, ix, iy = ax, ay, 0, 0
    while ix < dx or iy < dy:
        # Compare fractional progress (ix+.5)/dx vs (iy+.5)/dy via cross-multiplication.
        t1, t2 = (2 * ix + 1) * dy, (2 * iy + 1) * dx
        if t1 == t2:
            x, y, ix, iy = x + sx, y + sy, ix + 1, iy + 1
        elif t1 < t2:
            x, ix = x + sx, ix + 1
        else:
            y, iy = y + sy, iy + 1
        if (x, y) == (bx, by):
            return False
        if (x, y) in rocks:
            return True
    return False


def _attack_damage(p: Fighter, q: Fighter, rocks: frozenset, cfg: Config) -> int | None:
    """Damage if p can legally hit q with its current weapon, else None.

    Distance metrics: unarmed/sword Chebyshev 1 (diagonals allowed unless the rock
    corner between the fighters is fully sealed); spear orthogonal line Manhattan
    1..2 (intermediate tile must not be rock); bow 8-direction ray of 2..6 steps
    with a rock-free path and an arrow in the quiver. Fighters never block attacks.
    """
    dx, dy = q.x - p.x, q.y - p.y
    manhattan = abs(dx) + abs(dy)
    if p.weapon in MELEE_DAMAGE:
        if max(abs(dx), abs(dy)) != 1:
            return None
        if dx and dy and (p.x + dx, p.y) in rocks and (p.x, p.y + dy) in rocks:
            return None
        return MELEE_DAMAGE[p.weapon]
    if p.weapon == "spear":
        if (dx == 0 or dy == 0) and 1 <= manhattan <= cfg.spear_range:
            if manhattan == 2 and (p.x + dx // 2, p.y + dy // 2) in rocks:
                return None
            return SPEAR_DAMAGE
        return None
    if p.weapon == "bow":
        if p.arrows <= 0 or not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
            return None
        steps = max(abs(dx), abs(dy))
        if not (cfg.bow_min_range <= steps <= cfg.bow_max_range):
            return None
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        if any((p.x + sx * i, p.y + sy * i) in rocks for i in range(1, steps)):
            return None
        return BOW_DAMAGE
    return None


def _snapshot(tick: int, cfg: Config, players: dict[str, Fighter], loot: Loot, events: list[dict]) -> dict:
    return {"tick": tick, "safe_radius": _radius(tick, cfg),
            "players": {k: asdict(players[k]) for k in sorted(players)},
            "loot": [{"x": x, "y": y, "kind": k, "count": n}
                     for (x, y) in sorted(loot) for k, n in sorted(loot[(x, y)].items())],
            "events": events}


def _observe(me: Fighter, players: dict[str, Fighter], loot: Loot, rocks: frozenset,
             tick: int, cfg: Config, alive_count: int) -> dict:
    """Fog of war applies here only; replay frames stay omniscient for spectators."""
    r = cfg.vision_radius
    enemies = [{"id": p.id, "x": p.x, "y": p.y, "hp": p.hp, "weapon": p.weapon, "hat": p.hat}
               for p in sorted(players.values(), key=lambda p: p.id)
               if p.alive and p.id != me.id and max(abs(p.x-me.x), abs(p.y-me.y)) <= r
               and not occluded(me.x, me.y, p.x, p.y, rocks)]
    visible_loot = [{"x": x, "y": y, "kind": k, "count": n}
                    for (x, y) in sorted(loot) for k, n in sorted(loot[(x, y)].items())
                    if max(abs(x-me.x), abs(y-me.y)) <= r and not occluded(me.x, me.y, x, y, rocks)]
    return {"phase": "play", "tick": tick, "size": cfg.size, "safe_radius": _radius(tick, cfg),
            "alive": alive_count,
            "self": {"id": me.id, "x": me.x, "y": me.y, "hp": me.hp, "weapon": me.weapon,
                     "arrows": me.arrows, "medkits": me.medkits, "hat": me.hat},
            "enemies": enemies, "loot": visible_loot,
            "rocks": [{"x": x, "y": y} for (x, y) in sorted(rocks)
                      if max(abs(x-me.x), abs(y-me.y)) <= r]}


def run_match(strategies: dict[str, Strategy], seed: int = 1, config: Config | None = None,
              game_map: GameMap | None = None) -> dict:
    """Run built-in trusted strategies and return a self-contained replay dictionary.

    game_map is a testing hook: provided maps keep their spawn order (no shuffle).
    """
    cfg = config or Config()
    if cfg.size < 9 or cfg.size % 2 != 1 or cfg.max_ticks < 1:
        raise ValueError("size must be odd >= 9 and max_ticks >= 1")
    if not (2 <= len(strategies) <= 12):
        raise ValueError("Prototype supports 2 to 12 fighters")
    rng = random.Random(seed)
    c = cfg.size // 2
    if game_map is None:
        game_map = generate_map(rng, cfg.size, len(strategies), cfg.rock_density)
        spawn_positions = list(game_map.spawns)
        rng.shuffle(spawn_positions)
    else:
        spawn_positions = list(game_map.spawns)
    rocks = frozenset(game_map.rocks)
    loot: Loot = {}
    for x, y, kind, count in game_map.loot:
        loot.setdefault((x, y), {})[kind] = loot.get((x, y), {}).get(kind, 0) + count
    ids = sorted(strategies)
    # Final-ranking tiebreak priority: seed-derived, independent of the gameplay
    # RNG stream, and recorded in match metadata for auditability.
    priority = sorted(ids)
    random.Random(f"tiebreak:{seed}").shuffle(priority)
    priority_index = {id_: i for i, id_ in enumerate(priority)}
    players = {id_: Fighter(id=id_, x=spawn_positions[i][0], y=spawn_positions[i][1], hp=cfg.max_health)
               for i,id_ in enumerate(ids)}
    memory: dict[str, dict] = {id_: {} for id_ in ids}
    guard_streak: dict[str, int] = {id_: 0 for id_ in ids}

    # Setup phase: each bot picks a public hat color once; invalid choices become RED.
    setup_events: list[dict] = [{"type": "start", "fighters": ids}]
    for id_ in ids:
        p = players[id_]
        hat = None
        try:
            action, next_memory = strategies[id_](
                {"phase": "setup", "tick": 0, "size": cfg.size, "fighters": len(ids),
                 "safe_radius": _radius(0, cfg),
                 "self": {"id": id_, "x": p.x, "y": p.y, "hp": p.hp}},
                copy.deepcopy(memory[id_]))
            if not isinstance(action, dict) or not isinstance(next_memory, dict):
                raise ValueError("bad bot result")
            memory[id_] = copy.deepcopy(next_memory)
            if action.get("type") == "hat":
                hat = action.get("color")
        except Exception as exc:
            setup_events.append({"type": "bot_error", "id": id_, "reason": type(exc).__name__})
        p.hat = hat if hat in HATS else "RED"
        setup_events.append({"type": "hat", "id": id_, "color": p.hat})
    frames = [_snapshot(0, cfg, players, loot, setup_events)]

    for tick in range(1, cfg.max_ticks + 1):
        events = []
        living = [id_ for id_ in ids if players[id_].alive]
        if len(living) <= 1:
            break
        actions = {}
        # Reads use a consistent pre-action snapshot. Bot exceptions become WAIT.
        for id_ in living:
            try:
                action, next_memory = strategies[id_](
                    _observe(players[id_], players, loot, rocks, tick, cfg, len(living)),
                    copy.deepcopy(memory[id_]))
                if not isinstance(action, dict) or not isinstance(next_memory, dict):
                    raise ValueError("bad bot result")
                memory[id_] = copy.deepcopy(next_memory)
                actions[id_] = action
            except Exception as exc:
                actions[id_] = {"type": "wait"}
                events.append({"type": "bot_error", "id": id_, "reason": type(exc).__name__})

        for id_ in living:
            guard_streak[id_] = guard_streak[id_] + 1 if actions[id_].get("type") == "guard" else 0

        # Movement uses a rotation of player order so lower IDs don't always go first.
        order = living[tick % len(living):] + living[:tick % len(living)]
        for id_ in order:
            p = players[id_]
            action = actions[id_]
            if action.get("type") != "move" or action.get("direction") not in MOVES:
                continue
            dx, dy = MOVES[action["direction"]]
            tx, ty = p.x + dx, p.y + dy
            if not (0 <= tx < cfg.size and 0 <= ty < cfg.size) or (tx, ty) in rocks:
                continue
            if any(q.alive and q.id != id_ and q.x == tx and q.y == ty for q in players.values()):
                continue
            p.x, p.y = tx, ty
            events.append({"type": "move", "id": id_, "x": tx, "y": ty})

        # Free auto-pickup: arrows always, medkits up to cap, a weapon only when unarmed.
        for id_ in sorted(living):
            p = players[id_]
            tile = loot.get((p.x, p.y))
            if not tile:
                continue
            for kind in sorted(tile):
                take = 0
                if kind == "arrows":
                    take = tile[kind]
                    p.arrows += take
                elif kind == "medkit":
                    take = min(tile[kind], cfg.medkit_cap - p.medkits)
                    p.medkits += take
                elif p.weapon == "unarmed":
                    p.weapon = kind
                    take = 1
                if take > 0:
                    tile[kind] -= take
                    if tile[kind] <= 0:
                        del tile[kind]
                    events.append({"type": "pickup", "id": id_, "kind": kind, "count": take})
            if not tile:
                del loot[(p.x, p.y)]

        # Full-turn item actions resolve before combat: medkit use and weapon swap.
        for id_ in sorted(living):
            p, action = players[id_], actions[id_]
            if action.get("type") == "use" and action.get("item") == "medkit":
                if p.medkits > 0 and p.hp < cfg.max_health:
                    amount = min(cfg.medkit_heal, cfg.max_health - p.hp)
                    p.medkits -= 1
                    p.hp += amount
                    events.append({"type": "use", "id": id_, "item": "medkit", "amount": amount})
            elif action.get("type") == "pickup" and p.weapon != "unarmed":
                tile = loot.get((p.x, p.y))
                here = [k for k in sorted(tile) if k in WEAPON_KINDS] if tile else []
                if here:
                    new = here[0]
                    tile[new] -= 1
                    if tile[new] <= 0:
                        del tile[new]
                    tile[p.weapon] = tile.get(p.weapon, 0) + 1
                    events.append({"type": "pickup", "id": id_, "kind": new, "swapped": p.weapon})
                    p.weapon = new

        # Attacks resolve in the same rotated initiative order as movement, against
        # post-move positions. A fighter already at 0 hp loses its attack, so
        # symmetric trades cannot double-KO. Fighters never block arrows.
        hits: list[tuple[str,str,int]] = []
        for id_ in order:
            if players[id_].hp <= 0:
                continue
            action = actions[id_]
            if action.get("type") != "attack":
                continue
            target_id = action.get("target")
            if not isinstance(target_id, str) or target_id not in players or target_id == id_:
                continue
            p, q = players[id_], players[target_id]
            if not q.alive or q.hp <= 0:
                continue
            damage = _attack_damage(p, q, rocks, cfg)
            if damage is None:
                continue
            if p.weapon == "bow":
                p.arrows -= 1
            # Guard fatigues: only the first consecutive guard ticks halve damage.
            if actions.get(target_id, {}).get("type") == "guard" and guard_streak[target_id] <= cfg.guard_fatigue:
                damage = (damage + 1) // 2
            q.hp -= damage
            p.damage_done += damage
            hits.append((id_, target_id, damage))
            events.append({"type": "hit", "attacker": id_, "target": target_id,
                           "damage": damage, "weapon": p.weapon})

        radius = _radius(tick, cfg)
        storm = _storm_damage(tick, cfg)
        for id_ in living:
            p = players[id_]
            if max(abs(p.x-c), abs(p.y-c)) > radius:
                p.hp -= storm
                events.append({"type": "storm", "id": id_, "damage": storm})

        for id_ in living:
            p = players[id_]
            p.survival_tick = tick
            if p.hp <= 0:
                p.alive = False
                events.append({"type": "elimination", "id": id_})
                drops = {}
                if p.weapon != "unarmed":
                    drops[p.weapon] = 1
                if p.arrows > 0:
                    drops["arrows"] = p.arrows
                if p.medkits > 0:
                    drops["medkit"] = p.medkits
                if drops:
                    tile = loot.setdefault((p.x, p.y), {})
                    for kind, count in drops.items():
                        tile[kind] = tile.get(kind, 0) + count
                    events.append({"type": "drop", "id": id_,
                                   "items": [{"kind": k, "count": n} for k, n in sorted(drops.items())]})
                p.weapon, p.arrows, p.medkits = "unarmed", 0, 0
                # Credit most damaging attacker this tick; tie broken lexicographically.
                attackers = [(damage, attacker) for attacker,target,damage in hits if target == id_]
                if attackers:
                    killer = sorted(attackers, key=lambda d: (-d[0], d[1]))[0][1]
                    players[killer].eliminations += 1
                    k = players[killer]
                    # Kill reward counters pure-attrition play; hp>0 check is order-independent.
                    if k.hp > 0 and cfg.elim_heal > 0:
                        healed = min(cfg.max_health - k.hp, cfg.elim_heal)
                        if healed > 0:
                            k.hp += healed
                            events.append({"type": "heal", "id": killer, "amount": healed})
        frames.append(_snapshot(tick, cfg, players, loot, events))

    # Deterministic fallback when several remain at timeout or all die in one tick
    # (e.g. simultaneous storm deaths): alive, then latest survival tick, highest hp
    # (negative hp differentiates overkill), most damage, then the seed-derived
    # participant priority (replaces lexicographic id, which favored low ids).
    ranking = sorted(ids, key=lambda id_: (-int(players[id_].alive), -players[id_].survival_tick,
                                         -players[id_].hp, -players[id_].damage_done,
                                         priority_index[id_]))
    return {"version": VERSION, "seed": seed, "config": asdict(cfg), "fighters": ids,
            "hats": {id_: players[id_].hat for id_ in ids},
            "tiebreak_priority": priority,
            "rocks": [{"x": x, "y": y} for (x, y) in sorted(rocks)],
            "winner": ranking[0], "ranking": ranking, "frames": frames,
            "end_reason": "last_survivor" if sum(p.alive for p in players.values()) == 1
                          else "elimination_or_timeout_tiebreak"}
