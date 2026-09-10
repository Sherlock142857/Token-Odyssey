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
  scene_builder_profile: builder
  world_summary_profile: flash
  character_memory_profile: flash
  npc_profile: flash
  max_generation_retries: 3
```

这些 profile 使用原有 backend 配置，因此可以分别连接不同服务或代理。没有 `campaign` 段时，
原有 `web/run/selftest` 仍可正常使用；`play` 会明确拒绝启动。

## 流程

1. 用户的创作要求首先进入唯一的 append-only 导演会话。导演创建带3～7个宽粒度剧情节点的 Campaign Bible 和第一幕简报；节点是可随玩家后果改道的因果骨架，不是固定分幕表。
2. 每幕新建独立场景 Agent，注入 `scenario-generation.md`、`router.md`、实时 Scenario schema、动作 schema、世界观与重大历史、导演简报、公开物理角色资料与物理连续性账本，输出 Scenario v3 JSON。人物性格、记忆和内心状态不发送给它，也不允许它输出 roles/cast/scripts；这些资料由外层在物理场景通过校验后注入。角色 description 由 Campaign Bible 的稳定身份/外貌统一覆盖，当前动作和姿态只属于本幕公开背景。
3. 玩家阅读开场并进入 Act。玩家固定控制导演创建的主角，其余角色使用 NPC profile。
4. `end_when`、玩家主动收幕或预算耗尽会冻结本幕。API/世界执行故障只进入技术暂停。
5. 全局总结 Agent 只读取已提交事件的紧凑时间线、初终状态差异和结束原因；感知 cues、重复事务包装和未形成事件的失败意图不会进入摘要。随后导演安排后果、幕间和下一幕。
6. 每个相关角色单独调用记忆 Agent，只传入该角色去重后的已观察事件、到访地点、首次辨认细节、已有获准记忆和角色专属幕间观察。输出是完整替换后的长期记忆集合，不是只追加本幕内容。
7. 导演只有在主线已经闭合时才能准备终幕。终幕结束后再由导演生成最终旁白，整局才完成。

约 120 分钟和约 5 幕属于提示词节奏目标，不是硬上限。第一幕则由数据校验保证玩家在场且总角色数不超过三人。

## 结束、故障与恢复

“结束本幕”会等待当前行动权安全完成。未完成任务和重大失败属于剧情结果，照常进入导演判断；
模型传输、格式重试耗尽或世界执行异常属于技术故障，可在页面重试，不会被写成角色失败。

Campaign 在场景已就绪、Act 已结束、下一幕已就绪时原子写 checkpoint。服务在幕中退出时，
未完成的 Act 目录会保留作调试，但恢复会从该幕开始前重跑，不恢复 Router RNG、感知 inbox 或半个行动权。
每次编排调用都有稳定 operation ID 和独立响应缓存，恢复不会重复计费或重复追加已经落盘的导演回复。
Scene Builder 应使用低温度、足够输出预算的独立 profile；示例配置为 temperature=0.2、max_output_tokens=8192。供应商报告长度截断或实际用满预算时，重试不会携带残缺 assistant 输出，而是从本幕原始输入重新生成更紧凑的完整 JSON。
普通不可开闭对象上多余的 `open=true`、不可上锁对象上多余的 `locked=false` 会在 Campaign 编译前按等价默认语义清理；相反状态仍视为建模错误。其他 JSON 解析或场景校验失败也会从原始输入重新重试，并附带包含具体对象 ID 的错误，不再携带整份无效回复。

存档包含 `checkpoint.json`、`manifest.json`、编排模型交换、全局摘要、逐角色记忆、幕间观察、
逐幕 Scenario，以及每一幕原有的完整运行目录。

## 页面与信息边界

普通 `/api/state` 只返回公开世界信息、玩家自己的角色资料、幕间文字和当前角色安全视图。
Campaign Bible、原始场景、导演消息和全知 World Log 只由独立 `/api/debug` 返回；页面默认不请求该接口，
只有展开“开发者信息”时才按游标增量轮询。调用时间线覆盖导演、场景构建、世界摘要、角色记忆和幕内 NPC，并将发送、pending、回复或错误明确分开。与单 Act 测试台相同，这一边界用于防止界面误混数据，不是多用户权限系统。

游玩时左栏常驻本幕开场、公开背景与玩家目标，并可随时展开世界观和重大历史。动作区提供直接提交单个 `wait` 的“快速等待”；说话和展示的多人对象使用复选框，不选择说话对象表示公开发言。

网页仍只绑定 `127.0.0.1`，检查 Host/Origin，并对所有写请求验证页面 token。
