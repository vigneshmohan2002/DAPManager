"""Bounded, disposable on-disk cache for proxied album artwork."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Iterable, Iterator, Optional


logger = logging.getLogger(__name__)

DEFAULT_MAX_ARTWORK_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_CACHE_BYTES = 512 * 1024 * 1024
ARTWORK_CACHE_DIRECTORY = "artwork_cache"
_CONTENT_TYPE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _normalized_content_type(content_type: str) -> Optional[str]:
    normalized = content_type.partition(";")[0].strip().lower()
    if normalized in _CONTENT_TYPE_SUFFIXES:
        return normalized
    return None


def _cache_key(album_id: str) -> str:
    return hashlib.sha256(album_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedArtwork:
    body: bytes
    content_type: str


class ArtworkCache:
    """Store complete image responses under hashed, traversal-safe names."""

    def __init__(
        self,
        directory: Path,
        *,
        max_artwork_bytes: int = DEFAULT_MAX_ARTWORK_BYTES,
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        if max_artwork_bytes <= 0:
            raise ValueError("max_artwork_bytes must be positive")
        if max_cache_bytes < max_artwork_bytes:
            raise ValueError(
                "max_cache_bytes must be at least max_artwork_bytes"
            )
        self.directory = directory
        self.max_artwork_bytes = max_artwork_bytes
        self.max_cache_bytes = max_cache_bytes

    def load(self, album_id: str) -> Optional[CachedArtwork]:
        key = _cache_key(album_id)
        for content_type, suffix in _CONTENT_TYPE_SUFFIXES.items():
            path = self.directory / f"{key}{suffix}"
            try:
                with path.open("rb") as cached_file:
                    body = cached_file.read(self.max_artwork_bytes + 1)
                if not body or len(body) > self.max_artwork_bytes:
                    continue
                try:
                    path.touch(exist_ok=True)
                except OSError:
                    pass
                return CachedArtwork(body, content_type)
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning(
                    "Could not read cached artwork for %s",
                    album_id,
                    exc_info=True,
                )
                return None
        return None

    def store(self, album_id: str, body: bytes, content_type: str) -> bool:
        normalized_type = _normalized_content_type(content_type)
        if (
            normalized_type is None
            or not body
            or len(body) > self.max_artwork_bytes
        ):
            return False

        key = _cache_key(album_id)
        suffix = _CONTENT_TYPE_SUFFIXES[normalized_type]
        target = self.directory / f"{key}{suffix}"
        temporary_path: Optional[Path] = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.directory,
                prefix=f".{key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(body)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, target)
            temporary_path = None
            self._remove_other_formats(key, suffix)
            self._enforce_capacity(retained=target)
            return True
        except OSError:
            logger.warning(
                "Could not cache artwork for %s",
                album_id,
                exc_info=True,
            )
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _remove_other_formats(self, key: str, retained_suffix: str) -> None:
        for suffix in _CONTENT_TYPE_SUFFIXES.values():
            if suffix == retained_suffix:
                continue
            try:
                (self.directory / f"{key}{suffix}").unlink(missing_ok=True)
            except OSError:
                logger.debug(
                    "Could not remove stale artwork format for %s",
                    key,
                    exc_info=True,
                )

    def _enforce_capacity(self, *, retained: Path) -> None:
        entries = []
        total_bytes = 0
        for path in self.directory.iterdir():
            if path.suffix not in _CONTENT_TYPE_SUFFIXES.values():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime_ns, path, stat.st_size))
            total_bytes += stat.st_size

        for _, path, size in sorted(entries):
            if total_bytes <= self.max_cache_bytes:
                return
            if path == retained:
                continue
            try:
                path.unlink(missing_ok=True)
                total_bytes -= size
            except OSError:
                logger.debug(
                    "Could not evict cached artwork %s",
                    path,
                    exc_info=True,
                )


def artwork_cache_for_database(db_path: str) -> Optional[ArtworkCache]:
    """Place cache data beside the database; in-memory databases have none."""
    normalized_path = str(db_path or "").strip()
    if not normalized_path or normalized_path == ":memory:":
        return None
    database_path = Path(normalized_path).expanduser().resolve(strict=False)
    return ArtworkCache(database_path.parent / ARTWORK_CACHE_DIRECTORY)


def cache_complete_stream(
    chunks: Iterable[bytes],
    *,
    cache: ArtworkCache,
    album_id: str,
    content_type: str,
) -> Iterator[bytes]:
    """Tee a complete bounded stream into the cache without delaying clients."""
    buffered = bytearray()
    cacheable = _normalized_content_type(content_type) is not None
    completed = False
    iterator = iter(chunks)
    try:
        for chunk in iterator:
            if (
                cacheable
                and len(buffered) + len(chunk) <= cache.max_artwork_bytes
            ):
                buffered.extend(chunk)
            else:
                cacheable = False
                buffered.clear()
            yield chunk
        completed = True
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
        if completed and cacheable and buffered:
            cache.store(album_id, bytes(buffered), content_type)
