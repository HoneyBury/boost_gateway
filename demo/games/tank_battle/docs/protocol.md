# Tank Battle — Protocol

## Framework Envelope

All tank messages travel inside the framework `realtime_instance` envelope.
The framework routes by `instance_id` and `payload_type`; it does not
interpret the tank payload.

```json
{
  "domain": "realtime_instance",
  "operation": "input",
  "instance_id": "room_001:battle_001",
  "payload_type": "tank.input",
  "payload": "{...}"
}
```

## Tank Input Payload

Sent by the client each tick (or on action):

```json
{
  "seq": 42,
  "actions": [
    {"type": "move", "dx": 0, "dy": -1},
    {"type": "fire", "direction": 0}
  ]
}
```

### Actions

| type | fields | description |
|------|--------|-------------|
| `move` | `dx`, `dy` | Move tank by (dx, dy). Each must be -1, 0, or 1. |
| `fire` | `direction` | Fire bullet in direction (0=up, 90=right, 180=down, 270=left). |
| `stop` | none | Stop movement this tick. |

## Tank Snapshot Payload (push)

Pushed to all players each tick:

```json
{
  "frame": 150,
  "tanks": [
    {"user_id": "alice", "x": 5, "y": 3, "hp": 100, "direction": 0, "alive": true},
    {"user_id": "bob",   "x": 12,"y": 8, "hp": 50,  "direction": 180, "alive": true}
  ],
  "bullets": [
    {"id": "b_17", "x": 7, "y": 4, "dx": 0, "dy": -1}
  ],
  "events": [
    {"type": "bullet_hit", "bullet_id": "b_17", "target": "bob", "damage": 25}
  ],
  "finished": false
}
```

## Settlement Payload

Sent by the tank plugin when the battle ends:

```json
{
  "battle_id": "battle_001",
  "room_id": "room_001",
  "reason": "last_standing",
  "players": [
    {
      "user_id": "alice",
      "display_name": "Alice",
      "kills": 3,
      "deaths": 1,
      "damage": 1200,
      "score": 1350,
      "win": true
    }
  ]
}
```

## Error Codes

| code | meaning |
|------|---------|
| 4001 | invalid action |
| 4002 | move out of bounds |
| 4003 | fire cooldown active |
| 4004 | tank not found |
| 4005 | battle not found |
| 4006 | battle already finished |
