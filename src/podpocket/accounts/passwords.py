import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher()

_DUMMY_PASSWORD = "dummy password for unknown users"
_dummy_hash: tuple[PasswordHasher, str] | None = None


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(HASHER.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify, password_hash, password)


async def spend_verification_time(password: str) -> None:
    global _dummy_hash
    hasher = HASHER
    if _dummy_hash is None or _dummy_hash[0] is not hasher:
        _dummy_hash = (hasher, await asyncio.to_thread(hasher.hash, _DUMMY_PASSWORD))
    await verify_password(_dummy_hash[1], password)


def needs_rehash(password_hash: str) -> bool:
    return HASHER.check_needs_rehash(password_hash)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
