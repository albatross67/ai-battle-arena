import hashlib
import json
import random
import unittest
from collections import Counter

from arena.bots import REFERENCE_BOTS
from arena.engine import Config, occluded, run_match, _radius, _storm_damage
from arena.mapgen import GameMap, generate_map, _reachable
from arena.viewer import render_html


def scripted(decide, hat="RED"):
    """Wrap a per-tick decide(obs) into a strategy that also answers setup."""
    def bot(obs, memory):
        if obs.get("phase") == "setup":
            return {"type": "hat", "color": hat}, memory
        return decide(obs), memory
    return bot


def waiter(_obs):
    return {"type": "wait"}


def attacker(target):
    return lambda obs: {"type": "attack", "target": target}


def tiny_map(size=25, rocks=(), spawns=((2, 2), (22, 22)), loot=()):
    return GameMap(size=size, rocks=frozenset(rocks), spawns=tuple(spawns),
                   loot=tuple(loot))


def run_duel(pos_a, pos_b, bot_a, bot_b, rocks=(), loot=(), cfg=None, ticks=5):
    gm = tiny_map(rocks=rocks, spawns=(pos_a, pos_b), loot=loot)
    config = cfg or Config(max_ticks=ticks)
    return run_match({"A": scripted(bot_a), "B": scripted(bot_b)},
                     seed=1, config=config, game_map=gm)


def hits_of(log):
    return [e for fr in log["frames"] for e in fr["events"] if e["type"] == "hit"]


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_every_byte(self):
        a = json.dumps(run_match(REFERENCE_BOTS, 42), sort_keys=True)
        b = json.dumps(run_match(REFERENCE_BOTS, 42), sort_keys=True)
        self.assertEqual(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())

    def test_different_seeds_change_setup(self):
        a = run_match(REFERENCE_BOTS, 11)
        b = run_match(REFERENCE_BOTS, 12)
        self.assertNotEqual(a["frames"][0]["players"], b["frames"][0]["players"])

    def test_valid_result_and_frames(self):
        result = run_match(REFERENCE_BOTS, 21)
        self.assertIn(result["winner"], REFERENCE_BOTS)
        self.assertEqual(result["frames"][0]["tick"], 0)
        self.assertLessEqual(len(result["frames"]), result["config"]["max_ticks"] + 1)
        self.assertEqual(result["frames"][-1]["tick"], len(result["frames"]) - 1)

    def test_short_match_still_finishes(self):
        result = run_match(REFERENCE_BOTS, 42, Config(max_ticks=1))
        self.assertIn(result["winner"], REFERENCE_BOTS)
        self.assertEqual(len(result["frames"]), 2)

    def test_twelve_fighters_supported(self):
        twelve = dict(list(REFERENCE_BOTS.items())
                      + [("G1_Extra", REFERENCE_BOTS["A1_Berserker"]),
                         ("G2_Extra", REFERENCE_BOTS["D1_Evader"])])
        result = run_match(twelve, 7)
        self.assertEqual(len(result["fighters"]), 12)


class MapTests(unittest.TestCase):
    def test_map_deterministic_per_seed(self):
        a = generate_map(random.Random(9), 25, 10, 0.08)
        b = generate_map(random.Random(9), 25, 10, 0.08)
        self.assertEqual(a, b)

    def test_spawns_and_loot_accessible(self):
        for seed in range(1, 21):
            m = generate_map(random.Random(seed), 25, 10, 0.08)
            seen = _reachable(m.size, m.rocks, (12, 12))
            for s in m.spawns:
                self.assertIn(s, seen, f"seed {seed}: spawn {s} unreachable")
            for x, y, kind, _ in m.loot:
                self.assertIn((x, y), seen, f"seed {seed}: {kind} at {(x, y)} unreachable")

    def test_spawns_distinct_and_on_perimeter(self):
        m = generate_map(random.Random(3), 25, 12, 0.08)
        self.assertEqual(len(set(m.spawns)), 12)
        for x, y in m.spawns:
            self.assertTrue(x in (0, 24) or y in (0, 24))

    def test_center_band_has_premium_loot(self):
        m = generate_map(random.Random(4), 25, 10, 0.08)
        center_kinds = [k for x, y, k, _ in m.loot if max(abs(x-12), abs(y-12)) <= 1]
        self.assertIn("sword", center_kinds)
        self.assertIn("bow", center_kinds)


