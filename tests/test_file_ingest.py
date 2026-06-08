import os

from src.db_manager import DatabaseManager, Track
from src.file_ingest import ingest_audio_file


class FakeScanner:
    """Stands in for LibraryScanner: optionally upserts a track row the way
    the real scanner would after reading tags."""

    def __init__(self, db, track=None):
        self.db = db
        self.track = track

    def _process_file(self, path):
        if self.track is not None:
            t = self.track
            t.local_path = path
            self.db.add_or_update_track(t)


def test_ingest_moves_file_and_links_track(tmp_path):
    db = DatabaseManager(":memory:")
    src = tmp_path / "raw.flac"
    src.write_bytes(b"audio")
    music = tmp_path / "music"

    scanner = FakeScanner(db, Track(
        mbid="mb-x", title="Roygbiv", artist="Boards of Canada",
        album="Music Has the Right to Children", track_number=4,
    ))

    dest = ingest_audio_file(
        db, scanner, str(music), str(src), mbid_guess="mb-x",
    )

    assert dest.endswith(
        "Boards of Canada/Music Has the Right to Children/04 Roygbiv.flac"
    )
    assert os.path.exists(dest)
    assert not os.path.exists(str(src))
    assert db.get_track_local_path("mb-x") == dest
    db.close()


def test_ingest_falls_back_to_contribution_identity(tmp_path):
    # Scanner yields no row (unreadable/unfingerprintable) → use the passed
    # artist/title/album/mbid.
    db = DatabaseManager(":memory:")
    src = tmp_path / "raw.flac"
    src.write_bytes(b"audio")
    music = tmp_path / "music"

    dest = ingest_audio_file(
        db, FakeScanner(db, None), str(music), str(src),
        mbid_guess="mb-y", artist="Aphex Twin", title="Xtal", album="SAW 85-92",
    )

    assert dest.endswith("Aphex Twin/SAW 85-92/Xtal.flac")
    assert os.path.exists(dest)
    assert db.get_track_local_path("mb-y") == dest
    db.close()
