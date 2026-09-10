# 运行、验证与回放

> **职责：** 说明从仓库根目录启动、验收和回放当前版本的方法。输入是 Scenario v3、可选 RunConfig v3 与 CLI 参数；输出是运行结果和 Run Log v4。它覆盖核心循环的组合入口与已提交日志支路，不改变 Harness 的权威写入语义。

## 命令总览

```bash
token-odyssey --version
token-odyssey validate scenarios/floodgate_dispatch.yaml
token-odyssey run
token-odyssey selftest
token-odyssey replay runs/<run-id>
```

默认场景是 `scenarios/floodgate_dispatch.yaml`。`run` 支持 `--scenario`、`--run-config`、`--rounds`、`--seed`、`--player-view` 和 `--runs-dir`。

`--player-view Andy` 只在终端打印 Andy 获准的事件信息；完整作者记录仍写入运行目录。`completed` 表示非空 `end_when` 已达成，`limit_reached` 只表示行动预算耗尽，`waiting_for_input` 表示 CLI 遇到了 Human 参与者而没有持续交互端口。

## 永远离线的 selftest

```bash
token-odyssey selftest
token-odyssey selftest --mode scripted
token-odyssey selftest --mode translated --runs-dir runs/selftest
```

- `scripted`：直接从场景脚本产生类型化意图。
- `translated`：使用真正的 `LLMTranslator`、`LLMAgent`、会话与 JSON 解析，但回复来自确定性的本地 backend。
- `all`：默认依次运行以上两条链路。

两条链路都从 YAML 加载开始，经过路由、动作、机关、观测、终止条件、记录、`expected` 检查和回放。`selftest` 不接受 RunConfig，永远不读取 API key、也不访问网络。省略 `--runs-dir` 时使用自动清理的临时目录；显式传入后才保留 `acceptance.json` 等产物。

Python 层可使用同一实现，但 Python 模块路径不属于 v0.1 稳定 API：

```python
from token_odyssey.verification import run_acceptance

report = run_acceptance(
    "scenarios/floodgate_dispatch.yaml",
    root="runs/selftest",
    mode="translated",
)
assert report.success
```

## 真实 API 验收

下列命令会产生模型费用，输出也具有非确定性：

```bash
token-odyssey test-connection \
  --run-config configs/llm.local.yaml --profile flash

token-odyssey verify-live \
  --scenario scenarios/floodgate_dispatch.yaml \
  --run-config configs/llm.local.yaml \
  --profile flash --rounds 8 --runs-dir runs/live-check
```

`test-connection` 只请求一次 JSON 回复。`verify-live` 要求场景全部角色使用 LLM，以相同 profile 覆盖角色后完成一个受限 Act，并检查状态、`expected` 和回放；只有全部通过才返回 0。传输异常只输出异常类型，完整诊断留在本地运行记录。

真实 API 不进入 CI。发布前人工验收应分别执行一次连接测试、一次受限单 Act，以及 Campaign 的启动和从幕边界恢复。记录状态与 token 用量，但不要提交包含 prompt、回复或角色私密信息的原始目录。

## 运行目录

发行版本与格式版本相互独立。当前只读取 Run Log v4，不迁移旧格式。

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 记录版本、场景、种子、运行状态和结果 |
| `scenario.json` | 本次编译后的完整作者场景，可能含私有角色资料与脚本 |
| `initial_state.json` / `final_state.json` | 初始与结束动态状态 |
| `transactions.jsonl` | 权威 World Log：事务、事件、Change 与因果关系 |
| `events.jsonl` | 展平事件索引，回放时与事务流比对 |
| `observations.jsonl` | 每个角色实际获准的 Observation |
| `perception_samples.jsonl` | 感知分数、阈值与抽样；仅供作者调试 |
| `views.jsonl` | 每个新行动权实际生成的 ActorView |
| `requests.jsonl` / `decisions.jsonl` | 参与者请求、决策及 private thought |
| `action_results.jsonl` | 动作接受、拒绝、事务编号和 notice |
| `routing.jsonl` | Router 选择、权重、等待年龄和感知刺激证据 |
| `llm_exchanges.jsonl` / `prompt_flow.md` | 使用 LLM 时的完整请求与回复 |
| `token_usage.json` | 供应商报告的逐角色 token 用量 |
| `acceptance.json` | `selftest`、`verify-live` 或网页验收结论 |

没有数据的 JSONL 流可以不存在。API key 是 backend 建立连接时读取的配置输入，不进入上述记录；但 prompt、回复、角色私有想法和用户输入仍可能敏感，因此 `runs/` 已被 Git 忽略。

## 回放语义

```bash
token-odyssey replay runs/<run-id>
```

Replay 从初始快照开始，按已提交 Transaction 和事件顺序应用记录的 Change，检查 before 值、不变量、revision 与因果引用，再对照 final state、事件索引以及 Observation/ActorView 引用。

回放不会重新选择 Router、请求 Participant、执行今天的 Action 算法或重新抽样感知。它证明已记录变化、终态与投影内部一致，不证明未来规则仍会作出同样裁决。日志也没有签名或防篡改保证。

## 开发验收门槛

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=token_odyssey --cov-report=term-missing
token-odyssey selftest
```

覆盖率门槛为 85%。HTTP 集成测试只绑定临时的 `127.0.0.1` 端口；完整测试与 `selftest` 都不会调用真实模型。
