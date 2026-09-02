# ADR 0003：原子 Turn frames 与追加式 Context

状态：已接受。

TurnPlan 最多包含五个 Action，不区分 say 与非 say 配额；同 frame 同时、跨 frame 顺序规划，后一 frame 可以使用前一 frame 确定产生的知识。Harness 对单次 resolve 原子提交；Runner 在 move 后的环境交互失效时可改为提交截至最后有效 move frame 的前缀。LLM 对话历史只追加，不压缩、摘要、删除或改写失败分支；面向角色的增量 prompt 不暴露 Router 轮次。