class LosTests(unittest.TestCase):
    def test_straight_line_blocked_by_rock(self):
        self.assertTrue(occluded(0, 0, 4, 0, frozenset({(2, 0)})))
        self.assertFalse(occluded(0, 0, 4, 0, frozenset({(2, 1)})))

    def test_diagonal_line_checks_diagonal_cells_only(self):
        self.assertTrue(occluded(0, 0, 3, 3, frozenset({(2, 2)})))
        self.assertFalse(occluded(0, 0, 3, 3, frozenset({(1, 2), (2, 1)})))

    def test_endpoints_never_block(self):
        self.assertFalse(occluded(0, 0, 2, 0, frozenset({(0, 0), (2, 0)})))

    def test_symmetric(self):
        rocks = frozenset({(3, 2), (5, 5), (7, 4)})
        for a in ((0, 0), (2, 6), (8, 1)):
            for b in ((9, 9), (1, 8), (6, 3)):
                self.assertEqual(occluded(*a, *b, rocks), occluded(*b, *a, rocks),
                                 f"asymmetric LOS between {a} and {b}")


class CombatTests(unittest.TestCase):
    def test_unarmed_hits_adjacent_only(self):
        log = run_duel((2, 2), (3, 2), attacker("B"), waiter)
        self.assertEqual(hits_of(log)[0]["damage"], 8)
        far = run_duel((2, 2), (4, 2), attacker("B"), waiter)
        self.assertEqual(hits_of(far), [])

    def test_sword_damage(self):
        loot = ((2, 2, "sword", 1),)
        log = run_duel((2, 2), (3, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(log)[0]["damage"], 25)
        self.assertEqual(hits_of(log)[0]["weapon"], "sword")

    def test_diagonal_melee_hits_at_chebyshev_one(self):
        log = run_duel((2, 2), (3, 3), attacker("B"), waiter)
        self.assertEqual(hits_of(log)[0]["damage"], 8)

    def test_diagonal_melee_blocked_by_sealed_rock_corner(self):
        sealed = run_duel((2, 2), (3, 3), attacker("B"), waiter, rocks=((3, 2), (2, 3)))
        self.assertEqual(hits_of(sealed), [])
        half_open = run_duel((2, 2), (3, 3), attacker("B"), waiter, rocks=((3, 2),))
        self.assertEqual(hits_of(half_open)[0]["damage"], 8)

    def test_spear_reach_two_orthogonal(self):
        loot = ((2, 2, "spear", 1),)
        log = run_duel((2, 2), (4, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(log)[0]["damage"], 16)

    def test_spear_blocked_by_rock_between(self):
        loot = ((2, 2, "spear", 1),)
        log = run_duel((2, 2), (4, 2), attacker("B"), waiter, rocks=((3, 2),), loot=loot)
        self.assertEqual(hits_of(log), [])

    def test_spear_no_diagonal(self):
        loot = ((2, 2, "spear", 1),)
        log = run_duel((2, 2), (3, 3), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(log), [])

    def test_spear_keeps_manhattan_metric_at_chebyshev_one(self):
        # Unlike sword/unarmed, the spear cannot strike diagonally adjacent targets.
        loot = ((2, 2, "spear", 1),)
        log = run_duel((2, 2), (3, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(log)[0]["damage"], 16)

    def test_bow_diagonal_and_ammo_depletion(self):
        loot = ((2, 2, "bow", 1), (2, 2, "arrows", 2))
        log = run_duel((2, 2), (5, 5), attacker("B"), waiter, loot=loot, ticks=6)
        hits = hits_of(log)
        self.assertEqual(len(hits), 2)  # third shot has no arrow
        self.assertTrue(all(h["damage"] == 14 and h["weapon"] == "bow" for h in hits))

    def test_bow_min_and_max_range(self):
        loot = ((2, 2, "bow", 1), (2, 2, "arrows", 4))
        adjacent = run_duel((2, 2), (3, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(adjacent), [])
        beyond = run_duel((2, 2), (9, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(beyond), [])
        edge = run_duel((2, 2), (8, 2), attacker("B"), waiter, loot=loot)
        self.assertEqual(hits_of(edge)[0]["damage"], 14)

    def test_bow_blocked_by_rock_not_by_fighter(self):
        loot = ((2, 2, "bow", 1), (2, 2, "arrows", 4))
        blocked = run_duel((2, 2), (6, 2), attacker("B"), waiter, rocks=((4, 2),), loot=loot)
        self.assertEqual(hits_of(blocked), [])
        gm = tiny_map(spawns=((2, 2), (6, 2), (4, 2)), loot=loot)
        threeway = run_match({"A": scripted(attacker("B")), "B": scripted(waiter),
                              "C": scripted(waiter)}, seed=1,
                             config=Config(max_ticks=3), game_map=gm)
        shots = [h for h in hits_of(threeway) if h["target"] == "B"]
        self.assertTrue(shots, "fighter C should not block the arrow")

    def test_guard_fatigue_still_applies(self):
        def turtle(_obs):
            return {"type": "guard"}
        log = run_duel((2, 2), (3, 2), attacker("B"), turtle, ticks=4)
        self.assertEqual([h["damage"] for h in hits_of(log)], [4, 4, 8, 8])

    def test_rotating_initiative_prevents_double_ko(self):
        cfg = Config(max_ticks=160)
        log = run_duel((2, 2), (3, 2), attacker("B"), attacker("A"), cfg=cfg)
        final = log["frames"][-1]["players"]
        self.assertEqual(sum(p["alive"] for p in final.values()), 1)
        self.assertEqual(log["end_reason"], "last_survivor")


class ItemTests(unittest.TestCase):
    def test_auto_pickup_weapon_only_when_unarmed(self):
        loot = ((2, 2, "sword", 1), (3, 2, "spear", 1))
        def walk_east(_obs):
            return {"type": "move", "direction": "E"}
        # Pickup is post-move: tick 1 lands on the sword, tick 2 crosses the spear.
        log = run_duel((1, 2), (22, 22), walk_east, waiter, loot=loot, ticks=3)
        final = log["frames"][-1]["players"]["A"]
        self.assertEqual(final["weapon"], "sword")
        remaining = [item for item in log["frames"][-1]["loot"] if item["kind"] == "spear"]
        self.assertEqual(len(remaining), 1)

    def test_explicit_pickup_swaps_weapon(self):
        loot = ((2, 2, "sword", 1), (2, 2, "bow", 1))
        def swap_once(obs):
            if obs["self"]["weapon"] == "bow":
                return {"type": "pickup"}
            return {"type": "wait"}
        log = run_duel((2, 2), (22, 22), swap_once, waiter, loot=loot, ticks=3)
        final = log["frames"][-1]["players"]["A"]
        self.assertEqual(final["weapon"], "sword")
        dropped = [item for item in log["frames"][-1]["loot"]
                   if item["kind"] == "bow" and item["x"] == 2]
        self.assertEqual(len(dropped), 1)

    def test_medkit_cap_heal_amount_and_turn_cost(self):
        loot = ((2, 2, "medkit", 3),)
        def hurt_then_heal(obs):
            if obs["self"]["hp"] < 100 and obs["self"]["medkits"] > 0:
                return {"type": "use", "item": "medkit"}
            return {"type": "wait"}
        gm = tiny_map(spawns=((2, 2), (3, 2)), loot=loot)
        log = run_match({"A": scripted(hurt_then_heal), "B": scripted(attacker("A"))},
                        seed=1, config=Config(max_ticks=12), game_map=gm)
        a_frames = [fr["players"]["A"] for fr in log["frames"]]
        self.assertEqual(max(p["medkits"] for p in a_frames), 2)  # cap, not 3
        self.assertLessEqual(max(p["hp"] for p in a_frames), 100)
        uses = [e for fr in log["frames"] for e in fr["events"] if e["type"] == "use"]
        self.assertTrue(uses)
        self.assertTrue(all(0 < e["amount"] <= 30 for e in uses))

    def test_corpse_drops_gear(self):
        loot = ((3, 2, "bow", 1), (3, 2, "arrows", 4), (3, 2, "medkit", 1))
        log = run_duel((2, 2), (3, 2), attacker("B"), waiter, loot=loot,
                       cfg=Config(max_ticks=30))
        drops = [e for fr in log["frames"] for e in fr["events"] if e["type"] == "drop"]
        self.assertEqual(len(drops), 1)
        kinds = {item["kind"]: item["count"] for item in drops[0]["items"]}
        self.assertEqual(kinds, {"bow": 1, "arrows": 4, "medkit": 1})

    def test_elimination_heal_is_ten(self):
        log = run_duel((2, 2), (3, 2), attacker("B"), attacker("A"),
                       cfg=Config(max_ticks=160))
        heals = [e for fr in log["frames"] for e in fr["events"] if e["type"] == "heal"]
        self.assertEqual(len(heals), 1)
        self.assertEqual(heals[0]["amount"], 10)
        self.assertEqual(heals[0]["id"], log["winner"])


class FogTests(unittest.TestCase):
    def observed(self, pos_a, pos_b, rocks=()):
        seen = {}
        def spy(obs):
            seen["enemies"] = obs["enemies"]
            return {"type": "wait"}
        gm = tiny_map(spawns=(pos_a, pos_b), rocks=rocks)
        run_match({"A": scripted(spy), "B": scripted(waiter)}, seed=1,
                  config=Config(max_ticks=1), game_map=gm)
        return seen["enemies"]

    def test_enemy_beyond_vision_radius_hidden(self):
        self.assertEqual(self.observed((2, 2), (11, 2)), [])

    def test_enemy_at_vision_radius_visible(self):
        enemies = self.observed((2, 2), (10, 2))
        self.assertEqual(len(enemies), 1)
        self.assertIn("hat", enemies[0])

    def test_rock_occludes_enemy(self):
        self.assertEqual(self.observed((2, 2), (6, 2), rocks=((4, 2),)), [])


class HatTests(unittest.TestCase):
    def test_bot_chosen_hats_recorded(self):
        log = run_match({"A": scripted(waiter, hat="YELLOW"), "B": scripted(waiter, hat="BLUE")},
                        seed=1, config=Config(max_ticks=1), game_map=tiny_map())
        self.assertEqual(log["hats"], {"A": "YELLOW", "B": "BLUE"})
        hat_events = [e for e in log["frames"][0]["events"] if e["type"] == "hat"]
        self.assertEqual(len(hat_events), 2)

    def test_invalid_hat_defaults_to_red_with_error(self):
        def bad_setup(obs, memory):
            if obs.get("phase") == "setup":
                raise RuntimeError("boom")
            return {"type": "wait"}, memory
        log = run_match({"A": bad_setup, "B": scripted(waiter, hat="GREEN")},
                        seed=1, config=Config(max_ticks=1), game_map=tiny_map())
        self.assertEqual(log["hats"]["A"], "RED")
        errors = [e for e in log["frames"][0]["events"] if e["type"] == "bot_error"]
        self.assertEqual([e["id"] for e in errors], ["A"])


class ZoneAndEndingTests(unittest.TestCase):
    def test_storm_damage_ramps_and_caps(self):
        cfg = Config()
        self.assertEqual(_storm_damage(1, cfg), 10)
        self.assertEqual(_storm_damage(20, cfg), 15)
        self.assertEqual(_storm_damage(30, cfg), 20)
        self.assertEqual(_storm_damage(160, cfg), 40)

    def test_radius_schedule(self):
        cfg = Config()
        self.assertEqual(_radius(0, cfg), 12)
        self.assertEqual(_radius(19, cfg), 12)
        self.assertEqual(_radius(20, cfg), 11)
        self.assertEqual(_radius(120, cfg), 1)
        self.assertEqual(_radius(130, cfg), 0)  # zone fully closes
        self.assertEqual(_radius(160, cfg), 0)

    def test_simultaneous_storm_deaths_rank_by_seed_priority(self):
        # Both corner waiters die to the storm on the same tick with identical hp,
        # survival tick, and damage: the seed-derived priority breaks the tie.
        gm = tiny_map(spawns=((0, 0), (24, 24)))
        log = run_match({"A": scripted(waiter), "B": scripted(waiter)}, seed=1,
                        config=Config(max_ticks=160), game_map=gm)
        self.assertEqual(log["end_reason"], "elimination_or_timeout_tiebreak")
        self.assertEqual(sorted(log["tiebreak_priority"]), ["A", "B"])
        self.assertEqual(log["winner"], log["tiebreak_priority"][0])
        self.assertEqual(log["ranking"], log["tiebreak_priority"])

    def test_tied_ranking_uses_recorded_priority_not_id(self):
        ids = [f"P{i}" for i in range(6)]
        gm = tiny_map(spawns=tuple((2 + 2 * i, 2) for i in range(6)))
        for seed in (1, 2, 3, 4, 5):
            log = run_match({id_: scripted(waiter) for id_ in ids}, seed=seed,
                            config=Config(max_ticks=3), game_map=gm)
            self.assertEqual(sorted(log["tiebreak_priority"]), ids)
            self.assertEqual(log["ranking"], log["tiebreak_priority"],
                             f"seed {seed}: full tie must follow recorded priority")
        # Same seed always derives the same priority; priorities vary across seeds.
        again = run_match({id_: scripted(waiter) for id_ in ids}, seed=3,
                          config=Config(max_ticks=3), game_map=gm)
        self.assertEqual(again["tiebreak_priority"],
                         run_match({id_: scripted(waiter) for id_ in ids}, seed=3,
                                   config=Config(max_ticks=3), game_map=gm)["tiebreak_priority"])

    def test_max_tick_timeout_is_deterministic(self):
        gm = tiny_map(spawns=((11, 12), (13, 12)))
        log = run_match({"A": scripted(waiter), "B": scripted(waiter)}, seed=1,
                        config=Config(max_ticks=5), game_map=gm)
        self.assertEqual(log["end_reason"], "elimination_or_timeout_tiebreak")
        self.assertEqual(log["winner"], log["tiebreak_priority"][0])


class ViewerTests(unittest.TestCase):
    def test_rendered_replay_is_self_contained_and_escaped(self):
        import tempfile
        from pathlib import Path
        log = run_match({"A": scripted(waiter), "B<x>": scripted(waiter)}, seed=1,
                        config=Config(max_ticks=2), game_map=tiny_map())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "replay.html"
            render_html(log, out)
            html = out.read_text(encoding="utf-8")
        self.assertNotIn("__REPLAY__", html)
        self.assertNotIn("B<x>", html)  # HTML-significant chars must be escaped
        self.assertIn("B\\u003cx\\u003e", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)  # self-contained: no external fetches


class BalanceRegressionTests(unittest.TestCase):
    # Win-rate and timeout thresholds are advisory diagnostics in arena.analyze,
    # not correctness requirements. Only a loose smoke ceiling stays hard here.
    def test_no_archetype_wins_almost_everything(self):
        wins = Counter(run_match(REFERENCE_BOTS, seed=s)["winner"].split("_", 1)[1]
                       for s in range(1, 101))
        self.assertLess(max(wins.values()) / 100, 0.70,
                        f"smoke ceiling exceeded: {wins}")


if __name__ == "__main__":
    unittest.main()
