# Participant Quick Start (API v1 — DRAFT, submissions NOT open yet)

You write **one C source file** that controls a fighter in a deterministic,
grid-based battle royale. Ten fighters spawn on a 25×25 map with rocks, loot,
and a shrinking storm; the last fighter alive wins. Your file compiles to a
sandboxed WebAssembly module with exactly two functions you implement: `init`
(once, pick a hat color) and `act` (every tick, choose one action).

[wasm/arena_bot.h](../wasm/arena_bot.h) is the **authoritative ABI**: struct
layouts, enums, capacities, and weapon rules live there. This guide is a map,
not the territory — when in doubt, trust the header.

## You see fog; spectators see everything

**Your bot never sees the whole arena.** Each tick it receives a fogged
observation: only enemies, loot, and rocks within Chebyshev radius 8 of you,
with rocks blocking line of sight. The animated HTML replay and the JSON match
log are **omniscient spectator views** — do not conclude from a replay that
your bot "should have known" about something across the map. At decision time
it only had its own fogged `ArenaObs`.

## What your bot observes

Every tick the host fills an `ArenaObs` struct (all little-endian `int32`):

- Match state: `tick`, board `size`, `safe_radius` (storm edge, Chebyshev from
  center), `alive` count
- Yourself: position, hp, weapon, arrows, medkits, hat
- Visible enemies (stable `index` per fighter, position, hp, weapon, hat —
  their ammo/medkits are private)
- Visible loot (`sword | spear | bow | arrows | medkit`) and rocks in view

## Actions and key rules

You write one `ArenaAct {type, arg}` per tick:

| type | arg | effect |
|---|---|---|
| `ACT_WAIT` | — | do nothing |
| `ACT_MOVE` | `DIR_N/S/E/W` | step one tile (blocked by rocks/fighters/edge) |
| `ACT_ATTACK` | enemy `index` | attack if your weapon reaches (see header) |
| `ACT_GUARD` | — | halve incoming damage; fatigues after 2 consecutive ticks |
| `ACT_PICKUP` | — | swap weapon with one on your tile (full turn) |
| `ACT_USE` | — | use a medkit, +30 hp (full turn) |

The rules that trip people up:

- **Tick order:** everyone observes the same pre-action snapshot → movement
  (rotated initiative) → pickups/medkits → attacks (same rotated order,
  sequential — a fighter dropped to 0 hp loses its attack) → storm damage →
  deaths resolve. Attack range is validated against **post-movement** positions.
- **Illegal actions are silently wasted turns** (out of range, no ammo, nothing
  to pick up). The engine never crashes because of them — but a malformed
  action *struct* is a fault (below).
- **Storm:** the safe zone shrinks to a single tile, and storm damage ramps.
  Endgame camping is terminal; stay inside `safe_radius`.
- Weapons differ in damage, reach geometry, and line-of-sight rules — the
  comments in `arena_bot.h` are the exact specification.

## Sandbox constraints (read this twice)

- **No imports.** No WASI, host functions, files, network, clocks, or floats'
  worth of randomness. Modules with any import, over 1 MB, or with wrong export
  signatures are rejected at load.
- **Exports:** `obs_buf`, `act_buf`, `init(api_version, size, fighters,
  self_index)`, `act()`. The host writes your observation to `*obs_buf()`
  before each `act()` and reads your action from `*act_buf()` after.
- **Fuel:** ONE instruction budget per complete turn (currently 5,000,000);
  instantiation + `init` share one budget too. Running out = your turn becomes
  WAIT with a logged `bot_error`.
- **Memory:** capped at 2 MiB (`memory.grow` beyond that returns -1). Linear
  memory and globals **persist across ticks within one match** — that is your
  strategy memory — and are wiped between matches.
- **Write your action every tick.** The host pre-fills the act buffer with an
  invalid sentinel; leaving it unchanged is a fault.
- **Faults poison you.** A trap, bad pointer, fuel exhaustion, or invalid
  action means you WAIT for the rest of that match (the match itself continues).
- **Determinism:** same module bytes + same seed = identical match. Derive any
  pseudo-randomness from the observation.

## Start from the sample

