# seekdb 与 PowerContext 接入指引

> 本文档给出从零获取两个组件凭据的完整步骤。全部官方入口，费用说明见文末。

## 一、seekdb（OceanBase AI 原生混合搜索数据库）

### 推荐：D0 免费试用入口（零注册，5 分钟跑通）

seekdb D0 是 OceanBase 开源 seekdb 的免费试用入口：**不需要账号、邮箱或信用卡**，一行 curl 即可拿到一个 MySQL 兼容实例（向量、全文、JSON、GIS、数据分支默认就绪）。

1. **创建实例**（每次调用都会新建一个实例，密码只在创建时返回一次）：

   ```bash
   curl -s -X POST 'https://d0.seekdb.ai/api/v1/instances'
   ```

   返回（201）：

   ```json
   {
     "instance_id": "aB3kR7mN2pQx",
     "username": "u_aB3kR7mN2pQx",
     "password": "<一次性密码，立即保存>",
     "database": "d0_aB3kR7mN2pQx",
     "host": "gw0.apac.seekdb.ai",
     "port": "2881",
     "expires_at": "2026-11-13T00:00:00Z"
   }
   ```

2. **填写到应用**：打开温故 → 数据源 → 真实接入模式 → 填 `instance_id` 与 `password` → 保存 → 点击「初始化 seekdb 表结构」（自动建 `knowledge_points` 表 + 全文索引）。

### 注意事项

- 实例默认 **7 天后自动过期**，数据安全删除。参赛演示建议在提交前 1~2 天创建新实例并重新灌数据；正式/长期使用请用开源版（github.com/oceanbase/seekdb，Apache-2.0）或 OceanBase Cloud。
- D0 的 HTTP Query API 有严格速率限制（官方定位为"临时/低频使用"），生产流量应走 MySQL 协议（PyMySQL / mysql2 等，需开启 TLS）。
- D0 禁用库内 AI 函数（AI_EMBED 等）；本项目使用全文索引检索，不需要向量嵌入，可直接用。若要加语义向量检索，需在应用侧用 embedding 模型生成向量后写入 `VECTOR` 列。
- 禁止压测/滥用，会被封 IP。

### 官方文档

- 创建实例 / HTTP Query API 全文：`https://d0.seekdb.ai/SKILL.md`
- 混合搜索（向量+全文）：`https://d0.seekdb.ai/skills/seekdb-search.md`
- 数据分支（FORK/DIFF/MERGE）：`https://d0.seekdb.ai/skills/seekdb-branch.md`
- 开源仓库：`https://github.com/oceanbase/seekdb`

## 二、PowerContext（AI Agent 上下文服务）

PowerContext 是 OceanBase 开源的 Agent 上下文服务（开源仓库 github.com/oceanbase/powercontext），本地 Server 即可运行，把"决策、约束、证据、进度"沉淀在会话之外。

### 本地安装与启动（Python 3.11+，需 Git 与 uv）

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run          # 默认监听 http://127.0.0.1:8000
```

验证：

```bash
curl --fail http://127.0.0.1:8000/health/live
```

### 创建 Scope（应用边界）

```bash
curl --fail --header 'Content-Type: application/json' \
  --data '{"title":"温故学习助手","summary":"复习与掌握记录上下文","idempotency_key":"wengu"}' \
  http://127.0.0.1:8000/v1/scopes
# 记下返回的 scope_id，填入应用「数据源」页
```

### 本项目用到的四个端点

| 端点 | 作用 |
|------|------|
| `POST /v1/sources/content` | 复习会话写入 Source 证据（谁、何时、复习了什么、评价如何） |
| `POST /v1/memory/remember` | 「已掌握」判定写入长期 Memory（kind=decision） |
| `POST /v1/context/prepare` | 按查询召回有界历史上下文（status: ready / empty） |
| `POST /v1/scopes` | 首次初始化创建 scope |

如启用 Server 鉴权，把 Bearer Token 填在应用「数据源」页（仅存浏览器本地）。

### 注意事项

- 本地 Server 默认用 SQLite，无需配置推理模型（显式 Memory 写入不需要 LLM）。
- 语义召回需配置向量搜索（见仓库 docs/workflows/configure-vector-search.md）；默认全文检索即可支撑本项目的上下文召回演示。
- scope_id 是数据分区键，不是访问控制；不要把 Server Token 交给模型或写进提交材料。

## 三、费用说明（对应题目"费用自担"要求）

| 组件 | 方案 | 费用 |
|------|------|------|
| seekdb | D0 试用实例 | 免费（7 天 TTL，无 SLA） |
| seekdb | 开源自部署 / OceanBase Cloud | 服务器或云资源费用自担 |
| PowerContext | 开源本地 Server（SQLite） | 免费 |
| PowerContext | 配置向量搜索需 embedding 模型 | 模型 API 费用自担（本项目默认不启用） |

本项目演示与验证全部可在**零费用**方案下完成。
