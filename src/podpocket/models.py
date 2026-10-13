from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from podpocket.db import Base, UTCDateTime


class Podcast(Base):
    __tablename__ = "podcasts"

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    feed_url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    website_url: Mapped[str | None] = mapped_column(Text)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(Text, default="")
    show_type: Mapped[str] = mapped_column(String(16), default="episodic")
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    funding_url: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_refresh_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class PodcastAlias(Base):
    __tablename__ = "podcast_aliases"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    podcast_uuid: Mapped[str] = mapped_column(
        ForeignKey("podcasts.uuid", ondelete="CASCADE"), index=True
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (Index("ix_episodes_podcast_published", "podcast_uuid", "published"),)

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    podcast_uuid: Mapped[str] = mapped_column(ForeignKey("podcasts.uuid", ondelete="CASCADE"))
    guid: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    enclosure_url: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(Text, default="")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    published: Mapped[datetime] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    season: Mapped[int | None] = mapped_column(Integer)
    number: Mapped[int | None] = mapped_column(Integer)
    episode_type: Mapped[str] = mapped_column(String(16), default="full")
    show_notes: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(Text)
    chapters_url: Mapped[str | None] = mapped_column(Text)
    transcripts: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)


class User(Base):
    __tablename__ = "users"

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_change_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    up_next_modified_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    up_next_replaced_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    history_modified_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    history_cleared_ms: Mapped[int] = mapped_column(BigInteger, default=0)


