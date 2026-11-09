# 早市风云 · 老王菜摊

> OceanBase 2026 数据库大赛 · Dify 题目答案
> **Dify 工作流负责对话编排，seekdb 负责"记住这一局"，结算全部由确定性代码完成**

## 一句话玩法

6 轮之内，把老王的黄瓜从 12.0 元/斤砍到 **8.0 元/斤及以下**才算成交。
老王有脾气（情绪 ≤20 直接掀摊）、有底价（7.2 元，玩家看不见）、**记得你这一招用过几次**（重复使用效果递减）；
想赢得靠"试探行情"套出进货价——这条行情存在 seekdb 里，读出来之后每轮多让 0.15 元。

关掉页面、第二天再来，只要 `player` 不变，局面还在原处：状态不在 Dify 内存里，而在 seekdb 的 `zaoshi_state` 表里。

## 技术分工（为什么不是"套壳"）

| 组件 | 在本作品里真实承担的事 | 对应文件 |
|------|------------------------|----------|
| **Dify Workflow** | 采集 `player / action / request_id` → 调后端 → 把摊主回话返回给玩家；网络重试时 `request_id` 不变，保证不重复扣轮次 | `zaoshi-fengyun.yml` |
| **seekdb**（MySQL 协议，OceanBase 内核） | ① `zaoshi_state` 存每局局面 ② `zaoshi_events` 存 (player, request_id) 幂等表 ③ `zaoshi_intel` 存行情文本，"试探行情"时读出来 | `app.py` |
| **确定性结算内核** | 8 种招式的让价系数、重复衰减、情绪阈值、抹零、成交/掀摊/超时判定 | `game.py` |

关键取舍：**不把数值交给模型**。招式由玩家输入、接口归一化到 8 个标准动作，`game.py` 纯函数结算，
因此 `rules_check.py` 与 `verify.py` 都能用同一份代码推演出期望值，再和实际结果逐字段比对。

## 规则表（声明 = 代码，逐条可核对）

下表每一条都有编号，`game.py` 顶部注释与 `rules_check.py` 的断言使用同一套编号；
`python rules_check.py` 会把每一条单独算一遍，再和代码的实际输出比对。

| 编号 | 规则 | 取值 / 公式 |
|------|------|-------------|
| R1 | 开局标价 | 12.0 元/斤，情绪 50，第 0 轮 |
| R2 | 老王底价（任何情况不低于） | 7.2 元/斤 |
| R3 | **成交条件（唯一）** | 本轮结算后价格 **≤ 8.0** 才算 deal |
| R4 | 轮数上限 | 6 轮，第 6 轮后必为终局 |
| R5 | 每轮让价 | (现价 − 7.2) × 0.35 × 招式系数 × 衰减 |
| R6 | 重复使用衰减 | 第 2 次 ×0.6，第 3 次起 ×0.25（下限，不再继续降） |
| R7 | 情绪 < 35 时 | 本轮让价再 ×0.6（"被惹毛就不松口"） |
| R8 | 情绪 ≥ 85 且轮次 ≥ 3 | 额外抹零 0.3 元（仅该轮） |
| R9 | 首次「试探行情」 | 一次性多让 0.2 元，并解锁 R10 |
| R10 | 解锁情报后每轮 | 额外让 0.15 × 本轮衰减 |
| R11 | 情绪 ≤ 20 | 掀摊（broke），**优先级高于成交** |
| R12 | 第 6 轮结束仍 > 8.0 | 收摊没谈成（timeout），不算成交 |
| R13 | 终局后再出招 | 自动重开新局（返回 `restarted=true`，轮次回到 1） |

招式系数与情绪增减（R5 的第一个乘数）：

| 招式 | 系数 | 情绪 | 招式 | 系数 | 情绪 |
|------|------|------|------|------|------|
| 对比邻摊 | 1.30 | −6 | 卖惨 | 0.80 | +2 |
| 挑刺 | 1.10 | −4 | 硬砍 | 0.50 | −8 |
| 走人威胁 | 1.40 | −12 | 试探行情 | 0.55 | 0 |
| 套近乎 | 0.90 | +8 | 认错缓和 | 0.70 | +6 |

**结局只有三种**，互斥且有明确优先级：掀摊（R11）> 成交（R3）> 超时（R12）。

### 逐轮算式（对应证据里的剧本 A，可直接手算复核）

