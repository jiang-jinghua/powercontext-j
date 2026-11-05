"""MiniMax clients plus the offline provider used for reproducible runs.

Two credentials work with MiniMax:

* pay-as-you-go API keys on the native ``/v1/text/chatcompletion_v2`` endpoint;
* M Plan subscription keys on the Anthropic-compatible ``/v1/messages`` endpoint
  (``https://api.minimax.cn/anthropic``), which is what the competition grants.

Both clients share the same ``Provider`` contract and transcript format.
"""

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
    api_style: str = "native"

    def to_record(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "api_style": self.api_style,
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


class _HttpClient:
    """Shared retry, transcript, and error handling for one JSON endpoint."""

    name = "minimax"
    api_style = "native"

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: float = 180.0) -> None:
        if not api_key:
            raise ConfigurationError("MiniMax API key is empty")
        if not model.strip():
            raise ConfigurationError("MiniMax model id is empty")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._counter = 0

    # -- per-endpoint hooks -------------------------------------------------
    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _body(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict:
        raise NotImplementedError

    def _parse(self, payload: dict) -> tuple[str, str, dict, str, str]:
        raise NotImplementedError

    def _business_error(self, payload: dict) -> tuple[int, str] | None:
        return None

    # -- shared -------------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        record_dir: Path | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
    ) -> Completion:
        body = self._body(messages, temperature, max_tokens)
        self._counter += 1
        tag = f"{self._counter:02d}"
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)
            (record_dir / f"minimax-request-{tag}.json").write_text(
                json.dumps(
                    {
                        "api_style": self.api_style,
                        "url": self._endpoint(),
                        "headers": {"Authorization": "Bearer <redacted>", "Content-Type": "application/json"},
                        "body": body,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = httpx.post(
                    self._endpoint(),
                    headers=self._headers(),
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
                detail = response.text[:300]
                try:
                    payload = response.json()
                    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                        detail = f"{payload['error'].get('type', '')}: {payload['error'].get('message', '')}"
                except ValueError:
                    pass
                raise ModelError(f"MiniMax HTTP {response.status_code}: {detail}")
            payload = response.json()
            if record_dir is not None:
                (record_dir / f"minimax-response-{tag}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            business = self._business_error(payload)
            if business is not None:
                code, message = business
                if code in _RETRYABLE_STATUS and attempt < 3:
                    last_error = ModelError(f"MiniMax error {code}: {message}")
                    time.sleep(2**attempt)
                    continue
                raise ModelError(f"MiniMax error {code}: {message}")
            content, reasoning, usage, model, request_id = self._parse(payload)
            if not content:
                raise ModelError("MiniMax returned an empty reply")
            latency_ms = int((time.perf_counter() - started) * 1000)
            return Completion(
                content=content,
                reasoning=reasoning,
                usage=usage,
                model=model or self._model,
                request_id=request_id,
                latency_ms=latency_ms,
                api_style=self.api_style,
            )
        raise ModelError(f"MiniMax call failed after retries: {last_error}")


class MiniMaxClient(_HttpClient):
    """Pay-as-you-go key against ``/v1/text/chatcompletion_v2``."""

    api_style = "native"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimaxi.com",
        model: str = "MiniMax-M3.1-Flash",
        timeout_seconds: float = 180.0,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model, timeout_seconds=timeout_seconds)

    def _endpoint(self) -> str:
        return f"{self._base_url}/v1/text/chatcompletion_v2"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _body(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict:
        return {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "stream": False,
        }

    def _business_error(self, payload: dict) -> tuple[int, str] | None:
        status = int((payload.get("base_resp") or {}).get("status_code") or 0)
        if status == 0:
            return None
        return status, str((payload.get("base_resp") or {}).get("status_msg", ""))

    def _parse(self, payload: dict) -> tuple[str, str, dict, str, str]:
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return (
            (message.get("content") or "").strip(),
            (message.get("reasoning_content") or "").strip(),
            payload.get("usage") or {},
            str(payload.get("model") or self._model),
            str(payload.get("id") or ""),
        )


class AnthropicClient(_HttpClient):
    """M Plan subscription key against the Anthropic-compatible Messages API."""

    api_style = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimax.cn/anthropic",
        model: str = "MiniMax-M3.1-Flash",
        timeout_seconds: float = 180.0,
        effort: str | None = None,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model, timeout_seconds=timeout_seconds)
        self._effort = effort

    def _endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _body(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict:
        system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        conversation = [
            {"role": message["role"], "content": [{"type": "text", "text": message["content"]}]}
            for message in messages
            if message["role"] != "system"
        ]
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conversation,
        }
        if system:
            body["system"] = system
        if self._effort:
            body["output_config"] = {"effort": self._effort}
        return body

    def _business_error(self, payload: dict) -> tuple[int, str] | None:
        return None

    def _parse(self, payload: dict) -> tuple[str, str, dict, str, str]:
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in payload.get("content") or ():
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") in {"thinking", "reasoning"}:
                thinking_parts.append(block.get("thinking") or block.get("text") or "")
        return (
            "".join(text_parts).strip(),
            "".join(thinking_parts).strip(),
            payload.get("usage") or {},
            str(payload.get("model") or self._model),
            str(payload.get("id") or ""),
        )


def build_client(
    *, api_key: str, base_url: str, model: str, api_style: str, effort: str | None = None
) -> _HttpClient:
    """Select the endpoint that matches the credential type."""
    style = api_style.lower()
    if style == "anthropic":
        return AnthropicClient(api_key=api_key, base_url=base_url, model=model, effort=effort)
    return MiniMaxClient(api_key=api_key, base_url=base_url, model=model)


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
                        "patch": self._patch_path.name,
                        "note": "No model call was made for this run; the reviewed patch was applied directly.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return Completion(content=content, provider="manual", model="manual-patch")
