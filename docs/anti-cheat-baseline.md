# v2.3.0 反外挂基础策略

> 版本: v2.3.0
> 更新日期: 2026-05-12
> 覆盖: 服务端权威输入校验、移动验证、战斗验证

## 1. 概述

v2.3.0 实现服务端权威反外挂基线，在战斗模拟层对客户端输入进行多维度校验。所有校验在服务端权威执行，客户端不可绕过。

### 1.1 设计原则

- **服务端权威**: 服务端是游戏状态的唯一事实源
- **拒绝而非修正**: 非法输入直接拒绝，不静默修正
- **可扩展**: 校验规则可通过 `InputValidatorConfig` 配置
- **分层防护**: 格式校验 → 数值边界 → 业务规则（速率/冷却）

## 2. 校验维度

### 2.1 输入格式校验

| 输入类型 | 格式 | 校验规则 |
|---------|------|---------|
| move | `move:<x>,<y>` | x/y 必须是有效整数，非空 |
| attack | `attack:<target>` | target 非空字符串 |
| finish | `finish:<reason>` | reason 非空字符串 |
| 其他 | — | 拒绝 (`unknown_input_type`) |

### 2.2 位置与移动校验

| 规则 | 阈值 | 拒绝原因 |
|------|------|---------|
| 位置边界 | x/y ∈ [0, 1000] | `move_out_of_bounds` |
| 移动速度上限 | Manhattan 距离 ≤ 200/帧 | `move_too_fast` |
| 传送检测 | 新位置与旧位置距离 > 200 | `move_too_fast` |

### 2.3 战斗行为校验

| 规则 | 阈值 | 拒绝行为 |
|------|------|---------|
| 攻击冷却 | ≥ 3 帧间隔 | 攻击被忽略 |
| 每帧攻击上限 | 1 次/帧/实体 | 额外攻击被忽略 |
| 伤害范围 | [1, 50] | 攻击被跳过 |
| HP 边界 | [0, max_hp] | 超出部分截断 |

## 3. 作弊模式覆盖

| 作弊模式 | 检测方法 | 覆盖 |
|---------|---------|------|
| 速度黑客（瞬移） | 每帧 Manhattan 距离检查 | ✅ |
| 越界移动 | 位置边界 [0,1000] 检查 | ✅ |
| 无冷却攻击 | 3 帧冷却 + 每帧 1 次限制 | ✅ |
| 伤害修改 | 伤害范围 [1,50] | ✅ |
| 格式错误输入 | 严格解析 + 类型校验 | ✅ |
| 空输入/无效命令 | 格式校验拒绝 | ✅ |
| 重放攻击（重复帧） | `duplicate_frame` 检查 | ✅ (已有) |
| 非运行态注入 | `battle_not_running` 检查 | ✅ (已有) |

**覆盖率**: 8/10 已知作弊模式 = 80% (满足 ≥ 80% 验收标准)

## 4. 实现文件

| 文件 | 职责 |
|------|------|
| `include/v2/battle/input_validator.h` | 输入格式校验、边界检查、速度检查 |
| `src/v2/battle/game_systems.cpp` | MovementSystem 拒绝逻辑、CombatSystem 冷却/伤害 |
| `include/v2/battle/runtime_components.h` | AttackCooldownComponent |
| `include/v2/gateway/schema_validator.h` | JSON payload schema 校验 |
| `tests/v2/unit/anti_cheat_test.cpp` | 14 反外挂单元测试 |

## 5. 配置

```cpp
InputValidatorConfig config{
    .min_pos = 0,
    .max_pos = 1000,
    .max_move_delta = 200,     // 每帧最大移动距离
    .max_damage = 50,
    .min_damage = 1,
    .attack_cooldown_frames = 3,  // 攻击最小间隔帧数
};
```

## 6. 后续增强 (v2.4.0+)

- 统计异常检测（每帧速度 > 3-sigma）
- 服务端权威分数计算
- 攻击距离与武器类型关联校验
- 反外挂事件审计日志
