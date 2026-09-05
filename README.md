# Token Odyssey

由 LLM、脚本或人类网页界面控制角色的 RPG demo。参与者只提交动作意图；程序维护世界事实，并决定每个角色实际看见和听见什么。

本轮使用 **Scenario / API 配置版本 3、运行记录版本 4**。旧 `inside_act` 内核、旧场景与旧记录接口已移除。

## 从完整场景开始

### 用 localhost 网页测试一个 act

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m token_odyssey web --run-config configs/llm.deepseek.yaml
```

打开 **http://localhost:8000**。默认由你扮演押运信使林雁，另外五名 NPC 使用 LLM；
在网页中可逐角色改成人类、LLM 或离线脚本。点击“开始 Act”后才会调用模型 API。
不传 `--run-config` 则默认使用人类＋脚本，可离线测试。

网页提供角色状态、独立滚动的角色日志 / World Log、物品和出口点选、全部动作的表单与队列、
自动推进 / 单回合推进，以及结束条件和日志回放检查。刷新页面可继续当前运行。
具体交互、数据边界及测试方式见 [网页测试台](docs/web.md)。

```bash
source /home/xuanz/miniconda3/etc/profile.d/conda.sh
conda activate airpg
python -m token_odyssey validate scenarios/floodgate_dispatch.yaml
python -m token_odyssey selftest
pytest
```

`selftest` 默认运行两次：一条使用脚本参与者，一条使用真实的 LLM 翻译器和会话层、由离线脚本模拟 API 回复。两次都检查场景声明的最终条件，并通过日志回放检查状态及投影记录。**此命令不联网、不使用 API key。**

默认场景[“雨夜渡口：最后一箱药”](scenarios/floodgate_dispatch.yaml)包含两间房、1名玩家与5名立场不同的NPC。查验封存账册、取出备件并复锁 → 修复绞盘、释放药柜 → 交接证据与药物、归还钥匙 → 发出救援信号。交谈、展示、交付和实际感知驱动加权Router。

旧[“封存圣杯”](scenarios/sealed_chalice.yaml)保留为洗牌Router与机制回归样本，其中有一次故意失败用于验证已成功前缀保留。可使用 `--scenario scenarios/sealed_chalice.yaml` 离线运行。

## 运行与检查

```bash
# 按 scenario.cast 运行；示例默认全部使用脚本。
python -m token_odyssey run --rounds 24

# 终端只展示这一角色实际获得的事件信息。
python -m token_odyssey run --player-view guard

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
使用根目录 `api.txt` 的单行密钥，六个角色均由 `deepseek-v4-flash` 驱动。
服务地址和模型 ID 对照 [DeepSeek 官方文档](https://api-docs.deepseek.com/)；
通过 `extra.thinking` 显式关闭思考模式，输出预算用于动作 JSON。
密钥文件已被 Git 忽略，不要把密钥填入 YAML。

在项目根目录、激活 `airpg` 环境后运行（会实际调用 API）：

```bash
python -m token_odyssey test-connection --run-config configs/llm.deepseek.yaml --profile standard
python scripts/live_selftest.py --scenario scenarios/floodgate_dispatch.yaml --run-config configs/llm.deepseek.yaml
```

真实全流程入口使用原有运行器、翻译器和 API 适配器，按场景默认 24 个预算轮次运行，
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

10. [交互加权 Router 的算法、参数与调试](docs/router.md)
11. [内核算法总结与 Mermaid 绘图草图](docs/kernel-algorithm.md)
12. [场景构建 AI 的字段规范、跨幕约定与生成提示词](docs/scenario-generation.md)

已实现交互加权 Router 和开场角色简报；自然语言场景生成与跨幕状态/记忆协调仍由后续模块衔接。
