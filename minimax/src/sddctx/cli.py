"""Command line entry point: ``python -m sddctx <command>``."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import Settings, load_settings
from .context import pack_json, render_markdown
from .corpus import load_project
from .errors import SddError
from .minimax import ManualProvider, build_client
from .pipeline import render_status, run_task
from .retrieve import Task, retrieve
from .state import StateStore
from .store import CorpusStore


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(home=args.home, project=args.project, db_path=args.db)
    settings.ensure_dirs()
    return settings


def cmd_init(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with CorpusStore.open(db_path=settings.db_path) as store:
        store.ensure_schema()
        print(json.dumps({"db": str(settings.db_path), "schema": "ready"}, ensure_ascii=False))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    settings = _settings(args)
    overlay = Path(args.spec_overlay).resolve() if args.spec_overlay else None
    corpus = load_project(
        settings.project_dir,
        settings.project_dir.name,
        spec_version=args.spec_version,
        spec_overlay=overlay,
    )
    with CorpusStore.open(db_path=settings.db_path) as store:
        store.ensure_schema()
        summary = store.replace_project(corpus, spec_version=args.spec_version)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    settings = _settings(args)
    task = Task.load(settings.home / "tasks" / f"{args.task}.yaml")
    state = StateStore(settings.state_db, settings.scheduler_db).recall(task.project)
    with CorpusStore.open(db_path=settings.db_path) as store:
        store.ensure_schema()
        pack = retrieve(store, task, state=state, budget=args.budget)
    if args.json:
        print(pack_json(pack))
    else:
        print(render_markdown(pack))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.provider == "manual":
        if not args.patch:
            raise SddError("--provider manual needs --patch <file>")
        provider = ManualProvider(Path(args.patch).resolve())
    else:
        provider = build_client(
            api_key=settings.require_minimax(),
            base_url=settings.minimax_base_url,
            model=args.model or settings.minimax_model,
            api_style=args.api_style or settings.minimax_api_style,
            effort=settings.minimax_effort,
        )
    with CorpusStore.open(db_path=settings.db_path) as store:
        store.ensure_schema()
        result = run_task(
            settings,
            task_id=args.task,
            provider=provider,
            store=store,
            tasks_dir=settings.home / "tasks",
            budget=args.budget,
            max_tokens=settings.minimax_max_tokens,
            output_mode=args.output_mode or settings.minimax_output,
        )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "run_dir": str(result.run_dir),
                "verification": result.verification,
                "changes": [change.__dict__ for change in result.changes],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "passed" else 1


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    task_id = args.task
    project = args.project_name or settings.project_dir.name
    records = StateStore(settings.state_db, settings.scheduler_db).recall(project, task_id=task_id)
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        print(render_status(records))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with CorpusStore.open(db_path=settings.db_path) as store:
        store.ensure_schema()
        print(json.dumps(store.stats(args.project_name or settings.project_dir.name), ensure_ascii=False, indent=2))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if settings.db_path.is_dir():
        shutil.rmtree(settings.db_path)
    elif settings.db_path.exists():
        settings.db_path.unlink()
    for suffix in ("", "-wal", "-shm"):
        Path(str(settings.state_db) + suffix).unlink(missing_ok=True)
        Path(str(settings.scheduler_db) + suffix).unlink(missing_ok=True)
    print(json.dumps({"reset": [str(settings.db_path), str(settings.state_db)]}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description="Spec-anchored SDD context module")
    parser.add_argument("--home", help="submission root (defaults to cwd)")
    parser.add_argument("--project", help="business project directory")
    parser.add_argument("--db", help="embedded seekdb directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the corpus schema").set_defaults(func=cmd_init)

    import_parser = sub.add_parser("import", help="scan specs/code/tests into seekdb")
    import_parser.add_argument("--spec-version", help="override the specification version")
    import_parser.add_argument("--spec-overlay", help="replace one spec file during the scan")
    import_parser.set_defaults(func=cmd_import)

    context_parser = sub.add_parser("context", help="print the cited context pack for a task")
    context_parser.add_argument("task")
    context_parser.add_argument("--json", action="store_true")
    context_parser.add_argument("--budget", type=int, default=3600)
    context_parser.set_defaults(func=cmd_context)

    run_parser = sub.add_parser("run", help="run the task end to end")
    run_parser.add_argument("task")
    run_parser.add_argument("--provider", choices=("minimax", "manual"), default="minimax")
    run_parser.add_argument("--patch", help="reviewed patch for --provider manual")
    run_parser.add_argument("--model", help="MiniMax model id override")
    run_parser.add_argument(
        "--api-style",
        choices=("native", "anthropic"),
        help="native = pay-as-you-go key; anthropic = M Plan subscription key",
    )
    run_parser.add_argument(
        "--output-mode",
        choices=("files", "diff"),
        help="files = model returns whole files (default); diff = model returns a unified diff",
    )
    run_parser.add_argument("--budget", type=int, default=3600)
    run_parser.set_defaults(func=cmd_run)

    status_parser = sub.add_parser("status", help="read state saved by earlier runs")
    status_parser.add_argument("task", nargs="?")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--project-name", help="project name stored in state")
    status_parser.set_defaults(func=cmd_status)

    stats_parser = sub.add_parser("stats", help="show corpus statistics")
    stats_parser.add_argument("--project-name")
    stats_parser.set_defaults(func=cmd_stats)

    reset_parser = sub.add_parser("reset", help="remove the corpus and PowerContext state")
    reset_parser.set_defaults(func=cmd_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SddError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
