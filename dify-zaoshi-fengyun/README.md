# 《早市风云 · 老王菜摊》— 2026 OceanBase 大赛 · Dify 题

**Just for Fun：6 轮之内，把老王的黄瓜从 12 元/斤砍到 8 元以下。**

一个 Dify Chatflow 小游戏。老王有脾气、有底价、记得你用过什么招；想赢就得去知识库里套情报——进货价、邻摊报价、收摊暗号，全存在 OceanBase seekdb 里。**seekdb 同时充当 Dify 的元数据库、向量库与全文索引**，一个容器替掉 PostgreSQL + 向量库 + Elasticsearch 三件套。

## 提交材料索引（对应题目第四章）

| 题目条目 | 内容 | 位置 |
|---|---|---|
| 4.1 Dify DSL | 应用 DSL（Dify 1.x / version 0.1.5，6 节点，零插件依赖）+ 生成脚本 | `dsl/zaoshi-fengyun.yml`（由 `build/build_dsl.py` 生成） |
| 4.2 运行说明 | 全貌 / 环境 / seekdb 一体化部署 / 知识库 / DSL 导入 / 常见问题 | `docs/02-运行说明.md`（环境变量样例：`deploy/seekdb.env.example`） |
| 4.3 验证样例与运行证据 | 4 组实机用例 + 已生成的确定性层证据 | `docs/03-验证样例与运行证据.md`、`evidence/` |
| 4.4 按依赖补充的材料 | seekdb 部署、知识库语料、跨轮状态实现、PowerContext 可选配方 | `docs/04-材料清单.md` + `seed/knowledge/` |

其他文档：`docs/01-创意与玩法设计.md`（玩法与数值）、`docs/05-验收自检与自评.md`（对照 3.1/3.2）。

## 30 秒了解玩法

```
你:  李姐那边才 9 块 5，你这 12 块是金子做的？
老王: 「李姐是李姐，我是我！她那瓜你掐掐，能响吗？」
      📢 价签：12.0 → 9.8 元/斤（让了 2.2）
      💰 9.8 元/斤 ｜ 目标 ≤ 8.0 ｜ 第 2/6 轮
      😤 老王情绪 █████░░░░░ 44/100
      🗣 招式：对比邻摊×1   🕵 情报：无
```

- 8 种招式各有让价系数，重复用会被拆穿（效果 -40%/次）；
- 套出进货价（在知识库《今日特价与暗号》里）每轮多让 0.15 元；
- 惹毛老王（情绪 < 20）他会掀摊；哄开心了会主动抹零；
- 结局：成交 / 友情价 / 掀摊 / 超时，各配成就。

## 快速开始（30 分钟内跑通）

```bash
# 1) 部署 Dify × seekdb 一体化（详见 docs/02 §3）
git clone https://github.com/langgenius/dify.git && cd dify/docker && cp .env.example .env
#   把 deploy/seekdb.env.example 中的参数写入 .env，然后：
docker compose up -d

# 2) Dify 控制台：配模型 → 建知识库（上传 seed/knowledge/ 全部 6 篇，混合检索）→ 导入 dsl/zaoshi-fengyun.yml
# 3) 导入后改 2 个占位：LLM 节点选模型、知识检索节点选知识库（docs/02 §6）→ 发布运行
```

本地确定性验证（无需 Dify）：

```bash
python tests/test_settle.py    # 结算内核 22 项断言（全部通过，见 evidence/test-settle-output.txt）
python tests/simulate.py       # 三个完整剧本逐轮回放 → evidence/transcript-settle-sim.md
```

## 目录结构

```
├── dsl/zaoshi-fengyun.yml        # 可导入的 Dify DSL（主交付物 4.1）
├── build/
│   ├── settle_code.py            # 结算内核（DSL 代码节点的唯一真实来源）
│   ├── build_dsl.py              # DSL 装配器：注入代码 + 生成 + round-trip 校验
│   └── yaml_emit.py              # 极简 YAML 发射器/校验器
├── tests/
│   ├── test_settle.py            # 结算内核单元测试（22 项断言）
│   └── simulate.py               # 剧本回放，生成 evidence/transcript-settle-sim.md
├── seed/knowledge/*.md           # 早市情报库语料 × 6（主交付物 4.4）
├── deploy/seekdb.env.example     # seekdb 一体化环境变量样例
├── docs/01~05-*.md               # 玩法 / 运行 / 验证 / 材料 / 自检
└── evidence/                     # 已生成的运行证据
```

## 交付状态与如实说明

| 项 | 状态 |
|---|---|
| DSL 语法 | 按 Dify 1.x 导出格式（version 0.1.5）编写；节点结构对齐官方示例 DSL；生成时经结构 round-trip 校验 |
| 结算内核（代码节点） | ✅ 真实执行验证：22 项断言全过，3 剧本逐轮回放留档 |
| 端到端（LLM + seekdb 检索） | ⏳ 需在目标 Dify 实例执行 docs/03 的用例 A~D 并按其 §4 采集截图 |
| 外部依赖 | 仅 seekdb（随 Dify compose 拉起）+ 一个 Chat 模型凭证；零第三方插件 |
