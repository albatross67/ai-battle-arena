import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    import wasmtime
    HAVE_WASMTIME = True
except ImportError:
    HAVE_WASMTIME = False

from arena.bots import REFERENCE_BOTS
from arena.engine import Config, run_match
from arena.mapgen import GameMap

REPO = Path(__file__).resolve().parents[1]
STARTER = REPO / "wasm" / "starter_bot.wasm"

MINI_PREFIX = """(module
  (memory (export "memory") 1)
  (func (export "obs_buf") (result i32) i32.const 4096)
  (func (export "act_buf") (result i32) i32.const 8192)
"""
GOOD_INIT = '(func (export "init") (param i32 i32 i32 i32) (result i32) i32.const 2)\n'
WRITE_WAIT = '(i32.store (i32.const 8192) (i32.const 0))'

WAT_BOTS = {
    "waits": MINI_PREFIX + GOOD_INIT
        + f'(func (export "act") (result i32) {WRITE_WAIT} i32.const 0))',
    "writes_nothing": MINI_PREFIX + GOOD_INIT
        + '(func (export "act") (result i32) i32.const 0))',
    "traps_in_act": MINI_PREFIX + GOOD_INIT + '(func (export "act") (result i32) unreachable))',
    "traps_in_init": MINI_PREFIX
        + '(func (export "init") (param i32 i32 i32 i32) (result i32) unreachable)\n'
        + '(func (export "act") (result i32) i32.const 0))',
    "infinite_loop": MINI_PREFIX + GOOD_INIT
        + '(func (export "act") (result i32) (loop br 0) i32.const 0))',
    "garbage_action": MINI_PREFIX + GOOD_INIT
        + '(func (export "act") (result i32) (i32.store (i32.const 8192) (i32.const 99)) i32.const 0))',
    "bad_pointer": MINI_PREFIX.replace('i32.const 8192', 'i32.const 999999999') + GOOD_INIT
        + f'(func (export "act") (result i32) {WRITE_WAIT} i32.const 0))',
    "overlapping_buffers": MINI_PREFIX.replace('i32.const 8192', 'i32.const 4100') + GOOD_INIT
        + f'(func (export "act") (result i32) {WRITE_WAIT} i32.const 0))',
    "memory_hog": MINI_PREFIX + GOOD_INIT
        + f'(func (export "act") (result i32) (drop (memory.grow (i32.const 100))) {WRITE_WAIT} i32.const 0))',
    "bad_hat": MINI_PREFIX
        + '(func (export "init") (param i32 i32 i32 i32) (result i32) i32.const 77)\n'
        + f'(func (export "act") (result i32) {WRITE_WAIT} i32.const 0))',
    "wrong_init_signature": MINI_PREFIX
        + '(func (export "init") (param i32 i32 i32) (result i32) i32.const 2)\n'
        + f'(func (export "act") (result i32) {WRITE_WAIT} i32.const 0))',
    "act_is_a_global": MINI_PREFIX + GOOD_INIT
        + '(global (export "act") i32 (i32.const 0)))',
}


def wat_file(tmp: Path, name: str) -> Path:
    p = tmp / f"{name}.wasm"
    p.write_bytes(wasmtime.wat2wasm(WAT_BOTS[name]))
    return p


def waiter(obs, mem):
    if obs.get("phase") == "setup":
        return {"type": "hat", "color": "BLUE"}, mem
    return {"type": "wait"}, mem


def wasm_lineup(strategy):
    lineup = dict(REFERENCE_BOTS)
    del lineup["A2_Berserker"]
    lineup["W1_Wasm"] = strategy
    return lineup


