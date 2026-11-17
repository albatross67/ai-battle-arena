"""Batch balance analysis over seeded matches.

Examples:
    python -m arena.analyze --seeds 500
    python -m arena.analyze --seeds 500 --elim-heal 0
    python -m arena.analyze --seeds 100 --encounters 3
"""
import argparse
from collections import Counter
from dataclasses import replace

from .bots import REFERENCE_BOTS
from .engine import Config, run_match


def archetype(fighter_id: str) -> str:
    return fighter_id.split("_", 1)[1] if "_" in fighter_id else fighter_id


def analyze(seeds: int, config: Config | None = None) -> dict:
    cfg = config or Config()
    wins = Counter()
    ranking_points = Counter()  # field-size points: 1st gets n-1, last gets 0
    end_reasons = Counter()
    durations = []
    kills = Counter()
    kill_dist = Counter()  # eliminations-per-win histogram
    deaths_by_storm = Counter()
    deaths_by_combat = Counter()
    weapon_hits = Counter()
    weapon_damage = Counter()
    medkit_uses = Counter()
    elim_heals = Counter()
    hat_matrix = Counter()  # (attacker_hat, target_hat) hit counts
    archetypes = sorted({archetype(id_) for id_ in REFERENCE_BOTS})
    entrants = Counter(archetype(id_) for id_ in REFERENCE_BOTS)

    for seed in range(1, seeds + 1):
        log = run_match(REFERENCE_BOTS, seed=seed, config=cfg)
        hats = log["hats"]
        wins[archetype(log["winner"])] += 1
        end_reasons[log["end_reason"]] += 1
        durations.append(len(log["frames"]) - 1)
        for place, fighter in enumerate(log["ranking"]):
            ranking_points[archetype(fighter)] += len(log["ranking"]) - 1 - place
        final = log["frames"][-1]["players"]
        kill_dist[final[log["winner"]]["eliminations"]] += 1
        last_damage: dict[str, str] = {}
        for frame in log["frames"]:
            for ev in frame["events"]:
                kind = ev["type"]
                if kind == "hit":
                    weapon_hits[ev["weapon"]] += 1
                    weapon_damage[ev["weapon"]] += ev["damage"]
                    hat_matrix[(hats[ev["attacker"]], hats[ev["target"]])] += 1
                    last_damage[ev["target"]] = "combat"
                elif kind == "storm":
                    last_damage[ev["id"]] = "storm"
                elif kind == "use":
                    medkit_uses[archetype(ev["id"])] += 1
                elif kind == "heal":
                    elim_heals[archetype(ev["id"])] += 1
                elif kind == "elimination":
                    if last_damage.get(ev["id"]) == "storm":
                        deaths_by_storm[archetype(ev["id"])] += 1
                    else:
                        deaths_by_combat[archetype(ev["id"])] += 1
        for id_, p in final.items():
            if p["eliminations"]:
                kills[archetype(id_)] += p["eliminations"]

    durations.sort()
    return {"seeds": seeds, "archetypes": archetypes, "entrants": entrants, "wins": wins,
            "ranking_points": ranking_points, "end_reasons": end_reasons,
            "durations": durations, "kills": kills, "kill_dist": kill_dist,
            "deaths_by_storm": deaths_by_storm, "deaths_by_combat": deaths_by_combat,
            "weapon_hits": weapon_hits, "weapon_damage": weapon_damage,
            "medkit_uses": medkit_uses, "elim_heals": elim_heals, "hat_matrix": hat_matrix}


