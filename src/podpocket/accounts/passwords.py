import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher()

DEFAULT_CONCURRENCY = 4
_DUMMY_PASSWORD = "dummy password for unknown users"
_dummy_hash: tuple[PasswordHasher, str] | None = None


class HashingBusy(Exception):
    pass


class _Capacity:
    def __init__(self, size: int):
        self.executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="argon2")
        self.slots = asyncio.Semaphore(size)


_capacity = _Capacity(DEFAULT_CONCURRENCY)


def configure(max_concurrent: int) -> None:
    global _capacity
    previous, _capacity = _capacity, _Capacity(max_concurrent)
    previous.executor.shutdown(wait=False)


@asynccontextmanager
async def hashing_slot(wait_seconds: float) -> AsyncIterator[None]:
    slots = _capacity.slots
    try:
        await asyncio.wait_for(slots.acquire(), wait_seconds)
    except TimeoutError as exc:
        raise HashingBusy from exc
    try:
        yield
    finally:
        slots.release()


async def _in_hashing_thread[**P, R](function: Callable[P, R], *args: P.args) -> R:
    return await asyncio.get_running_loop().run_in_executor(_capacity.executor, function, *args)


async def hash_password(password: str) -> str:
    return await _in_hashing_thread(HASHER.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await _in_hashing_thread(_verify, password_hash, password)


async def spend_verification_time(password: str) -> None:
    global _dummy_hash
    hasher = HASHER
    if _dummy_hash is None or _dummy_hash[0] is not hasher:
        _dummy_hash = (hasher, await _in_hashing_thread(hasher.hash, _DUMMY_PASSWORD))
    await verify_password(_dummy_hash[1], password)


def needs_rehash(password_hash: str) -> bool:
    return HASHER.check_needs_rehash(password_hash)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
