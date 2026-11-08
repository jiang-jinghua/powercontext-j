"""上下文工程（Context Engineering）模块。

职责分工：
- store     工程资料持久化与全文检索（seekdb/OceanBase 优先，本地 FTS 兜底）
- ingest    文档/代码/测试分块与元数据抽取
- retrieve  面向任务的多路召回
- package   生成"规格—代码—测试"三段式上下文包
- minimax   MiniMax 模型接入（唯一的模型调用出口，含调用留痕）
- patch     补丁校验与应用（git）
- state     任务状态保存/恢复（PowerContext 优先，JSON 兜底）
- runner    检索 → 生成 → 应用 → 回归 → 留痕 的任务闭环
"""