def report(stats: dict) -> str:
    seeds = stats["seeds"]
    d = stats["durations"]
    lines = [
        f"seeds={seeds}  end_reasons={dict(stats['end_reasons'])}",
        f"duration ticks: min={d[0]} p25={d[len(d)//4]} median={d[len(d)//2]} "
        f"p75={d[3*len(d)//4]} max={d[-1]}",
        f"winner kill counts: {dict(sorted(stats['kill_dist'].items()))}",
        "",
        f"{'archetype':<13}{'entr':>5}{'wins':>6}{'win%/slot':>10}{'rankpts':>9}"
        f"{'kills':>7}{'d_storm':>9}{'d_combat':>9}{'medkits':>9}{'elimheal':>9}",
    ]
    for a in stats["archetypes"]:
        n = stats["entrants"][a]
        per_slot = 100 * stats["wins"][a] / (seeds * n)
        lines.append(f"{a:<13}{n:>5}{stats['wins'][a]:>6}{per_slot:>9.1f}%"
                     f"{stats['ranking_points'][a]:>9}{stats['kills'][a]:>7}"
                     f"{stats['deaths_by_storm'][a]:>9}{stats['deaths_by_combat'][a]:>9}"
                     f"{stats['medkit_uses'][a]:>9}{stats['elim_heals'][a]:>9}")
    lines.append("")
    lines.append(f"{'weapon':<10}{'hits':>7}{'damage':>9}{'dmg/hit':>9}")
    for w in sorted(stats["weapon_hits"]):
        hits, dmg = stats["weapon_hits"][w], stats["weapon_damage"][w]
        lines.append(f"{w:<10}{hits:>7}{dmg:>9}{dmg/hits:>9.1f}")
    lines.append("")
    same = sum(n for (a, t), n in stats["hat_matrix"].items() if a == t)
    diff = sum(n for (a, t), n in stats["hat_matrix"].items() if a != t)
    lines.append(f"hat aggression: same-hat hits={same}  cross-hat hits={diff}")
    lines.append("")
    lines.append("advisory diagnostics (informational, not pass/fail):")
    advisories = []
    for a in stats["archetypes"]:
        share = stats["wins"][a] / seeds
        if share > 0.30:
            advisories.append(f"  - {a} wins {share:.0%} of matches "
                              f"({stats['entrants'][a]} slot(s)) — watch for dominance")
    timeout_rate = 1 - stats["end_reasons"].get("last_survivor", 0) / seeds
    if timeout_rate > 0.10:
        advisories.append(f"  - {timeout_rate:.1%} of matches end by tiebreak — investigate standoffs")
    for w in ("sword", "spear", "bow", "unarmed"):
        if stats["weapon_hits"].get(w, 0) == 0:
            advisories.append(f"  - weapon '{w}' recorded zero hits — dead content?")
    lines.extend(advisories or ["  - none"])
    return "\n".join(lines)


def narrate_encounters(seed: int, limit: int = 3) -> str:
    """Pull readable combat sequences out of one match for the report."""
    log = run_match(REFERENCE_BOTS, seed=seed)
    lines = [f"--- seed {seed}: winner {log['winner']} after {len(log['frames'])-1} ticks ---"]
    shown = 0
    for frame in log["frames"]:
        combat = [e for e in frame["events"] if e["type"] in ("hit", "elimination", "heal", "drop")]
        if not any(e["type"] == "hit" for e in combat):
            continue
        for e in combat:
            if e["type"] == "hit":
                lines.append(f"  t{frame['tick']:>3}: {e['attacker']} -> {e['target']} "
                             f"{e['damage']} dmg ({e['weapon']})")
            elif e["type"] == "elimination":
                lines.append(f"  t{frame['tick']:>3}: {e['id']} ELIMINATED")
            elif e["type"] == "heal":
                lines.append(f"  t{frame['tick']:>3}: {e['id']} +{e['amount']} elim heal")
            elif e["type"] == "drop":
                items = ", ".join(f"{i['count']}x{i['kind']}" for i in e["items"])
                lines.append(f"  t{frame['tick']:>3}: {e['id']} drops {items}")
        if any(e["type"] == "elimination" for e in combat):
            shown += 1
            lines.append("")
            if shown >= limit:
                break
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=500)
    parser.add_argument("--elim-heal", type=int, default=None,
                        help="override Config.elim_heal for A/B balance comparison")
    parser.add_argument("--encounters", type=int, default=0,
                        help="narrate combat from this many seeds")
    args = parser.parse_args()
    cfg = replace(Config(), elim_heal=args.elim_heal) if args.elim_heal is not None else Config()
    print(report(analyze(args.seeds, cfg)))
    for s in range(1, args.encounters + 1):
        print()
        print(narrate_encounters(seed=s))
