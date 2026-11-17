/* Starter fighter for the AI Battle Arena. TRUSTED SAMPLE.
 *
 * Build (LLVM clang, no libc or wasi-sdk needed):
 *   clang --target=wasm32 -O2 -nostdlib -Wl,--no-entry ^
 *     -Wl,--export=obs_buf -Wl,--export=act_buf -Wl,--export=init -Wl,--export=act ^
 *     -o starter_bot.wasm starter_bot.c
 *
 * Strategy, in priority order (edit these to make your own fighter):
 *   1. low on health and nobody adjacent  -> use a medkit
 *   2. an enemy is in range of my weapon  -> attack the weakest one
 *   3. unarmed and a weapon is visible    -> walk to the nearest weapon
 *   4. outside the safe zone              -> walk to the center
 *   5. enemy visible                      -> walk toward the nearest enemy
 *   6. otherwise                          -> walk to the center
 */
#include "arena_bot.h"

static ArenaObs OBS;
static ArenaAct ACT;

int32_t *obs_buf(void) { return (int32_t *)&OBS; }
int32_t *act_buf(void) { return (int32_t *)&ACT; }

int32_t init(int32_t api_version, int32_t size, int32_t fighters,
             int32_t self_index) {
  (void)size; (void)fighters; (void)self_index;
  if (api_version != ARENA_API_VERSION) return -1;
  return HAT_YELLOW; /* your public signal; no gameplay effect */
}

static int32_t iabs(int32_t v) { return v < 0 ? -v : v; }
static int32_t imax(int32_t a, int32_t b) { return a > b ? a : b; }
static int32_t cheb(int32_t x, int32_t y) {
  return imax(iabs(x - OBS.self_x), iabs(y - OBS.self_y));
}
static int32_t manhattan(int32_t x, int32_t y) {
  return iabs(x - OBS.self_x) + iabs(y - OBS.self_y);
}

static int is_rock(int32_t x, int32_t y) {
  for (int32_t i = 0; i < OBS.rock_count; i++)
    if (OBS.rocks[i].x == x && OBS.rocks[i].y == y) return 1;
  return 0;
}

static int is_enemy_tile(int32_t x, int32_t y) {
  for (int32_t i = 0; i < OBS.enemy_count; i++)
    if (OBS.enemies[i].x == x && OBS.enemies[i].y == y) return 1;
  return 0;
}

/* Can my current weapon legally hit this enemy? (Mirrors the engine rules;
 * bow line-of-sight through rocks is NOT checked here — a blocked shot is a
 * wasted turn, which is fine for a starter.) */
static int in_range(const ArenaEnemy *e) {
  int32_t dx = e->x - OBS.self_x, dy = e->y - OBS.self_y;
  switch (OBS.self_weapon) {
  case WEAPON_UNARMED:
  case WEAPON_SWORD:
    return imax(iabs(dx), iabs(dy)) == 1;
  case WEAPON_SPEAR:
    return (dx == 0 || dy == 0) && manhattan(e->x, e->y) <= 2;
  case WEAPON_BOW: {
    if (OBS.self_arrows <= 0) return 0;
    if (dx != 0 && dy != 0 && iabs(dx) != iabs(dy)) return 0;
    int32_t steps = imax(iabs(dx), iabs(dy));
    return steps >= 2 && steps <= 6;
  }
  }
  return 0;
}

/* One greedy step toward (tx,ty), avoiding rocks, fighters, board edges and
 * never leaving the safe zone. Falls back to WAIT when boxed in. */
static void step_toward(int32_t tx, int32_t ty) {
  int32_t dx = tx - OBS.self_x, dy = ty - OBS.self_y;
  int32_t c = OBS.size / 2;
  int32_t prefs[4], n = 0;
  int32_t first = dx > 0 ? DIR_E : DIR_W, second = dy > 0 ? DIR_S : DIR_N;
  if (iabs(dy) > iabs(dx)) { int32_t t = first; first = second; second = t; }
  if (dx != 0 || dy != 0) { prefs[n++] = first; prefs[n++] = second; }
  for (int32_t d = 0; d < 4; d++) {
    int already = 0;
    for (int32_t i = 0; i < n; i++) if (prefs[i] == d) already = 1;
    if (!already) prefs[n++] = d;
  }
  static const int32_t DX[4] = {0, 0, 1, -1}, DY[4] = {-1, 1, 0, 0};
  int inside = imax(iabs(OBS.self_x - c), iabs(OBS.self_y - c)) <= OBS.safe_radius;
  for (int32_t i = 0; i < n; i++) {
    int32_t d = prefs[i];
    int32_t nx = OBS.self_x + DX[d], ny = OBS.self_y + DY[d];
    if (nx < 0 || ny < 0 || nx >= OBS.size || ny >= OBS.size) continue;
    if (is_rock(nx, ny) || is_enemy_tile(nx, ny)) continue;
    if (inside && imax(iabs(nx - c), iabs(ny - c)) > OBS.safe_radius) continue;
    ACT.type = ACT_MOVE;
    ACT.arg = d;
    return;
  }
  ACT.type = ACT_WAIT;
  ACT.arg = 0;
}

int32_t act(void) {
  ACT.type = ACT_WAIT;
  ACT.arg = 0;
  int32_t c = OBS.size / 2;

  int adjacent_threat = 0;
  for (int32_t i = 0; i < OBS.enemy_count; i++)
    if (cheb(OBS.enemies[i].x, OBS.enemies[i].y) <= 1) adjacent_threat = 1;

  /* 1. heal when hurt and safe */
  if (OBS.self_hp < 40 && OBS.self_medkits > 0 && !adjacent_threat) {
    ACT.type = ACT_USE;
    return 0;
  }

  /* 2. attack the weakest enemy my weapon can reach */
  int32_t best = -1;
  for (int32_t i = 0; i < OBS.enemy_count; i++)
    if (in_range(&OBS.enemies[i]) &&
        (best < 0 || OBS.enemies[i].hp < OBS.enemies[best].hp))
      best = i;
  if (best >= 0) {
    ACT.type = ACT_ATTACK;
    ACT.arg = OBS.enemies[best].index;
    return 0;
  }

  /* 3. unarmed: grab the nearest visible weapon */
  if (OBS.self_weapon == WEAPON_UNARMED) {
    int32_t bl = -1, bd = 1 << 30;
    for (int32_t i = 0; i < OBS.loot_count; i++) {
      int32_t k = OBS.loot[i].kind;
      if (k != LOOT_SWORD && k != LOOT_SPEAR && k != LOOT_BOW) continue;
      int32_t d = manhattan(OBS.loot[i].x, OBS.loot[i].y);
      if (d < bd) { bd = d; bl = i; }
    }
    if (bl >= 0) { step_toward(OBS.loot[bl].x, OBS.loot[bl].y); return 0; }
  }

  /* 4. stay inside the shrinking zone */
  if (imax(iabs(OBS.self_x - c), iabs(OBS.self_y - c)) >= OBS.safe_radius) {
    step_toward(c, c);
    return 0;
  }

  /* 5. hunt the nearest visible enemy */
  int32_t ne = -1, nd = 1 << 30;
  for (int32_t i = 0; i < OBS.enemy_count; i++) {
    int32_t d = manhattan(OBS.enemies[i].x, OBS.enemies[i].y);
    if (d < nd) { nd = d; ne = i; }
  }
  if (ne >= 0) { step_toward(OBS.enemies[ne].x, OBS.enemies[ne].y); return 0; }

  /* 6. drift to the center */
  step_toward(c, c);
  return 0;
}
