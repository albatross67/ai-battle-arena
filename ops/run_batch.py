"""In-container match batch runner (W3). Trusted code, untrusted modules.

    python ops/run_batch.py schedule.json --submissions DIR --out DIR [--shard N --of M]

schedule.json: [{"match_id": "m0001", "seed": 123, "lineup": ["P01", ...]}, ...]
Writes per match: <out>/<match_id>.replay.json and <match_id>.manifest.json.
Scheduling and scoring are produced elsewhere; this runner only executes.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PID_PATTERN = r"^P[0-9]{2}$"


def engine_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, cwd=Path(__file__).resolve().parents[1],
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_one(entry: dict, submissions: Path, out: Path, commit: str) -> dict:
    import re
    from importlib.metadata import version as pkg_version
    from arena.engine import VERSION, run_match
    from arena.wasm_bot import API_VERSION, FUEL_PER_TURN, MEMORY_LIMIT, make_wasm_strategy

    pids = entry["lineup"]
    if len(set(pids)) != len(pids) or not all(re.match(PID_PATTERN, p) for p in pids):
        raise ValueError(f"{entry['match_id']}: invalid lineup {pids}")
    fighters = {}
    hashes = {}
    for pid in pids:
        wasm = submissions / pid / "bot.wasm"
        strategy = make_wasm_strategy(wasm, pids)
        fighters[pid] = strategy
        hashes[pid] = strategy.sha256
    log = run_match(fighters, seed=entry["seed"])
    encoded = (json.dumps(log, sort_keys=True, indent=2) + "\n").encode()
    replay_sha = hashlib.sha256(encoded).hexdigest()
    (out / f"{entry['match_id']}.replay.json").write_bytes(encoded)
    faults = {}
    for frame in log["frames"]:
        for ev in frame["events"]:
            if ev["type"] == "bot_error":
                faults.setdefault(ev["id"], []).append(ev["reason"])
    manifest = {"match_id": entry["match_id"], "seed": entry["seed"],
                "engine_version": VERSION, "engine_commit": commit,
                "abi_version": API_VERSION,
                "wasmtime_version": pkg_version("wasmtime"),
                "fuel_per_turn": FUEL_PER_TURN, "memory_limit_bytes": MEMORY_LIMIT,
                "lineup": pids, "wasm_sha256": hashes,
                "winner": log["winner"], "ranking": log["ranking"],
                "end_reason": log["end_reason"], "ticks": len(log["frames"]) - 1,
                "faults": faults, "replay_sha256": replay_sha, "status": "ok"}
    (out / f"{entry['match_id']}.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("schedule", type=Path)
    parser.add_argument("--submissions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--of", type=int, default=1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    commit = engine_commit()
    mine = [m for i, m in enumerate(schedule) if i % args.of == args.shard]
    done = 0
    for entry in mine:
        manifest = run_one(entry, args.submissions, args.out, commit)
        done += 1
        print(f"{manifest['match_id']}: winner={manifest['winner']} "
              f"ticks={manifest['ticks']} faults={len(manifest['faults'])}")
    print(f"shard {args.shard}/{args.of}: {done} matches complete")


if __name__ == "__main__":
    main()
