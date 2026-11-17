"""Wasmtime adapter: runs a TRUSTED sample .wasm bot as an engine strategy.

Sandbox posture for this milestone: no imports of any kind (no WASI, no host
functions), ONE fuel budget per complete turn (and one for instantiation+init),
capped linear memory/tables/stack, module size cap before compilation, export
signature validation, disjoint validated buffers, and a per-tick action sentinel
so stale output cannot repeat. This is defense-in-depth for trusted samples, not
yet a reviewed boundary for untrusted iLands submissions — those will compile
and run on isolated remote infrastructure.

Every fault (trap, fuel exhaustion, bad pointer, invalid or missing action)
raises a typed exception; the engine's existing handler turns it into WAIT plus
a bot_error event, so a faulty bot can never crash or hang a match. After a
fault the instance is poisoned for the rest of the match.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

API_VERSION = 1
FUEL_PER_TURN = 5_000_000  # one budget per complete turn (and one for setup)
FUEL_PER_CALL = FUEL_PER_TURN  # backwards-compatible alias
MEMORY_LIMIT = 2 * 1024 * 1024  # bytes of guest linear memory
MAX_WASM_BYTES = 1024 * 1024  # checked before compilation, which fuel cannot meter
MAX_WASM_STACK = 256 * 1024
TABLE_ELEMENT_LIMIT = 10_000

MAX_ENEMIES, MAX_LOOT, MAX_ROCKS = 11, 64, 160
OBS_WORDS = 15 + MAX_ENEMIES * 6 + 1 + MAX_LOOT * 4 + 1 + MAX_ROCKS * 2
ACT_WORDS = 2
ACT_SENTINEL = struct.pack("<2i", -1, 0)  # pre-filled so stale actions can't repeat

# Required exports: name -> (param types, result types); memory checked separately.
EXPORT_SIGNATURES = {"obs_buf": ([], ["i32"]), "act_buf": ([], ["i32"]),
                     "init": (["i32", "i32", "i32", "i32"], ["i32"]),
                     "act": ([], ["i32"])}

HATS = ("RED", "BLUE", "GREEN", "YELLOW")
DIRS = ("N", "S", "E", "W")
WEAPONS = ("unarmed", "sword", "spear", "bow")
LOOT_KINDS = ("sword", "spear", "bow", "arrows", "medkit")
TRUNC_LOOT, TRUNC_ROCKS = 1, 2


class WasmBotError(RuntimeError):
    """Base for all adapter faults; subclass names surface in bot_error events."""


class WasmTrap(WasmBotError): pass
class WasmFuelExhausted(WasmBotError): pass
class WasmInvalidAction(WasmBotError): pass
class WasmBadPointer(WasmBotError): pass


def _wasmtime():
    import wasmtime
    return wasmtime


class WasmStrategy:
    """Engine-compatible callable. One live instance per match; the setup-phase
    call always re-instantiates, so reusing the adapter across matches is safe."""

    def __init__(self, wasm_path: str | Path, fighter_ids,
                 fuel: int = FUEL_PER_TURN, memory_limit: int = MEMORY_LIMIT):
        wt = _wasmtime()
        self.wasm_bytes = Path(wasm_path).read_bytes()
        if len(self.wasm_bytes) > MAX_WASM_BYTES:
            raise ValueError(f"wasm module is {len(self.wasm_bytes)} bytes; "
                             f"limit is {MAX_WASM_BYTES}")
        self.sha256 = hashlib.sha256(self.wasm_bytes).hexdigest()
        self.fuel = fuel
        self.memory_limit = memory_limit
        self.ids = sorted(fighter_ids)
        self.index_of = {id_: i for i, id_ in enumerate(self.ids)}
        config = wt.Config()
        config.consume_fuel = True
        config.max_wasm_stack = MAX_WASM_STACK
        self._engine = wt.Engine(config)
        self._module = wt.Module(self._engine, self.wasm_bytes)
        imports = list(self._module.imports)
        if imports:
            raise ValueError(f"module must have no imports, found {len(imports)}")
        self._validate_exports(wt)
        self._store = None
        self._poisoned = False

    def _validate_exports(self, wt):
        have = {e.name: e.type for e in self._module.exports}
        missing = sorted(n for n in {"memory", *EXPORT_SIGNATURES} if n not in have)
        if missing:
            raise ValueError(f"module missing exports: {missing}")
        if not isinstance(have["memory"], wt.MemoryType):
            raise ValueError("export 'memory' must be a linear memory")
        for name, (params, results) in EXPORT_SIGNATURES.items():
            t = have[name]
            ok = (isinstance(t, wt.FuncType)
                  and [str(p) for p in t.params] == params
                  and [str(r) for r in t.results] == results)
            if not ok:
                raise ValueError(f"export '{name}' must be a function "
                                 f"({', '.join(params)}) -> ({', '.join(results)})")

    # ---- engine strategy interface -------------------------------------
    def __call__(self, obs: dict, memory: dict):
        if obs.get("phase") == "setup":
            return self._setup(obs), {}
        return self._turn(obs), {}

    # ---- lifecycle ------------------------------------------------------
    def _setup(self, obs: dict) -> dict:
        wt = _wasmtime()
        self._store = None  # drop any previous match's instance first
        self._poisoned = False
        store = wt.Store(self._engine)
        store.set_limits(self.memory_limit, TABLE_ELEMENT_LIMIT, 2, 2, 1)
        self._set_fuel(store)  # one budget for the whole setup, instantiation included
        try:
            instance = wt.Instance(store, self._module, [])
            exports = instance.exports(store)
            self._mem = exports["memory"]
            self._obs_buf = exports["obs_buf"]
            self._act_buf = exports["act_buf"]
            self._init = exports["init"]
            self._act = exports["act"]
            hat = self._init(store, API_VERSION, obs["size"], obs["fighters"],
                             self.index_of[obs["self"]["id"]])
        except Exception as exc:
            self._poisoned = True
            raise self._classify(exc)
        self._store = store
        if not isinstance(hat, int) or not 0 <= hat < len(HATS):
            self._poisoned = True
            raise WasmInvalidAction(f"init returned invalid hat {hat!r}")
        return {"type": "hat", "color": HATS[hat]}

    def _turn(self, obs: dict) -> dict:
        if self._poisoned or self._store is None:
            raise WasmBotError("bot is poisoned or uninitialized")
        store = self._store
        data = self._encode(obs)
        try:
            self._set_fuel(store)  # ONE budget covers the entire turn
            optr = self._obs_buf(store)
            aptr = self._act_buf(store)
            self._check_ptr(store, optr, OBS_WORDS * 4)
            self._check_ptr(store, aptr, ACT_WORDS * 4)
            if optr < aptr + ACT_WORDS * 4 and aptr < optr + OBS_WORDS * 4:
                raise WasmBadPointer("obs and act buffers overlap")
            self._mem.write(store, data, optr)
            self._mem.write(store, ACT_SENTINEL, aptr)
            self._act(store)  # return value is reserved and currently ignored
            raw = self._mem.read(store, aptr, aptr + ACT_WORDS * 4)
        except WasmBotError:
            self._poisoned = True
            raise
        except Exception as exc:
            self._poisoned = True
            raise self._classify(exc)
        if struct.unpack("<2i", raw)[0] == -1:
            self._poisoned = True
            raise WasmInvalidAction("bot wrote no action this turn")
        action = self._decode_action(raw)
        if action is None:
            self._poisoned = True
            raise WasmInvalidAction(f"bad action words {struct.unpack('<2i', raw)}")
        return action

    # ---- helpers ---------------------------------------------------------
    def _set_fuel(self, store):
        store.set_fuel(self.fuel)

    def _check_ptr(self, store, ptr, nbytes):
        if not isinstance(ptr, int) or ptr <= 0 or ptr % 4 != 0 \
                or ptr + nbytes > self._mem.data_len(store):
            raise WasmBadPointer(f"pointer {ptr!r} (+{nbytes}B) outside guest memory")

    @staticmethod
    def _classify(exc: Exception) -> WasmBotError:
        wt = _wasmtime()
        if isinstance(exc, wt.Trap):
            if "fuel" in str(exc).lower():
                return WasmFuelExhausted(str(exc).splitlines()[0])
            return WasmTrap(str(exc).splitlines()[0])
        return WasmBotError(f"{type(exc).__name__}: {exc}")

    def _encode(self, obs: dict) -> bytes:
        me = obs["self"]
        truncated = (TRUNC_LOOT if len(obs["loot"]) > MAX_LOOT else 0) \
            | (TRUNC_ROCKS if len(obs["rocks"]) > MAX_ROCKS else 0)
        words = [API_VERSION, obs["tick"], obs["size"], obs["safe_radius"],
                 obs["alive"], truncated,
                 self.index_of[me["id"]], me["x"], me["y"], me["hp"],
                 WEAPONS.index(me["weapon"]), me["arrows"], me["medkits"],
                 HATS.index(me["hat"])]

        def nearest(items):
            return sorted(items, key=lambda it: (max(abs(it["x"] - me["x"]),
                                                     abs(it["y"] - me["y"])),
                                                 it["x"], it["y"], it.get("kind", "")))
        enemies = obs["enemies"]  # already sorted by id; <= 11 by rule
        words.append(len(enemies))
        for e in enemies:
            words += [self.index_of[e["id"]], e["x"], e["y"], e["hp"],
                      WEAPONS.index(e["weapon"]), HATS.index(e["hat"])]
        words += [0] * ((MAX_ENEMIES - len(enemies)) * 6)
        loot = nearest(obs["loot"])[:MAX_LOOT]
        words.append(len(loot))
        for it in loot:
            words += [it["x"], it["y"], LOOT_KINDS.index(it["kind"]), it["count"]]
        words += [0] * ((MAX_LOOT - len(loot)) * 4)
        rocks = nearest(obs["rocks"])[:MAX_ROCKS]
        words.append(len(rocks))
        for r in rocks:
            words += [r["x"], r["y"]]
        words += [0] * ((MAX_ROCKS - len(rocks)) * 2)
        if len(words) != OBS_WORDS:
            raise RuntimeError(f"obs encoding invariant broken: {len(words)} words")
        return struct.pack(f"<{OBS_WORDS}i", *words)

    def _decode_action(self, raw: bytes) -> dict | None:
        kind, arg = struct.unpack("<2i", raw)
        if kind == 0:
            return {"type": "wait"}
        if kind == 1 and 0 <= arg < 4:
            return {"type": "move", "direction": DIRS[arg]}
        if kind == 2 and 0 <= arg < len(self.ids):
            return {"type": "attack", "target": self.ids[arg]}
        if kind == 3:
            return {"type": "guard"}
        if kind == 4:
            return {"type": "pickup"}
        if kind == 5:
            return {"type": "use", "item": "medkit"}
        return None


def make_wasm_strategy(wasm_path: str | Path, fighter_ids, **kw) -> WasmStrategy:
    return WasmStrategy(wasm_path, fighter_ids, **kw)
