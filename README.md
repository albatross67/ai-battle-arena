# AI Battle Arena

**A programming game.** Ten fighters spawn on a 25×25 grid. A storm shrinks the
safe zone, loot is scattered around the map, and the last fighter standing wins.
Every fighter is controlled by a small program — a *bot* — that looks at what it
can see each turn and picks one action: move, attack, guard, pick up, heal, or
wait.

This repository contains the complete game: the match engine, ten ready-made
reference bots, an animated browser viewer for replays, and everything you need
to **write your own fighter in C**, compile it to WebAssembly, and test it
against the reference lineup on your machine.

## Project status — read this first

| | Status |
|---|---|
| Game engine, reference bots, replay viewer | ✅ Working, deterministic, tested |
| WASM bot sandbox + local practice harness | ✅ Implemented and tested in this repo |
| Bot API (`ARENA_API_VERSION 1`) | ⚠️ **Draft** — may still change before it is frozen |
| Hosted tournament pipeline (remote compile/validate/match CI) | 🚧 **Prepared but not yet activated** |
| Submissions | ❌ **Not open yet** — nothing to submit to today |

Game balance numbers are also still provisional. Until submissions open,
everything here is for playing, reading, and practicing.

## Quick start: watch a match (Python 3.11+, no dependencies)

```bash
python -m arena.cli --seed 42
```

This plays one full match between the ten reference bots and writes
`output/replay_seed_42.html` — open it in any browser for an animated replay
with a timeline slider and speed controls. `output/replay_seed_42.json` is the
same match as structured per-turn data, and the command prints its SHA-256.

The game is **deterministic**: the same seed and the same code produce a
byte-identical replay, every time, on every machine. That property is what
makes fair, auditable tournaments possible.

Run the test suite with:

```bash
python -m unittest discover -s tests -v
```

## Write your own fighter

Your bot is a WebAssembly (WASM) module — a tiny sandboxed program. You write
two functions in C (`init` and `act`), the engine calls them, and a shared
block of integers carries your observation in and your action out. No game SDK
to learn: [wasm/starter_bot.c](wasm/starter_bot.c) is a complete working
fighter in ~150 commented lines.

```bash
pip install wasmtime            # the WASM runtime the engine embeds

# compile your bot (needs LLVM clang; on Windows: winget install -e --id LLVM.LLVM)
# run from the repo root; -Iwasm tells the compiler where arena_bot.h lives
clang --target=wasm32 -O2 -nostdlib -Iwasm -Wl,--no-entry \
  -Wl,--export=obs_buf -Wl,--export=act_buf -Wl,--export=init -Wl,--export=act \
  -o my_bot.wasm my_bot.c

# practice: 50 matches against the reference lineup on public practice seeds
python -m arena.practice my_bot.wasm --seeds 50

# watch one animated match with your bot in it
python -m arena.cli --seed 99 --wasm my_bot.wasm
```

Start with the **[participant quick start](docs/QUICKSTART.md)** — objective,
what your bot sees (fog of war), actions, sandbox rules, both participation
paths, and a five-minute smoke test. Then [docs/WASM_BOT.md](docs/WASM_BOT.md)
walks through the whole contract, the sandbox rules, building, and testing in
full. The commented header
[wasm/arena_bot.h](wasm/arena_bot.h) is the authoritative interface definition.
A prebuilt `wasm/starter_bot.wasm` is included (compiled from
`starter_bot.c` with the tournament's pinned toolchain) so you can try the
practice harness before installing a compiler.

> **Never run anyone else's bot on your machine** — only modules you compiled
> from source you wrote or read. See [SECURITY.md](SECURITY.md).

## What's in this repository

```
arena/        the game: engine, map generator, reference bots, CLI,
              replay viewer, WASM sandbox adapter, practice harness
wasm/         C bot kit: arena_bot.h (the interface), starter_bot.c (example),
              starter_bot.wasm (prebuilt example), build.ps1 (Windows build)
docs/         QUICKSTART.md (participant quick start: start here),
              WASM_BOT.md (how to write a fighter),
              BOT_API_DRAFT.md (full game-rules & API reference, draft)
ops/          scripts the hosted tournament pipeline will run — a submission
              validator, a match batch runner, and an isolation self-check;
              published for transparency, nothing here runs automatically
tests/        engine determinism/rules tests and hostile-module sandbox tests
```

## The reference bots are open — study them

[arena/bots.py](arena/bots.py) contains the full source of all ten reference
strategies (berserkers, archers, scavengers, evaders, and more). Reading them
and optimizing against them is allowed and encouraged. If a tournament is held,
official seeds, map realizations, and opponent lineups will be hidden and will
differ from practice.

## How a tournament would run (prepared, not yet active)

Tournament operations are managed from a separate private repository. The
planned flow, whose in-container tooling is published here under
[ops/](ops) for transparency:

1. At a deadline, submitted C source is snapshotted.
2. Each submission is compiled and validated on remote, disposable
   infrastructure — inside containers with no network, capped CPU/memory,
   a read-only filesystem, and no credentials — never on anyone's workstation.
3. Matches run the same way, at an exact pinned commit of this repository, and
   produce deterministic replays plus a reproducibility manifest (module
   hashes, engine/runtime versions, seed, replay hash) as the results of
   record.

**This pipeline has not been activated yet, and submissions are not open.**
Announcements will come separately; nothing in this repository signs you up
for anything.

## License

[MIT](LICENSE).
