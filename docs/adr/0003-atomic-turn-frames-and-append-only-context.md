# ADR 0003：原子 Turn frames 与追加式 Context

状态：已接受。

TurnPlan 最多包含四个 Action，不区分 say 与非 say 配额；同 frame 同时、跨 frame 顺序规划，后一 frame 可以使用前一 frame 确定产生的知识。整份计划原子提交。LLM 对话历史只追加，不压缩、摘要、删除或改写失败分支；面向角色的增量 prompt 不暴露 Router 轮次。
