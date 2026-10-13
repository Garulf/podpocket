import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

logger = logging.getLogger(__name__)

FORGET_AFTER_SECONDS = 3600


@dataclass(frozen=True)
class ImportBatch:
    uuids: list[str]
    poll_uuids: list[str]
    failed: int


class OpmlImports:
    def __init__(
        self,
        resolve: Callable[[str], Awaitable[str]],
        wait_seconds: float = 20.0,
        concurrency: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._resolve = resolve
        self._wait_seconds = wait_seconds
        self._limit = asyncio.Semaphore(concurrency)
        self._clock = clock
        self._jobs: dict[str, tuple[float, asyncio.Task[str]]] = {}

    async def start(self, urls: list[str]) -> ImportBatch:
        self._forget_stale()
        tokens = []
        for url in urls:
            token = str(uuid4())
            self._jobs[token] = (self._clock(), asyncio.create_task(self._limited(url)))
            tokens.append(token)
        return await self.poll(tokens)

    async def poll(self, tokens: list[str]) -> ImportBatch:
        known = [token for token in tokens if token in self._jobs]
        pending = [self._jobs[token][1] for token in known if not self._jobs[token][1].done()]
        if pending:
            await asyncio.wait(pending, timeout=self._wait_seconds)
        uuids: list[str] = []
        waiting: list[str] = []
        failed = len(tokens) - len(known)
        for token in known:
            task = self._jobs[token][1]
            if not task.done():
                waiting.append(token)
                continue
            del self._jobs[token]
            if task.cancelled() or task.exception() is not None:
                failed += 1
            else:
                uuids.append(task.result())
        return ImportBatch(uuids=uuids, poll_uuids=waiting, failed=failed)

    async def _limited(self, url: str) -> str:
        async with self._limit:
            try:
                return await self._resolve(url)
            except Exception as exc:
                logger.info("OPML import of %s failed: %s", url, exc)
                raise

    def _forget_stale(self) -> None:
        cutoff = self._clock() - FORGET_AFTER_SECONDS
        for token, (created, task) in list(self._jobs.items()):
            if created < cutoff and task.done():
                if not task.cancelled():
                    task.exception()
                del self._jobs[token]
