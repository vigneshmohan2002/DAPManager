"""Tests for catalog_linker — the on-disk file → catalog row binder."""

from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from src.catalog_linker import main_run_catalog_linker
from src.db_manager import DatabaseManager, Track


def _minimal_flac(path):
    """Write a FLAC header mutagen can open and tag.

    STREAMINFO must carry a non-zero sample rate or mutagen rejects it.
    We pack 44100 Hz / 1 channel / 16 bps / 0 samples into STREAMINFO.
    """
    data = bytearray()
    data += b"fLaC"
    data += bytes([0x80, 0x00, 0x00, 0x22])  # last block | STREAMINFO | len=34
    data += (4096).to_bytes(2, "big")  # min block
    data += (4096).to_bytes(2, "big")  # max block
    data += (0).to_bytes(3, "big")     # min frame
    data += (0).to_bytes(3, "big")     # max frame
    packed = (44100 << 44) | (0 << 41) | (15 << 36) | 0
    data += packed.to_bytes(8, "big")
    data += b"\x00" * 16  # md5
    with open(path, "wb") as f:
        f.write(data)


def _tag_flac(path, **kwargs):
    """Write the given tag keys onto a FLAC file. Keys map 1:1 to
    vorbis comment names (artist, title, album, isrc,
    musicbrainz_trackid)."""
    audio = FLAC(str(path))
    for key, value in kwargs.items():
        if value is None:
            continue
        audio[key] = value
    audio.save()


@pytest.fixture
def db():
    m = DatabaseManager(":memory:")
    yield m
    m.close()


@pytest.fixture
def library(tmp_path):
    """Return (root_dir, make_file) where make_file(name, **tags) writes
    a tagged FLAC and returns its absolute path."""
    root = tmp_path / "music"
    root.mkdir()

    def make_file(name, **tags):
        p = root / name
        _minimal_flac(str(p))
        _tag_flac(p, **tags)
        return str(p)

    return root, make_file


def _config(music_root):
    return SimpleNamespace(music_library=str(music_root))


def test_link_by_mbid_wins_over_artist_title(db, library):
    # The file's MBID tag takes precedence over a same-name unlinked row.
    root, make = library
    db.add_or_update_track(Track(mbid="by-mbid", title="Song", artist="A"))
    db.add_or_update_track(Track(mbid="by-name", title="Song", artist="A"))
    make("t.flac", artist=["A"], title=["Song"], musicbrainz_trackid=["by-mbid"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked"] == 1
    assert summary["linked_by_mbid"] == 1
    assert db.get_track_by_mbid("by-mbid").local_path is not None
    # The name-matched row stayed unlinked because MBID claimed the file first.
    assert db.get_track_by_mbid("by-name").local_path is None


def test_link_by_isrc_when_no_mbid(db, library):
    root, make = library
    db.add_or_update_track(Track(mbid="t1", title="S", artist="A", isrc="ISRC-1"))
    make("t.flac", artist=["A"], title=["S"], isrc=["ISRC-1"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked_by_isrc"] == 1
    assert db.get_track_by_mbid("t1").local_path is not None


def test_link_by_artist_title_case_insensitive(db, library):
    root, make = library
    db.add_or_update_track(Track(mbid="t1", title="Song", artist="Artist"))
    make("t.flac", artist=["ARTIST"], title=["song"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked_by_name"] == 1
    assert db.get_track_by_mbid("t1").local_path is not None


def test_ambiguous_artist_title_with_same_album_is_skipped(db, library):
    # Two unlinked rows sharing artist+title+album — linker can't choose.
    root, make = library
    db.add_or_update_track(Track(mbid="a", title="S", artist="A", album="Al"))
    db.add_or_update_track(Track(mbid="b", title="S", artist="A", album="Al"))
    make("t.flac", artist=["A"], title=["S"], album=["Al"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked"] == 0
    assert summary["ambiguous"] == 1
    assert db.get_track_by_mbid("a").local_path is None
    assert db.get_track_by_mbid("b").local_path is None


def test_ambiguous_artist_title_disambiguated_by_album(db, library):
    root, make = library
    db.add_or_update_track(Track(mbid="a", title="S", artist="A", album="Alpha"))
    db.add_or_update_track(Track(mbid="b", title="S", artist="A", album="Beta"))
    make("t.flac", artist=["A"], title=["S"], album=["Beta"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked_by_name"] == 1
    assert db.get_track_by_mbid("a").local_path is None
    assert db.get_track_by_mbid("b").local_path is not None


def test_already_linked_file_is_skipped_not_errored(db, library):
    # A file on disk that's already in tracks.local_path belongs to
    # whichever mbid claimed it; we must not try to re-bind it.
    root, make = library
    path = make("t.flac", artist=["A"], title=["S"])
    db.add_or_update_track(Track(mbid="owner", title="S", artist="A", local_path=path))
    db.add_or_update_track(Track(mbid="other", title="S", artist="A"))

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked"] == 0
    assert summary["skipped"] == 1
    assert db.get_track_by_mbid("other").local_path is None


def test_mbid_tag_pointing_to_linked_row_falls_through_and_is_skipped(db, library):
    # File's MBID tag names a row that's already linked — we don't
    # stomp the existing binding, and since no other priority matches
    # in this setup, the file is skipped.
    root, make = library
    db.add_or_update_track(Track(
        mbid="linked", title="S", artist="A", local_path="/old/path.flac",
    ))
    make("new.flac", artist=["A"], title=["S"], musicbrainz_trackid=["linked"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["linked"] == 0
    # The existing binding is preserved.
    assert db.get_track_by_mbid("linked").local_path == "/old/path.flac"


def test_mbid_tag_pointing_to_soft_deleted_row_falls_through(db, library):
    # A soft-deleted row shouldn't get resurrected by a rogue MBID tag.
    root, make = library
    db.add_or_update_track(Track(mbid="dead", title="S", artist="A"))
    db.soft_delete_track("dead")
    make("t.flac", artist=["A"], title=["S"], musicbrainz_trackid=["dead"])

    summary = main_run_catalog_linker(db, _config(root))
    # No live unlinked row remains that the fallback could claim either.
    assert summary["linked"] == 0


def test_no_match_counts_as_skipped(db, library):
    root, make = library
    make("t.flac", artist=["Unknown"], title=["Whatever"])

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["scanned"] == 1
    assert summary["linked"] == 0
    assert summary["ambiguous"] == 0
    assert summary["skipped"] == 1


def test_non_audio_extensions_are_ignored(db, library):
    root, make = library
    # Drop a non-audio file in the library; the linker should skip it.
    (root / "cover.jpg").write_bytes(b"not audio")
    make("t.flac", artist=["A"], title=["S"])
    db.add_or_update_track(Track(mbid="t1", title="S", artist="A"))

    summary = main_run_catalog_linker(db, _config(root))
    assert summary["scanned"] == 1  # only the FLAC counted
    assert summary["linked"] == 1


def test_missing_library_path_returns_clean_summary(db, tmp_path):
    cfg = SimpleNamespace(music_library=str(tmp_path / "does-not-exist"))
    summary = main_run_catalog_linker(db, cfg)
    assert summary["scanned"] == 0
    assert summary["linked"] == 0
    assert "message" in summary


def test_progress_callback_is_invoked(db, library):
    root, make = library
    make("t.flac", artist=["A"], title=["S"])
    db.add_or_update_track(Track(mbid="t1", title="S", artist="A"))

    messages = []
    main_run_catalog_linker(db, _config(root), progress_callback=messages.append)
    # "Done" report always fires at the end.
    assert any(m.startswith("Done.") for m in messages)
