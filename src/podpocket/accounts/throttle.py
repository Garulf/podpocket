import time
from collections.abc import Callable
from dataclasses import dataclass

from podpocket.accounts import passwords
from podpocket.accounts.users import Sessionmaker, authenticate, normalize_login
from podpocket.models import User

LOGIN_WAIT_SECONDS = 2.0
MAX_FAILURES = 5
LOCKOUT_SECONDS = 30.0
FAILURE_MEMORY_SECONDS = 600.0
MAX_TRACKED_LOGINS = 10_000
MAX_KEY_LENGTH = 320


class LoginRefused(Exception):
    pass


class LoginBusy(LoginRefused):
    pass


class LoginLocked(LoginRefused):
    pass


@dataclass
class _Failures:
    count: int
    last_at: float


class LoginGate:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, _Failures] = {}

    async def authenticate(
        self, sessionmaker: Sessionmaker, login: str, password: str
    ) -> User | None:
        key = normalize_login(login)[:MAX_KEY_LENGTH]
        try:
            async with passwords.hashing_slot(LOGIN_WAIT_SECONDS):
                if self._count(key) >= MAX_FAILURES:
                    await passwords.spend_verification_time("")
                    raise LoginLocked
                user = await authenticate(sessionmaker, login, password)
        except passwords.HashingBusy as exc:
            raise LoginBusy from exc
        if user is None:
            self._record_failure(key)
        else:
            self._failures.pop(key, None)
        return user

    def _count(self, key: str) -> int:
        entry = self._failures.get(key)
        if entry is None:
            return 0
        window = LOCKOUT_SECONDS if entry.count >= MAX_FAILURES else FAILURE_MEMORY_SECONDS
        if self._clock() - entry.last_at >= window:
            del self._failures[key]
            return 0
        return entry.count

    def _record_failure(self, key: str) -> None:
        count = self._count(key) + 1
        self._failures.pop(key, None)
        self._failures[key] = _Failures(count, self._clock())
        while len(self._failures) > MAX_TRACKED_LOGINS:
            del self._failures[next(iter(self._failures))]
