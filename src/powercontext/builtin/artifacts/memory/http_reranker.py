# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP cross-encoder reranking for coarse Memory search candidates."""

from __future__ import annotations

import math
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_RERANK_POLICY_ID: Final = "http-cross-encoder-v1"
DEFAULT_BATCH_SIZE: Final = 32


class _Score(BaseModel):
    """One scored candidate identified by its original coarse rank."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: StrictInt
    score: float


class _Scores(BaseModel):
    """The complete score set for one batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[_Score, ...]


class HTTPMemoryReranker:
    """Score Memory candidates through one HTTP cross-encoder endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_BATCH_SIZE,
        policy_id: str = DEFAULT_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")  # noqa: TRY003
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty URL")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once and return the best ranks in global order."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            payload = {
                "query": query,
                "candidates": [
                    {"rank": start + offset + 1, "text": candidate.text}
                    for offset, candidate in enumerate(batch)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            expected = set(range(start + 1, start + len(batch) + 1))
            scores.update(_validated_scores(response, expected))

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in ranked[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _validated_scores(response: httpx.Response, expected: set[int]) -> dict[int, float]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("rerank response must be valid JSON") from error
    try:
        parsed = _Scores.model_validate(payload)
    except ValidationError as error:
        raise ValueError("rerank response must match the scoring schema") from error

    scores: dict[int, float] = {}
    for item in parsed.scores:
        if not math.isfinite(item.score):
            raise ValueError("rerank scores must be finite numbers")  # noqa: TRY003
        if item.rank in scores:
            raise ValueError(f"rerank response repeats rank {item.rank}")  # noqa: TRY003
        scores[item.rank] = item.score
    if set(scores) != expected:
        raise ValueError("rerank response must cover every candidate rank exactly once")  # noqa: TRY003
    return scores


__all__ = ["DEFAULT_BATCH_SIZE", "DEFAULT_RERANK_POLICY_ID", "HTTPMemoryReranker"]
