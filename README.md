# Token Odyssey

由 LLM、脚本或未来人类界面控制角色的 RPG demo。参与者只提交动作意图；程序维护世界事实，并决定每个角色实际看见和听见什么。

本轮使用 **Scenario / API 配置版本 3、运行记录版本 4**。旧 `inside_act` 内核、旧场景与旧记录接口已移除。

## 从完整场景开始

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m token_odyssey validate scenarios/sealed_chalice.yaml
python -m token_odyssey selftest
pytest
```

`selftest` 默认运行两次：一条使用脚本参与者，一条使用真实的 LLM 翻译器和会话层、由离线脚本模拟 API 回复。两次都检查场景声明的最终条件，并通过日志回放检查状态及投影记录。**此命令不联网、不使用 API key。**

新场景“封存圣杯”覆盖：交付钥匙 → 解锁并开门 → 打开盒子 → 发现和取出圣杯 → 放入凹槽 → 机关连锁释放门闩 → 安装组件并操作设备 → 重新锁好盒子 → 进入内室。

场景故意安排一次失败：透明柜中的组件看得见，但柜子没开，不能取出。此前已经放好的圣杯和启动的机关必须保留。

## 运行与检查

```bash
# 按 scenario.cast 运行；示例默认全部使用脚本。
python -m token_odyssey run --rounds 12

# 终端只展示这一角色实际获得的事件信息。
python -m token_odyssey run --player-view witness

# 从记录的状态变化和角色视图回放，不重做决策或随机感知。
python -m token_odyssey replay runs/<run-id>

# 全流程入口可单独选择脚本或经过翻译器的模式。
python -m token_odyssey selftest --mode translated --runs-dir /tmp/airpg-runs
```

`run`、`selftest` 支持 `--scenario`。所有运行产物写入独立目录；`selftest` 还生成 `acceptance.json`。详情见 [运行与记录](docs/running.md)。

需要安装命令行入口时：

```bash
python -m pip install -e '.[dev]'
token-odyssey validate
```

## 接入真实模型

本地 DeepSeek 接入配置为 [configs/llm.deepseek.yaml](configs/llm.deepseek.yaml)，
使用根目录 `api.txt` 的单行密钥，三个角色均由 `deepseek-v4-flash` 驱动。
服务地址和模型 ID 对照 [DeepSeek 官方文档](https://api-docs.deepseek.com/)；
通过 `extra.thinking` 显式关闭思考模式，输出预算用于动作 JSON。
密钥文件已被 Git 忽略，不要把密钥填入 YAML。

在项目根目录、激活 `airpg` 环境后运行（会实际调用 API）：

```bash
python -m token_odyssey test-connection --run-config configs/llm.deepseek.yaml --profile standard
python scripts/live_selftest.py --scenario scenarios/sealed_chalice.yaml --run-config configs/llm.deepseek.yaml
```

真实全流程入口使用原有运行器、翻译器和 API 适配器，按场景默认 12 轮运行，
检查 `completed`、全部 `expected` 及日志回放，写入 `runs/<run-id>/acceptance.json`。
任一检查失败或 API 异常都会返回非零退出码；模型自主决策不保证每次满足全部条件。
完整对话与用量见同目录 `prompt_flow.md` 和 `token_usage.json`。
可用 `--rounds` 调整轮数，`--runs-dir` 指定产物目录。

接入其他服务时：

编辑 [API 配置示例](configs/llm.example.yaml) 中的服务地址与模型 ID，并设置 `AIRPG_API_KEY`。示例没有可直接使用的供应商或模型配置。

```bash
python -m token_odyssey test-connection --run-config configs/llm.example.yaml --profile standard
python -m token_odyssey run --run-config configs/llm.example.yaml --rounds 1
```

这两个命令会实际调用你配置的服务。每个角色可以使用不同 profile；凭据不进入 scenario、世界状态或模型上下文。

## 核心规则

- 一次回复可含多个动作；每个动作及其即时机关反应独立提交。
- 后续动作失败只停止剩余队列，成功前缀不会撤销或重做。
- 默认移动成功后结束队列；`turn_policy.continue_after_move` 可以开启后续互动。
- `inside / attached` 只表示空间关系；安装连接单独记录。
- 同房扫描与跨房事件传播分开；可见、可接触和可通行分别判断。
- 观测按事实授权；只看到离开，不会因此知道目的地。
- 模型使用自然语言上下文和 JSON 回复；人类接口接受表单动作，不需要人类输入 JSON 文本。

## 阅读顺序

1. [架构、职责与事务时序](docs/architecture.md)
2. [空间模型与 Fluent](docs/spatial-model.md)
3. [动作参数与新增动作](docs/actions.md)
4. [机关规则与因果连锁](docs/mechanics.md)
5. [观测、数值判定与位置记忆](docs/observation.md)
6. [Scenario 编写与校验](docs/scenario.md)
7. [Agent、翻译器、API 与 Human 接口](docs/agent-llm.md)
8. [完整运行、记录、回放与验证](docs/running.md)
9. [阶段完成记录与后续扩展](docs/implementation-plan.md)

首版完成 YAML 编译和 Human 接口，尚未实现自然语言场景生成、网页前端或交互加权 Router。