def owner_key() -> Mapped[str]:
    return mapped_column(ForeignKey("users.uuid", ondelete="CASCADE"), primary_key=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_uuid: Mapped[str] = mapped_column(ForeignKey("users.uuid", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="mobile")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    rotated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Subscription(Base):
    __tablename__ = "subscriptions"

    user_uuid: Mapped[str] = owner_key()
    podcast_uuid: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    folder_uuid: Mapped[str | None] = mapped_column(Text)
    sort_position: Mapped[int | None] = mapped_column(Integer)
    episodes_sort_order: Mapped[int | None] = mapped_column(Integer)
    auto_start_from: Mapped[int | None] = mapped_column(Integer)
    auto_skip_last: Mapped[int | None] = mapped_column(Integer)
    date_added: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Folder(Base):
    __tablename__ = "folders"

    user_uuid: Mapped[str] = owner_key()
    folder_uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[int] = mapped_column(Integer, default=0)
    sort_position: Mapped[int] = mapped_column(Integer, default=0)
    podcasts_sort_type: Mapped[int] = mapped_column(Integer, default=0)
    date_added: Mapped[datetime] = mapped_column(UTCDateTime)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class UserEpisodeState(Base):
    __tablename__ = "user_episode_state"
    __table_args__ = (Index("ix_user_episode_state_podcast", "user_uuid", "podcast_uuid"),)

    user_uuid: Mapped[str] = owner_key()
    episode_uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    podcast_uuid: Mapped[str] = mapped_column(Text, default="")
    playing_status: Mapped[int | None] = mapped_column(Integer)
    playing_status_modified: Mapped[int | None] = mapped_column(BigInteger)
    played_up_to: Mapped[int | None] = mapped_column(BigInteger)
    played_up_to_modified: Mapped[int | None] = mapped_column(BigInteger)
    duration: Mapped[int | None] = mapped_column(BigInteger)
    duration_modified: Mapped[int | None] = mapped_column(BigInteger)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    archived_modified: Mapped[int | None] = mapped_column(BigInteger)
    starred: Mapped[bool | None] = mapped_column(Boolean)
    starred_modified: Mapped[int | None] = mapped_column(BigInteger)
    deselected_chapters: Mapped[str | None] = mapped_column(Text)
    deselected_chapters_modified: Mapped[int | None] = mapped_column(BigInteger)


class UpNextItem(Base):
    __tablename__ = "up_next_items"

    user_uuid: Mapped[str] = owner_key()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_uuid: Mapped[str] = mapped_column(Text)
    podcast_uuid: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Playlist(Base):
    __tablename__ = "playlists"

    user_uuid: Mapped[str] = owner_key()
    uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(Text, default="")
    podcast_uuids: Mapped[str] = mapped_column(Text, default="")
    all_podcasts: Mapped[bool | None] = mapped_column(Boolean)
    not_downloaded: Mapped[bool | None] = mapped_column(Boolean)
    downloaded: Mapped[bool | None] = mapped_column(Boolean)
    finished: Mapped[bool | None] = mapped_column(Boolean)
    partially_played: Mapped[bool | None] = mapped_column(Boolean)
    unplayed: Mapped[bool | None] = mapped_column(Boolean)
    starred: Mapped[bool | None] = mapped_column(Boolean)
    manual: Mapped[bool | None] = mapped_column(Boolean)
    filter_duration: Mapped[bool | None] = mapped_column(Boolean)
    show_archived: Mapped[bool | None] = mapped_column(Boolean)
    audio_video: Mapped[int | None] = mapped_column(Integer)
    sort_position: Mapped[int | None] = mapped_column(Integer)
    sort_type: Mapped[int | None] = mapped_column(Integer)
    icon_id: Mapped[int | None] = mapped_column(Integer)
    filter_hours: Mapped[int | None] = mapped_column(Integer)
    longer_than: Mapped[int | None] = mapped_column(Integer)
    shorter_than: Mapped[int | None] = mapped_column(Integer)
    episode_order: Mapped[list[str]] = mapped_column(JSON, default=list)
    episodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Bookmark(Base):
    __tablename__ = "bookmarks"

    user_uuid: Mapped[str] = owner_key()
    bookmark_uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    podcast_uuid: Mapped[str] = mapped_column(Text, default="")
    episode_uuid: Mapped[str] = mapped_column(Text, default="")
    time: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str] = mapped_column(Text, default="")
    title_modified: Mapped[int | None] = mapped_column(BigInteger)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted_modified: Mapped[int | None] = mapped_column(BigInteger)
    passage: Mapped[str | None] = mapped_column(Text)
    passage_location: Mapped[int | None] = mapped_column(Integer)
    passage_modified: Mapped[int | None] = mapped_column(BigInteger)
    reference_time: Mapped[int | None] = mapped_column(Integer)
    reference_time_modified: Mapped[int | None] = mapped_column(BigInteger)


class HistoryEntry(Base):
    __tablename__ = "history"
    __table_args__ = (Index("ix_history_user_server_modified", "user_uuid", "server_modified_ms"),)

    user_uuid: Mapped[str] = owner_key()
    episode_uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    podcast_uuid: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published: Mapped[datetime | None] = mapped_column(UTCDateTime)
    action: Mapped[int] = mapped_column(Integer)
    modified_ms: Mapped[int] = mapped_column(BigInteger)
    server_modified_ms: Mapped[int] = mapped_column(BigInteger)


class NamedSetting(Base):
    __tablename__ = "named_settings"

    user_uuid: Mapped[str] = owner_key()
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    modified_ms: Mapped[int] = mapped_column(BigInteger)


class DeviceStats(Base):
    __tablename__ = "device_stats"

    user_uuid: Mapped[str] = owner_key()
    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_type: Mapped[int] = mapped_column(Integer, default=2)
    times_started_at: Mapped[int | None] = mapped_column(BigInteger)
    time_silence_removal: Mapped[int] = mapped_column(BigInteger, default=0)
    time_skipping: Mapped[int] = mapped_column(BigInteger, default=0)
    time_intro_skipping: Mapped[int] = mapped_column(BigInteger, default=0)
    time_variable_speed: Mapped[int] = mapped_column(BigInteger, default=0)
    time_listened: Mapped[int] = mapped_column(BigInteger, default=0)


class ChangeLog(Base):
    __tablename__ = "change_log"
    __table_args__ = (Index("ix_change_log_user_server_ms", "user_uuid", "server_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_uuid: Mapped[str] = mapped_column(ForeignKey("users.uuid", ondelete="CASCADE"))
    entity: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(String(32))
    modified_ms: Mapped[int] = mapped_column(BigInteger)
    server_ms: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))
