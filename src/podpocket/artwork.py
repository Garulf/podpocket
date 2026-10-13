import asyncio
import hashlib
import io
import json
import logging
import shutil
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy, fetch
from podpocket.models import Podcast

logger = logging.getLogger(__name__)

SIZES = (200, 480, 960)
DEFAULT_COLORS = {"background": "#3D3D3D", "tintForDarkBg": "#B3B3B3", "tintForLightBg": "#3D3D3D"}
RETRY_FAILED_AFTER_SECONDS = 3600
MAX_PIXELS = 25_000_000
RENDER_CONCURRENCY = 2
ACCEPTED_FORMATS = ["PNG", "JPEG", "WEBP", "GIF"]
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


class ArtworkError(Exception):
    pass


def render_artwork(content: bytes, directory: Path) -> None:
    try:
        with Image.open(io.BytesIO(content), formats=ACCEPTED_FORMATS) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ArtworkError(f"image of {source.width}x{source.height} pixels is too large")
            upright = ImageOps.exif_transpose(source).convert("RGB")
        square = ImageOps.fit(upright, (SIZES[-1], SIZES[-1]), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ArtworkError(str(exc)) from exc
    staging = directory.with_name(f"{directory.name}.partial")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for size in SIZES:
            resized = square.resize((size, size), Image.Resampling.LANCZOS)
            resized.save(staging / f"{size}.webp", "WEBP", quality=85)
        (staging / "colors.json").write_text(json.dumps(palette(square)))
        staging.rename(directory)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ArtworkError(str(exc)) from exc


def palette(image: Image.Image) -> dict[str, str]:
    average = image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return {
        "background": _hex(average),
        "tintForDarkBg": _hex(_mix(average, WHITE, 0.6)),
        "tintForLightBg": _hex(_mix(average, BLACK, 0.4)),
    }


def _mix(color, target, amount: float) -> tuple[int, ...]:
    return tuple(round(c + (t - c) * amount) for c, t in zip(color, target, strict=True))


def _hex(color) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


class ArtworkStore:
    def __init__(
        self,
        root: Path,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        policy: FetchPolicy,
        clock: Callable[[], float] = time.time,
    ):
        self._root = root
        self._sessionmaker = sessionmaker
        self._http = http
        self._policy = policy
        self._clock = clock
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._renders = asyncio.Semaphore(RENDER_CONCURRENCY)

    async def image(self, podcast_uuid: str, size: int) -> Path | None:
        directory = await self._rendered(podcast_uuid)
        return directory / f"{size}.webp" if directory is not None else None

    async def colors(self, podcast_uuid: str) -> dict[str, str]:
        directory = await self._rendered(podcast_uuid)
        if directory is None:
            return dict(DEFAULT_COLORS)
        try:
            return json.loads((directory / "colors.json").read_text())
        except (OSError, ValueError):
            return dict(DEFAULT_COLORS)

    async def _rendered(self, podcast_uuid: str) -> Path | None:
        async with self._sessionmaker() as session:
            artwork_url = await session.scalar(
                select(Podcast.artwork_url).where(Podcast.uuid == podcast_uuid)
            )
        if not artwork_url:
            return None
        key = hashlib.sha256(artwork_url.encode()).hexdigest()[:32]
        directory = self._root / key
        if directory.is_dir():
            return directory
        async with self._locks[key]:
            if directory.is_dir():
                return directory
            marker = self._root / f"{key}.failed"
            if self._failed_recently(marker):
                return None
            try:
                result = await fetch(self._http, artwork_url, self._policy)
                async with self._renders:
                    await asyncio.to_thread(render_artwork, result.content or b"", directory)
            except (FeedError, ArtworkError, OSError) as exc:
                logger.info("artwork for %s unavailable: %s", podcast_uuid, exc)
                self._mark_failed(marker)
                return None
            marker.unlink(missing_ok=True)
            return directory

    def _failed_recently(self, marker: Path) -> bool:
        try:
            failed_at = float(marker.read_text())
        except (OSError, ValueError):
            return False
        return self._clock() - failed_at < RETRY_FAILED_AFTER_SECONDS

    def _mark_failed(self, marker: Path) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(self._clock()))
        except OSError as exc:
            logger.warning("cannot record artwork failure: %s", exc)
