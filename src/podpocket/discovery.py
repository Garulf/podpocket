import logging

from podpocket import catalog
from podpocket.catalog import PodcastSummary
from podpocket.directory.itunes import DirectoryError, apple_podcast_id
from podpocket.feeds.errors import FeedError
from podpocket.services import Services

logger = logging.getLogger(__name__)


async def find_podcasts(
    services: Services, term: str, *, directory_limit: int
) -> list[PodcastSummary]:
    async with services.sessionmaker() as session:
        local = [
            PodcastSummary(p.uuid, p.title, p.author)
            for p in await catalog.search_podcasts(session, term)
        ]
    remote: list[PodcastSummary] = []
    if directory_limit:
        try:
            found = await services.directory.search(term, limit=directory_limit)
        except DirectoryError as exc:
            logger.warning("directory search failed: %s", exc)
        else:
            remote = await catalog.save_stubs(services.sessionmaker, found)
    seen: set[str] = set()
    results = []
    for summary in [*local, *remote]:
        if summary.uuid not in seen:
            seen.add(summary.uuid)
            results.append(summary)
    return results


URL_PREFIXES = ("http://", "https://", "feed:", "pcast:", "itpc:", "rss:")


def looks_like_url(term: str) -> bool:
    return term.lower().startswith(URL_PREFIXES)


async def resolve_user_url(services: Services, url: str) -> str:
    apple_id = apple_podcast_id(url)
    if apple_id is not None:
        try:
            found = await services.directory.lookup([apple_id])
        except DirectoryError as exc:
            raise FeedError(f"Apple Podcasts lookup failed: {exc}") from exc
        if not found:
            raise FeedError("Apple Podcasts has no public feed for that link")
        url = found[0].feed_url
    return await services.refresher.resolve(url)
