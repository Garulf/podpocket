import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(HASHER.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify, password_hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
