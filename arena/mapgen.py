"""Deterministic map generation: rocks, spawns, tiered loot.

Hat colors are chosen by bot strategies at match setup, never by map generation.
"""
from __future__ import annotations

from dataclasses import dataclass
import random

WEAPON_KINDS = ("bow", "spear", "sword")
ARROW_BUNDLE = 4


@dataclass(frozen=True)
class GameMap:
    size: int
    rocks: frozenset[tuple[int, int]]
    spawns: tuple[tuple[int, int], ...]
    loot: tuple[tuple[int, int, str, int], ...]  # (x, y, kind, count)


def _perimeter(size: int) -> list[tuple[int, int]]:
    e = size - 1
    return ([(x, 0) for x in range(size)] + [(e, y) for y in range(1, size)]
            + [(x, e) for x in range(e - 1, -1, -1)] + [(0, y) for y in range(e - 1, 0, -1)])


def _reachable(size: int, rocks: frozenset, start: tuple[int, int]) -> set[tuple[int, int]]:
    seen, stack = {start}, [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nxt = (x + dx, y + dy)
            if 0 <= nxt[0] < size and 0 <= nxt[1] < size and nxt not in rocks and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _band_items(fighters: int) -> tuple[list[str], list[str], list[str]]:
    # Center is premium, mid ring is the economy, outer keeps viable gear off-meta.
    center = ["sword", "sword", "bow", "arrows"]
    mid = ["spear", "spear", "spear", "bow", "arrows", "arrows", "medkit", "medkit", "medkit"]
    outer = ["spear", "spear", "sword", "medkit", "medkit"]
    if fighters > 10:
        mid.append("spear")
        outer += ["sword", "medkit"]
    return center, mid, outer


def generate_map(rng: random.Random, size: int, fighters: int, rock_density: float) -> GameMap:
    """Generate a map where every spawn and loot tile can reach the center on foot."""
    c = size // 2
    for _ in range(100):
        rocks = frozenset((x, y) for y in range(1, size - 1) for x in range(1, size - 1)
                          if max(abs(x - c), abs(y - c)) > 1 and rng.random() < rock_density)
        perim = _perimeter(size)
        offset = rng.randrange(len(perim))
        spawns = tuple(perim[(offset + i * len(perim) // fighters) % len(perim)] for i in range(fighters))
        bands = zip(_band_items(fighters), ((0, 1), (2, 8), (9, c - 2)))
        loot: list[tuple[int, int, str, int]] = []
        taken: set[tuple[int, int]] = set()
        feasible = True
        for items, (lo, hi) in bands:
            cand = sorted((x, y) for y in range(1, size - 1) for x in range(1, size - 1)
                          if lo <= max(abs(x - c), abs(y - c)) <= hi
                          and (x, y) not in rocks and (x, y) not in taken)
            if len(cand) < len(items):
                feasible = False
                break
            for kind, pos in zip(items, rng.sample(cand, len(items))):
                taken.add(pos)
                loot.append((pos[0], pos[1], kind, ARROW_BUNDLE if kind == "arrows" else 1))
        if not feasible:
            continue
        seen = _reachable(size, rocks, (c, c))
        if all(s in seen for s in spawns) and all((x, y) in seen for x, y, _, _ in loot):
            return GameMap(size=size, rocks=rocks, spawns=spawns, loot=tuple(sorted(loot)))
    raise ValueError("could not generate an accessible map")
