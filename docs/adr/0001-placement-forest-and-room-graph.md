# ADR 0001：Placement forest 与 Room Graph 分离

状态：已接受。

Character 和 Item 通过唯一 `inside/attached` 父边形成以 Room 为根的森林；Room 之间的传播使用独立有向最大乘积图。不增加 Space 根节点。每条 inside 边都独立贡献父容器可见度。
