from __future__ import annotations

import json
from pathlib import Path

import pytest

from sddctx import minimax
from sddctx.errors import ModelError
from sddctx.minimax import ManualProvider, MiniMaxClient


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def reply(content: str = "```diff\n--- a/x\n+++ b/x\n```") -> dict:
    return {
        "id": "req-1",
        "model": "MiniMax-M3.1-Flash",
        "choices": [{"message": {"content": content, "reasoning_content": "think"}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 42},
        "base_resp": {"status_code": 0, "status_msg": ""},
    }


def client() -> MiniMaxClient:
    return MiniMaxClient(api_key="k", base_url="https://example.test", model="MiniMax-M3.1-Flash")


def test_success_records_content_and_usage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(minimax.httpx, "post", lambda *a, **k: FakeResponse(reply()))
    completion = client().complete([{"role": "user", "content": "hi"}], record_dir=tmp_path)
    assert "--- a/x" in completion.content
    assert completion.usage == {"total_tokens": 42}
    assert (tmp_path / "minimax-request-01.json").is_file()
    assert (tmp_path / "minimax-response-01.json").is_file()


def test_business_error_raises(monkeypatch) -> None:
    payload = {"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}
    monkeypatch.setattr(minimax.httpx, "post", lambda *a, **k: FakeResponse(payload))
    with pytest.raises(ModelError) as excinfo:
        client().complete([{"role": "user", "content": "hi"}])
    assert "1004" in str(excinfo.value)


def test_server_error_is_retried(monkeypatch) -> None:
    responses = [FakeResponse({}, status_code=503), FakeResponse(reply())]
    monkeypatch.setattr(minimax.httpx, "post", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(minimax.time, "sleep", lambda _seconds: None)
    assert client().complete([{"role": "user", "content": "hi"}]).content


def test_empty_reply_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(minimax.httpx, "post", lambda *a, **k: FakeResponse(reply(content="")))
    with pytest.raises(ModelError):
        client().complete([{"role": "user", "content": "hi"}])


def test_manual_provider_marks_its_source(tmp_path: Path) -> None:
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    completion = ManualProvider(patch).complete([], record_dir=tmp_path / "model")
    assert completion.provider == "manual"
    record = json.loads((tmp_path / "model" / "manual-source.json").read_text(encoding="utf-8"))
    assert record["provider"] == "manual"
