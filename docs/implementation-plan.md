# 重构实施记录

本次直接替换旧内核、scenario 和运行记录格式，不维护旧接口。

已对齐的规则：批量提交、逐动作执行；失败保留成功前缀；默认 move 后结束队列；give 使用 recipient_id；同房扫描，事件允许跨房传播；首版使用 YAML。

## 阶段

- [x] 1. 静态定义、动态状态、Fluent 和统一协议
- [x] 2. 单动作事务、明确的 Poss 和基础动作
- [x] 3. 门、容器、安装连接和机关连锁
- [x] 4. 结构化观测、同房扫描和位置记忆
- [x] 5. 运行循环、翻译器、API 配置和 Human 接口
- [x] 6. 新场景、全流程入口、回放和扩展文档

每个动作及其即时机关反应属于一次事务；安装连接独立于 attached。详细设计以本轮更新后的 architecture、actions、observation、scenario 和运行说明为准。

## 交付位置

| 阶段 | 主要实现与证据 |
|---|---|
| 1 | kernel/definitions.py、state.py、fluents.py；世界结构和能力校验测试 |
| 2 | kernel/actions、harness.py、runtime/runner.py；成功前缀与移动策略测试 |
| 3 | kernel/mechanics.py；自动放置触发、安装、操作、连锁回滚测试 |
| 4 | perception、translators；位置签名、多锚点事实授权、跨房声音测试 |
| 5 | agents、runtime/composition.py、config；每角色 profile、Human 暂停恢复测试 |
| 6 | sealed_chalice.yaml、verification.py、recording；CLI selftest 与完整文档 |

## 后续扩展顺序

1. 调整动作阈值、显著度和上下文措辞：已有独立参数和翻译模块，用 perception_samples 与 prompt_flow 检查效果。
2. 已实现交互加权 Router：消费已提交事件的实际观察，使用定向回应、关注、衰减和等待补偿；见 router.md。
3. 已实现 localhost 网页：连接 HumanAgent 与 Runner，新增开场即展示的完整角色简报和Router测试信息。
4. 自然语言世界翻译：生成可检查的 Scenario v3 YAML，复用确定性编译器；运行时不能绕过 Harness 改世界。
5. 更大框架接入：通过 Participant、LLMBackend、TurnRouter、Recorder 接口替换外部实现；异步世界事件需另定义受控的事务入口。

本轮不实现旧格式兼容、通用逻辑证明器、延时物理机制或跨进程 Human 会话恢复。

## 历史重构验证

- conda airpg 环境：Python 3.12。
- `pytest`：55 项测试全部通过。
- `token-odyssey selftest --runs-dir runs/refactor-validation`：scripted 和 translated 两种模式的最终条件、世界日志回放及投影记录检查均通过。
- `git diff --check`：通过；README 和 docs 的本地 Markdown 链接检查通过。
- 未使用真实模型 API；translated 模式模拟传输回复，但经过真实翻译器、会话和解析流程。真实服务由独立 test-connection / run 入口验证。

## 交互与场景增强验证

- 当前84项测试通过，其中localhost HTTP测试因沙箱禁止监听端口而单独在授权环境运行。
- 渡口新场景的scripted/translated两种离线流程完成，覆盖15类动作，实际动作均接受；状态、观察、视图、路由记录一致，回放通过。
- 五个种子下新场景均完成；Router另验证直接互动概率、信息隔离、去重、刺激衰减和多人等待上界。
- 使用真实WebSession数据检查首次行动前角色简报、动作失败反馈及文本转义；JavaScript语法、差异格式和文档本地链接检查通过。
- 本次未调用真实模型API，新场景的真实模型体验仍需后续试玩标定。
