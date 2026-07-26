import pytest

from src.artwork_cache import (
    ArtworkCache,
    CachedArtwork,
    artwork_cache_for_database,
    cache_complete_stream,
)


def test_artwork_cache_round_trips_supported_images_with_hashed_names(tmp_path):
    cache = ArtworkCache(tmp_path / "covers")

    assert cache.store("../album/with/slashes", b"JPEG", "image/jpeg")
    assert cache.load("../album/with/slashes") == CachedArtwork(
        b"JPEG",
        "image/jpeg",
    )

    files = list(cache.directory.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".jpg"
    assert ".." not in files[0].name
    assert "/" not in files[0].name


def test_artwork_cache_replaces_an_older_format_atomically(tmp_path):
    cache = ArtworkCache(tmp_path / "covers")

    assert cache.store("album-1", b"PNG", "image/png")
    assert cache.store("album-1", b"WEBP", "image/webp; charset=binary")

    assert cache.load("album-1") == CachedArtwork(b"WEBP", "image/webp")
    assert [path.suffix for path in cache.directory.iterdir()] == [".webp"]


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"", "image/jpeg"),
        (b"not an image", "text/html"),
        (b"12345", "image/jpeg"),
    ],
)
def test_artwork_cache_rejects_empty_unsupported_or_oversized_entries(
    tmp_path,
    body,
    content_type,
):
    cache = ArtworkCache(tmp_path / "covers", max_artwork_bytes=4)

    assert not cache.store("album-1", body, content_type)
    assert cache.load("album-1") is None
    assert not cache.directory.exists()


def test_artwork_cache_location_is_beside_database(tmp_path):
    database = tmp_path / "state" / "library.db"

    cache = artwork_cache_for_database(str(database))

    assert cache is not None
    assert cache.directory == database.parent / "artwork_cache"
    assert artwork_cache_for_database(":memory:") is None
    assert artwork_cache_for_database("") is None


def test_artwork_cache_evicts_old_entries_at_total_capacity(tmp_path):
    cache = ArtworkCache(
        tmp_path / "covers",
        max_artwork_bytes=4,
        max_cache_bytes=5,
    )

    assert cache.store("older", b"1234", "image/jpeg")
    assert cache.store("newer", b"5678", "image/jpeg")

    assert cache.load("older") is None
    assert cache.load("newer") == CachedArtwork(b"5678", "image/jpeg")


def test_artwork_cache_rejects_a_total_limit_below_the_per_image_limit(
    tmp_path,
):
    with pytest.raises(ValueError, match="at least max_artwork_bytes"):
        ArtworkCache(
            tmp_path / "covers",
            max_artwork_bytes=5,
            max_cache_bytes=4,
        )


def test_complete_stream_caches_only_after_successful_consumption(tmp_path):
    cache = ArtworkCache(tmp_path / "covers")

    streamed = list(cache_complete_stream(
        [b"JP", b"EG"],
        cache=cache,
        album_id="album-1",
        content_type="image/jpeg",
    ))

    assert streamed == [b"JP", b"EG"]
    assert cache.load("album-1") == CachedArtwork(b"JPEG", "image/jpeg")


def test_incomplete_stream_is_never_cached(tmp_path):
    cache = ArtworkCache(tmp_path / "covers")

    def interrupted():
        yield b"partial"
        raise OSError("connection lost")

    with pytest.raises(OSError, match="connection lost"):
        list(cache_complete_stream(
            interrupted(),
            cache=cache,
            album_id="album-1",
            content_type="image/jpeg",
        ))

    assert cache.load("album-1") is None


def test_oversized_stream_passes_through_without_being_cached(tmp_path):
    cache = ArtworkCache(tmp_path / "covers", max_artwork_bytes=4)

    streamed = list(cache_complete_stream(
        [b"1234", b"5"],
        cache=cache,
        album_id="album-1",
        content_type="image/jpeg",
    ))

    assert streamed == [b"1234", b"5"]
    assert cache.load("album-1") is None
