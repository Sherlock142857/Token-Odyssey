# 运行、全流程验证与回放

## 命令

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m token_odyssey validate scenarios/sealed_chalice.yaml
python -m token_odyssey run --scenario scenarios/sealed_chalice.yaml --rounds 12
python -m token_odyssey selftest --mode all
python -m token_odyssey replay runs/<run-id>
pytest
```

默认场景为 sealed_chalice。run 支持 --scenario、--run-config、--rounds、--seed、--player-view、--runs-dir。

--player-view 只输出该角色获准的事件信息，不同时输出上帝视角事件。运行统计和输出目录属于调试信息。完整世界事实仍在作者记录目录中。

`run` 根据实际状态返回 completed、limit_reached 或 waiting_for_input。completed 表示非空 end_when 已达成；limit_reached 表示行动预算耗尽，并不保证目标达成。

## 全流程入口

CLI `selftest` 和 Python `verification.run_acceptance` 使用同一实现：

```python
from token_odyssey.verification import run_acceptance

report = run_acceptance(
    "scenarios/sealed_chalice.yaml",
    root="runs",
    mode="translated",
)
assert report.success
```

- scripted：直接从场景脚本产生类型化意图。
- translated：模拟 API 的回复内容，但经过真正的 LLMTranslator、LLMAgent 会话、回复解析和相同内核。
- all：CLI 依次运行以上两种模式。

两种模式都从 YAML 加载开始，经角色初始化、路由、动作、机关、观测、反馈和终止条件，到记录、最终条件断言和回放结束。接受场景必须声明 expected；检查失败时 CLI 返回非零。

translated 并不检验真实模型能否理解谜题，也不验证供应商连通性。真实服务使用 test-connection 和带 --run-config 的 run；离线 selftest 不读取凭据或联网。

### 真实 API 全流程验收

```bash
python scripts/live_selftest.py --scenario scenarios/sealed_chalice.yaml --run-config configs/llm.deepseek.yaml
```

这是独立接入脚本，不修改内核运行规则，也不改变离线 `selftest` 的行为。
它要求所有角色使用 LLM，以原始场景启动真实 API 对话，按场景的轮数预算运行，
随后检查终止目标、全部 `expected` 和日志回放，生成 `mode: live` 的 `acceptance.json`。
仅当全部通过时退出码为 0；达到轮数上限、未满足条件、回放失败或调用异常均返回非零。
传输失败的运行目录保留 `manifest.json` 和已记录的交换，不能视为验收成功。
`--rounds` 可覆盖轮数，`--runs-dir` 可指定运行目录。

真实模型可能选择与作者脚本不同的合法路径，也可能遗漏目标。
场景脚本中故意安排的“关闭透明柜内取物失败、保留成功前缀”仍由离线验收和单元测试覆盖，
不要求自主模型重复这一错误；此入口不会向模型注入作者脚本或 `expected`。

## 运行目录

Scenario/API 配置版本为 3，运行记录版本为 4。旧格式不做兼容。

| 文件 | 内容 |
|---|---|
| manifest.json | 记录版本、场景、种子、运行状态、结果 |
| scenario.json | 本次编译后的完整作者场景，含私人角色资料与脚本 |
| initial_state.json / final_state.json | 初始和结束动态状态 |
| transactions.jsonl | 权威 WorldLog；单动作事务、事件、Change、因果关系 |
| events.jsonl | 展平的事件索引，回放时与事务内容比对 |
| observations.jsonl | 每个角色实际获准的观测结果 |
| perception_samples.jsonl | 感知分数、抽样、阈值与判定；仅供作者调试 |
| views.jsonl | 每次新行动权实际生成的 ActorView |
| requests.jsonl | 初次请求与输入修正请求 |
| decisions.jsonl | 通用参与者决策，包括本人的 private_thought |
| action_results.jsonl | 每个尝试动作的接受、失败、事务编号与 notice |
| routing.jsonl | 实际选择的角色顺序 |
| fallbacks.jsonl | 仅在重试耗尽时生成 |
| llm_exchanges.jsonl | 使用 LLM 适配器时的实际请求与响应 |
| prompt_flow.md | 可阅读的模型输入输出增量 |
| token_usage.json | 按角色汇总的模型用量 |
| acceptance.json | selftest 或 scripts/live_selftest.py 生成的验收结果 |

JSONL 中没有记录的流可能不创建文件。例如顺利运行没有 fallbacks.jsonl；纯脚本运行没有 llm_exchanges.jsonl。

## 回放语义

replay 读取初始快照，按 Transaction 和事件顺序应用记录的 Change，检查前值、不变量、事务 revision 和事件因果引用，再与 final_state 比较。同时校验 events 索引与观测/视图之间的引用。

返回的 ReplayReport.views 是实际记录过的角色视图，可供未来回放前端使用。回放不重做 Router 选择、不重新请求角色、不重新运行动作算法，也不再次抽样感知。

这验证记录的世界变化与终态一致，并保留原有视角，不等价于证明另一个版本的规则仍会作出相同判定。要验证新规则，应开始新运行。

记录目前没有签名、数据库事务或崩溃恢复协议；它用于 demo 调试和复现实验，不宣称是防篡改审计系统。

## 关键验收案例

测试集中覆盖：

- 透明关闭容器：能看见、不能拿取；自己携带的关闭容器同样会阻挡。
- 门可从两侧操作，可通行性不由视觉连通性代替。
- give 使用 recipient_id；同房检查具体，失败不修改世界。
- 后续失败保留前缀；move 开关两种行为；新发现的 ID 不能补授权给预先提交的队列。
- place 与 install 分离；operate 条件依赖真实安装连接。
- 多步机关因果顺序、一次性规则、错误回滚与循环上限。
- 同房扫描、完整位置签名、关闭遮挡、未知目的地与跨房声音隔离。
- Human 等待与恢复、模型格式修正、每角色 profile 与私人背景隔离。
- 完整新场景两种模式产生相同世界与角色视图，回放不调用 RNG。

验证服务连接可单独运行：

```bash
python -m token_odyssey test-connection --run-config configs/llm.example.yaml --profile standard
```

先把示例的占位地址、模型 ID 和凭据配置换成你的实际服务。
