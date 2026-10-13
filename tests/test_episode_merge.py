import dataclasses
import itertools

from sqlalchemy import select

from podpocket.models import ChangeLog, UserEpisodeState
from podpocket.sync.episodes import EpisodeChange, FieldUpdate, apply_episode_changes
from podpocket.sync.writer import ChangeWriter


async def apply(services, user_uuid, *changes, source="app"):
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user_uuid, source=source, now_ms=1)
        changed = await apply_episode_changes(writer, changes)
        await session.commit()
    return changed


async def load(services, user_uuid, episode_uuid):
    async with services.sessionmaker() as session:
        return await session.get(UserEpisodeState, (user_uuid, episode_uuid))


async def test_newer_field_wins_and_older_is_ignored(services, alice):
    await apply(services, alice.uuid, EpisodeChange("e", "p", played_up_to=FieldUpdate(100, 2000)))

    changed = await apply(
        services, alice.uuid, EpisodeChange("e", "p", played_up_to=FieldUpdate(50, 1000))
    )
    assert changed == set()
    assert (await load(services, alice.uuid, "e")).played_up_to == 100

    await apply(services, alice.uuid, EpisodeChange("e", "p", played_up_to=FieldUpdate(300, 3000)))
    assert (await load(services, alice.uuid, "e")).played_up_to == 300


async def test_fields_merge_independently(services, alice):
    await apply(
        services,
        alice.uuid,
        EpisodeChange(
            "e", "p", starred=FieldUpdate(True, 5000), played_up_to=FieldUpdate(10, 1000)
        ),
    )
    await apply(
        services,
        alice.uuid,
        EpisodeChange(
            "e", "p", starred=FieldUpdate(False, 4000), played_up_to=FieldUpdate(20, 2000)
        ),
    )

    state = await load(services, alice.uuid, "e")
    assert (state.starred, state.starred_modified) == (True, 5000)
    assert (state.played_up_to, state.played_up_to_modified) == (20, 2000)


async def test_accepted_changes_are_logged_with_their_source(services, alice):
    change = EpisodeChange(
        "e", "p", playing_status=FieldUpdate(3, 7000), archived=FieldUpdate(True, 7000)
    )

    await apply(services, alice.uuid, change, source="gpodder")

    async with services.sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    ChangeLog.entity,
                    ChangeLog.entity_id,
                    ChangeLog.field,
                    ChangeLog.modified_ms,
                    ChangeLog.source,
                ).order_by(ChangeLog.field)
            )
        ).all()
    assert rows == [
        ("episode", "e", "archived", 7000, "gpodder"),
        ("episode", "e", "playing_status", 7000, "gpodder"),
    ]


async def test_invalid_values_are_ignored(services, alice):
    change = EpisodeChange(
        "e",
        "p",
        playing_status=FieldUpdate(9, 1),
        duration=FieldUpdate(0, 1),
        played_up_to=FieldUpdate(-5, 1),
    )

    assert await apply(services, alice.uuid, change) == set()


async def test_merge_is_independent_of_arrival_order(services, alice):
    changes = [
        EpisodeChange(
            "x", "p", played_up_to=FieldUpdate(100, 1000), starred=FieldUpdate(True, 1000)
        ),
        EpisodeChange(
            "x", "p", played_up_to=FieldUpdate(200, 1000), starred=FieldUpdate(False, 2000)
        ),
        EpisodeChange(
            "x",
            "p",
            played_up_to=FieldUpdate(50, 900),
            deselected_chapters=FieldUpdate("1,2", 1500),
        ),
        EpisodeChange(
            "x", "p", playing_status=FieldUpdate(3, 1200), deselected_chapters=FieldUpdate("", 1500)
        ),
    ]
    outcomes = set()

    for index, order in enumerate(itertools.permutations(changes)):
        episode = f"ep-{index}"
        for change in order:
            await apply(services, alice.uuid, dataclasses.replace(change, episode_uuid=episode))
        state = await load(services, alice.uuid, episode)
        outcomes.add(
            (
                state.played_up_to,
                state.played_up_to_modified,
                state.starred,
                state.playing_status,
                state.deselected_chapters,
            )
        )

    assert outcomes == {(200, 1000, False, 3, "1,2")}
