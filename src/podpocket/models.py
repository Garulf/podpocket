from datetime import datetime

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
