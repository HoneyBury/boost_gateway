# Tank Battle — Acceptance Criteria

## D0: Docs & Boundaries

- [ ] demo directory exists at `demo/games/tank_battle/`
- [ ] business protocol separated from framework envelope
- [ ] SDK adapter does not modify public SDK
- [ ] no `Tank`, `Bullet`, `Collision` types leaked into `include/` or `src/`
- [ ] default build does not depend on demo

## D1: Minimum Business Flow

- [ ] two players register and login
- [ ] create room, join room, both ready
- [ ] start realtime instance
- [ ] send move/fire input
- [ ] receive snapshot push
- [ ] finish battle on demand
- [ ] query leaderboard rank

## D2: Disconnect & Reconnect

- [ ] client disconnects mid-battle
- [ ] reconnect within window restores room and instance
- [ ] resume snapshot received after reconnect
- [ ] can continue sending input or finish after resume

## D3: Business Rules

- [ ] deterministic simulation: same inputs → same output
- [ ] movement bounded to one tile per tick
- [ ] fire cooldown enforced (3 ticks)
- [ ] bullets blocked by walls
- [ ] bullet hit reduces HP
- [ ] HP <= 0 triggers death
- [ ] illegal speed/fire-rate/direction rejected
- [ ] win/loss/score rules reproducible

## D4: Reliability

- [ ] demo full-flow summary output
- [ ] gateway/backend metrics visible for demo traffic
- [ ] leaderboard idempotent settlement verifiable
- [ ] backend down / reconnect / timeout produces clear errors

## D5: Performance

- [ ] demo perf smoke: 2/20/100 rooms
- [ ] report shows connections, tick rate, snapshot rate, P50/P90/P99, err, CPU/RSS
- [ ] conclusions scoped to tank demo, not framework baseline
