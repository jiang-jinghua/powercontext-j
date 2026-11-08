# SeekSearch —— 浏览器本地语义搜索（阶跃星辰赛题）

AI 模型量化优化与 seekdb 混合检索：一个纯浏览器本地推理 + seekdb 全文/向量混合检索的语义搜索 Web 应用。

## 1. 模型与推理

- **推理栈**：Transformers.js v3（`@huggingface/transformers`）+ ONNX Runtime Web，全部 embedding 计算在浏览器内完成。
- **后端**：优先 WebGPU（`navigator.gpu` + `requestAdapter` 探测），不可用时自动降级 WASM，界面顶部实时展示当前后端；也可手动强制指定用于对比。
- **模型**（界面可切换，collection 按维度隔离）：
  - `Xenova/all-MiniLM-L6-v2`（384 维，英文/多语言）
  - `Xenova/bge-small-zh-v1.5`（512 维，中文优化）
- **量化**：同一模型可加载 `fp32`（基准）/ `fp16` / `q8`（INT8）三种 ONNX 权重，直接加载 HF 提供的量化权重文件，无需转换步骤。

## 2. seekdb 使用方式

- 浏览器无法直接走 MySQL 协议，故提供一个**本地薄代理** `server/seekdb-proxy.mjs`（seekdb.js SDK，仅转发浏览器已算好的向量和查询，**零推理、零云端调用**）。
- 存储：向量 + 原文 + 元数据（docId/docName/chunkIndex）写入 seekdb collection（`semantic_chunks_<dim>d`）。
- 检索：**完全由 seekdb 完成** —— `collection.hybridSearch({knn:{queryEmbeddings}, rank:{rrf:{}}})`，全文 + 向量在 seekdb 内部做 RRF 融合；前端不做任何自评分或结果拼接。
- 幂等导入：写入前按 `docId` 删除旧分块；支持按文档删除与重建。

## 3. 检索流程

```
文档(txt/md/pdf) → 段落感知滑窗分块(默认400字符/重叠60)
  → 浏览器本地向量化(Transformers.js, INT8/FP16, WebGPU/WASM)
  → /api/rpc → seekdb collection.add({documents, embeddings, metadatas})

查询 → 浏览器本地向量化 → seekdb.hybridSearch(全文 + 向量, RRF)
  → 返回片段 + 来源文档 + 排名 → 前端高亮渲染
```

## 4. 优化手段

- 批量 embedding（可配置 batch size）+ 内容 hash 缓存（相同文本零重复计算）
- WebGPU 硬件加速，WASM 兜底保证兼容性
- INT8 量化：模型体积与加载耗时大幅下降，精度损失见性能指标
- 性能面板实时记录：模型加载耗时、embedding 吞吐、检索延迟，可一键导出 `performance-results.json`

## 5. 运行与测试方法

```bash
cd stepfun
npm install --legacy-peer-deps   # seekdb@1.4.0 的 peer 依赖含未发布的 workspace:* 协议，需跳过 peer 自动安装
npm run dev        # 拉起 seekdb.js 代理(8787) + Vite(5173)
```

1. 连接 seekdb（默认 `127.0.0.1:2881`，数据库 `test`）
2. 加载模型（默认 all-MiniLM-L6-v2 / INT8 / 自动后端）
3. 导入文档（txt/md/pdf，中英文均可）或粘贴文本
4. 输入查询测试混合检索；关键词框可选全文过滤
5. 性能对比测试方法：
   - **量化对比**：分别以 fp32/fp16/q8 加载模型导入同一文档集，记录加载耗时与吞吐；用固定查询集对比检索结果与 FP32 的差异（Recall@5、余弦相似度漂移）
   - **后端对比**：分别强制 WebGPU / WASM 导入同一文档集，对比吞吐
   - **规模测试**：导入 100/400/800 分块规模，记录查询向量化与 seekdb 检索延迟
   - 全部指标由性能面板自动记录，「导出 performance-results.json」按钮生成实测文件

## 6. 目录结构

```
stepfun/
├── index.html              # 浏览器应用入口
├── src/
│   ├── embedder.js         # Transformers.js 本地推理（WebGPU→WASM 降级、量化切换、批处理+缓存）
│   ├── chunker.js          # txt/md/pdf 解析与分块
│   ├── seekdb-client.js    # 浏览器侧 seekdb 代理客户端
│   ├── main.js             # UI 与流程编排
│   └── style.css
├── server/
│   └── seekdb-proxy.mjs    # seekdb.js SDK 本地薄代理（无模型、无云端 API）
├── scripts/dev.mjs         # 一键启动代理 + Vite
├── submission-results.json # 提交结果清单
├── compliance-results.json # 合规自检
├── performance-results.json# 性能指标（应用内实测导出）
└── README.md
```
