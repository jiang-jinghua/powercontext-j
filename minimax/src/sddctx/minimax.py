"""MiniMax text API client plus offline providers used for reproducible runs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .errors import ConfigurationError, ModelError

_RETRYABLE_STATUS = {1000, 1001, 1002, 1013}


@dataclass(frozen=True)
class Completion:
    """One model reply with the metadata needed for the development record."""

    content: str
    reasoning: str = ""
    usage: dict[str, object] | None = None
    model: str = ""
    request_id: str = ""
    latency_ms: int = 0
    provider: str = "minimax"

    def to_record(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "latency_ms": self.latency_ms,
            "usage": self.usage or {},
            "content_chars": len(self.content),
            "reasoning_chars": len(self.reasoning),
        }


class Provider(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        record_dir: Path | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
    ) -> Completion: ...


class MiniMaxClient:
    """Call ``/v1/text/chatcompletion_v2`` with retries and a recorded transcript."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimaxi.com",
        model: str = "MiniMax-M3.1-Flash",
        timeout_seconds: float = 180.0,
    ) -> None:
        if not api_key:
            raise ConfigurationError("MiniMax API key is empty")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._counter = 0
        self.name = "minimax"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        record_dir: Path | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
    ) -> Completion:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "stream": False,
        }
        self._counter += 1
        tag = f"{self._counter:02d}"
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)
            (record_dir / f"minimax-request-{tag}.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = httpx.post(
                    f"{self._base_url}/v1/text/chatcompletion_v2",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue
            if response.status_code >= 500 or response.status_code == 429:
                last_error = ModelError(f"MiniMax HTTP {response.status_code}")
                time.sleep(2**attempt)
                continue
            if response.status_code != 200:
                raise ModelError(f"MiniMax HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            if record_dir is not None:
                (record_dir / f"minimax-response-{tag}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            status = int((payload.get("base_resp") or {}).get("status_code") or 0)
            if status in _RETRYABLE_STATUS and attempt < 3:
                last_error = ModelError(f"MiniMax base_resp {status}: {payload.get('base_resp', {}).get('status_msg', '')}")
                time.sleep(2**attempt)
                continue
            if status != 0:
                message = (payload.get("base_resp") or {}).get("status_msg", "")
                raise ModelError(f"MiniMax error {status}: {message}")
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = (message.get("content") or "").strip()
            if not content:
                finish = choice.get("finish_reason", "unknown")
                raise ModelError(f"MiniMax returned an empty reply (finish_reason={finish})")
            latency_ms = int((time.perf_counter() - started) * 1000)
            return Completion(
                content=content,
                reasoning=(message.get("reasoning_content") or "").strip(),
                usage=payload.get("usage") or {},
                model=str(payload.get("model") or self._model),
                request_id=str(payload.get("id") or ""),
                latency_ms=latency_ms,
            )
        raise ModelError(f"MiniMax call failed after retries: {last_error}")


class ManualProvider:
    """Apply a reviewed patch file; used when no model credential is available."""

    name = "manual"

    def __init__(self, patch_path: Path) -> None:
        self._patch_path = patch_path

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        record_dir: Path | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
    ) -> Completion:
        if not self._patch_path.is_file():
            raise ModelError(f"patch file not found: {self._patch_path}")
        content = "```diff\n" + self._patch_path.read_text(encoding="utf-8").rstrip() + "\n```"
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)
            (record_dir / "manual-source.json").write_text(
                json.dumps(
                    {
                        "provider": "manual",
                        "patch": str(self._patch_path),
                        "note": "No model call was made for this run; the reviewed patch was applied directly.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return Completion(content=content, provider="manual", model="manual-patch")
