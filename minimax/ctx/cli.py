"""上下文工程 CLI（本项目唯一的操作入口）。

常用命令：
    python -m ctx.cli ingest --paths spec tasks src tests docs
    python -m ctx.cli stats
    python -m ctx.cli search --query "尾差 分摊" --k 5
    python -m ctx.cli context --task REQ-2026-011
    python -m ctx.cli run --task REQ-2026-011 --provider minimax
    python -m ctx.cli resume --task REQ-2026-011
    python -m ctx.cli verify
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import minimax, runner, state as state_mod
from .ingest import ingest
from .package import ContextPackage
from .retrieve import retrieve_for_task
from .store import get_store

ROOT = Path(__file__).resolve().parent.parent


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_ingest(args) -> int:
    store = get_store(args.backend)
    result = ingest(store, ROOT, args.paths, version=args.version)
    out = {"store": store.stats(), **{k: v for k, v in result.items() if k != "manifest"}}
    out["documents_ingested"] = result["documents"]
    out["chunks_ingested"] = result["chunks"]
    out["manifest_sample"] = result["manifest"][:5]
    _print(out)
    return 0


def cmd_stats(args) -> int:
    store = get_store(args.backend)
    _print({"stats": store.stats(), "spec_docs": {k: v for k, v in store.doc_versions().items() if k.startswith("spec/")}})
    return 0


def cmd_search(args) -> int:
    store = get_store(args.backend)
    hits = store.search(args.query, k=args.k, kinds=args.kind or None)
    _print(
        [
            {
                "chunk_id": h.chunk.chunk_id,
                "path": h.chunk.path,
                "title": h.chunk.title,
                "symbol": h.chunk.symbol,
                "spec_codes": list(h.chunk.spec_codes),
                "score": round(h.score, 4),
                "excerpt": h.chunk.text[:300],
            }
            for h in hits
        ]
    )
    return 0


def cmd_context(args) -> int:
    task = json.loads((ROOT / "tasks" / f"{args.task}.json").read_text(encoding="utf-8"))
    store = get_store(args.backend)
    retrieval = retrieve_for_task(store, task)
    st = state_mod.get_state()
    prev = st.load(args.task) or {}
    package = ContextPackage.build(
        task,
        retrieval,
        {"done": prev.get("done", []), "todo": task.get("followups", []), "risks": task.get("risks", [])},
    )
    if args.out:
        _print(package.save(ROOT / args.out))
    else:
        print(package.to_markdown())
    return 0


def cmd_run(args) -> int:
    record = runner.run_task(
        args.task,
        provider_name=args.provider,
        max_attempts=args.max_attempts,
        skip_tests=args.skip_tests,
        mode=getattr(args, "mode", "diff"),
    )
    _print({k: record[k] for k in ("task_id", "status", "run_id", "attempts", "tests", "state_backend", "run_dir")})
    return 0 if record["status"] == "done" else 1


def cmd_resume(args) -> int:
    info = runner.resume(args.task)
    print(info.pop("handoff"))
    _print(info)
    return 0


def cmd_ask(args) -> int:
    provider = minimax.get_provider(args.provider, args.task or "adhoc")
    messages = [
        {"role": "system", "content": "你是结算系统的资深工程师，回答简洁、给结论。"},
        {"role": "user", "content": args.question},
    ]
    completion = provider.complete(messages, attempt=1)
    minimax.record_call(
        ROOT / "artifacts" / "records" / "minimax_calls.jsonl",
        task_id=args.task or "adhoc",
        attempt=1,
        messages=messages,
        completion=completion,
    )
    print(completion.text)
    return 0


def cmd_doctor(args) -> int:
    """环境体检：三件套各自是否真的可用，以及不可用时怎么修。

    只做探测，不写任何索引或状态；输出直接给评审/队友照着复现。
    """
    report: dict = {"checks": {}, "verdict": "PASS", "fix": []}

    # 1) 存储后端：seekdb 能否连通，否则本地 FTS5 兜底
    seekdb_error = ""
    try:
        from .store import SeekdbStore

        seek = SeekdbStore()
        stats = seek.stats()
        report["checks"]["store"] = {
            "backend": stats.get("backend"),
            "database": stats.get("database"),
            "fts_mode": stats.get("fts_mode"),
            "documents": stats.get("documents"),
            "chunks": stats.get("chunks"),
        }
    except Exception as exc:  # 连不上就在报告里说明原因，不抛栈
        seekdb_error = f"{type(exc).__name__}: {exc}"[:300]
        local = get_store("local")
        stats = local.stats()
        report["checks"]["store"] = {
            "backend": stats.get("backend"),
            "documents": stats.get("documents"),
            "chunks": stats.get("chunks"),
            "seekdb_error": seekdb_error,
        }
        report["fix"].append("seekdb 不可用：启动它后 `CTX_STORE_BACKEND=seekdb python -m ctx.cli ingest`（见 scripts/start_seekdb.sh）")

    # 2) PowerContext：健康检查 + Scope + 已写入记忆条数
    st = state_mod.get_state()
    pc = {"backend": st.backend, "url": getattr(st, "base_url", ""), "scope_id": getattr(st, "scope_id", "")}
    if getattr(st, "backend", "") == "powercontext":
        online = bool(getattr(st, "online", False))
        pc["online"] = online
        recent = st.recall(args.task) if online else []
        pc["recalled_for_task"] = len(recent)
        pc["latest"] = (recent[0]["text"].splitlines()[0] if recent else "")
        if not online:
            report["verdict"] = "WARN"
            report["fix"].append("PowerContext 未响应：`bash scripts/start_powercontext.sh`（默认 127.0.0.1:8077）")
    report["checks"]["powercontext"] = pc

    # 3) MiniMax：只检查配置是否齐备，不发请求（避免误消耗额度）
    key = os.environ.get("MINIMAX_API_KEY", "")
    report["checks"]["minimax"] = {
        "api_key": "set" if key else "missing",
        "base_url": os.environ.get("MINIMAX_BASE_URL") or minimax.DEFAULT_BASE_URL,
        "model": os.environ.get("MINIMAX_MODEL") or minimax.DEFAULT_MODEL,
        "recorded_replay_available": (ROOT / "artifacts" / "recorded").exists(),
    }
    if not key:
        report["verdict"] = "WARN"
        report["fix"].append("MiniMax 未配置 Key：`export MINIMAX_API_KEY=...`，或继续用 `--provider recorded` 离线回放")

    if not report["fix"]:
        report["fix"] = ["三件套均已就绪，可直接 `python -m ctx.cli run --task REQ-2026-011 --provider minimax`"]
    _print(report)
    return 0


def cmd_verify(args) -> int:
    """提交前自检：资料是否入库、规格版本、测试是否全绿、模型调用是否留痕。"""
    store = get_store(args.backend)
    stats = store.stats()
    specs = {k: v for k, v in store.doc_versions().items() if k.startswith("spec/")}
    task = json.loads((ROOT / "tasks" / f"{args.task}.json").read_text(encoding="utf-8"))
    outcome = runner.run_tests(task, ROOT)
    records = ROOT / "artifacts" / "records" / "minimax_calls.jsonl"
    calls = 0
    if records.exists():
        calls = sum(1 for line in records.read_text(encoding="utf-8").splitlines() if line.strip())
    checks = {
        "store_backend": stats.get("backend"),
        "documents": stats.get("documents"),
        "chunks": stats.get("chunks"),
        "chunks_by_kind": stats.get("by_kind"),
        "spec_versions": specs,
        "tests": outcome.to_dict(),
        "model_calls_recorded": calls,
    }
    ok = (
        stats.get("documents", 0) > 0
        and outcome.ok
        and bool(specs)
        and (calls > 0 or args.allow_no_records)
    )
    checks["verdict"] = "PASS" if ok else "FAIL"
    _print(checks)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctx", description="MiniMax × seekdb 上下文工程脚手架")
    parser.add_argument("--backend", default=None, help="store 后端：seekdb | local（默认自动探测）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="把工程资料写入 seekdb/本地索引")
    p.add_argument("--paths", nargs="+", default=["spec", "tasks", "src", "tests", "docs"])
    p.add_argument("--version", default="worktree")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("stats", help="查看索引统计与规格文档版本")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("search", help="全文检索")
    p.add_argument("--query", required=True)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--kind", nargs="*", default=None, help="过滤 kind：spec/code/test/doc")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("context", help="生成任务上下文包")
    p.add_argument("--task", required=True)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("run", help="执行任务闭环（生成补丁 + 回归）")
    p.add_argument("--task", required=True)
    p.add_argument("--provider", default="minimax", choices=["minimax", "recorded"])
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--mode", choices=["diff", "files"], default="diff", help="交付形式：diff=补丁，files=整文件")
    p.add_argument("--skip-tests", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="新会话恢复任务状态")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("ask", help="直接问一次模型（也留痕）")
    p.add_argument("--question", required=True)
    p.add_argument("--task", default="")
    p.add_argument("--provider", default="minimax", choices=["minimax", "recorded"])
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("doctor", help="环境体检：seekdb / PowerContext / MiniMax 是否真的可用")
    p.add_argument("--task", default="REQ-2026-011")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("verify", help="提交前自检")
    p.add_argument("--task", default="REQ-2026-011")
    p.add_argument("--allow-no-records", action="store_true")
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "backend", None) is None and hasattr(args, "func"):
        args.backend = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
