# ADR 0003：顺序 Actions、内部原子 Frames 与追加式 Context

状态：已接受。

TurnPlan 最多包含五个顺序 Action，不区分 say 与非 say 配额；每个 action 在内部独占一个 frame，后一 action 可以使用前一 action 确定产生的知识。Harness 对单次 resolve 原子提交；Runner 在 move 后的环境交互失效时可改为提交截至最后有效 move action 的前缀。LLM 对话历史只追加，不压缩、摘要、删除或改写失败分支；面向角色的 JSON context 不暴露 frame、Router 轮次或内部日志术语。
