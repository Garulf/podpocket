import asyncio

import pytest
from sqlalchemy import select

from podpocket.models import ChangeLog, NamedSetting, User
from podpocket.sync.writer import (
    EPISODE,
    FOLDER,
    ChangeWriter,
    changed_since,
    load_by_keys,
    lock_user,
    wins,
)


async def last_change(services, user_uuid):
    async with services.sessionmaker() as session:
        return (await session.get(User, user_uuid)).last_change_ms


async def write(services, user_uuid, now_ms, *entries):
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user_uuid, source="app", now_ms=now_ms)
        for entity, entity_id, field, modified in entries:
            writer.record(entity, entity_id, field, modified)
        await session.commit()
        return writer.cursor


async def test_changes_share_one_stamp_per_writer(services, alice):
    before = await last_change(services, alice.uuid)

    cursor = await write(
        services,
        alice.uuid,
        before + 50,
        (EPISODE, "e1", "starred", 1),
        (EPISODE, "e2", "starred", 2),
    )

    assert cursor == before + 50
    async with services.sessionmaker() as session:
        rows = (await session.execute(select(ChangeLog.server_ms, ChangeLog.source))).all()
    assert rows == [(before + 50, "app"), (before + 50, "app")]


async def test_stamps_strictly_increase_even_when_the_clock_does_not(services, alice):
    before = await last_change(services, alice.uuid)

    first = await write(services, alice.uuid, 5, (EPISODE, "e", "starred", 1))
    second = await write(services, alice.uuid, 5, (EPISODE, "e", "starred", 2))

    assert (first, second) == (before + 1, before + 2)


async def test_concurrent_writers_get_distinct_increasing_stamps(services, alice):
    writers = 20
    before = await last_change(services, alice.uuid)

    async def record_one(index):
        return await write(services, alice.uuid, 1, (EPISODE, f"e{index}", "starred", 1))

    cursors = await asyncio.gather(*(record_one(index) for index in range(writers)))

    assert sorted(cursors) == list(range(before + 1, before + 1 + writers))
    assert await last_change(services, alice.uuid) == max(cursors)
    async with services.sessionmaker() as session:
        stamps = (await session.scalars(select(ChangeLog.server_ms))).all()
    assert sorted(stamps) == sorted(cursors)


async def test_writer_without_changes_keeps_the_cursor(services, alice):
    before = await last_change(services, alice.uuid)

    assert await write(services, alice.uuid, before + 1000) == before
    assert await last_change(services, alice.uuid) == before


async def test_changed_since_returns_only_this_users_later_changes(services, alice, bob):
    old = await write(services, alice.uuid, 1, (EPISODE, "old", "starred", 1))
    await write(services, alice.uuid, 1, (EPISODE, "new", "starred", 1), (FOLDER, "f", "name", 1))
    await write(services, bob.uuid, 1, (EPISODE, "bobs", "starred", 1))

    async with services.sessionmaker() as session:
        changed = await changed_since(session, alice.uuid, old)

    assert changed == {EPISODE: {"new"}, FOLDER: {"f"}}


async def test_lock_user_rejects_unknown_users(services):
    async with services.sessionmaker() as session:
        with pytest.raises(LookupError):
            await lock_user(session, "ghost")


async def test_load_by_keys_chunks_and_stays_inside_one_user(services, alice, bob):
    names = [f"setting-{index}" for index in range(1200)]
    async with services.sessionmaker() as session:
        session.add_all(
            NamedSetting(user_uuid=alice.uuid, name=name, value=1, modified_ms=1) for name in names
        )
        session.add(NamedSetting(user_uuid=bob.uuid, name="setting-0", value=2, modified_ms=1))
        await session.commit()

        rows = await load_by_keys(session, NamedSetting, NamedSetting.name, alice.uuid, names)

    assert len(rows) == 1200
    assert {row.user_uuid for row in rows} == {alice.uuid}


def test_wins_prefers_newer_and_breaks_ties_by_value():
    assert wins(2, "a", 1, "b")
    assert not wins(1, "b", 2, "a")
    assert wins(5, True, None, None)
    assert wins(5, "b", 5, "a")
    assert not wins(5, "a", 5, "b")
    assert not wins(5, "a", 5, "a")
