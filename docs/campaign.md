# 多幕 Campaign 游玩

Campaign 是 `ActRunner` 之外的编排层。它负责导演、场景构建、幕间叙事、记忆和存档；
幕内的动作、机关、感知和 Router 仍完全由单 Act 组件执行。

## 启动与配置

```bash
python -m token_odyssey play --run-config configs/llm.deepseek.yaml
python -m token_odyssey play --run-config configs/llm.deepseek.yaml \
  --resume runs/campaigns/<campaign-id>
```

RunConfig 的可选 `campaign` 段只保存 profile 名称，不保存凭据：

```yaml
campaign:
  director_profile: pro
  scene_builder_profile: pro
  world_summary_profile: flash
  character_memory_profile: flash
  npc_profile: flash
  max_generation_retries: 3
```

这些 profile 使用原有 backend 配置，因此可以分别连接不同服务或代理。没有 `campaign` 段时，
原有 `web/run/selftest` 仍可正常使用；`play` 会明确拒绝启动。

## 流程

1. 用户的创作要求首先进入唯一的 append-only 导演会话。导演创建 Campaign Bible 和第一幕简报。
2. 新建场景 Agent，注入 `scenario-generation.md`、`router.md`、实时 Scenario schema 和动作 schema，
   输出 Scenario v3 JSON。编译错误会回送同一个 Agent 修正，不增加评审 Agent。
3. 玩家阅读开场并进入 Act。玩家固定控制导演创建的主角，其余角色使用 NPC profile。
4. `end_when`、玩家主动收幕或预算耗尽会冻结本幕。API/世界执行故障只进入技术暂停。
5. 全局总结 Agent 只读取 World Log、初终状态和结束原因；随后导演安排后果、幕间和下一幕。
6. 每个相关角色单独调用记忆 Agent，只传入该角色的 Observation、已有获准记忆和角色专属幕间观察。
7. 导演只有在主线已经闭合时才能准备终幕。终幕结束后再由导演生成最终旁白，整局才完成。

约 120 分钟和约 5 幕属于提示词节奏目标，不是硬上限。第一幕则由数据校验保证玩家在场且总角色数不超过三人。

## 结束、故障与恢复

“结束本幕”会等待当前行动权安全完成。未完成任务和重大失败属于剧情结果，照常进入导演判断；
模型传输、格式重试耗尽或世界执行异常属于技术故障，可在页面重试，不会被写成角色失败。

Campaign 在场景已就绪、Act 已结束、下一幕已就绪时原子写 checkpoint。服务在幕中退出时，
未完成的 Act 目录会保留作调试，但恢复会从该幕开始前重跑，不恢复 Router RNG、感知 inbox 或半个行动权。
每次编排调用都有稳定 operation ID 和独立响应缓存，恢复不会重复计费或重复追加已经落盘的导演回复。

存档包含 `checkpoint.json`、`manifest.json`、编排模型交换、全局摘要、逐角色记忆、幕间观察、
逐幕 Scenario，以及每一幕原有的完整运行目录。

## 页面与信息边界

普通 `/api/state` 只返回公开世界信息、玩家自己的角色资料、幕间文字和当前角色安全视图。
Campaign Bible、原始场景、导演消息和全知 World Log 只由独立 `/api/debug` 返回；页面默认不请求该接口，
只有展开“开发者信息”时才加载。与单 Act 测试台相同，这一边界用于防止界面误混数据，不是多用户权限系统。

网页仍只绑定 `127.0.0.1`，检查 Host/Origin，并对所有写请求验证页面 token。
