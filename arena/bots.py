"""TRUSTED local examples. Never import third-party submissions into this process.

Each bot handles the tick-0 setup phase by choosing a hat color. Hats carry no
engine meaning; they exist so strategies can signal, ally, or deceive.
"""
from .engine import MOVES, WEAPON_KINDS


def chebyshev(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def closest(x, y, things):
    return min(things, key=lambda t: (abs(x - t["x"]) + abs(y - t["y"]),
                                      t.get("id", ""), t.get("kind", ""), t["x"], t["y"])) if things else None


def _blocked(obs, x, y):
    if not (0 <= x < obs["size"] and 0 <= y < obs["size"]):
        return True
    if any(r["x"] == x and r["y"] == y for r in obs["rocks"]):
        return True
    return any(e["x"] == x and e["y"] == y for e in obs["enemies"])


def step_toward(obs, tx, ty):
    """Deterministic greedy step with rock/fighter avoidance fallbacks."""
    me = obs["self"]
    x, y = me["x"], me["y"]
    if (x, y) == (tx, ty):
        return {"type": "wait"}
    dx, dy = tx - x, ty - y
    prefs = []
    first = ("E" if dx > 0 else "W") if dx else None
    second = ("S" if dy > 0 else "N") if dy else None
    if abs(dx) < abs(dy):
        first, second = second, first
    for d in (first, second, "N", "E", "S", "W"):
        if d and d not in prefs:
            prefs.append(d)
    c = obs["size"] // 2
    for d in prefs:
        mx, my = MOVES[d]
        nx, ny = x + mx, y + my
        # Never step outside the safe zone while inside it.
        if chebyshev(x, y, c, c) <= obs["safe_radius"] < chebyshev(nx, ny, c, c):
            continue
        if not _blocked(obs, nx, ny):
            return {"type": "move", "direction": d}
    return {"type": "wait"}


def step_away(obs, tx, ty):
    me = obs["self"]
    x, y, c = me["x"], me["y"], obs["size"] // 2
    options = []
    for d, (mx, my) in sorted(MOVES.items()):
        nx, ny = x + mx, y + my
        if _blocked(obs, nx, ny):
            continue
        if chebyshev(x, y, c, c) <= obs["safe_radius"] < chebyshev(nx, ny, c, c):
            continue
        options.append((-(abs(nx - tx) + abs(ny - ty)), d))
    if options:
        return {"type": "move", "direction": min(options)[1]}
    return {"type": "guard"}


def can_hit(obs, enemy):
    """Mirror of engine attack legality for the bot's own weapon."""
    me = obs["self"]
    rocks = {(r["x"], r["y"]) for r in obs["rocks"]}
    dx, dy = enemy["x"] - me["x"], enemy["y"] - me["y"]
    manhattan = abs(dx) + abs(dy)
    weapon = me["weapon"]
    if weapon in ("unarmed", "sword"):
        if max(abs(dx), abs(dy)) != 1:
            return False
        return not (dx and dy and (me["x"] + dx, me["y"]) in rocks
                    and (me["x"], me["y"] + dy) in rocks)
    if weapon == "spear":
        if (dx == 0 or dy == 0) and 1 <= manhattan <= 2:
            return not (manhattan == 2 and (me["x"] + dx // 2, me["y"] + dy // 2) in rocks)
        return False
    if weapon == "bow":
        if me["arrows"] <= 0 or not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
            return False
        steps = max(abs(dx), abs(dy))
        if not 2 <= steps <= 6:
            return False
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        return not any((me["x"] + sx * i, me["y"] + sy * i) in rocks for i in range(1, steps))
    return False


def attack_if_possible(obs, avoid_hats=()):
    me = obs["self"]
    for e in sorted(obs["enemies"], key=lambda e: (e["hp"], chebyshev(me["x"], me["y"], e["x"], e["y"]), e["id"])):
        if e["hat"] in avoid_hats:
            continue
        if can_hit(obs, e):
            return {"type": "attack", "target": e["id"]}
    return None


def loot_of(obs, kinds):
    return [item for item in obs["loot"] if item["kind"] in kinds]


def _setup(obs, memory, color):
    if obs.get("phase") == "setup":
        return ({"type": "hat", "color": color}, memory)
    return None


def berserker(obs, memory):
    """Rush equipment, then relentlessly hunt the nearest visible fighter."""
    done = _setup(obs, memory, "RED")
    if done:
        return done
    me = obs["self"]
    attack = attack_if_possible(obs)
    if attack:
        return attack, memory
    if me["weapon"] == "unarmed":
        item = closest(me["x"], me["y"], loot_of(obs, WEAPON_KINDS))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    enemy = closest(me["x"], me["y"], obs["enemies"])
    if enemy:
        return step_toward(obs, enemy["x"], enemy["y"]), memory
    c = obs["size"] // 2
    return step_toward(obs, c, c), memory


def archer(obs, memory):
    """Find a bow, keep distance, shoot anything with a clear line."""
    done = _setup(obs, memory, "GREEN")
    if done:
        return done
    me = obs["self"]
    enemy = closest(me["x"], me["y"], obs["enemies"])
    near = enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 2
    if me["medkits"] > 0 and me["hp"] < 40 and not near:
        return {"type": "use", "item": "medkit"}, memory
    has_bow = me["weapon"] == "bow"
    if has_bow and enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) == 1:
        return step_away(obs, enemy["x"], enemy["y"]), memory
    attack = attack_if_possible(obs)
    if attack:
        return attack, memory
    if not has_bow:
        item = closest(me["x"], me["y"], loot_of(obs, ("bow",)))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    elif me["arrows"] <= 1:
        item = closest(me["x"], me["y"], loot_of(obs, ("arrows",)))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    if has_bow and enemy:
        if chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 2:
            return step_away(obs, enemy["x"], enemy["y"]), memory
        return step_toward(obs, enemy["x"], enemy["y"]), memory
    if me["weapon"] == "unarmed":
        item = closest(me["x"], me["y"], loot_of(obs, WEAPON_KINDS))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    c = obs["size"] // 2
    if chebyshev(me["x"], me["y"], c, c) > max(2, obs["safe_radius"] - 2):
        return step_toward(obs, c, c), memory
    return {"type": "wait"}, memory


def scavenger(obs, memory):
    """Loot-driven economy: gear up, heal up, fight only winnable fights."""
    done = _setup(obs, memory, "YELLOW")
    if done:
        return done
    me = obs["self"]
    enemy = closest(me["x"], me["y"], obs["enemies"])
    near = enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 2
    if me["medkits"] > 0 and me["hp"] <= 50 and not near:
        return {"type": "use", "item": "medkit"}, memory
    if near and me["weapon"] != "unarmed" and enemy["hp"] < me["hp"]:
        attack = attack_if_possible(obs)
        if attack:
            return attack, memory
    if enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 3 and (
            me["weapon"] == "unarmed" or enemy["hp"] >= me["hp"]):
        return step_away(obs, enemy["x"], enemy["y"]), memory
    wanted = ("medkit",) + WEAPON_KINDS if me["weapon"] == "unarmed" else ("medkit", "arrows")
    item = closest(me["x"], me["y"], loot_of(obs, wanted))
    if item:
        return step_toward(obs, item["x"], item["y"]), memory
    c = obs["size"] // 2
    return step_toward(obs, c, c), memory


def evader(obs, memory):
    """Survive: hug the inside of the zone, run from everyone, fight only cornered."""
    done = _setup(obs, memory, "BLUE")
    if done:
        return done
    me = obs["self"]
    enemy = closest(me["x"], me["y"], obs["enemies"])
    if enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) == 1:
        attack = attack_if_possible(obs)
        if attack:
            return attack, memory
    if me["medkits"] > 0 and me["hp"] <= 60 and not (
            enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 2):
        return {"type": "use", "item": "medkit"}, memory
    if enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 4:
        return step_away(obs, enemy["x"], enemy["y"]), memory
    c, r = obs["size"] // 2, obs["safe_radius"]
    if chebyshev(me["x"], me["y"], c, c) >= r:
        return step_toward(obs, c, c), memory
    item = closest(me["x"], me["y"], loot_of(obs, ("medkit",)))
    if item:
        return step_toward(obs, item["x"], item["y"]), memory
    return {"type": "guard"}, memory


def opportunist(obs, memory):
    """Third-party vulture: strike only wounded targets, otherwise lurk."""
    done = _setup(obs, memory, "YELLOW")
    if done:
        return done
    me = obs["self"]
    wounded = [e for e in obs["enemies"] if e["hp"] <= 45]
    attack = attack_if_possible(obs)
    if attack and any(e["id"] == attack["target"] for e in wounded):
        return attack, memory
    if me["medkits"] > 0 and me["hp"] <= 55:
        return {"type": "use", "item": "medkit"}, memory
    target = closest(me["x"], me["y"], wounded)
    if target and me["weapon"] != "unarmed":
        return step_toward(obs, target["x"], target["y"]), memory
    if me["weapon"] == "unarmed":
        item = closest(me["x"], me["y"], loot_of(obs, WEAPON_KINDS))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    enemy = closest(me["x"], me["y"], obs["enemies"])
    if enemy and chebyshev(me["x"], me["y"], enemy["x"], enemy["y"]) <= 2:
        return step_away(obs, enemy["x"], enemy["y"]), memory
    c = obs["size"] // 2
    if chebyshev(me["x"], me["y"], c, c) > obs["safe_radius"] - 1:
        return step_toward(obs, c, c), memory
    return {"type": "wait"}, memory


def hat_ally(obs, memory):
    """Spares same-hat fighters until the field thins; betrayal is built in."""
    done = _setup(obs, memory, "BLUE")
    if done:
        return done
    me = obs["self"]
    avoid = () if obs["alive"] <= 3 else (me["hat"],)
    attack = attack_if_possible(obs, avoid_hats=avoid)
    if attack:
        return attack, memory
    if me["medkits"] > 0 and me["hp"] <= 50:
        return {"type": "use", "item": "medkit"}, memory
    if me["weapon"] == "unarmed":
        item = closest(me["x"], me["y"], loot_of(obs, WEAPON_KINDS))
        if item:
            return step_toward(obs, item["x"], item["y"]), memory
    foes = [e for e in obs["enemies"] if e["hat"] not in avoid]
    enemy = closest(me["x"], me["y"], foes)
    if enemy:
        return step_toward(obs, enemy["x"], enemy["y"]), memory
    c = obs["size"] // 2
    return step_toward(obs, c, c), memory


REFERENCE_BOTS = {
    "A1_Berserker": berserker, "A2_Berserker": berserker,
    "B1_Archer": archer, "B2_Archer": archer,
    "C1_Scavenger": scavenger, "C2_Scavenger": scavenger,
    "D1_Evader": evader, "D2_Evader": evader,
    "E1_Opportunist": opportunist,
    "F1_HatAlly": hat_ally,
}

