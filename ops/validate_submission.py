"""In-container submission validation (W2 stage 2). Trusted code, untrusted input.

    python ops/validate_submission.py path/to/bot.wasm [--json out.json]

Checks: module size, imports/exports/signatures (via the hardened adapter),
init + hat handshake, a fault-free 5-seed smoke run against the reference
lineup, and byte-identical determinism across two fresh-adapter runs.
Exit 0 = ACCEPTED, 1 = REJECTED (reasons in the report).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SMOKE_SEEDS = 5
DETERMINISM_SEED = 1


def validate(wasm_path: Path) -> dict:
    from arena.bots import REFERENCE_BOTS
    from arena.engine import VERSION, run_match
    from arena.practice import run_practice
    from arena.wasm_bot import API_VERSION, FUEL_PER_TURN, MEMORY_LIMIT, make_wasm_strategy

    report = {"wasm": str(wasm_path), "engine_version": VERSION,
              "abi_version": API_VERSION, "fuel_per_turn": FUEL_PER_TURN,
              "memory_limit": MEMORY_LIMIT, "reasons": [], "verdict": "REJECTED"}
    try:
        report["wasm_sha256"] = hashlib.sha256(wasm_path.read_bytes()).hexdigest()
    except OSError as exc:
        report["reasons"].append(f"unreadable: {exc}")
        return report

    ids = sorted(set(REFERENCE_BOTS) - {"A2_Berserker"}) + ["W1_You"]
    try:
        make_wasm_strategy(wasm_path, ids)  # size/imports/exports/signatures
    except ValueError as exc:
        report["reasons"].append(f"module rejected: {exc}")
        return report

    smoke = run_practice(wasm_path, seeds=SMOKE_SEEDS)
    report["smoke"] = {k: smoke[k] for k in ("placement", "bot_errors", "first_error")}
    if smoke["bot_errors"]:
        report["reasons"].append(f"bot faulted during smoke run: {smoke['bot_errors']} "
                                 f"(first: {smoke['first_error']})")

    def one_run():
        lineup = {k: v for k, v in REFERENCE_BOTS.items() if k != "A2_Berserker"}
        lineup["W1_You"] = make_wasm_strategy(wasm_path, ids)
        log = run_match(lineup, seed=DETERMINISM_SEED)
        return hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest()

    first = one_run()
    if one_run() != first:
        report["reasons"].append("nondeterministic: identical runs produced different replays")
    report["determinism_replay_sha256"] = first

    if not report["reasons"]:
        report["verdict"] = "ACCEPTED"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wasm", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    report = validate(args.wasm)
    print(json.dumps(report, indent=2))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.exit(0 if report["verdict"] == "ACCEPTED" else 1)


if __name__ == "__main__":
    main()
