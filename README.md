# AirPG

AirPG 是一个 LLM 驱动、程序约束的终端 RPG 原型。它把模型限制在“决定意图和台词”的位置；世界事实、动作合法性、物品移动、空间关系、事件记录和角色可见信息全部由 Python World Harness 管理。

当前版本实现单个固定 Act 内的循环，不负责 Act 结束判定。默认测试上限为 50 轮，每轮随机打乱角色顺序，每名角色行动一次。

## 核心边界

```text
Router
  -> 某角色的 Observation Domain / 私有上下文
  -> Agent 输出 TurnIntent
  -> World Harness 原子校验
  -> WorldState 状态变化 + 结构化 WorldEvent
  -> Observation System 为每名角色分别投影
  -> 各自的 AgentRuntime / ObservationLog
```

- `WorldState` 是当前事实的唯一真相。
- 只有 `WorldHarness` 能改动 `WorldState`。
- `WorldEvent` 只记录成功发生的动作和对话；非法意图不会进入世界日志。
- 每个 NPC 只接收自己的观察、知识、记忆和私有想法。
- 每个 NPC 拥有永久追加的独立 `AgentSession`，不会截断、摘要或重建历史。
- Agent 不知道其他角色由人类还是 AI 控制。
- 相同 seed 可复现 Router 顺序与观察掷骰。

更详细的不变量见 [docs/architecture.md](docs/architecture.md)。

## 安装

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m pip install -e '.[dev]'
```

`api.txt` 必须只有一行 API key。它已被 `.gitignore` 排除。

测试场景默认关闭 DeepSeek thinking mode，以降低每回合延迟、避免 reasoning 挤占结构化 JSON 的输出预算。该开关属于 Actor 的 `model.thinking_enabled`，需要时可单独开启。

## 运行

先验证测试场景：

```bash
airpg validate scenarios/rainy_night.yaml
```

验证 DeepSeek API、base URL 和模型名（会产生一次很小的模型调用）：

```bash
airpg test-connection \
  --api-key-file api.txt \
  --base-url https://api.deepseek.com \
  --model deepseek-v4-flash
```

让所有角色使用 DeepSeek 连续运行 50 轮：

```bash
airpg run \
  --scenario scenarios/rainy_night.yaml \
  --provider deepseek \
  --rounds 50
```

3 名角色运行 50 轮最多会触发约 150 次正常模型调用；输出非法时会额外重试。API、认证或网络故障会立即终止 Act。

每次 `run` 都会在 `runs/<run_id>/` 下保存完整运行记录。终端默认只显示按轮排列的舞台对白和动作。

不调用外部 API 的 Harness 冒烟测试：

```bash
airpg run --provider demo --rounds 10 --seed 19
```

导出只包含成功事件的 canonical World Log：

```bash
airpg run --provider demo --rounds 10 --world-log run/world-log.jsonl
```

模拟未来玩家界面，只即时显示某个角色收到的观察：

```bash
airpg run --provider demo --rounds 10 --player-view shen_lan
```

不调用 LLM，使用已记录的原始模型输出重放并校验世界结果：

```bash
airpg replay runs/<run_id>
```

Replay 会比较所有 `WorldEvent` 与最终 `WorldState`，适合验证 Harness 重构没有改变语义。

## Append-only NPC 会话

DeepSeek API 是无状态接口，因此客户端每轮仍需发送完整消息数组。AirPG 不再重建单个大 user prompt，而是为每个 NPC 永久追加：

```text
system      固定世界、角色和动作规范
user        Act 初始投影
assistant   原始 TurnIntent JSON
user        新观察、上一行动结果和当前行动条件
assistant   下一次 TurnIntent JSON
...
```

- 旧 World Log、Observation、assistant 输出和私有想法不会被删除。
- Harness 拒绝的 assistant 输出及私有反馈也保留在该角色会话中。
- 后续请求完整复用上一请求作为前缀，以利用 DeepSeek 自动上下文缓存。
- `token_usage.json` 分别记录总 prompt、cache hit、cache miss、completion 和命中率。
- 当前阶段不做 token 压缩、摘要、checkpoint 或最近 N 条截断。

## 完整运行记录

```text
runs/<run_id>/
├── manifest.json
├── scenario.json
├── initial_state.json
├── final_state.json
├── world_events.jsonl
├── observations.jsonl
├── decisions.jsonl
├── state_changes.jsonl
├── trace.jsonl
├── token_usage.json
├── sessions.json
├── transcript.md
└── agents/<actor_id>.jsonl
```

Canonical 世界事实、角色私有会话、模型原始输出、Observation Domain、随机判定和可读剧情彼此分开，不会混成一份日志。

## Developer mode

`--developer` 会显示内部信息；完整信息无论是否显示都会写入 Run 目录。使用 `--developer-view context,intent,state_change` 可以只显示指定类别，使用 `all` 显示全部：

- 每轮 Router 顺序；
- 每次发给角色的完整隔离上下文；
- Agent 输出及私有想法；
- 非法动作原因与重试；
- WorldState 变化和结构化 WorldEvent；
- 每名角色的观察阈值、随机数与 full/partial/none 结果；
- 移动或搜索产生的私有环境更新。

## 动作模型

每回合允许一项物理动作与一段可选对话。若两者同时存在，对话先在当前房间发生，然后执行物理动作。

物理动作包括 `move`、`search`、`take`、`give`、`place`、`show`、`hide`、`wait`。对话对象、展示对象和物品接收者必须处于同一房间；它们不会自动移动。每项动作与对话都有独立的 `normal`、`secretive`、`conspicuous` 模式。

物品只拥有一个位置关系：`room`、`container`、`held`、`hidden` 或 `attached`。物品的有效 room 沿容器或角色位置递归推导，因此随身物品会自然跟随角色移动。

动作实现位于 `src/airpg/harness/actions/`，每个动作拥有独立 Pydantic schema 和 handler。`WorldHarness` 只通过 registry 调度 `validate → ActionEffect → atomic commit`；新增动作不需要扩展中央 `if/elif`。

## 测试

```bash
pytest
pytest --cov=airpg --cov-report=term-missing
```

合成场景“雨夜遗嘱”包含 3 个房间、3 名互相冲突的 NPC、一个不透明容器、一个半透明容器和 6 件物品，可覆盖当前 Harness 的主要交互。

## 当前限制

- 没有 Act 结束条件或更高层剧情导演。
- 没有门锁、阻挡、战斗、同意/拒收、角色隐藏和遗忘系统。
- 视觉与听觉暂时共用一个有方向的 room 可见度矩阵。
- `owner_id` 只是社会事实，不构成拿取权限；`controller` 暂未建模。
- 容量以整数等级表示，只统计容器直接子物品的 size。
- 人类玩家 Agent 尚未接入，但 Observation listener 与 `--player-view` 已保留边界。
- 当前明确选择保留完整 NPC 会话，长 Act 的上下文长度与注意力管理留到后续阶段。
