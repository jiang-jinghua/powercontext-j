"""任务状态存取。

赛题 2.4 要求：任务状态（规格版本、证据、补丁、验收结果、遗留事项）能保存，
并在"新会话"里恢复后继续使用。

本模块提供统一门面：
- :class:`PowerContextState` —— 优先使用 PowerContext（pip 包 / HTTP 服务）落库，
  这是赛题指定的记忆与交接组件；
- :class:`JsonState` —— 无 PowerContext 环境时的等价实现（同一份 payload，本地 JSON + JSONL 历史）。

两者写入的都是同一个 dict 结构，``backend`` 字段记录实际落库位置，便于评审核对。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "artifacts" / "state"


class BaseState:
    backend = "base"

    def save(self, task_id: str, payload: dict) -> dict:
        raise NotImplementedError

    def load(self, task_id: str) -> dict | None:
        raise NotImplementedError

    def history(self, task_id: str) -> list[dict]:
        raise NotImplementedError


class JsonState(BaseState):
    backend = "json-file"

    def __init__(self, directory: str | Path | None = None) -> None:
        self.dir = Path(directory or STATE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.json"

    def _hist(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.jsonl"

    def save(self, task_id: str, payload: dict) -> dict:
        record = dict(payload)
        record["task_id"] = task_id
        record.setdefault("backend", self.backend)
        record["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._path(task_id).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._hist(task_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def load(self, task_id: str) -> dict | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def history(self, task_id: str) -> list[dict]:
        path = self._hist(task_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PowerContextState(BaseState):
    """通过 PowerContext Server 保存与召回任务状态。

    接口（PowerContext 1.5 REST）：
    - ``GET  /v1/scopes``                 取默认 Scope（首次运行自动创建 Default）
    - ``POST /v1/memory/remember``        写入一条 kind=task-state 的记忆（显式写入，不触发生成模型）
    - ``POST /v1/atomic-memory/search``   新会话按任务号召回历史状态
    - ``POST /v1/memory/entries/list``    列最近状态（供 verify 核对）

    由于单条记忆上限 8192 字节，写入 PowerContext 的是"可读摘要 + 快照指针"，
    完整 payload（含全部证据 chunk、补丁哈希、测试细节）落在本地快照，
    二者用 ``snapshot_path`` 关联。
    """

    backend = "powercontext"

    def __init__(self, base_url: str | None = None, snapshot_dir: str | Path | None = None) -> None:
        self.base_url = (base_url or os.environ.get("POWERCONTEXT_URL") or "http://127.0.0.1:8077").rstrip("/")
        self.snapshot = JsonState(snapshot_dir)
        self.api_token = os.environ.get("POWERCONTEXT_TOKEN", "")
        self.scope_id = os.environ.get("POWERCONTEXT_SCOPE", "")
        self._last_error = ""
        if not self.scope_id:
            self.scope_id = self._resolve_scope()

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _request(self, method: str, path: str, payload: dict | None = None):
        try:
            import httpx

            resp = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=payload,
                timeout=20.0,
            )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None
        if resp.status_code >= 400:
            self._last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return None
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}

    def _resolve_scope(self) -> str:
        data = self._request("GET", "/v1/scopes")
        if data and data.get("items"):
            return data["items"][0]["scope_id"]
        return ""

    @property
    def online(self) -> bool:
        return bool(self.scope_id) and self._request("GET", "/health/live") is not None

    # ------------------------------------------------------------------ api
    def save(self, task_id: str, payload: dict) -> dict:
        record = self.snapshot.save(task_id, payload)
        snapshot_path = str(self.snapshot._path(task_id))
        summary = _state_summary(record, snapshot_path)
        remote = None
        if self.scope_id:
            remote = self._request(
                "POST",
                "/v1/memory/remember",
                {
                    "scope_id": self.scope_id,
                    "kind": "task-state",
                    "text": summary,
                    "reason": f"任务 {task_id} 验收结果与交接信息",
                    "description": f"{task_id} {record.get('status')} {record.get('spec_version')}"[:200],
                },
            )
        record["backend"] = self.backend if remote else f"{self.backend}(offline-json)"
        record["powercontext"] = {
            "scope_id": self.scope_id,
            "url": self.base_url,
            "written": bool(remote),
            "artifact": (remote or {}).get("records", [{}])[0].get("artifact") if remote else None,
            "error": "" if remote else self._last_error,
        }
        self.snapshot.save(task_id, record)
        return record

    def load(self, task_id: str) -> dict | None:
        return self.snapshot.load(task_id)

    def history(self, task_id: str) -> list[dict]:
        return self.snapshot.history(task_id)

    def recall(self, task_id: str, limit: int = 3) -> list[dict]:
        """新会话按任务号召回（PowerContext 侧的真实检索，用于跨会话演示）。"""
        if not self.scope_id:
            return []
        data = self._request(
            "POST",
            "/v1/atomic-memory/search",
            {"scope_id": self.scope_id, "query": task_id, "limit": limit},
        )
        if not data:
            return []
        return [
            {
                "artifact_id": hit["memory"]["artifact"]["artifact_id"],
                "kind": hit["memory"]["kind"],
                "score": round(hit.get("score", 0), 4),
                "text": hit["memory"]["text"][:600],
            }
            for hit in data.get("hits", [])
        ]


def _state_summary(record: dict, snapshot_path: str) -> str:
    """把状态压成不超过 8192 字节的可读摘要（写入 PowerContext）。"""
    tests = record.get("tests", {})
    lines = [
        f"[{record.get('task_id')}] {record.get('task_title', '')}",
        f"status={record.get('status')} spec_version={record.get('spec_version')} iterations={record.get('iterations')}",
        f"patch_sha={record.get('patch_sha', '')[:12]} store={record.get('store_backend')}",
        f"tests: passed={tests.get('passed')} failed={tests.get('failed')} ok={tests.get('ok')}",
        f"done={'; '.join(record.get('done', []))[:400]}",
        f"todo={'; '.join(record.get('todo', []))[:400]}",
        f"risks={'; '.join(record.get('risks', []))[:400]}",
        f"evidence={len(record.get('evidence', []))} chunks, first={','.join(record.get('evidence', [])[:5])}",
        f"run_dir={record.get('run_dir')}",
        f"snapshot_path={snapshot_path}",
    ]
    text = "\n".join(lines)
    return text.encode("utf-8")[:8000].decode("utf-8", "ignore")


def get_state(backend: str | None = None) -> BaseState:
    name = (backend or os.environ.get("CTX_STATE_BACKEND") or "").strip().lower()
    if name in ("powercontext", "pc"):
        return PowerContextState()
    if name == "json":
        return JsonState()
    # 自动：能用 PowerContext 就用，否则 JSON
    if os.environ.get("POWERCONTEXT_URL") or _sdk_available():
        return PowerContextState()
    return JsonState()


def _sdk_available() -> bool:
    try:
        import powercontext  # noqa: F401

        return True
    except Exception:
        return False


def handoff_summary(record: dict) -> str:
    """把状态记录渲染成交接摘要（新会话开局直接读这段）。"""
    if not record:
        return "（无历史状态）"
    lines = [
        f"任务：{record.get('task_id')} · {record.get('task_title', '')}",
        f"状态：{record.get('status')}　规格版本：{record.get('spec_version')}　迭代：{record.get('iterations')}",
        f"补丁：{record.get('patch_sha', '')[:12]}　测试：{json.dumps(record.get('tests', {}), ensure_ascii=False)}",
        f"已完成：{'; '.join(record.get('done', [])) or '无'}",
        f"待办：{'; '.join(record.get('todo', [])) or '无'}",
        f"遗留：{'; '.join(record.get('risks', [])) or '无'}",
        f"证据片段：{len(record.get('evidence', []))} 条",
        f"落库后端：{record.get('backend')}",
        f"保存时间：{record.get('saved_at')}",
    ]
    return "\n".join(lines)
