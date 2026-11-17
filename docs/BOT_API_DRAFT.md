# Game Rules & Bot API Reference — DRAFT, NOT FROZEN (prototype-0.4)

This document is the authoritative reference for the **game rules**: observations,
actions, combat mechanics, tick ordering, and how matches end. It describes the
contract conceptually, using Python-style dicts, because the engine and its
reference bots are written in Python.

For the **C/WASM interface** your fighter actually implements, this document is
*not* authoritative: [wasm/arena_bot.h](../wasm/arena_bot.h) is the single
source of truth for struct layouts, enums, and capacities (ABI v1, draft), and
[WASM_BOT.md](WASM_BOT.md) documents the sandbox rules (fuel budget, memory cap,
fault behavior). The WASM structs carry the same information described below,
flattened into int32 fields.

Conceptual Python reference function:

```python
def act(observation: dict, memory: dict) -> tuple[dict, dict]:
    return {"type": "move", "direction": "N"}, memory
```

## Setup phase (tick 0)

Each bot is called once before the match with `{"phase": "setup", "tick": 0, "size", "fighters",
"safe_radius", "self": {id, x, y, hp}}` and should answer `{"type": "hat", "color": C}` where `C`
is one of `RED | BLUE | GREEN | YELLOW`. Invalid answers or exceptions default to RED and log a
`bot_error` event. Hats are **public cosmetic signals chosen by strategies**: no gameplay bonus,
no enforced teams, friendly fire stays on, one winner per match. The returned memory dict is kept.

## Play-phase observations (fog of war)

Vision is Chebyshev radius 8, and rocks occlude (integer supercover line between tile centers;
exact corner crossings pass diagonally and do not block). Replay files remain omniscient.

- `phase`: `"play"`; `tick`: 1-indexed; `size`: odd board dimension; `alive`: living fighter count
- `safe_radius`: Chebyshev radius of the safe square measured from board center
- `self`: `id, x, y, hp, weapon, arrows, medkits, hat`
- `enemies`: visible living fighters — `id, x, y, hp, weapon, hat` (ammo/medkits are private)
- `loot`: visible items — `x, y, kind, count` with kind in `sword | spear | bow | arrows | medkit`
- `rocks`: impassable, LOS-blocking tiles within vision radius

## Actions (one per tick; invalid/impossible actions are silent no-ops)

- `{"type": "move", "direction": "N|S|E|W"}` — blocked by board edge, rocks, living fighters
- `{"type": "attack", "target": "<id>"}` — validated against post-movement positions:
  - unarmed: 8 dmg, Chebyshev distance 1 (all 8 neighbors); a diagonal swing is blocked
    only when BOTH orthogonal tiles flanking the shared corner are rocks
  - sword: 25 dmg, same Chebyshev-1 metric and corner rule as unarmed
  - spear: 16 dmg, orthogonal line, Manhattan 1–2; at reach 2 the between-tile must not be rock
    (no diagonals — reach is the spear's identity, coverage is the sword's)
  - bow: 14 dmg, 8-direction ray (same row, column, or exact diagonal), 2–6 steps
    (Chebyshev along ray), every intermediate ray tile rock-free, consumes 1 arrow.
    Fighters never block arrows or line of sight.
- `{"type": "guard"}` — halves incoming damage (rounded up), only for the first 2 *consecutive*
  guard ticks (guard fatigue)
- `{"type": "pickup"}` — swap equipped weapon with a weapon item on the current tile (old weapon
  drops to the tile); full-turn action
- `{"type": "use", "item": "medkit"}` — +30 hp capped at 100; full-turn action
- `{"type": "wait"}`

Free auto-pickup happens after movement each tick: arrows always, medkits up to the carry cap
of 2, and a weapon only if currently unarmed.

## Tick phase order (authoritative)

1. All bots observe the same pre-action snapshot, then actions are collected
   (exceptions/malformed results become `wait` plus a `bot_error` event).
2. Movement in rotated initiative order (`tick % living` offset into the sorted id list).
3. Auto-pickup, then explicit `pickup`/`use` actions, in sorted id order.
4. Attacks in the same rotated initiative order, sequentially; a fighter already at 0 hp loses
   its attack (no simultaneous double-KO).
5. Storm damage outside the safe radius: 10 base, +5 per completed shrink step, capped at 40.
6. Deaths resolve: corpse drops gear on its tile, the most-damaging attacker that tick is
   credited (+10 hp elimination heal, provisional) and frames are snapshotted.

## Deterministic endings

The safe radius shrinks to **0** (a single safe tile at ~tick 130 with default config), so
endgame standoffs are terminal. A match ends when one fighter remains (`last_survivor`) or at
`max_ticks` / total wipe (`elimination_or_timeout_tiebreak`). Ranking is always: alive, then
latest survival tick, then highest hp (negative hp counts), then most damage done, then a
**seed-derived participant priority** (shuffled with an RNG stream independent of gameplay,
recorded in replay metadata as `tiebreak_priority`) — so simultaneous storm deaths and timeouts
are fully deterministic without favoring lexicographically low ids.

## Deliberately unresolved before public submissions

- Final balance numbers (all of the above are provisional; elim heal is being A/B tested at 0/10/25)
- Whether bots receive a per-match deterministic RNG seed
- Tournament scoring, lineup sampling for ~40 entrants, and seed selection
- **Freezing** the C/WASM ABI: v1 is drafted in `wasm/arena_bot.h` with fuel/memory
  limits and fault behavior documented in `WASM_BOT.md`, but layouts and limits may
  still change until the freeze is announced
