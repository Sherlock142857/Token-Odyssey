# Token Odyssey

Token Odyssey 是一个将语言模型角色与可验证世界分离的幕内 RPG 运行时。Character 只提交意图，确定性的 World Harness 负责合法性、状态变化和 canonical World Event；每个参与者只获得事件与环境针对自己的投影。

## 核心边界

- **World Harness**：世界状态的唯一写入者；非法计划不会部分发生。
- **Context as Projection**：角色上下文来自 World Event、环境扫描和私有反馈，不共享 canonical state。
- **Player as Human Agent**：LLM、脚本、replay 和未来人类界面使用同一个 `Participant` port。
- **Append-only Session**：LLM 历史不压缩、不摘要、不重写，后续请求保持精确缓存前缀。

空间由以 Room 为根的 Placement forest 和 Room 有向可见度图组成。NPC 与物品都使用 `inside` 或 `attached` 指向唯一父节点。Action 通过冻结的 Registry 扩展，每个动作模块拥有自己的 schema、校验、effect、可见度、renderer 和 prompt metadata。

Scenario 还可用隐藏的 `world.mechanics` 声明组件安装配对与设备操作响应。角色通过 `install` 和 `operate` 提交意图，设备反应以独立的 `source=world` 事件进入 canonical World Log。

## 安装与运行

```bash
python -m pip install -e '.[dev]'
token-odyssey validate scenarios/rainy_night.yaml
token-odyssey run --scenario scenarios/rainy_night.yaml --rounds 10
pytest
```

不提供 `--run-config` 时使用离线 DemoAgent。LLM 配置与 Scenario 分离，示例见 [`configs/llm.example.yaml`](configs/llm.example.yaml)：

```bash
token-odyssey run \
  --scenario scenarios/rainy_night.yaml \
  --run-config configs/llm.example.yaml
```

仓库中的第二个完整 Act 可用真实 LLM 做连接检查和端到端运行：

```bash
token-odyssey test-connection \
  --run-config configs/llm.after_storm_relay.example.yaml \
  --mode standard

token-odyssey run \
  --scenario scenarios/after_storm_relay.yaml \
  --run-config configs/llm.after_storm_relay.example.yaml \
  --rounds 24
```

示例配置默认从被 git 忽略的项目根目录 `api.txt` 读取一行 API key；也可以把 backend 改为 `api_key_env`，通过环境变量注入密钥。建议首次接入时先用 `--rounds 1` 做低成本冒烟测试。

运行会写入 schema-v2 artifact；可用以下命令进行不调用 LLM 的确定性重放：

```bash
token-odyssey replay runs/<run-id>
```

## 文档

- [架构与依赖边界](docs/architecture.md)
- [空间模型与可见度数学](docs/spatial-model.md)
- [新增 Action 规范](docs/actions.md)
- [Observation 与角色记忆](docs/observation.md)
- [Agent 与 LLM 分层](docs/agent-llm.md)
- [Act 输入、输出和记录格式](docs/inside-act-io.md)
