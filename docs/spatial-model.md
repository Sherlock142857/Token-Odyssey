# 空间模型与 Fluent

## Placement forest

每个非 Room 实体恰好拥有一个父节点，边是 `inside` 或 `attached`。父节点必须是实体，父链必须无环并终止于 Room。

| 关系 | 含义 |
|---|---|
| Item attached Character | 手持或直接携带 |
| Item inside Character | 藏在身上 |
| Item inside Item | 在容器内部 |
| Item attached Item | 放在表面或附着；本身不表示安装 |
| Character inside Room | 角色在房间中 |

inside 的 Item 父节点必须有 Container 能力。容纳尺寸是等级比较，不是累加体积；Character 使用 concealment_size。随父节点移动的整个子树不会逐项重写 placement。

安装另存 `connections[item_id] = slot_id`。有效连接要求物品与 Slot 兼容，并且物品 attached 到该 Slot；每个 Slot 最多有一个安装连接。普通 attached 不自动建立或占用安装连接。

## Passage

Passage 是两个 Room 的边界对象，不在 placement 表中。两个端点都可以接触同一扇门，不需要双父节点。

- `forward_travel / reverse_travel` 分别控制移动方向。
- `forward_visibility / reverse_visibility` 分别控制视觉传播方向。
- `sound` 控制声音传播。
- Openable 再根据当前开闭状态提供传播系数。

因此可以表达单向通路、关闭的玻璃门和只能透视但不能通行的窗户。移动一次只跨越一条相邻通道，不自动寻路穿过多个房间。

## 能力与动态状态

Item 可以组合 Container、Openable、Lockable、Slot、operable。Passage 可以组合 Openable、Lockable。

- Container：容纳等级和没有开关机构时的透视系数。
- Openable：打开/关闭时的视觉系数、关闭时的声音系数。
- Lockable：匹配钥匙的静态 ID 集合。
- `WorldState.openings` 和 `locks`：这些对象当前的开闭与锁态。

没有 Openable 的普通托盘视为敞开。初始 Openable 默认关闭，Lockable 默认未锁；scenario 可以覆盖。锁着且打开的组合不合法。

## 查询分别回答什么

`same_room(a, b)` 只比较根 Room。

`controller(item)` 找最近的 Character 祖先，表示物品位于谁的控制链。它不等价于可接触：自己背包里的物品也可能被关闭的背包阻挡。

`accessible(actor, object)` 检查同房、控制关系及相关路径上的闭合边界。其他角色控制的物品不能直接 take；交付需要持有者提交 give。

`can_traverse(actor, passage, destination)` 检查端点、方向、接触通道的路径和门的开闭。

`transmission(observer, target, channel)` 只回答感知传播系数，不进行随机抽样，也不授予交互权限。

## 传播与共同容器

同房时，寻找观察者与目标的最低共同祖先，累乘双方到该共同空间之间的 inside 边界。共同容器本身的外壁不重复遮挡内部的两个对象。

跨房时，乘以双方到根 Room 的边界系数和 Room 之间的传播系数。房间传播使用各通道组成的最大乘积路径；视觉再乘目标房间 light 和目标 Item 的 visibility。

Room 的 light 是环境光，不是额外的容器墙。Passage 的近侧表面作为本房间出口处理，看到门面不要求能透过门。

## 位置记忆

内部定位签名包含实体到根的全部 `(node, relation, parent)`，不是只有直接 Placement。将箱子从桌上移到柜子里时，箱中物品的签名也会变化。

签名只供引擎比较，不能直接发给角色。公开的 placement 仅在该父节点身份已经获准时给出；知道某件物品存在，不代表知道所有隐藏祖先或当前所在房间。
