# ADR 0002：冻结的显式 Action Registry

状态：已接受。

ActionSpec 同时拥有 schema、校验、effect、观察配置、renderer 和 prompt metadata。builtin 使用一处显式注册，不进行目录自动发现。Act 启动后 Registry 不再变化。
