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

"""Coalesce concurrent embedding work and reuse previously computed vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

_DEFAULT_MAX_BATCH_SIZE: Final = 64
_DEFAULT_COALESCE_MS: Final = 5.0
_DEFAULT_CACHE_SIZE: Final = 1024
_CACHED_BATCH: Final = -1


@dataclass(frozen=True, slots=True)
class _Embedded:
    """One vector together with the batch that produced it."""

    vector: tuple[float, ...]
    batch: int
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    """Share one provider request across every concurrent caller asking for the same text."""

    def __init__(
        self,
        model,
        *,
        max_batch_size: int = _DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = _DEFAULT_COALESCE_MS,
        cache_size: int = _DEFAULT_CACHE_SIZE,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(coalesce_ms, (int, float)) or isinstance(coalesce_ms, bool) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_ms = float(coalesce_ms)
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Embedded]] = {}
        self._queue: dict[str, None] = {}
        self._timer: asyncio.Handle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._next_batch = 0

    @property
    def profile(self):
        return self._model.profile

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult:
        """Embed every input once, preserving order and duplicate positions."""

        ordered = tuple(texts)
        results: dict[str, _Embedded] = {}
        waiters: list[tuple[str, asyncio.Future[_Embedded]]] = []
        for text in dict.fromkeys(ordered):
            cached = self._cached(text)
            if cached is not None:
                results[text] = cached
                continue
            future = self._pending.get(text)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                future.add_done_callback(_retrieve_exception)
                self._pending[text] = future
                self._queue[text] = None
                self._schedule()
            waiters.append((text, future))
        for text, future in waiters:
            results[text] = await asyncio.shield(future)
        return EmbeddingResult(
            vectors=tuple(results[text].vector for text in ordered),
            usage=_sum_usage([results[text] for text in dict.fromkeys(ordered)]),
        )

    def _cached(self, text: str) -> _Embedded | None:
        if not self._cache_size or text not in self._cache:
            return None
        self._cache.move_to_end(text)
        return _Embedded(self._cache[text], _CACHED_BATCH, InferenceUsage(requests=0))

    def _schedule(self) -> None:
        if self._timer is not None:
            return
        loop = asyncio.get_running_loop()
        if self._coalesce_ms > 0:
            self._timer = loop.call_later(self._coalesce_ms / 1000.0, self._dispatch)
        else:
            self._timer = loop.call_soon(self._dispatch)

    def _dispatch(self) -> None:
        self._timer = None
        if not self._queue:
            return
        texts: list[str] = []
        for text in list(self._queue):
            if len(texts) == self._max_batch_size:
                break
            del self._queue[text]
            texts.append(text)
        if not texts:
            return
        task = asyncio.get_running_loop().create_task(self._run_batch(tuple(texts)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if self._queue:
            self._schedule()

    async def _run_batch(self, texts: tuple[str, ...]) -> None:
        batch = self._next_batch
        self._next_batch += 1
        try:
            result = await self._model.embed(texts)
        except BaseException as error:  # noqa: BLE001 - every waiter must observe the failure
            self._fail(texts, error)
            return
        vectors = tuple(result.vectors)
        if len(vectors) != len(texts):
            self._fail(texts, ValueError("embedding vector count does not match the batch size"))
            return
        for text, vector in zip(texts, vectors):
            if self._cache_size:
                self._cache[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            future = self._pending.pop(text, None)
            if future is not None and not future.done():
                future.set_result(_Embedded(vector, batch, result.usage))

    def _fail(self, texts: tuple[str, ...], error: BaseException) -> None:
        for text in texts:
            future = self._pending.pop(text, None)
            if future is None or future.done():
                continue
            if isinstance(error, asyncio.CancelledError):
                future.cancel()
            else:
                future.set_exception(error)


def _sum_usage(items: list[_Embedded]) -> InferenceUsage:
    batches: set[int] = set()
    requests = input_tokens = output_tokens = 0
    has_tokens = has_output = False
    for item in items:
        if item.batch == _CACHED_BATCH or item.batch in batches:
            continue
        batches.add(item.batch)
        requests += item.usage.requests
        if item.usage.input_tokens is not None:
            input_tokens += item.usage.input_tokens
            has_tokens = True
        if item.usage.output_tokens is not None:
            output_tokens += item.usage.output_tokens
            has_output = True
    return InferenceUsage(
        requests=requests,
        input_tokens=input_tokens if has_tokens else None,
        output_tokens=output_tokens if has_output else None,
    )


def _retrieve_exception(future: asyncio.Future[_Embedded]) -> None:
    if future.cancelled():
        return
    future.exception()


__all__ = ["CoalescingEmbeddingModel"]
