# Writing a WASM Fighter (API v1 — DRAFT, not frozen)

Your fighter is a WebAssembly module. You write **two functions in C** and the
arena does everything else. You compile and test **your own** bot locally with
the tools below; tournament submissions are **not open yet**, and when they do
open, they will be compiled and run only on isolated remote infrastructure —
never run anyone else's bot on your machine (see [SECURITY.md](../SECURITY.md)).


## The whole contract

```c
#include "arena_bot.h"

static ArenaObs OBS;            // the arena writes your observation here
static ArenaAct ACT;            // you write your action here

int32_t *obs_buf(void) { return (int32_t *)&OBS; }
int32_t *act_buf(void) { return (int32_t *)&ACT; }

int32_t init(int32_t api_version, int32_t size, int32_t fighters, int32_t self_index) {
    return HAT_RED;             // called once; pick your public hat color
}

int32_t act(void) {             // called every tick
    ACT.type = ACT_MOVE;        // read OBS, fill ACT
    ACT.arg  = DIR_N;
    return 0;
}
```

Everything is little-endian `int32`. [wasm/arena_bot.h](../wasm/arena_bot.h) is
the single source of truth for struct layouts, enums, capacities, and weapon
rules. [wasm/starter_bot.c](../wasm/starter_bot.c) is a complete working fighter
(~150 lines) that moves, loots, attacks, heals, and picks a hat — copy it and
edit the numbered priority blocks.

## Rules of the sandbox

- **No imports.** No WASI, no host functions, no files, no network, no clocks.
  A module that imports anything is rejected before the match, as is a module
  over 1 MB or with wrong export signatures.
- **Fuel-limited.** Each complete turn gets ONE instruction budget (currently
  5,000,000), shared by everything your module does that tick; instantiation
  plus `init` share one budget too. Run out and your turn becomes WAIT with a
  logged `bot_error`.
- **Write your action every tick.** The host pre-fills the act buffer with an
  invalid sentinel; leaving it unchanged is a fault. `act()`'s return value is
  reserved — currently ignored.
- **Memory-capped** at 2 MiB. `memory.grow` beyond that fails (returns -1).
- **Persistent memory.** Your linear memory and globals survive between ticks of
  one match — that's your strategy memory. It is wiped between matches.
- **Faults are survivable for the match, fatal for you.** A trap, bad pointer,
  or invalid action poisons your instance: you WAIT for the rest of the match.
- **Determinism.** Same module bytes + same seed = same match, always. Don't use
  floats; there is no randomness available (derive pseudo-randomness from the
  observation if you want it).

## Observation and action summary

`ArenaObs` gives you: tick, board size, safe-zone radius, living-fighter count,
your own stats (position, hp, weapon, arrows, medkits, hat), every **visible**
enemy (vision radius 8, rocks occlude; stable fighter indices), visible loot,
and rocks in view. `ArenaAct` is `{type, arg}`:

| type | arg | effect |
|---|---|---|
| `ACT_WAIT` | — | do nothing |
| `ACT_MOVE` | `DIR_N/S/E/W` | step one tile (blocked by rocks/fighters/edge) |
| `ACT_ATTACK` | enemy `index` | attack if your weapon reaches (see header) |
| `ACT_GUARD` | — | halve damage, fatigues after 2 consecutive ticks |
| `ACT_PICKUP` | — | swap weapon with one on your tile (full turn) |
| `ACT_USE` | — | use a medkit, +30 hp (full turn) |

Illegal actions (out of range, no ammo, nothing to pick up) are silently wasted
turns — the engine never crashes because of them.

## Building

Requires LLVM clang (Windows: `winget install -e --id LLVM.LLVM`). No libc, no
wasi-sdk. Run from the repo root — `-Iwasm` is what lets the compiler find
`arena_bot.h` (if your `.c` file lives inside `wasm/` instead, you can omit it):

```
clang --target=wasm32 -O2 -nostdlib -Iwasm -Wl,--no-entry \
  -Wl,--export=obs_buf -Wl,--export=act_buf -Wl,--export=init -Wl,--export=act \
  -o my_bot.wasm my_bot.c
```

or on Windows just run `wasm/build.ps1`.

## Testing locally

```
pip install wasmtime
python -m arena.practice my_bot.wasm --seeds 50
```

The practice harness plays your fighter against nine reference bots across the
public practice seeds and reports wins, placements, survival, damage, deaths,
and — most importantly — any `bot_error` faults (exit code 1 if your bot faulted,
2 if the module was rejected at load). Official tournament seeds, maps, and
lineups are hidden and will differ. For a single animated match:

```
python -m arena.cli --seed 99 --wasm my_bot.wasm
```

This plays your fighter (as `W1_Wasm`) against nine reference bots, writes the
animated replay `output/replay_seed_99.html`, and records a reproducibility
manifest (`.manifest.json`) with your module's SHA-256, the API/engine/wasmtime
versions, and the fuel/memory limits. `python -m pytest tests/test_wasm.py`
exercises the adapter, including hostile-module negative tests.

## Versioning

`ARENA_API_VERSION` is 1 and **not frozen** — struct layouts may still change.
`init` receives the engine's API version; return `-1` if you don't support it
(you'll sit the match out as RED rather than misread memory).
