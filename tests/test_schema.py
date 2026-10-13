from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from podpocket.models import (
    Bookmark,
    ChangeLog,
    DeviceStats,
    Folder,
    HistoryEntry,
    NamedSetting,
    Playlist,
    RefreshToken,
    Subscription,
    UpNextItem,
    User,
    UserEpisodeState,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
OWNED = [
    RefreshToken,
    Subscription,
    Folder,
    UserEpisodeState,
    UpNextItem,
    Playlist,
    Bookmark,
    HistoryEntry,
    NamedSetting,
    DeviceStats,
    ChangeLog,
]


def owned_rows(user_uuid):
    return [
        RefreshToken(
            id="s1",
            user_uuid=user_uuid,
            scope="mobile",
            created_at=NOW,
        ),
        Subscription(user_uuid=user_uuid, podcast_uuid="p", subscribed=True),
        Folder(user_uuid=user_uuid, folder_uuid="f", date_added=NOW),
        UserEpisodeState(
            user_uuid=user_uuid,
            episode_uuid="e",
            podcast_uuid="p",
            starred=True,
            starred_modified=1,
        ),
        UpNextItem(user_uuid=user_uuid, position=0, episode_uuid="e"),
        Playlist(user_uuid=user_uuid, uuid="pl"),
        Bookmark(user_uuid=user_uuid, bookmark_uuid="b", created_at=NOW),
        HistoryEntry(
            user_uuid=user_uuid, episode_uuid="e", action=1, modified_ms=1, server_modified_ms=1
        ),
        NamedSetting(user_uuid=user_uuid, name="skipForward", value=30, modified_ms=1),
        DeviceStats(user_uuid=user_uuid, device_id="phone"),
        ChangeLog(
            user_uuid=user_uuid,
            entity="episode",
            entity_id="e",
            field="starred",
            modified_ms=1,
            server_ms=2,
            source="app",
        ),
    ]


async def test_removing_a_user_removes_everything_they_own(services):
    async with services.sessionmaker() as session:
        session.add(User(uuid="u1", username="alice", password_hash="x", created_at=NOW))
        await session.flush()
        session.add_all(owned_rows("u1"))
        await session.commit()

        await session.delete(await session.get(User, "u1"))
        await session.commit()

        for model in OWNED:
            assert (await session.scalars(select(model))).all() == [], model.__tablename__


async def test_usernames_are_unique(services):
    async with services.sessionmaker() as session:
        session.add(User(uuid="u1", username="alice", password_hash="x", created_at=NOW))
        await session.commit()
        session.add(User(uuid="u2", username="alice", password_hash="y", created_at=NOW))

        with pytest.raises(IntegrityError):
            await session.commit()