| 轮 | 招式 | 算式 | 价格 | 情绪 |
|----|------|------|------|------|
| 1 | 对比邻摊 | 12.0 − (12.0−7.2)×0.35×1.30×1.00 = 12.0 − 2.184 | 9.8 | 50−6=44 |
| 2 | 走人威胁 | 9.8 − (9.8−7.2)×0.35×1.40×1.00 = 9.8 − 1.274 | 8.5 | 44−12=32 |
| 3 | 试探行情 | 8.5 − (8.5−7.2)×0.35×0.55×1.00 − 0.2 = 8.5 − 0.250 − 0.2 | 8.1 | 32+0+8=40 |
| 4 | 套近乎 | 8.1 − (8.1−7.2)×0.35×0.90×1.00 − 0.15 = 8.1 − 0.284 − 0.15 | 7.7 | 40+8=48 |

第 4 轮 7.7 ≤ 8.0 → 成交（R3），比成交线低 0.3 元。

## 目录结构

```
dify/
├── zaoshi-fengyun.yml     # 4.1 Dify DSL（workflow：开始 → HTTP → 结束）
├── game.py                # 结算内核（纯函数，无 IO、无随机）+ 规则表注释
├── app.py                 # HTTP 服务：/turn、/state/{player}、/healthz
├── rules_check.py         # 规则一致性自检：12 条规则逐条验，含 8^6 全序列穷举
├── verify.py              # 4.3 端到端验收（3 个剧本，写 evidence/backend-results.json）
├── negative.py            # 异常路径验收（401 / 422 / 幂等 / 脏写）
├── compose.yaml           # seekdb + api 一键起
├── Dockerfile
├── requirements.txt
├── .env.example
├── evidence/              # 真实运行结果（见 §3）
└── docs/验收样例-逐轮对话.md
```

## 1. 运行（Docker Compose）

```bash
cd dify
cp .env.example .env          # 改两个值：DB_PASSWORD、GAME_TOKEN
docker compose up -d --build
```

* `seekdb` 首次启动要 bootstrap（约 30–90 秒），`api` 启动时按 3 秒间隔重试建库建表，**不需要手工等**。
* 起来后：`curl http://127.0.0.1:8000/healthz` → `{"ok":true,"database":"zaoshi","storage":"seekdb"}`。
* 表结构、行情数据由 `api` 自动创建（`CREATE DATABASE/TABLE IF NOT EXISTS` + `INSERT IGNORE`），可重复执行。
* 不用 Docker 时：`pip install -r requirements.txt` 后 `uvicorn app:app --port 8000`，`.env` 里把 `DB_HOST/DB_PORT` 指向任意 seekdb 实例（默认 2881）。

接口（全部要求 `Authorization: Bearer $GAME_TOKEN`）：

```bash
# 出一招：8 个标准招式之一（也接受"隔壁卖八块""不买了"这类口语，由 game.parse_action 归一）
curl -s -X POST http://127.0.0.1:8000/turn \
  -H "Authorization: Bearer $GAME_TOKEN" -H "Content-Type: application/json" \
  -d '{"player":"demo-1","action":"试探行情","request_id":"r1"}'

# 读局面（验收用，直接来自 seekdb）
curl -s http://127.0.0.1:8000/state/demo-1 -H "Authorization: Bearer $GAME_TOKEN"
```

## 2. 在 Dify 里装这个应用（4.2）

1. 准备一个能访问本机 8000 端口的 Dify（自部署版；本作品不依赖第三方插件、不依赖模型）。
2. 「工作室 → 导入 DSL → 选 `zaoshi-fengyun.yml`」。
3. 打开应用的**环境变量**：`GAME_TOKEN` 填成 `.env` 里的同一个值；`ZAOSHI_API` 默认 `http://host.docker.internal:8000`，若 Dify 与后端在同一 Docker 网络则改成 `http://api:8000`。
4. Dify 默认 SSRF 保护会拦截私网地址：需在该实例的 `ssrf_proxy` 白名单放行 `host.docker.internal` / `api`，**不要**图省事关掉 SSRF。
5. 运行：`player` 填固定字符串（如 `dify-张三`），`action` 填招式，`request_id` 填递增串（`1`、`2`、`3`）。
   每次运行对应一轮；重跑同一个 `request_id` 得到完全相同回话、轮次不推进——这就是幂等。

> DSL 版本 0.1.5，3 节点（start / http-request / end）。若你的 Dify 版本提示个别字段不兼容，按上面第 3、5 步重建这 3 个节点即可，字段含义见本文 §6。

## 3. 验证与证据（4.3）

```bash
python rules_check.py   # 规则一致性：声明 vs 实际，逐条 PASS/FAIL
python verify.py        # 端到端：把服务当黑盒，三个剧本逐字段比对
python negative.py      # 异常路径：401 / 422 / 状态不被脏写
```

**`evidence/` 全部是真实运行产物**，不是手写样例：

