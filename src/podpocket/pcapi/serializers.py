from typing import Any

from podpocket.feeds.parse import plain_text
from podpocket.models import Episode, Podcast
from podpocket.timefmt import iso_z, pc_gmt


def podcast_full(podcast: Podcast, episodes: list[Episode]) -> dict[str, Any]:
    seasons = {episode.season for episode in episodes if episode.season is not None}
    return {
        "episode_frequency": "",
        "episode_count": len(episodes),
        "has_more_episodes": False,
        "has_seasons": bool(seasons),
        "season_count": len(seasons),
        "refresh_allowed": True,
        "podcast": {
            "uuid": podcast.uuid,
            "url": podcast.website_url,
            "title": podcast.title,
            "author": podcast.author,
            "description": podcast.description,
            "description_html": podcast.description_html,
            "show_type": podcast.show_type,
            "category": podcast.category,
            "audio": True,
            "is_private": False,
            "slug": "",
            "explicit": podcast.explicit,
            "web_feed": False,
            "fundings": [{"url": podcast.funding_url}] if podcast.funding_url else [],
            "episodes": [episode_info(episode) for episode in episodes],
        },
    }


def episode_info(episode: Episode) -> dict[str, Any]:
    return {
        "uuid": episode.uuid,
        "title": episode.title,
        "season": episode.season,
        "number": episode.number,
        "type": episode.episode_type,
        "url": episode.enclosure_url,
        "file_type": episode.file_type,
        "file_size": episode.file_size,
        "duration": episode.duration,
        "published": iso_z(episode.published),
        "slug": "",
        "has_generated_transcript": False,
    }


def refresh_episode(episode: Episode) -> dict[str, Any]:
    return {
        "uuid": episode.uuid,
        "title": episode.title,
        "url": episode.enclosure_url,
        "file_type": episode.file_type,
        "size_in_bytes": episode.file_size,
        "duration_in_secs": episode.duration,
        "description": plain_text(episode.show_notes),
        "published_at": pc_gmt(episode.published),
    }


def show_notes(podcast_uuid: str, episodes: list[Episode]) -> dict[str, Any]:
    return {
        "podcast": {
            "uuid": podcast_uuid,
            "episodes": [
                {
                    "uuid": episode.uuid,
                    "show_notes": episode.show_notes,
                    "image": episode.image_url,
                    "chapters_url": episode.chapters_url,
                    "transcripts": episode.transcripts,
                }
                for episode in episodes
            ],
        }
    }


def episode_search_result(episode: Episode, podcast: Podcast) -> dict[str, Any]:
    return {
        "uuid": episode.uuid,
        "title": episode.title,
        "duration": episode.duration,
        "published_date": iso_z(episode.published),
        "podcast_uuid": podcast.uuid,
        "podcast_title": podcast.title,
    }
