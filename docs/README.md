# Token Odyssey 文档地图

技术文档遵循“先看职责与总体架构，再进入字段和算法”的阅读顺序。当前文档对应 Scenario v3、RunConfig v3、Run Log v4 和 Campaign Checkpoint v1；旧格式不在文档范围内。

## 1. 概念

1. [架构与职责](architecture.md)：权威状态、模块所有权和提交边界。
2. [核心算法](kernel-algorithm.md)：一张宏观循环图，再展开事务闭包、感知、Router、回放与不变量。

## 2. 运行

1. [运行、验证与回放](running.md)：CLI、离线 selftest、真实 API 验收与 Run Log。
2. [localhost 单 Act 测试台](web.md)：Human＋LLM 网页流程和 HTTP 信息边界。
3. [多幕 Campaign](campaign.md)：Director、Scene Builder、记忆、checkpoint 与恢复。

## 3. 核心子系统

1. [空间模型与 Fluents](spatial-model.md)
2. [动作协议与扩展](actions.md)
3. [机关与因果闭包](mechanics.md)
4. [观测与角色记忆](observation.md)
5. [Router](router.md)
6. [Agent、翻译器与 API](agent-llm.md)

## 4. 场景与高级扩展

1. [Scenario v3 编写与校验](scenario.md)
2. [场景构建 AI 规范](scenario-generation.md)

从仓库根目录运行所有文档命令。项目的发行版本和数据格式版本彼此独立；Python 模块路径与 localhost HTTP 路由目前属于内部实现，不是 v0.1 稳定接口。
