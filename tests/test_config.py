import pytest
from pydantic import ValidationError

from podpocket.config import DatabaseSettings, Settings

SECRET = "s" * 32


def test_secret_key_is_required(monkeypatch):
    monkeypatch.delenv("PODPOCKET_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="secret_key"):
        Settings(public_url="http://pod.test")


def test_short_secret_key_is_rejected():
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(public_url="http://pod.test", secret_key="too-short")


def test_secret_key_is_hidden_from_repr():
    settings = Settings(public_url="http://pod.test", secret_key=SECRET)

    assert SECRET not in repr(settings)
    assert settings.secret_key.get_secret_value() == SECRET
    assert settings.access_token_minutes == 60


def test_database_settings_need_only_the_database_url(monkeypatch):
    monkeypatch.delenv("PODPOCKET_PUBLIC_URL", raising=False)
    monkeypatch.delenv("PODPOCKET_SECRET_KEY", raising=False)
    monkeypatch.setenv("PODPOCKET_DATABASE_URL", "sqlite+aiosqlite:///x.db")

    assert DatabaseSettings().database_url == "sqlite+aiosqlite:///x.db"


@pytest.mark.parametrize("minutes", [0, -5, 1441])
def test_access_token_lifetime_must_be_between_a_minute_and_a_day(minutes):
    with pytest.raises(ValidationError, match="access_token_minutes"):
        Settings(public_url="http://pod.test", secret_key=SECRET, access_token_minutes=minutes)


@pytest.mark.parametrize("minutes", [1, 1440])
def test_access_token_lifetime_accepts_its_bounds(minutes):
    settings = Settings(
        public_url="http://pod.test", secret_key=SECRET, access_token_minutes=minutes
    )

    assert settings.access_token_minutes == minutes
