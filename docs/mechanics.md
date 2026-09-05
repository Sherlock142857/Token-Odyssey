# 世界机关与即时反应

## 规则格式

```yaml
world:
  flag_names: [pedestal_active]
  mechanics:
    - id: chalice_seated
      trigger: placement_changed
      subject_id: chalice
      when:
        - {kind: inside, subject_id: chalice, object_id: recess}
      effects:
        - {kind: flag, subject_id: pedestal_active, value: true}
      source_id: recess
      once: true
      visual_description: 石座中的金属片缓缓下沉。
      sound_description: 隔墙传来一声低沉的金属撞击。
```

这是场景数据，具体实体 ID 可以出现在这里；通用动作和 Harness 中不应出现剧情 ID。

| 字段 | 含义 |
|---|---|
| id | 规则唯一 ID |
| trigger | placement_changed、state_changed 或 operated |
| subject_id | 可选；仅响应涉及该对象或 flag 的信号 |
| when | 所有条件都成立才触发；空列表表示无附加条件 |
| effects | 结构化状态赋值 |
| source_id | 反应的感官来源实体或 Passage |
| once | 默认 true；本幕只触发一次，事实保存在 fired_rules |
| visual_description / sound_description | 可观察外观变化或听到的声音，可分别省略 |
| visibility / audibility | 各通道显著度，默认 1 |

公开描述只能写感官信息。若把隐藏密码、兼容关系或机关推理直接写进声音描述，观察者听到时就会得到这些作者主动公开的信息。

## 条件与效果词汇

条件 Predicate 支持：

- inside / attached：subject_id 与 object_id 的直接位置关系。
- installed：真实安装连接，不能由普通 attached 代替。
- open / locked：对象当前开闭或锁态。
- flag：scenario 声明的布尔状态。

`value: false` 表示该原子条件不成立。关系型条件必须有 object_id；其他条件不得有 object_id。首版 when 是合取，没有表达式字符串、任意 eval 或隐式名称推理。

Effect 支持 open、locked、flag 的布尔赋值。效果引用必须具备对应能力；同一规则不能给同一事实写两次。复杂设备可以用少量具名 flag 描述状态，但不应复制可从 placement、connection 推导出的事实。

## 队列如何执行

每个已暂存事件携带 signals 和 subject_ids。规则按 YAML 声明顺序检查；条件读取最新草稿，因此后一规则能看到前一规则已经产生的效果。

一条反应会生成独立的 WORLD event。实际改变的对象/flag 成为后续 state_changed 信号的 subject。新事件进入队列，继续触发匹配规则，直到队列耗尽。

once=false 表示每次匹配信号到来时都可触发，不代表每回合持续轮询。例如组件取下再安装、圣杯移出再放入、再次按下按钮可以产生新信号。效果已经相同且没有新增状态变化时，不会凭空产生 state_changed 连锁；声音描述仍可作为一次响应发出。

`max_reactions_per_action` 默认 32。连锁超过上限、效果使门同时上锁且打开、或破坏其他不变量时，整个当前动作事务失败，不提交任何其中的事件。修复 scenario 后重新运行；不能让 LLM 反复猜动作来掩盖规则错误。

## 自动机关与 operate

凹槽根据 placement_changed 自动响应，不要求多余的 operate。按钮或控制台监听 operated，并可检查 installed、flag 等条件。

install 只建立空间关系和安装连接，不自行包含某个设备的成功算法。真正的设备行为写在规则中；同一套 install 可以用于多个 scenario。

## 观察与因果

一条 place 可以产生三条日志事件：

```text
E1: place 圣杯
E2: 石座启动     caused_by=E1
E3: 门闩释放     caused_by=E2
```

以上共享 transaction_id，但观察者未必看到全部。声音 Cue 不带 source_id 给角色，看到石座也不会自动知道背后的规则条件或 flag 名称。

每个事件有自己的前后快照用于感知判定，而不是拿整个队列最终状态解释所有中间动作。直接动作若失败，后续机关和观测都不会发生。

## 扩展边界

新增机关组合通常只修改 YAML。需要新的条件时，在 definitions.Predicate 的词汇、定义校验和 Fluents.satisfies 中补充语义。需要新的持久状态效果时，同时扩展 WorldState、Change 应用、不变量和 Effect；不要把某种新效果藏在 renderer 或 Recorder 内。

首版支持即时同步反应，不包含定时器、持续物理模拟或异步外部事件。以后可以增加受 Harness 控制的世界事件入口，复用事务与投影流程。
