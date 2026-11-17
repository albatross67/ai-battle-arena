"""Local practice harness: run your compiled bot against the reference lineup.

    python -m arena.practice my_bot.wasm --seeds 50 [--json report.json]

Practice seeds (1..N) are public. Official tournament seeds, map realizations,
and opponent lineups are hidden until evaluation and WILL differ. Exit codes:
0 = clean run, 1 = your bot faulted at least once, 2 = module rejected at load.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .bots import REFERENCE_BOTS
from .engine import run_match
from .wasm_bot import make_wasm_strategy

BOT_ID = "W1_You"


def run_practice(wasm_path: str | Path, seeds: int) -> dict:
    lineup_ids = sorted(set(REFERENCE_BOTS) - {"A2_Berserker"}) + [BOT_ID]
    strategy = make_wasm_strategy(wasm_path, lineup_ids)
    placements = []
    survival_ticks = []
    wins = 0
    kills = 0
    damage_done = 0
    damage_taken = 0
    deaths = Counter()
    errors = Counter()
    first_error = None
    for seed in range(1, seeds + 1):
        lineup = {k: v for k, v in REFERENCE_BOTS.items() if k != "A2_Berserker"}
        lineup[BOT_ID] = strategy
        log = run_match(lineup, seed=seed)
        placements.append(log["ranking"].index(BOT_ID) + 1)
        if log["winner"] == BOT_ID:
            wins += 1
        me = log["frames"][-1]["players"][BOT_ID]
        survival_ticks.append(me["survival_tick"])
        kills += me["eliminations"]
        damage_done += me["damage_done"]
        last_damage = None
        for frame in log["frames"]:
            for ev in frame["events"]:
                if ev["type"] == "bot_error" and ev["id"] == BOT_ID:
                    errors[ev["reason"]] += 1
                    if first_error is None:
                        first_error = {"seed": seed, "tick": frame["tick"],
                                       "reason": ev["reason"]}
                elif ev["type"] == "hit" and ev["target"] == BOT_ID:
                    damage_taken += ev["damage"]
                    last_damage = "combat"
                elif ev["type"] == "storm" and ev["id"] == BOT_ID:
                    last_damage = "storm"
                elif ev["type"] == "elimination" and ev["id"] == BOT_ID:
                    deaths[last_damage or "combat"] += 1
    placements.sort()
    n = len(placements)
    return {"wasm_sha256": strategy.sha256, "seeds": seeds, "wins": wins,
            "win_rate": wins / seeds,
            "placement": {"best": placements[0], "median": placements[n // 2],
                          "worst": placements[-1]},
            "median_survival_tick": sorted(survival_ticks)[n // 2],
            "kills": kills, "damage_done": damage_done, "damage_taken": damage_taken,
            "deaths": dict(deaths), "bot_errors": dict(errors),
            "first_error": first_error}


def report(stats: dict) -> str:
    lines = [f"bot sha256: {stats['wasm_sha256'][:16]}…  seeds: {stats['seeds']}",
             f"wins: {stats['wins']} ({stats['win_rate']:.0%})  "
             f"placement best/median/worst: {stats['placement']['best']}/"
             f"{stats['placement']['median']}/{stats['placement']['worst']}",
             f"median survival tick: {stats['median_survival_tick']}  "
             f"kills: {stats['kills']}  dmg dealt/taken: "
             f"{stats['damage_done']}/{stats['damage_taken']}",
             f"deaths: {stats['deaths']}"]
    if stats["bot_errors"]:
        lines.append(f"BOT ERRORS: {stats['bot_errors']}")
        lines.append(f"first error: {stats['first_error']} — fix this before submitting!")
    else:
        lines.append("no bot errors — your module ran cleanly on every tick")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wasm", type=Path)
    parser.add_argument("--seeds", type=int, default=50)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    try:
        stats = run_practice(args.wasm, args.seeds)
    except (ValueError, OSError) as exc:
        print(f"module rejected: {exc}")
        sys.exit(2)
    print(report(stats))
    if args.json:
        args.json.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
        print(f"json report: {args.json}")
    sys.exit(1 if stats["bot_errors"] else 0)


if __name__ == "__main__":
    main()
