# Token-Odyssey

Token-Odyssey 是一个探索 **LLM、可验证世界与人类玩家共同参与叙事** 的终端 RPG 项目。

它不让语言模型直接扮演世界本身。模型负责角色的意图、判断与表达；一个独立、确定性的 World Harness 负责世界事实、动作规则与状态变化。所有参与者——NPC 与人类玩家——看到的上下文，都是世界历史针对各自视角生成的投影。

项目围绕三个核心创新点展开。

## World As Harness

世界不是一段由模型自由续写的文本，而是一个可执行、可验证的规则环境。

- `WorldState` 是当前世界事实的唯一真相。
- 只有 `WorldHarness` 能验证动作并原子地修改状态。
- Agent 只提出意图，不能直接宣告结果。
- 成功发生的行为会生成结构化 `WorldEvent`；非法意图不会成为世界事实。
- 世界可以被记录、重放和测试，而不依赖模型“记住”此前发生过什么。

这让生成式角色拥有开放的行动空间，同时让世界仍然保持一致、可审计和可复现。

## Context As Projection of World Log

上下文不是共享的完整剧本，而是 **World Log 在特定观察者视角下的投影**。

```text
WorldState + WorldEvent
          │
          ▼
  Observation System
     ┌────┼────┐
     ▼    ▼    ▼
  Agent A  Agent B  Player
  Context  Context  Context
```

每个参与者只接收自己能够观察到的信息，并将其追加到各自独立的会话中。同一个世界事件可以被不同角色完整观察、部分观察或完全错过。

因此，秘密、误解、信息差和戏剧性不再依赖提示词约定，而是来自世界历史与观察规则本身。Canonical World Log 与各角色的主观经历彼此分离：前者定义“实际发生了什么”，后者定义“这个角色认为发生了什么”。

## Player As Human Agent

玩家不是凌驾于模拟之上的操作者，而是世界中的一个 **Human Agent**。

人类玩家与 AI 角色遵循同一套基本边界：拥有角色身份，接收有限观察，基于自己的上下文提出行动意图，并由 World Harness 判定结果。其他角色不需要知道一个参与者由人类还是模型控制。

这种设计希望让人与 AI 的差别体现在决策方式上，而不是世界权限上。最终，Human Agent、LLM Agent，乃至其他类型的 Agent，都可以通过同一种接口进入同一个可验证世界。

## 核心循环

```text
Router 选择参与者
  → 投影该参与者的私有上下文
  → Human / LLM Agent 提出 TurnIntent
  → World Harness 校验并执行
  → 更新 WorldState，追加 WorldEvent
  → 为每个观察者生成新的上下文投影
```

更详细的设计与不变量见 [架构文档](docs/architecture.md)。

## 当前开发状态

Token-Odyssey 仍处于非常早期的原型阶段。目前已有一个可运行的固定 Act、基础动作与观察系统、隔离的 Agent 会话，以及运行记录和确定性 Replay。人类玩家的完整交互入口和更高层的剧情推进仍在开发中，现有接口与数据格式也可能继续变化。

## 快速开始

当前 Python 包和命令行入口仍使用历史名称 `airpg`。

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m pip install -e '.[dev]'
```

先验证示例场景，再用不调用外部模型的 demo provider 运行：

```bash
airpg validate scenarios/rainy_night.yaml
airpg run --provider demo --rounds 10 --seed 19
```

使用 DeepSeek 运行：

```bash
airpg run \
  --scenario scenarios/rainy_night.yaml \
  --provider deepseek \
  --rounds 50
```

API key 需单独放在只有一行内容的 `api.txt` 中；该文件已被 `.gitignore` 排除。每次运行的世界事件、角色观察、模型决策、最终状态与可读 transcript 会分别保存在 `runs/<run_id>/`。

重放一次已有运行并验证世界结果：

```bash
airpg replay runs/<run_id>
```

运行测试：

```bash
pytest
```
