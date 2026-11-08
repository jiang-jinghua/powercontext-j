"""MiniMax 模型接入：任务里唯一的模型调用出口。

设计要求（对齐赛题 2.2 / 2.4）：
- 调用入口明确：``Provider.complete(messages) -> str``，CLI 亦可直接调用（``ctx.cli ask``）；
- 每次调用都落 ``artifacts/records/minimax_calls.jsonl``：请求、响应、模型名、耗时、token 用量、prompt 摘要，
  便于评审把"模型输出"与"提交的代码差异"对齐复核；
- 支持两种 Provider：
  * ``minimax``   —— 真实调用 MiniMax 开放平台（OpenAI 兼容的 /chat/completions）；
  * ``recorded``  —— 回放已归档的模型响应，用于无 Key 环境下的可复现演示与回归。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"


class MiniMaxError(RuntimeError):
    """模型调用失败（网络、鉴权、限流或响应不可解析）。"""


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    latency_ms: int
    usage: dict
    raw: dict


class Provider:
    name = "base"
    model = ""

    def complete(self, messages: list[dict], *, attempt: int = 1, **kwargs) -> Completion:
        raise NotImplementedError


class MiniMaxProvider(Provider):
    """MiniMax 开放平台（OpenAI 协议兼容）。"""

    name = "minimax"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 24576,
        timeout: float = 600.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.base_url = (base_url or os.environ.get("MINIMAX_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("MINIMAX_MODEL") or DEFAULT_MODEL
        self.temperature = float(os.environ.get("MINIMAX_TEMPERATURE", temperature))
        self.max_tokens = int(os.environ.get("MINIMAX_MAX_TOKENS", max_tokens))
        self.timeout = timeout
        # M3 默认开启 adaptive thinking，会把 40k token 花在推理上且常常截断；
        # 生成补丁这种结构化任务，直接用 thinking=disabled + reasoning_split 更快更稳
        self.thinking = (os.environ.get("MINIMAX_THINKING") or "disabled").strip().lower()
        if not self.api_key:
            raise MiniMaxError(
                "缺少 MINIMAX_API_KEY：请在 .env 或环境变量中配置后重试（也可用 --provider recorded 做离线回放）"
            )

    def complete(self, messages: list[dict], *, attempt: int = 1, **kwargs) -> Completion:
        import httpx

        url = f"{self.base_url}/chat/completions"
        # 推理型模型（M3）会先想再写补丁；被截断时按轮次放大预算，避免三次都撞同一个墙
        budget = min(self.max_tokens * (2 ** max(0, attempt - 1)), 65536)
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": budget,
            "stream": False,
            "reasoning_split": True,
            "thinking": {"type": "adaptive" if self.thinking == "adaptive" else "disabled"},
        }
        started = time.time()
        try:
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                # 推理模型长输出可能跑几分钟；读超时单独放宽，避免"生成到一半被掐断"
                timeout=httpx.Timeout(self.timeout, connect=15.0, read=self.timeout),
            )
        except Exception as exc:  # 网络层
            raise MiniMaxError(f"MiniMax 请求失败：{exc}") from exc
        latency = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            raise MiniMaxError(f"MiniMax HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise MiniMaxError(f"MiniMax 响应不是 JSON：{resp.text[:300]}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise MiniMaxError(f"MiniMax 响应缺少 choices：{json.dumps(data)[:300]}")
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        if isinstance(text, list):  # 部分版本返回 content 数组
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        finish = choices[0].get("finish_reason") or ""
        if finish == "length" and "```diff" not in strip_reasoning(text):
            raise MiniMaxError(
                f"模型输出被截断（finish_reason=length，max_tokens={budget}），未产出完整 diff；"
                "下一轮会自动放大预算，或调小上下文包"
            )
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=latency,
            usage=data.get("usage") or {},
            raw={**data, "finish_reason": finish, "reasoning": message.get("reasoning_content") or ""},
        )


class RecordedProvider(Provider):
    """回放归档响应：artifacts/recorded/<task_id>/response_<attempt>.md。"""

    name = "recorded"

    def __init__(self, task_id: str, root: str | Path | None = None, model: str = "recorded:MiniMax-M3") -> None:
        self.dir = Path(root or Path(__file__).resolve().parent.parent) / "artifacts" / "recorded" / task_id
        self.task_id = task_id
        self.model = model

    def complete(self, messages: list[dict], *, attempt: int = 1, **kwargs) -> Completion:
        for name in (f"response_{attempt}.md", f"response_{attempt}.diff"):
            path = self.dir / name
            if path.exists():
                return Completion(
                    text=path.read_text(encoding="utf-8"),
                    provider=f"{self.name}({name})",
                    model=self.model,
                    latency_ms=0,
                    usage={"prompt_tokens": None, "completion_tokens": None},
                    raw={"recorded_from": str(path)},
                )
        raise MiniMaxError(
            f"未找到回放响应：{self.dir}/response_{attempt}.md；请先用 --provider minimax 真实调用，"
            "或补充归档样例"
        )


def get_provider(name: str, task_id: str) -> Provider:
    name = (name or os.environ.get("CTX_PROVIDER") or "minimax").lower()
    if name == "recorded":
        return RecordedProvider(task_id)
    if name in ("minimax", "auto"):
        return MiniMaxProvider()
    raise ValueError(f"unknown provider: {name}")


# --------------------------------------------------------------------------- 留痕


def prompt_fingerprint(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def record_call(path: str | Path, *, task_id: str, attempt: int, messages: list[dict], completion: Completion) -> dict:
    """把一次模型调用写入 JSONL 留痕文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "task_id": task_id,
        "attempt": attempt,
        "provider": completion.provider,
        "model": completion.model,
        "prompt_sha256_16": prompt_fingerprint(messages),
        "system_prompt": messages[0]["content"] if messages else "",
        "user_prompt": _clip(messages[1]["content"] if len(messages) > 1 else "", 20000),
        "response": completion.text,
        "usage": completion.usage,
        "latency_ms": completion.latency_ms,
    }
    if "recorded_from" in completion.raw:
        entry["recorded_from"] = completion.raw["recorded_from"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _clip(text: str, limit: int) -> str:
    """留痕里的提示词截断（完整版在 run 目录的 prompt_attempt*.md）。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（截断，共 {len(text)} 字符；完整内容见 run 目录 prompt 文件）"


def strip_reasoning(text: str) -> str:
    """剥掉模型输出的推理块（MiniMax-M3 等模型会输出 <think>…</think>）。

    推理块里也可能出现 ```diff 片段，若不剥离会抽到"设想中的补丁"而不是最终答案。
    """
    out = text
    for tag in ("think", "thinking", "reasoning"):
        while True:
            start = out.find(f"<{tag}>")
            end = out.find(f"</{tag}>", start + 1)
            if start < 0 or end < 0:
                break
            out = out[:start] + out[end + len(tag) + 3 :]
    return out.strip()


def extract_diff(text: str) -> str:
    """从模型回复中抽取 unified diff（先剥离推理块，避免抽到草稿补丁）。"""
    text = strip_reasoning(text)
    if "```diff" in text:
        body = text.split("```diff", 1)[1]
        body = body.split("```", 1)[0]
        return body.strip() + "\n"
    if "```patch" in text:
        body = text.split("```patch", 1)[1].split("```", 1)[0]
        return body.strip() + "\n"
    idx = text.find("diff --git")
    if idx >= 0:
        return text[idx:].strip() + "\n"
    raise MiniMaxError("模型回复中未找到 unified diff（缺少 ```diff 代码块或 diff --git）")


FILE_BLOCK_RE = None


def extract_files(text: str) -> dict[str, str]:
    """从回复中抽取"整文件"模式的内容块。

    期望格式（每个文件一块）::

        ```file path=src/settlement/engine.py
        <完整文件内容>
        ```
    """
    import re

    pattern = re.compile(r"```file\s+path=([^\s`]+)\s*\n(.*?)\n```", re.DOTALL)
    files = {path.strip(): body for path, body in pattern.findall(strip_reasoning(text))}
    if not files:
        raise MiniMaxError("回复中未找到 ```file path=... 代码块")
    return files
