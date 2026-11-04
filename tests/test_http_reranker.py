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

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"version-{rank}",
        text=f"candidate {rank}",
        score=1.0 / rank,
        matched_by=("fts",),
    )


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_batches_once_per_candidate_and_orders_scores_globally() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "question"
        assert 1 <= len(body["candidates"]) <= 2
        bodies.append(body)
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": {1: 0.2, 2: 0.9, 3: 0.9}[item["rank"]]}
                    for item in reversed(body["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=_transport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)
            decision = await reranker.rerank("question", tuple(_hit(rank) for rank in range(1, 4)), 2)

        assert decision.selected_ranks == (2, 3)
        assert decision.usage.requests == 2
        assert sorted(item["rank"] for body in bodies for item in body["candidates"]) == [1, 2, 3]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [], "extra": True},
        {"scores": [{"rank": 2, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 1, "score": 0.4}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 2, "score": 0.4}]},
        {"scores": [{"rank": 1, "score": float("nan")}]},
        {"scores": [{"rank": 1, "score": float("inf")}]},
        {"scores": [{"rank": 1, "score": 0.5, "reason": "extra"}]},
        {"scores": "not-a-list"},
    ],
)
def test_rejects_invalid_responses(payload) -> None:
    async def scenario() -> None:
        transport = _transport(lambda _request: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_invalid_arguments() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=_transport(lambda _request: httpx.Response(200))) as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://invalid", client=client, batch_size=0)
            reranker = HTTPMemoryReranker("https://invalid", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("q", (), 1)
            with pytest.raises(ValueError):
                await reranker.rerank("q", (_hit(1),), 0)
            with pytest.raises(ValueError):
                await reranker.rerank("q", (_hit(1),), 2)

    asyncio.run(scenario())


def test_preserves_transport_and_status_errors() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=_transport(offline)) as client:
            with pytest.raises(httpx.ConnectError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

        failing = _transport(lambda _request: httpx.Response(503))
        async with httpx.AsyncClient(transport=failing) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await HTTPMemoryReranker("https://invalid", client=client).rerank("q", (_hit(1),), 1)

    asyncio.run(scenario())


def test_reranker_drives_native_memory_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"scores": [{"rank": item["rank"], "score": float(item["rank"])} for item in body["candidates"]]},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=_transport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=1)
            async with open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig()), memory_reranker=reranker
            ) as contexts:
                service = (await contexts.get("http-reranker-native")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=(
                        MemoryEntryInput(kind="fact", text="Project alpha fact."),
                        MemoryEntryInput(kind="fact", text="Project beta fact."),
                    ),
                    mode="append",
                )
                assert memory is not None
                result = await service.search("project", memories=(memory,), limit=1, mode="fts")

        assert result.rerank is not None
        assert result.rerank.policy_id == "http-cross-encoder-v1"
        assert result.hits[0].entry_version_id == result.rerank.candidate_hits[-1].entry_version_id
        assert result.rerank.usage.requests == 2

    asyncio.run(scenario())
