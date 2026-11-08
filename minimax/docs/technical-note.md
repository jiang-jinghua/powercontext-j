# 技术说明：把"上下文"做成可复核的工程产物

## 1. 问题拆解

赛题要求的不是"让模型改一行代码"，而是三段能力：

1. **找得准**：从规格、代码、测试里精准取到与本任务相关的那几段，并且能说清"为什么是这几段"；
2. **改得对**：模型产出的改动必须真的能落地、真的能过测试，且不破坏既有行为；
3. **记得住**：任务状态（做到哪、证据是什么、还剩什么）能在换会话/换进程后继续用。

对应实现：`ctx/retrieve.py` + `ctx/package.py`（找得准）、`ctx/minimax.py` + `ctx/patch.py` + `ctx/runner.py`（改得对）、
`ctx/state.py` + PowerContext（记得住）。

## 2. 资料入库：分块粒度决定检索质量

| 类型 | 分块策略 | 元数据 |
| --- | --- | --- |
| Markdown（规格/文档） | 按 `#/##/###` 切到小节；超 2200 字符再按空行切 | 标题路径、`spec_codes`、文档版本 |
| Python（源码/测试） | AST 切到函数/类 + 模块头块 | `symbol`（`模块.函数`）、`spec_codes`（docstring 里的编码） |
| JSON（订单样例/任务卡） | 整文件成块 | 路径即身份 |

第一版把 SPEC 条款合在「4. 规格条目」一个大块里，检索时根 section 抢占前排；改为三级标题切开后，
`SPEC-011-02 尾差` 这类查询的首位命中就是对应条款。这是本项目唯一一次"推倒重来"的检索改造，
留在了 `ctx/ingest.py::chunk_markdown` 的注释与提交历史里。

**覆盖式更新**：`replace_doc()` 先按 `doc_id` 删除旧片段再插入。规格 v1 → v2 后，v1 条款不会与新条款共存，
这是"模型必须使用最新内容"的工程保证（而不是靠提示词提醒模型"别看旧的"）。

## 3. 召回与重排：先广撒网，再给可解释的理由

```
任务卡(spec_codes / target_files / request)
   ├─ 规格路：title+request 语义召回 + 每个 SPEC 编码精确召回 → 取条款块
   ├─ 代码路：target_files 限定 + 语义召回 → 取函数块
   └─ 测试路：SPEC 编码 + request 召回 → 取带同名编码的用例
```

全文召回（seekdb `MATCH ... AGAINST` / 本地 FTS5 bm25）只负责"进候选池"，最终排序用可解释打分：

```
score = 3.0×正文覆盖度 + 1.0×标题覆盖度 + 0.1×词频 + 6.0×(规格编码精确命中) + 结构信号
```

选覆盖度做主信号的原因：中文全文检索（ngram/jieba）对短查询的长尾召回噪声大，而"这个片段讲的是不是这件事"用
覆盖度判断最稳；规格编码是强意图（用户/任务卡明确点名），给最大权重。每次召回的分数明细写入
`artifacts/runs/<run_id>/retrieval.json`，评审可以质疑权重，但不需要猜。

## 4. 生成与应用：把"模型说改了"变成"仓库真的改了"

- 提示词**先给事实，再给约束**：目标文件全文（`## 0.` 段）→ 规格条目 → 相关代码 → 测试 → 状态。
  只给片段时模型会自己"脑补"文件内容，产出的 hunk 必然对不上；给出全文后，hunk 才落在真实行上。
- 支持两种交付形态：
  - `--mode diff`：unified diff，`extract_diff()` 抽 ```diff 块；
  - `--mode files`：整文件重写（```file path=…），写入前先过 `ast.parse` 语法闸门，失败即回滚重来。
- 真实模型（MiniMax-M3）实测的三类"机械不合规"，都在应用前机械修复，且修了什么写进 `attempts[].repairs`：
  1. hunk 头行数写错（`@@ -33,5 +34,8 @@` 实际 9 行）→ 按正文重算；
  2. 代码围栏/Aider 标记残留（```` ``` ````、`*** Update File:`、`*** End Patch`）→ 清理；
  3. 空上下文行没有前导空格 → 补成 `" "`（否则 git 直接判 corrupt）。
- 应用顺序：`git apply --check` → 修复后重试 → GNU `patch -p1 --fuzz=3` 兜底（有 `.rej` 即视为失败）。
- 每轮跑 `pytest`，失败把日志尾部喂回模型重写；整文件模式在失败后**回滚到快照**再重试，避免多轮改动叠加。
- 推理模型的两个工程细节：M3 默认 `adaptive thinking` 会把 4 万 token 花在推理上并截断 → 请求里显式
  `thinking={"type":"disabled"}` + `reasoning_split=True`；`finish_reason=length` 时抛错并按轮次放大 `max_tokens`。

这套设计让"模型输出"与"提交的代码差异"建立了一一映射：`response_<n>.md` → `patch_attempt<n>.diff` → `git show`。
011 的 3 轮记录就是这条链路真实跑出来的：前 2 轮分别因回归用例失败（零权重分摊不再报错、全类目排除金额不对）
被打回，第 3 轮全绿；012 一轮通过。模型输出有随机性，同一任务换一次采样也可能需要更多轮，这点在 `attempts` 里如实记录。

## 5. 状态与交接：为什么不直接把整个 JSON 塞进记忆

PowerContext 单条记忆上限 8192 字节，而一次运行的状态（含约 30 条证据 chunk_id、测试日志尾部）远超这个量级。
所以采用「**摘要进记忆 + 完整快照落盘 + 指针关联**」：

```
PowerContext: [REQ-2026-011] status=done spec_version=... patch_sha=5c2a5e7e87b8 tests passed=28
              done=SPEC-011-01..05 ... snapshot_path=artifacts/state/REQ-2026-011.json
artifacts/state/REQ-2026-011.json: 全量字段（evidence[]、tests、attempts、powercontext.artifact.artifact_id）
```

好处：跨会话检索快（摘要可被 `atomic-memory/search` 命中），精确恢复靠快照（不丢字段），
且"记忆写没写进去"有 artifact_id 可查，不靠自述。

`parent_task` 让任务串成链：012 运行时读回 011 的交接摘要塞进上下文包，模型因此知道"前 five 条规格已实现、别再动"。

## 6. 取舍与遗留

- **没上向量检索**：语料规模（126 片段）下词法 + 覆盖度重排已足够，引入 embedding 会带来模型依赖与不可复现风险；
  接口上 `store.search()` 与 `package` 已隔离，后续接向量只需换 store 实现。
- **本地 FTS5 兜底是"能跑"不是"等价"**：它保证评审在任何机器上都能复现链路，但 `stats.backend` 会如实标注，
  最终成绩口径仍以 seekdb 实例为准。
- **一次退货动作对应一次退款**：多次部分退货的累积对账（`BACKLOG-311`）未实现，规格里已显式列为非目标。
- **折扣券 + 类目排除缺少专项用例**（`BACKLOG-312`）：实现上共用同一分摊路径，但验收覆盖不完整，属于已知缺口。