| 文件 | 内容 |
|------|------|
| `evidence/rules-check.txt` | 12 条规则逐条核对结果：含 8^6 = 262144 种出招序列穷举（验证"成交必然 ≤8.0""价格永不低于 7.2""轮次不超 6"） |
| `evidence/backend-results.json` | 16 条真实请求/响应：剧本 A（4 轮 7.7 元成交）、剧本 B（连用 3 次"走人威胁"→ 情绪 14 掀摊）、剧本 C（6 轮 9.3 元，**没到 8.0 判 timeout**）、幂等重放、`/state` 持久化核对、终局后重开 |
| `evidence/negative-results.json` | 4 条异常用例：错误令牌 401、缺令牌 401、非法招式 422 且局面不变、缺字段 422 |
| `evidence/seekdb-tables.txt` | 直连 seekdb 的 `SELECT VERSION()` / `SHOW TABLES` / 真实行数据 |
| `evidence/verify-output.txt` | 上面三个脚本 + `/healthz` 的控制台输出 |

实测环境：seekdb **v1.4.0.0**（`SELECT VERSION()` → `5.7.25-OceanBase seekdb-v1.4.0.0`），
后端 FastAPI 0.115.6 + uvicorn，Python 3.12，真实 HTTP 往返。

人话版四轮对话见 [`docs/验收样例-逐轮对话.md`](docs/验收样例-逐轮对话.md)。

## 4. 与题目四章提交材料的对应（4.4）

| 题目要求 | 位置 |
|----------|------|
| 4.1 Dify DSL | `zaoshi-fengyun.yml`（唯一应用文件，无插件依赖） |
| 4.2 运行说明 | 本文 §1（compose 起服务）+ §2（Dify 导入） |
| 4.3 验证样例与运行证据 | `rules_check.py` / `verify.py` / `negative.py` + `evidence/` 五份真实产物 + "逐轮算式" |
| 4.4 seekdb 使用说明 | "技术分工"表 + `app.py` 的 `ensure_schema()`；三张表见下表 |

seekdb 里的三张表（由 `app.py` 自动建立）：

| 表 | 主键 | 作用 |
|----|------|------|
| `zaoshi_state` | `player` | 每局局面：`round_no / price / mood / intel / tactics / status` |
| `zaoshi_events` | `(player, request_id)` | 幂等与审计：原始响应整包留痕，重试直接回放 |
| `zaoshi_intel` | `id` | 行情文本（进货价 / 邻摊价 / 收摊规矩），"试探行情"时读出 |

事务方式：`SELECT ... FOR UPDATE` 锁住该玩家的一行再结算、写回，提交后释放；同一玩家并发两招不会互相覆盖。

## 5. 已知边界（如实说明）

* **服务层已实机跑通**：上面的证据都来自真实 seekdb 实例 + 真实 HTTP 往返；换 `player` 可无限复测。
* **Dify 侧**：本仓库提供 DSL 与导入步骤，但评测环境的 Dify 实例不在本机，未做"导入后截图"这一步。
  按 §2 导入后，同一串输入的数字应与 `evidence/backend-results.json` 完全一致——因为数字只由 `game.py` 决定，与 Dify 版本无关。
* **没有用向量检索**：本作品用 seekdb 的结构化能力（事务 + 持久化 + 行情表），不宣称做了向量召回；若需要语义检索，可在 `zaoshi_intel` 上加全文/向量索引，属于后续增强。
* **`player` 是演示标识，不是身份认证**；令牌只用于防误调。

## 6. DSL 三个节点的字段含义

| 节点 | 关键字段 | 说明 |
|------|----------|------|
| `start`（玩家出招） | `player`、`action`、`request_id` | 三个必填文本输入，对应后端请求体 |
| `http`（读取局面并推进一轮） | `url={{#env.ZAOSHI_API#}}/turn`、`headers` 带 `Authorization: Bearer {{#env.GAME_TOKEN#}}`、`body` 为 JSON 字符串 | 用环境变量注入地址与令牌，不把密钥写进 DSL |
| `end`（输出摊主回话） | `outputs[0].value_selector = [http, body]` | 直接把后端返回整包回给玩家 |

## 7. 规则版本记录

| 版本 | 变化 |
|------|------|
| v1（上一版，被评测指出"核心玩法结果与声明规则不符"） | 声明"砍到 8.0 才算成交"，但代码里有一条未声明的"收摊友情价：第 6 轮 ≤8.5 也算成交"，导致 8.5 元被标成成交；另有"情绪 <35 让价 ×0.6""情绪 ≥85 抹零 0.3""首次情报一次性 −0.2"三条规则生效但没写进声明 |
| **v2（本版）** | 删掉与成交线冲突的"友情价"分支，**成交条件唯一为 ≤8.0**（R3）；三条隐藏规则显式写进规则表（R7/R8/R9）；掀摊阈值统一为"情绪 ≤20"并明确优先级（R11 高于 R3）；新增 `rules_check.py` 逐条验证声明与代码一致，并穷举 262144 种序列确认成交/底价/轮次边界 |
