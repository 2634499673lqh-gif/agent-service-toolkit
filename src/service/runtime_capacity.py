"""Per-process capacity bound for expensive agent/runtime work."""

import asyncio
from contextlib import asynccontextmanager

from core.settings import settings


class RuntimeCapacityFull(Exception):
    """No runtime slot is available without waiting."""


class RuntimeCapacity:
    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    @asynccontextmanager
    async def slot(self):
        if not self.try_acquire():
            raise RuntimeCapacityFull
        try:
            yield
        finally:
            self._semaphore.release()

    def try_acquire(self) -> bool:
        # Semaphore has no public nonblocking acquire; its value is protected
        # by the event loop and this check is atomic for synchronous code.
        if self._semaphore._value <= 0:  # noqa: SLF001
            return False
        self._semaphore._value -= 1  # noqa: SLF001
        return True

    def release(self) -> None:
        self._semaphore.release()


runtime_capacity = RuntimeCapacity(settings.RUNTIME_MAX_CONCURRENT)

__all__ = ["RuntimeCapacity", "RuntimeCapacityFull", "runtime_capacity"]
