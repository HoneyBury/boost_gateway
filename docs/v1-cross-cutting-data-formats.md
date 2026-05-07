# v1.x 横切数据格式与后端支持级别（v1.1.17 / T16）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md` Persistence·Audit 路线图第三步**与 **§11 T16**：把 **player 持久化后端**、**`AUDIT_LOG` 行格式**、**replay 磁盘形态与 `ReplayPlayer` 读取契约** 写成 **「支持级别 + 字段事实」**，防止「头文件里有接口」被误读成「线上可依赖的兼容格式」。
- **单一事实源**：**成熟度等级**仍以 **`docs/v1-maturity-matrix.md` §6 / §4.4** 为准；本文 **不升格** `experimental` / `reserved`，只补充 **实现层细节** 与 **兼容性声明**。
- **不在本版**：持久化 / 审计 / replay 的 **自动化回归**归 **`v1.2.4`（T20）**。

---

## 2. 支持级别用语（本文内）

| 用语 | 含义 |
|------|------|
| **演示级** | 与 showcase 手写停服保存等配套；**无**跨版本格式 semver **承诺**。 |
| **读侧契约（预留）** | 解析逻辑存在于 **`ReplayPlayer`** 等；**主链不产生**对应载荷时不构成「对外协议」。 |
| **格式脆弱** | 字符串拼接或未转义字段会导致 **非合法 JSON**；仅供排障与演示，**禁止**当作结构化日志管道协议。 |

---

## 3. Player 持久化

### 3.1 `JsonFilePlayerStore`（默认可见实现）

| 项 | 说明 |
|----|------|
| **支持级别** | **演示级**（矩阵 §6：`experimental`）。 |
| **路径规则** | 目录 `dir` + **`{user_id}.json`**（`user_id` **未**做路径穿越消毒，集成方须保证 id 安全子集）。 |
| **JSON 对象字段**（与 `player_store.h` 一致） | `user_id`（string）、`display_name`（string）、`score`（integer）、`last_login_ts`（integer）。缺失键按 **`value(..., 默认)`** 填补。 |
| **序列化** | `nlohmann::json::dump(2)`（带缩进）。 |
| **兼容性** | **v1.x 维护期内可随代码改动**；不作为对外存档冻结格式。 |

### 3.2 `SqlitePlayerStore`（条件编译）

| 项 | 说明 |
|----|------|
| **支持级别** | **演示级 / 备选**（矩阵 §6：`experimental`）。 |
| **编译** | 仅当定义 **`HAS_SQLITE`** 时类存在（见 **`include/game/persistence/sqlite_store.h`**）。**仓库默认 CMake 未统一开启该宏**，多数构建下 **无此类型**。 |
| **Schema** | 表 **`players`**：`user_id TEXT PRIMARY KEY`，`display_name TEXT`，`score INTEGER DEFAULT 0`，`last_login_ts INTEGER DEFAULT 0`。 |
| **配置** | **`GatewayAppConfig` / `auth_provider` 无 store 选择字段**；切换后端只能靠 **手工装配**。 |

---

## 4. Battle replay 磁盘与 JSON 契约

### 4.1 `JsonFileBattleReplayStore`

| 项 | 说明 |
|----|------|
| **支持级别** | **预留存储载体**（矩阵 §6：`reserved`）；**不参与** `BattleManager::end_battle()` 生产链。 |
| **路径规则** | `dir / (battle_id + ".replay")`。 |
| **载荷** | **`save_replay`** 将 **`replay_data` 原样按二进制写入**；**无** magic / 版本头 / JSON 封装 — **语义完全由调用方字符串决定**。 |

### 4.2 `ReplayPlayer::load` 读侧契约（JSON 文本）

**仅当**磁盘内容为 **合法 UTF-8 JSON 对象** 时，`parse` 成功；结构如下（字段名与 **`replay_player.h`** 一致）：

| JSON 路径 | 类型 | 说明 |
|-----------|------|------|
| `battle_id` | string | 可选回填 |
| `room_id` | string | 注入每帧 `FrameSnapshot.room_id` |
| `total_frames` | number | 可选元数据 |
| `frames` | array | **必填** |
| `frames[].frame` | number | 映射 `frame_number` |
| `frames[].inputs` | array | 每帧输入列表 |
| `frames[].inputs[].seq` | number | `InputEvent.sequence` |
| `frames[].inputs[].user_id` | string | |
| `frames[].inputs[].payload` | string | |

| 项 | 说明 |
|----|------|
| **支持级别** | **读侧契约（预留）**；**无**「官方 replay 导出」与之对齐，主链不落盘。 |
| **兼容性** | 新增字段可扩展；**破坏性改动无单独版本协商**。 |

---

## 5. 审计日志（`AUDIT_LOG`）

实现见 **`include/app/audit_log.h`**。

| 项 | 说明 |
|----|------|
| **支持级别** | **格式脆弱**（矩阵 §4.4：`experimental`）；**不等于** JSON Lines 标准产物。 |
| **物理位置** | **`logs/audit.log`**，追加写；进程内 mutex；首次写前创建 **`logs/`**。 |
| **行模板** | `{"ts":"<ISO本地时间无TZ>","event":"<event_type>","details":"<details>"}` + `\n`。 |
| **`ts`** | `YYYY-MM-DDThh:mm:ss`（本地时区），**无**毫秒与 **`Z`/`±hh:mm`**。 |
| **`details`** | **原文拼接进 JSON 字符串**，**未**做 `escape`；若含 `"`、换行或控制字符，**整行可能非法 JSON**。 |
| **兼容性** | **不提供**字段稳定 semver；**T11** 仅约束 **`admin_invoke`** 子集的 **必备键=k=v**（见 **`v1-admin-audit-rules.md`**），**不**修复全局 JSON 合法性。 |

---

## 6. 与 roadmap 的关系

| 版本 | 内容 |
|------|------|
| **`v1.1.15`**（T14） | **`v1-cross-cutting-capabilities.md`**：接线事实 |
| **`v1.1.16`**（T15） | **`v1-cross-cutting-lifecycle-binding.md`**：应收口规范 |
| **`v1.1.17`**（本文 / T16） | **格式与支持级别冻结（叙述层）** |
| **`v1.2.4`**（T20） | **已完成**：格式与行为回归测试（`persistence_replay_audit_test`） |
