# SECURITY

This project will eventually accept bot programs from strangers and run them in
matches. That makes "where does untrusted code run?" the central security
question. This document explains the rule for you as a user, what protections
already exist in this repository, and what is planned but **not yet active**.

## The one rule for everyone

**Never run third-party bot code on your own machine — not as Python, and not
as WASM.** Do not dynamically import, `exec`, `eval`, or install bots from
untrusted sources, and do not copy someone else's bot into `arena/bots.py`.

The only code this repository asks you to run locally is the engine itself,
the reference bots that ship with it, and **your own** WASM module via
`arena.practice` / `arena.cli`. WebAssembly is an isolation layer, not a
guarantee of perfect security — treat an unknown `.wasm` file with the same
suspicion as an unknown `.exe`.

## Implemented and tested in this repository ✅

The WASM sandbox adapter (`arena/wasm_bot.py`) enforces, for every module it
loads:

- **No imports of any kind** — no WASI, no host functions, no files, no
  network, no clocks. A module that imports anything is rejected before play.
- **Module validation before execution** — 1 MB size cap (checked before the
  module is even parsed), required exports present with exactly the right
  type signatures.
- **One metered instruction budget ("fuel") per turn** — a bot that loops
  forever simply runs out of fuel and forfeits that turn; instantiation and
  `init` share one budget too.
- **2 MiB memory cap**, plus stack and table limits; growing beyond the cap
  fails harmlessly.
- **Strict I/O validation** — buffer pointers are range- and alignment-checked
  and may not overlap; the action buffer is pre-filled with an invalid
  sentinel each turn, so a bot that writes nothing faults instead of silently
  repeating its previous action; malformed actions fault.
- **Fault containment** — any trap, fuel exhaustion, bad pointer, or invalid
  action becomes a logged `bot_error` and a wasted turn; the faulting bot is
  poisoned (it waits for the rest of the match) and can never crash or hang
  the engine.

These behaviors are exercised by a hostile-module test suite
(`tests/test_wasm.py`): traps, infinite loops, memory bombs, bad pointers,
overlapping buffers, wrong export signatures, import smuggling, oversized
modules, and unwritten actions.

## Prepared but NOT yet activated 🚧

A hosted tournament pipeline exists as reviewed-but-inactive automation in a
separate private operations repository. When activated, it will process
submissions only on remote, disposable infrastructure:

- **Isolated compilation**: participant C source compiled with a pinned
  toolchain inside a container with no network, capped CPU/memory/processes, a
  read-only root filesystem, all privileges dropped, and a non-root user; only
  the one submission and the public header are mounted.
- **Isolated execution**: validation and matches run under the same container
  profile — the WASM sandbox above is defense-in-depth inside that wall, not
  the only wall.
- **Boundary canary**: before any untrusted stage, `ops/isolation_canary.sh`
  (published in this repo) runs inside the container and aborts the job if the
  boundary is weaker than expected — root user, network interfaces, kernel
  capabilities, writable root filesystem, or visible credentials.
- **Least-privilege CI**: manually triggered, read-only, no long-lived
  credentials, cannot write to any repository; outputs leave only as build
  artifacts for human review. Everything pinned: CI actions by commit SHA,
  container images by digest, this engine by commit SHA.
- **Reproducibility**: every validated module and match manifest records
  SHA-256 hashes, engine/ABI/runtime versions, resource limits, and seeds;
  deterministic replays are the results of record.

**Until that pipeline is activated and announced, submissions are not open and
no external code is compiled or executed anywhere.**

## Known limitations (honest list)

- The bot API (`ARENA_API_VERSION 1`) is a **draft**; layouts and limits may
  change until it is frozen before submissions open.
- Game balance parameters are provisional.
- The Wasmtime adapter has not had an independent external security review as
  an untrusted-code boundary — one reason untrusted code will additionally be
  confined to disposable containers.

## Reporting a vulnerability

If you find a way for a bot module to escape the sandbox, affect another bot,
read anything it shouldn't, or break determinism, please report it privately
to the repository owner (GitHub's "Report a vulnerability" / private advisory,
if enabled, or a direct message) rather than opening a public issue.
