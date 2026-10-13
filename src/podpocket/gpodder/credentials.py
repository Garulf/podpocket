import hmac
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace

from podpocket.accounts.users import Sessionmaker, normalize_login
from podpocket.models import User

CREDENTIAL_TTL_SECONDS = 300.0
MAX_CACHED_CREDENTIALS = 1024


@dataclass(frozen=True)
class _Verified:
    user_uuid: str
    password_hash: str
    expires_at: float


class CredentialCache:
    """Remembers Basic credentials that already passed argon2, keyed by a keyed hash."""

    def __init__(self, secret_key: str, clock: Callable[[], float] = time.monotonic):
        self._secret = secret_key.encode()
        self._clock = clock
        self._verified: OrderedDict[bytes, _Verified] = OrderedDict()

    def _fingerprint(self, login: str, password: str) -> bytes:
        message = f"{normalize_login(login)}\0{password}".encode()
        return hmac.digest(self._secret, message, "sha256")

    async def lookup(self, sessionmaker: Sessionmaker, login: str, password: str) -> User | None:
        fingerprint = self._fingerprint(login, password)
        entry = self._verified.get(fingerprint)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._verified[fingerprint]
            return None
        async with sessionmaker() as session:
            user = await session.get(User, entry.user_uuid)
        if user is None or not _still_matches(user, entry, login):
            self._verified.pop(fingerprint, None)
            return None
        if self._verified.get(fingerprint) is entry:
            self._verified[fingerprint] = replace(
                entry, expires_at=self._clock() + CREDENTIAL_TTL_SECONDS
            )
            self._verified.move_to_end(fingerprint)
        return user

    def remember(self, login: str, password: str, user: User) -> None:
        fingerprint = self._fingerprint(login, password)
        expires_at = self._clock() + CREDENTIAL_TTL_SECONDS
        self._verified[fingerprint] = _Verified(user.uuid, user.password_hash, expires_at)
        self._verified.move_to_end(fingerprint)
        while len(self._verified) > MAX_CACHED_CREDENTIALS:
            self._verified.popitem(last=False)


def _still_matches(user: User, entry: _Verified, login: str) -> bool:
    return user.password_hash == entry.password_hash and normalize_login(login) in (
        user.username,
        user.email,
    )
