import logging

from podpocket import catalog
from podpocket.catalog import PodcastSummary
from podpocket.directory.itunes import DirectoryError
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