@unittest.skipUnless(HAVE_WASMTIME, "wasmtime not installed")
class WasmAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from arena.wasm_bot import make_wasm_strategy
        cls.make = staticmethod(make_wasm_strategy)
        cls.tmp = Path(tempfile.mkdtemp())

    def duel(self, name, ticks=5):
        strat = self.make(wat_file(self.tmp, name), ["P_python", "W_wasm"])
        gm = GameMap(size=25, rocks=frozenset(), spawns=((5, 5), (6, 5)), loot=())
        log = run_match({"W_wasm": strat, "P_python": waiter}, seed=1,
                        config=Config(max_ticks=ticks), game_map=gm)
        errors = [e for fr in log["frames"] for e in fr["events"] if e["type"] == "bot_error"]
        return log, errors

    def test_minimal_bot_plays_cleanly(self):
        log, errors = self.duel("waits")
        self.assertEqual(errors, [])
        self.assertEqual(log["hats"]["W_wasm"], "GREEN")

    def test_trap_in_act_becomes_wait_and_poisons(self):
        log, errors = self.duel("traps_in_act")
        self.assertTrue(all(e["id"] == "W_wasm" for e in errors))
        self.assertEqual({e["reason"] for e in errors}, {"WasmTrap", "WasmBotError"})
        self.assertEqual(len(errors), 5)  # every tick reported, match completed

    def test_trap_in_init_defaults_hat_and_waits(self):
        log, errors = self.duel("traps_in_init")
        self.assertEqual(log["hats"]["W_wasm"], "RED")
        self.assertGreaterEqual(len(errors), 1)

    def test_infinite_loop_exhausts_fuel_without_hanging(self):
        log, errors = self.duel("infinite_loop")
        self.assertIn("WasmFuelExhausted", {e["reason"] for e in errors})
        self.assertEqual(len(log["frames"]), 6)  # match ran to completion

    def test_garbage_action_rejected(self):
        _, errors = self.duel("garbage_action")
        self.assertIn("WasmInvalidAction", {e["reason"] for e in errors})

    def test_bad_pointer_rejected(self):
        _, errors = self.duel("bad_pointer")
        self.assertIn("WasmBadPointer", {e["reason"] for e in errors})

    def test_invalid_hat_rejected(self):
        log, errors = self.duel("bad_hat")
        self.assertEqual(log["hats"]["W_wasm"], "RED")
        self.assertIn("WasmInvalidAction", {e["reason"] for e in errors})

    def test_memory_growth_capped(self):
        from arena.wasm_bot import MEMORY_LIMIT
        strat = self.make(wat_file(self.tmp, "memory_hog"), ["P_python", "W_wasm"])
        gm = GameMap(size=25, rocks=frozenset(), spawns=((5, 5), (6, 5)), loot=())
        run_match({"W_wasm": strat, "P_python": waiter}, seed=1,
                  config=Config(max_ticks=3), game_map=gm)
        self.assertLessEqual(strat._mem.data_len(strat._store), MEMORY_LIMIT)

    def test_module_with_imports_rejected(self):
        wat = '(module (import "env" "f" (func)) (memory (export "memory") 1))'
        p = self.tmp / "imports.wasm"
        p.write_bytes(wasmtime.wat2wasm(wat))
        with self.assertRaises(ValueError):
            self.make(p, ["A", "B"])

    def test_missing_exports_rejected(self):
        p = self.tmp / "empty.wasm"
        p.write_bytes(wasmtime.wat2wasm('(module (memory (export "memory") 1))'))
        with self.assertRaises(ValueError):
            self.make(p, ["A", "B"])

    def test_wrong_export_signature_rejected_at_load(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(wat_file(self.tmp, "wrong_init_signature"), ["A", "B"])
        self.assertIn("init", str(ctx.exception))

    def test_non_function_export_rejected_at_load(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(wat_file(self.tmp, "act_is_a_global"), ["A", "B"])
        self.assertIn("act", str(ctx.exception))

    def test_oversized_module_rejected_before_compile(self):
        from arena.wasm_bot import MAX_WASM_BYTES
        p = self.tmp / "huge.wasm"
        p.write_bytes(b"\x00" * (MAX_WASM_BYTES + 1))  # size check precedes parsing
        with self.assertRaises(ValueError) as ctx:
            self.make(p, ["A", "B"])
        self.assertIn("bytes", str(ctx.exception))

    def test_overlapping_buffers_rejected(self):
        _, errors = self.duel("overlapping_buffers")
        self.assertIn("WasmBadPointer", {e["reason"] for e in errors})

    def test_unwritten_action_sentinel_faults(self):
        _, errors = self.duel("writes_nothing")
        self.assertIn("WasmInvalidAction", {e["reason"] for e in errors})

    def test_tiny_fuel_budget_faults_setup_deterministically(self):
        strat = self.make(wat_file(self.tmp, "waits"), ["P_python", "W_wasm"], fuel=1)
        gm = GameMap(size=25, rocks=frozenset(), spawns=((5, 5), (6, 5)), loot=())
        log = run_match({"W_wasm": strat, "P_python": waiter}, seed=1,
                        config=Config(max_ticks=3), game_map=gm)
        self.assertEqual(log["hats"]["W_wasm"], "RED")
        errors = {e["reason"] for fr in log["frames"] for e in fr["events"]
                  if e["type"] == "bot_error"}
        self.assertIn("WasmFuelExhausted", errors)


@unittest.skipUnless(HAVE_WASMTIME and STARTER.exists(), "starter_bot.wasm not built")
class StarterBotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from arena.wasm_bot import make_wasm_strategy
        cls.make = staticmethod(make_wasm_strategy)
        cls.ids = sorted(set(REFERENCE_BOTS) - {"A2_Berserker"}) + ["W1_Wasm"]

    def full_match(self, strategy, seed):
        return run_match(wasm_lineup(strategy), seed=seed)

    def test_full_match_vs_nine_python_bots_is_clean(self):
        log = self.full_match(self.make(STARTER, self.ids), seed=42)
        errors = [e for fr in log["frames"] for e in fr["events"]
                  if e["type"] == "bot_error" and e["id"] == "W1_Wasm"]
        self.assertEqual(errors, [])
        self.assertEqual(log["hats"]["W1_Wasm"], "YELLOW")
        moves = [e for fr in log["frames"] for e in fr["events"]
                 if e["type"] == "move" and e["id"] == "W1_Wasm"]
        self.assertGreater(len(moves), 5, "starter bot should actually move")

    def test_reproducible_with_fresh_adapters(self):
        a = json.dumps(self.full_match(self.make(STARTER, self.ids), 7), sort_keys=True)
        b = json.dumps(self.full_match(self.make(STARTER, self.ids), 7), sort_keys=True)
        self.assertEqual(hashlib.sha256(a.encode()).digest(),
                         hashlib.sha256(b.encode()).digest())

    def test_adapter_reuse_resets_state_between_matches(self):
        strat = self.make(STARTER, self.ids)
        first = json.dumps(self.full_match(strat, 7), sort_keys=True)
        again = json.dumps(self.full_match(strat, 7), sort_keys=True)
        fresh = json.dumps(self.full_match(self.make(STARTER, self.ids), 7), sort_keys=True)
        self.assertEqual(first, again)
        self.assertEqual(first, fresh)

    def test_practice_harness_reports_clean_run(self):
        from arena.practice import run_practice
        stats = run_practice(STARTER, seeds=3)
        self.assertEqual(stats["bot_errors"], {})
        self.assertIsNone(stats["first_error"])
        self.assertEqual(stats["seeds"], 3)
        self.assertTrue(1 <= stats["placement"]["median"] <= 10)
        self.assertEqual(stats["wasm_sha256"], self.make(STARTER, self.ids).sha256)

    def test_practice_harness_counts_faults(self):
        from arena.practice import run_practice
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        stats = run_practice(wat_file(tmp, "traps_in_act"), seeds=2)
        self.assertIn("WasmTrap", stats["bot_errors"])
        self.assertEqual(stats["first_error"]["seed"], 1)


if __name__ == "__main__":
    unittest.main()
