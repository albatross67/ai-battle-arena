/* AI Battle Arena — WASM bot ABI, version 1. DRAFT, not frozen.
 *
 * Everything is little-endian int32. Write only init() and act(); the host
 * (trusted Python engine) fills OBS before every act() call and reads ACT after.
 *
 * Module contract (exported symbols):
 *   int32_t* obs_buf(void);   where the host writes ArenaObs
 *   int32_t* act_buf(void);   where the host reads ArenaAct
 *   int32_t  init(int32_t api_version, int32_t size,
 *                 int32_t fighters, int32_t self_index);  // return a HAT_*
 *   int32_t  act(void);       // fill *act_buf() EVERY tick; return value is
 *                             // reserved and currently ignored
 *
 * You MUST write an action each tick: the host pre-fills the act buffer with an
 * invalid sentinel, and leaving it unchanged counts as a fault.
 *
 * Rules of the sandbox: no imports of any kind (no WASI, no host functions),
 * ONE fuel budget per complete turn (and one covering instantiation + init),
 * memory capped. Linear memory persists between turns of one match and is
 * discarded between matches.
 */
#ifndef ARENA_BOT_H
#define ARENA_BOT_H
#include <stdint.h>

#define ARENA_API_VERSION 1

/* Capacities. Counts never exceed these; if the arena ever holds more, the
 * nearest entries are kept and a TRUNC_* bit is set in obs.truncated. */
#define ARENA_MAX_ENEMIES 11
#define ARENA_MAX_LOOT 64
#define ARENA_MAX_ROCKS 160

#define TRUNC_LOOT 1
#define TRUNC_ROCKS 2

/* Hats: public cosmetic signal chosen in init(); no gameplay effect. */
#define HAT_RED 0
#define HAT_BLUE 1
#define HAT_GREEN 2
#define HAT_YELLOW 3

#define WEAPON_UNARMED 0 /* 8 dmg, Chebyshev 1 */
#define WEAPON_SWORD 1   /* 25 dmg, Chebyshev 1 */
#define WEAPON_SPEAR 2   /* 16 dmg, orthogonal, Manhattan 1-2 */
#define WEAPON_BOW 3     /* 14 dmg, 8-direction ray 2-6, uses 1 arrow */

#define LOOT_SWORD 0
#define LOOT_SPEAR 1
#define LOOT_BOW 2
#define LOOT_ARROWS 3
#define LOOT_MEDKIT 4

#define ACT_WAIT 0
#define ACT_MOVE 1   /* arg = DIR_* */
#define ACT_ATTACK 2 /* arg = enemy fighter index */
#define ACT_GUARD 3  /* halves damage, fatigues after 2 consecutive ticks */
#define ACT_PICKUP 4 /* swap weapon with one on this tile (full turn) */
#define ACT_USE 5    /* use a medkit, +30 hp (full turn) */

#define DIR_N 0
#define DIR_S 1
#define DIR_E 2
#define DIR_W 3

typedef struct {
  int32_t index; /* stable fighter index (sorted id order, fixed all match) */
  int32_t x, y;
  int32_t hp;
  int32_t weapon; /* WEAPON_* */
  int32_t hat;    /* HAT_* */
} ArenaEnemy;

typedef struct {
  int32_t x, y;
  int32_t kind; /* LOOT_* */
  int32_t count;
} ArenaLoot;

typedef struct {
  int32_t x, y;
} ArenaRock;

typedef struct {
  int32_t version; /* == ARENA_API_VERSION */
  int32_t tick;
  int32_t size;        /* board is size x size; center is size/2 */
  int32_t safe_radius; /* Chebyshev radius of the safe square from center */
  int32_t alive;       /* living fighters, including you */
  int32_t truncated;   /* TRUNC_* bits; 0 in practice */
  int32_t self_index;
  int32_t self_x, self_y;
  int32_t self_hp;
  int32_t self_weapon;  /* WEAPON_* */
  int32_t self_arrows;  /* bow ammunition */
  int32_t self_medkits; /* carried medkits, max 2 */
  int32_t self_hat;     /* HAT_* */
  int32_t enemy_count;  /* visible enemies only (vision radius 8, rocks occlude) */
  ArenaEnemy enemies[ARENA_MAX_ENEMIES];
  int32_t loot_count;
  ArenaLoot loot[ARENA_MAX_LOOT];
  int32_t rock_count; /* rocks within vision radius */
  ArenaRock rocks[ARENA_MAX_ROCKS];
} ArenaObs;

typedef struct {
  int32_t type; /* ACT_* */
  int32_t arg;  /* DIR_* for MOVE, enemy index for ATTACK, else ignored */
} ArenaAct;

#define ARENA_OBS_WORDS (sizeof(ArenaObs) / 4)
#define ARENA_ACT_WORDS (sizeof(ArenaAct) / 4)

#endif /* ARENA_BOT_H */
