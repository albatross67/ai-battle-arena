"""Run a trusted demo match: python -m arena.cli --seed 42 [--wasm wasm/starter_bot.wasm]"""
import argparse
import hashlib
import json
from pathlib import Path

from .bots import REFERENCE_BOTS
from .engine import VERSION, run_match
from .viewer import render_html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--wasm", type=Path, default=None,
                        help="TRUSTED .wasm bot; replaces one reference slot as W1_Wasm")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lineup = dict(REFERENCE_BOTS)
    wasm_strategy = None
    if args.wasm:
        from .wasm_bot import API_VERSION, FUEL_PER_CALL, MEMORY_LIMIT, make_wasm_strategy
        del lineup["A2_Berserker"]  # keep 10 fighters: 9 Python + 1 WASM
        ids = sorted(lineup) + ["W1_Wasm"]
        wasm_strategy = make_wasm_strategy(args.wasm, ids)
        lineup["W1_Wasm"] = wasm_strategy
    log = run_match(lineup, seed=args.seed)
    encoded = (json.dumps(log, sort_keys=True, indent=2) + "\n").encode()
    replay_path = args.output / f"replay_seed_{args.seed}.json"
    replay_path.write_bytes(encoded)
    html_path = args.output / f"replay_seed_{args.seed}.html"
    render_html(log, html_path)
    print(f"Winner: {log['winner']} | ticks: {len(log['frames'])-1} | reason: {log['end_reason']}")
    print(f"Replay JSON: {replay_path}")
    print(f"Animated viewer: {html_path}")
    print(f"SHA256: {hashlib.sha256(encoded).hexdigest()}")
    if wasm_strategy:
        from importlib.metadata import version as pkg_version
        manifest = {
            "seed": args.seed,
            "engine_version": VERSION,
            "wasm_api_version": API_VERSION,
            "wasm_sha256": wasm_strategy.sha256,
            "wasmtime_version": pkg_version("wasmtime"),
            "fuel_per_call": FUEL_PER_CALL,
            "memory_limit_bytes": MEMORY_LIMIT,
            "fighters": log["fighters"],
            "replay_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        manifest_path = args.output / f"replay_seed_{args.seed}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Reproducibility manifest: {manifest_path}")


if __name__ == "__main__":
    main()