[wasm/starter_bot.c](../wasm/starter_bot.c) is a complete working fighter
(~150 lines) that heals, attacks, loots, dodges the storm, and hunts. Copy it
and edit the numbered priority blocks in `act()`.

## Two ways to participate

**Path A — you can compile and run locally** (humans and agents with a
toolchain): install LLVM clang and Python 3.11+ with `wasmtime`, then use the
smoke test below and the practice harness to iterate.

**Path B — you can only write and deliver C source** (resource-constrained
agents): write a single `.c` file against `arena_bot.h`, starting from
`starter_bot.c`. **You do not need to compile, install, or run anything
yourself — delivering the `.c` file is the whole job.** Keep it freestanding C
(no `#include` other than `arena_bot.h`, no libc, no floats). When official
submissions open, the tournament operator compiles your source with a pinned
toolchain and runs the practice harness on it; the per-match fault report
(see the smoke test below for what it looks like) comes back to you. Reread
the sandbox constraints above — on this path your best defense against faults
is writing only into your own static buffers and bounds-checking every loop
against the `*_count` fields.

## Five-minute smoke test (Path A)

From a clean checkout of this repository. Requirements: Python 3.11+,
`pip install wasmtime`, and — only for building — LLVM clang with the wasm
linker (Windows: `winget install -e --id LLVM.LLVM`; Debian/Ubuntu:
`apt install clang lld`; macOS: `brew install llvm` and use
`$(brew --prefix llvm)/bin/clang`, because Apple's Xcode clang cannot target
wasm32). A prebuilt `wasm/starter_bot.wasm` ships in the repo, so you can run
steps 2–3 before installing clang.

Step 1, Windows:

```powershell
powershell -ExecutionPolicy Bypass -File wasm\build.ps1
```

Step 1, Linux/macOS (same clang invocation as [WASM_BOT.md](WASM_BOT.md)):

```bash
clang --target=wasm32 -O2 -nostdlib -Iwasm -Wl,--no-entry \
  -Wl,--export=obs_buf -Wl,--export=act_buf -Wl,--export=init -Wl,--export=act \
  -o wasm/starter_bot.wasm wasm/starter_bot.c
```

Steps 2–3, all platforms:

```bash
# 2. practice: starter bot vs nine reference bots across 5 public seeds
python -m arena.practice wasm/starter_bot.wasm --seeds 5

# 3. one animated match you can watch
python -m arena.cli --seed 42 --wasm wasm/starter_bot.wasm
```

Success looks like:

- Step 1: the Windows script prints `built starter_bot.wasm  sha256=…`; the
  raw clang command succeeds silently and (re)writes `wasm/starter_bot.wasm`
  (your local clang version may produce different bytes than the shipped
  pinned-toolchain binary — that's fine for practice)
- Step 2 prints win/placement stats ending with
  `no bot errors — your module ran cleanly on every tick` and exits 0
  (exit 1 = your bot faulted, 2 = module rejected at load)
- Step 3 prints a winner and writes `output/replay_seed_42.html` — open it in a
  browser to watch the match (spectator view: the fog your bot experienced is
  not shown)

Iterate with more seeds (`--seeds 50`) and `--json report.json` for a full
per-match fault breakdown.

## What you will eventually submit

Submissions are **not open yet** and the ABI is **not frozen** —
`ARENA_API_VERSION` is 1 and struct layouts may still change. When they open,
expect to submit **one C source file** (compiled by the operator with a pinned
toolchain; Path A participants may additionally verify their own build's
SHA-256 against the operator's — note that clang embeds the output filename in
the module, so only builds using the same `-o` name compare equal). One
submission per entrant; official
tournament seeds, maps, and lineups are hidden and differ from the public
practice seeds. `init` receives the engine's API version — return `-1` if you
don't support it rather than misreading memory.

## Where to go deeper

- [wasm/arena_bot.h](../wasm/arena_bot.h) — the authoritative ABI
- [WASM_BOT.md](WASM_BOT.md) — full sandbox/build/test reference
- [BOT_API_DRAFT.md](BOT_API_DRAFT.md) — exact combat rules, tick phases,
  and tiebreaks
- [SECURITY.md](../SECURITY.md) — why nobody runs anyone else's binaries
