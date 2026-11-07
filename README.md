# OceanBase 2026 大赛 · 团队答案仓库

| 赛题 | 答案位置 | 一句话说明 |
|------|----------|------------|
| **Qoder 赛道** · 温故 ReviewMate | 仓库根目录（即本文档） | AI 学习复习助手，seekdb 全文检索 + PowerContext 上下文沉淀 |
| **Dify 赛道** · 早市风云 · 老王菜摊 | [`dify-zaoshi-fengyun/`](./dify-zaoshi-fengyun/README.md) | Dify Chatflow 砍价游戏，seekdb 充当 Dify 元数据库 + 向量库 + 全文索引 |

> 下面为 Qoder 赛道（温故 ReviewMate）项目说明。

---

# 温故 ReviewMate · AI 学习复习助手

> OceanBase 2026 大赛 Qoder 赛道参赛项目
> **用 Qoder 开发 · seekdb（OceanBase AI 原生混合搜索数据库）做知识检索 · PowerContext（AI Agent 上下文服务）做学习上下文沉淀 · Qoder Sites 发布**

## 解决什么问题

学习/科研中最常见的真实痛点：**笔记散落在各处、复习凭感觉、不知道自己"学没学会"**。
温故把每个知识点做成复习卡片，用间隔重复算法（SM-2 lite）自动安排复习计划，并用两个官方技术组件解决两个深层问题：

| 组件 | 在本项目中的作用 |
|------|------------------|
| **seekdb**（MySQL 兼容，全文/向量/JSON 一体） | 知识点持久化存储；对标题+正文建全文索引（`MATCH ... AGAINST`），提供跨课程的相关性检索 |
| **PowerContext**（AI Agent 上下文服务） | 每次复习会话写入 Source 证据（`/v1/sources/content`）；「已掌握」判定写入 Memory 决策（`/v1/memory/remember`）；随时通过 `/v1/context/prepare` 召回历史学习上下文 |

## 功能一览

- **今日复习**：按到期时间出卡片，正面标题 / 背面笔记，三键评价（忘记/模糊/记住）驱动间隔 1→2→4→7→15→30 天
- **知识库**：知识点增删查，显示复习进度与掌握状态
- **智能搜索**：基于 seekdb 全文索引的相关性检索
- **上下文召回**：用自然语言查询"我过去学过什么、做过哪些决定"
- **调用日志**：每一次 seekdb / PowerContext 调用的请求与响应全量留痕，一键导出 JSON（即提交材料中的"验证说明"运行记录）

## 运行说明

### 方式一：演示模式（无需任何账号，任何人打开即用）

用任意静态服务器托管本目录即可，例如：

```bash
node server/proxy.mjs      # 打开 http://127.0.0.1:8787
# 或
python -m http.server 8080 # 打开 http://127.0.0.1:8080
```

默认为**内置演示模式**：8 条示例知识点（其中 4 条今天到期），全部功能可用，
seekdb / PowerContext 调用以本地模拟实现并如实标注 `(mock)`。

### 方式二：真实接入模式

1. **创建 seekdb D0 实例**（免费、零注册，7 天有效期）：

   ```bash
   curl -s -X POST 'https://d0.seekdb.ai/api/v1/instances'
   # 返回 instance_id / password（仅此一次）/ database / host / port
   ```

2. **启动 PowerContext Server**（Python 3.11+）：

   ```bash
   uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
   powercontext server run       # 默认 http://127.0.0.1:8000
   # 创建 scope 并记下返回的 scope_id：
   curl --header 'Content-Type: application/json' \
     --data '{"title":"温故学习助手","summary":"复习与掌握记录","idempotency_key":"wengu"}' \
     http://127.0.0.1:8000/v1/scopes
   ```

3. **启动本站代理**：`node server/proxy.mjs`（Node 18+，无第三方依赖）
4. 打开站点 → 「数据源」页 → 切换**真实接入模式** → 填入代理地址、seekdb instance_id/password、PowerContext Server 地址与 scope_id → 保存 → 点击「初始化 seekdb 表结构」
5. 之后的新增知识点、搜索、复习评价、上下文召回全部走真实 API，可在「调用日志」页核对

> 凭据仅保存在浏览器 localStorage，不会写入代码仓库或提交材料。

### 发布到 Qoder Sites

本项目为纯静态站点（无构建步骤），在 Qoder 中直接使用 Qoder Sites 托管发布：
打开项目 → 发布为站点 → 勾选"允许任何人访问"（题目硬性要求）→ 将生成的网址填入提交材料。

## 目录结构

```
index.html            单页应用入口
css/style.css
js/store.js           本地数据层（知识点、SM-2 lite 调度、调用日志、配置）
js/adapters.js        seekdb / PowerContext API 适配层 + 演示模式实现
server/proxy.mjs      可选本地代理（静态托管 + CORS 转发，零依赖）
docs/                 提交材料（验证说明、接入指引、开发记录模板等）
```

## 技术要点（对应评分维度）

- **零构建静态站**：Qoder Sites 直接托管，评委未登录也可完整体验
- **适配层模式**：mock / real 双实现同一接口，演示与真实接入无缝切换
- **API 规格与官方文档逐字对齐**：seekdb HTTP Query API 请求体/响应结构、PowerContext 四端点均按官方 SKILL.md 与 API Quick Start 实现
- **可验证性**：全部调用留痕可导出，验证说明可直接引用
