# Tank Battle — Game Rules

## Overview

2D top-down tank battle. Two or more players each control a tank on a
grid-based map with walls. The last player standing wins; if a time limit
expires the player with the most kills wins.

## Map

- Grid-based (e.g. 20x15 tiles).
- Walls are indestructible obstacles.
- Players spawn at fixed spawn points.

## Tank

- Each player has one tank.
- **Health**: starts at 100 HP.
- **Movement**: one tile per action, four directions (up/down/left/right).
- **Fire**: shoots a bullet in the facing direction. Cooldown: 3 ticks.
- **Death**: HP reaches 0 → tank explodes → player eliminated.

## Bullet

- Travels one tile per tick in the direction fired.
- Destroys itself on wall hit or tank hit.
- Cannot pass through walls.
- A hit deals 25 damage.

## Scoring

- Kill: +100 points.
- Damage dealt: +1 per HP.
- Survival bonus: +50 points (alive at the end).
- Win bonus: +200 points (last alive or most kills on time limit).

## Win Conditions

1. **Last tank standing** — all other players eliminated.
2. **Time limit reached** (e.g. 180 seconds / 5400 ticks at 30 Hz) —
   player with the most kills wins. Ties broken by remaining HP.

## Anti-Cheat

- Movement speed limit: max 1 move action per tick.
- Fire rate limit: max 1 fire every 3 ticks.
- Direction validation: direction must be one of 0/90/180/270 degrees.
- Seq must be monotonically increasing per player.
