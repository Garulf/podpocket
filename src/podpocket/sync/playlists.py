from collections.abc import Iterable
from typing import Any

from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value, StringValue

from podpocket.models import Playlist
from podpocket.protocol import from_timestamp, to_timestamp, wrapped
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.writer import PLAYLIST, ChangeWriter, load_by_keys
from podpocket.timefmt import iso_ms, parse_iso

BOOL_FIELDS = (
    "all_podcasts",
    "not_downloaded",
    "downloaded",
    "finished",
    "partially_played",
    "unplayed",
    "starred",
    "manual",
    "filter_duration",
    "show_archived",
)
INT_FIELDS = (
    "audio_video",
    "sort_position",
    "sort_type",
    "icon_id",
    "filter_hours",
    "longer_than",
    "shorter_than",
)
TEXT_FIELDS = ("title", "podcast_uuids")
EPISODE_TEXT_FIELDS = ("title", "url", "podcast_slug", "episode_slug")


async def apply_playlist_records(
    writer: ChangeWriter, records: Iterable[pb.SyncUserPlaylist]
) -> set[str]:
    batch = [(key, record) for record in records if (key := record.original_uuid or record.uuid)]
    playlists: dict[str, Playlist] = {
        playlist.uuid: playlist
        for playlist in await load_by_keys(
            writer.session,
            Playlist,
            Playlist.uuid,
            writer.user_uuid,
            (key for key, _ in batch),
        )
    }
    changed: set[str] = set()
    for key, record in batch:
        playlist = playlists.get(key)
        values = _incoming(record, playlist)
        if playlist is None:
            if not values:
                continue
            playlist = Playlist(
                user_uuid=writer.user_uuid,
                uuid=key,
                is_deleted=False,
                title="",
                podcast_uuids="",
                episode_order=[],
                episodes=[],
            )
            writer.session.add(playlist)
            playlists[key] = playlist
            writer.record(PLAYLIST, key, "created", writer.now_ms)
            changed.add(key)
        for field, value in values.items():
            if getattr(playlist, field) != value:
                setattr(playlist, field, value)
                writer.record(PLAYLIST, key, field, writer.now_ms)
                changed.add(key)
    return changed


def _incoming(record: pb.SyncUserPlaylist, playlist: Playlist | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in ("is_deleted", *TEXT_FIELDS, *BOOL_FIELDS, *INT_FIELDS):
        value = wrapped(record, field)
        if value is not None:
            values[field] = value
    stored_manual = playlist.manual if playlist else None
    if values.get("manual", stored_manual):
        order = list(record.episode_order)
        stored_episodes = (playlist.episodes if playlist else None) or []
        values["episode_order"] = order
        values["episodes"] = _merge_episodes(stored_episodes, record.episodes, order)
    return values


def _merge_episodes(
    stored: list[dict[str, Any]], incoming: Iterable[pb.SyncPlaylistEpisode], order: list[str]
) -> list[dict[str, Any]]:
    by_uuid = {episode["episode"]: episode for episode in stored}
    for episode in incoming:
        if episode.episode:
            by_uuid[episode.episode] = _episode_dict(episode)
    return [by_uuid[uuid] for uuid in order if uuid in by_uuid]


def _episode_dict(episode: pb.SyncPlaylistEpisode) -> dict[str, Any]:
    published = from_timestamp(episode.published) if episode.HasField("published") else None
    return {
        "episode": episode.episode,
        "podcast": episode.podcast,
        "added": wrapped(episode, "added"),
        "published": iso_ms(published) if published is not None else None,
        **{field: wrapped(episode, field) for field in EPISODE_TEXT_FIELDS},
    }


def _playlist_episode(stored: dict[str, Any]) -> pb.SyncPlaylistEpisode:
    episode = pb.SyncPlaylistEpisode(episode=stored["episode"], podcast=stored["podcast"])
    if stored.get("added") is not None:
        episode.added.CopyFrom(Int64Value(value=stored["added"]))
    published = parse_iso(stored.get("published"))
    if published is not None:
        episode.published.CopyFrom(to_timestamp(published))
    for field in EPISODE_TEXT_FIELDS:
        if stored.get(field) is not None:
            getattr(episode, field).CopyFrom(StringValue(value=stored[field]))
    return episode


def _shared_fields(playlist: Playlist) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "uuid": playlist.uuid,
        "original_uuid": playlist.uuid,
        "is_deleted": BoolValue(value=playlist.is_deleted),
        "episode_order": list(playlist.episode_order or []),
        "episodes": [_playlist_episode(episode) for episode in playlist.episodes or []],
    }
    for field in BOOL_FIELDS:
        value = getattr(playlist, field)
        if value is not None:
            fields[field] = BoolValue(value=value)
    for field in INT_FIELDS:
        value = getattr(playlist, field)
        if value is not None:
            fields[field] = Int32Value(value=value)
    return fields


def playlist_record(playlist: Playlist) -> pb.SyncUserPlaylist:
    return pb.SyncUserPlaylist(
        title=StringValue(value=playlist.title),
        podcast_uuids=StringValue(value=playlist.podcast_uuids),
        **_shared_fields(playlist),
    )


def playlist_list_entry(playlist: Playlist) -> pb.PlaylistSyncResponse:
    return pb.PlaylistSyncResponse(
        title=playlist.title, podcast_uuids=playlist.podcast_uuids, **_shared_fields(playlist)
    )
