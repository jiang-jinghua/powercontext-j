# SpecAnchor · 规有所依

面向软件持续演进的 SDD 上下文管理系统，参加 **2026 OceanBase 数据库大赛 · MiniMax × OceanBase**
赛题。它把「规格 → 代码 → 测试」组织成一次可核查的代码修改：seekdb 负责工程资料的存储与检索，
MiniMax 负责生成补丁，PowerContext 负责保存任务进展与验证结果。

## 快速开始（离线复核，不需要模型 Key）

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export PYTHONPATH="$PWD/src"
python -m sddctx init
python -m sddctx import
python -m sddctx run T-001 --provider manual --patch patches/T-001.patch
python -m sddctx status T-001
```

`run` 会输出目标测试在修改前失败、修改后通过，以及回归测试全部通过的结果；运行产物写入
`runs/<run_id>/`。

## 用 MiniMax 真实生成补丁

```bash
export MINIMAX_API_KEY=...            # 必填，不写入仓库
export MINIMAX_BASE_URL=https://api.minimaxi.com   # 国际站可用 https://api.minimax.io
export MINIMAX_MODEL=MiniMax-M3.1-Flash

python -m sddctx reset                 # 清空 seekdb 与 PowerContext 状态
python -m sddctx init
python -m sddctx import
python -m sddctx run T-001 --provider minimax
```

运行目录下的 `model/minimax-request-*.json`、`model/minimax-response-*.json` 是原始调用记录；
`diff.patch`、`pytest-before.log`、`pytest-after.log`、`result.json` 是这次修改的证据链。

## 目录

| 路径 | 说明 |
| --- | --- |
| `src/sddctx/` | 上下文模块：语料扫描、seekdb 存储、检索、MiniMax 客户端、补丁应用、PowerContext 状态 |
| `demo/order-service/` | 验证样例：可运行、带测试的订单计价工程（规格 / 实现 / 测试） |
| `demo/spec-updates/2026.2/` | 规格变更样例：SAVE30 门槛 200 → 150 |
| `tasks/` | 任务定义（T-001 实现 + T-002 规格变更复核） |
| `patches/T-001.patch` | 审阅过的参考补丁（离线模式使用） |
| `tests/` | 上下文模块自身的单元测试 |
| `runs/` | 一次完整验证运行的产物（上下文、提示词、补丁、测试日志、结果） |
| `docs/` | 运行说明、验证样例、技术说明、验收对照、技术使用记录 |

## 技术分工

| 技术 | 实际承担的工作 | 接入方式与版本 |
| --- | --- | --- |
| MiniMax | 读取检索出的规格/代码/测试上下文，生成 unified diff | HTTP `POST /v1/text/chatcompletion_v2`，默认 `MiniMax-M3.1-Flash` |
| seekdb | 存储规格条款、代码符号、测试函数；`MATCH … AGAINST` 全文检索 + 精确来源查询 | `pylibseekdb` 嵌入式（Linux/macOS），或 OceanBase/seekdb MySQL 协议 |
| PowerContext | 保存每次运行、验证结果、待办；重启后由 `status` 与下一次 `context` 读取 | `powercontext[server]==1.2.0` 进程内 `open_builtin_runtime`，Memory 工件（SQLite） |

## 文档

- [RUNBOOK.md](RUNBOOK.md)：环境、安装、初始化、验证步骤、重置与已知限制
- [docs/verification-sample.md](docs/verification-sample.md)：验证样例（任务、规格、步骤、预期）
- [docs/technical-note.md](docs/technical-note.md)：一页技术说明
- [docs/acceptance-checklist.md](docs/acceptance-checklist.md)：6 项必做项与证据对照
- [docs/technical-record.md](docs/technical-record.md)：MiniMax / 数据库 / PowerContext 使用记录
