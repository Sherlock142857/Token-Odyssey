# 封存圣杯：真实 API 实测

测试日期：2026-09-05。使用原始 `scenarios/sealed_chalice.yaml`、种子 19，
通过 `configs/llm.deepseek.yaml` 将三个角色全部接入 `deepseek-v4-flash`。
密钥从 `api.txt` 读取。核心代码、角色提示词和场景均未修改。

真实 API 接入和完整场景运行已验证；严格验收尚未全部通过。
扩大轮数预算后，模型完成了场景终止目标，但遗漏了两项收尾条件。

## 运行结果

| 项目 | 默认预算 | 扩大预算复测 |
| --- | --- | --- |
| 轮数预算 | 12 | 24 |
| 运行 ID | `20260905T053439Z-97d98f34` | `20260905T053537Z-33e4b37d` |
| 状态 | `limit_reached` | `completed` |
| 实际行动权 | 36 | 46 |
| 事务 / 事件 | 44 / 46 | 50 / 53 |
| 真实模型调用 | 39 | 51 |
| 记录的传输错误 | 0 | 0 |
| 预期条件 | 4/7 | 5/7 |
| 日志回放 | 通过 | 通过 |
| 严格验收退出码 | 1 | 1 |
| 总 token（含缓存输入） | 77,965 | 117,878 |

另有一次独立 `test-connection` 调用成功，其用量不包含在表中。
两次完整运行均记录了三个角色的真实模型回复，未使用作者脚本代替决策。
检查两次运行产物均未发现密钥内容。

## 复测的最终条件

| 条件 | 结果 |
| --- | --- |
| 圣杯放入三瓣凹槽 | 通过 |
| 继电组件安装到控制台 | 通过 |
| 门闩机关释放 | 通过 |
| 控制台灯亮 | 通过 |
| 封存盒重新锁好 | 未通过 |
| 透明柜关闭 | 未通过 |
| 探索者进入内室 | 通过 |

原场景 `end_when` 只要求探索者进入内室且控制台灯亮，
所以即使未重新锁盒、未关闭透明柜，运行器也会按原规则返回 `completed`。
`expected` 还要求这两项收尾状态，因此验收脚本正确报告失败。
锁盒是探索者的私人目标；关闭透明柜只出现在作者脚本和 `expected`，没有作为角色目标下发。
此结果表明当前模型自主行为未完全满足作者验收要求，不能表述为 7/7 全通过。

默认 12 轮运行中，模型绕行内室，并在放置圣杯之前操作控制台；
放置圣杯后未在预算内返回重新操作，因此灯未亮。
扩大预算复测是从同一场景初态开始的新运行，不是恢复第一次运行；模型输出也不保证一致。

## 复现与产物

以下是此次历史实测使用的命令。当前默认场景、提示词和Router已更新，configs/llm.deepseek.yaml的cast也已改为渡口六人；复测旧圣杯场景时须在另一个配置中将cast改回seeker/keeper/witness三人。本文结果不代表新场景已经经过真实API验证。

在项目根目录、激活 `airpg` 环境后执行：

```bash
python -m token_odyssey test-connection --run-config configs/llm.deepseek.yaml --profile standard
python scripts/live_selftest.py --scenario scenarios/sealed_chalice.yaml --run-config configs/llm.deepseek.yaml
python scripts/live_selftest.py --scenario scenarios/sealed_chalice.yaml --run-config configs/llm.deepseek.yaml --rounds 24
python -m token_odyssey replay runs/20260905T053537Z-33e4b37d
```

本次复测产物：

- [验收报告](../runs/20260905T053537Z-33e4b37d/acceptance.json)
- [完整模型对话](../runs/20260905T053537Z-33e4b37d/prompt_flow.md)
- [Token 用量](../runs/20260905T053537Z-33e4b37d/token_usage.json)

`runs/` 已被 Git 忽略，这些链接指向本地测试产物。
离线基线另已通过：55 项测试、scripted 和 translated 两种全流程及回放。
