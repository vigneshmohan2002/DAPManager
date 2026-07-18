import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.flac import FLAC, Picture

from src import tag_service
from src.db_manager import DatabaseManager, Track
from src.file_ingest import (
    ingest_audio_file,
    ingest_downloaded_audio_file,
    ingest_downloaded_audio_file_with_result,
    normalize_embedded_recording_mbid,
    read_embedded_recording_mbid,
)


RECORDING_MBID = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
OTHER_RECORDING_MBID = "cfba04e9-aadc-4e45-ae8d-3177e5e6f463"
RELEASE_MBID = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
RELEASE_TRACK_MBID = "10000000-0000-4000-8000-000000000002"


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


class PublicScanner(FakeScanner):
    def __init__(self, db, track=None):
        super().__init__(db, track)
        self.processed = []

    def process_file(self, path):
        self.processed.append(path)
        return super()._process_file(path)

    def _process_file(self, path):
        raise AssertionError("legacy scanner API should not be selected")


class DuplicateSkippingScanner:
    """Model the real scanner's MBID duplicate behavior."""

    def __init__(self):
        self.processed = []

    def process_file(self, path):
        self.processed.append(path)
        return "skipped"


def test_read_embedded_recording_mbid_returns_canonical_uuid():
    with patch("mediafile.MediaFile") as media_file:
        media_file.return_value.mb_trackid = f" {RECORDING_MBID.upper()} "

        result = read_embedded_recording_mbid("album-track.flac")

    assert result == RECORDING_MBID


@pytest.mark.parametrize(
    "embedded_value",
    ["not-a-uuid", "00000000-0000-0000-0000-000000000000"],
)
def test_read_embedded_recording_mbid_rejects_invalid_identity(embedded_value):
    with patch("mediafile.MediaFile") as media_file:
        media_file.return_value.mb_trackid = embedded_value

        result = read_embedded_recording_mbid("album-track.flac")

    assert result is None


def test_normalize_embedded_recording_mbid_persists_canonical_case():
    with patch("mediafile.MediaFile") as media_file, patch(
        "src.file_ingest.write_mbid_to_file",
    ) as write_mbid:
        tagged_file = media_file.return_value
        tagged_file.mb_trackid = RECORDING_MBID.upper()
        write_mbid.side_effect = lambda _path, mbid: (
            setattr(tagged_file, "mb_trackid", mbid) or True
        )

        result = normalize_embedded_recording_mbid("album-track.flac")

    assert result == RECORDING_MBID
    assert tagged_file.mb_trackid == RECORDING_MBID
    write_mbid.assert_called_once_with("album-track.flac", RECORDING_MBID)


def test_normalize_embedded_recording_mbid_fails_closed_on_invalid_tag():
    with patch("mediafile.MediaFile") as media_file:
        media_file.return_value.mb_trackid = "recording-ish-but-not-a-uuid"

        with pytest.raises(ValueError, match="not a valid UUID"):
            normalize_embedded_recording_mbid("album-track.flac")

    media_file.return_value.save.assert_not_called()


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


def test_contribution_canonicalizes_uppercase_mbid_guess_before_scan(
    tmp_path, monkeypatch
):
    db = DatabaseManager(":memory:")
    source = tmp_path / "raw.flac"
    source.write_bytes(b"audio")
    tag_state = {"mbid": None}
    scan_observations = []

    class RecordingScanner:
        def process_file(self, path):
            scan_observations.append(tag_state["mbid"])
            db.add_or_update_track(Track(
                mbid=tag_state["mbid"],
                title="Track",
                artist="Artist",
                album="Album",
                local_path=path,
            ))
            return "processed"

    monkeypatch.setattr(
        "src.file_ingest.normalize_embedded_recording_mbid",
        lambda _path: tag_state["mbid"],
    )
    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: tag_state["mbid"],
    )
    monkeypatch.setattr(
        "src.file_ingest.write_mbid_to_file",
        lambda _path, mbid: tag_state.update(mbid=mbid) or True,
    )

    destination = ingest_audio_file(
        db,
        RecordingScanner(),
        str(tmp_path / "music"),
        str(source),
        mbid_guess=RECORDING_MBID.upper(),
    )

    assert scan_observations == [RECORDING_MBID]
    assert db.get_track_local_path(RECORDING_MBID) == destination
    assert db.get_track_by_mbid(RECORDING_MBID.upper()) is None
    db.close()


def test_ingest_prefers_public_scanner_entry_point(tmp_path):
    db = DatabaseManager(":memory:")
    src = tmp_path / "raw.flac"
    src.write_bytes(b"audio")
    scanner = PublicScanner(db, Track(
        mbid="mb-public", title="Public", artist="API", album="Scanner",
    ))

    dest = ingest_audio_file(db, scanner, str(tmp_path / "music"), str(src))

    assert scanner.processed == [str(src)]
    assert db.get_track_local_path("mb-public") == dest
    db.close()


def test_download_ingest_preserves_sort_identity_and_links_scanned_row(tmp_path):
    db = DatabaseManager(":memory:")
    src = tmp_path / "raw.flac"
    src.write_bytes(b"audio")
    scanner = FakeScanner(db, Track(
        mbid="mb-download", title="Scanner Title", artist="Scanner Artist",
        album="Scanner Album", track_number=9,
    ))

    dest = ingest_downloaded_audio_file(
        db,
        scanner,
        str(tmp_path / "music"),
        str(src),
        artist="Sort Artist",
        album="Sort Album",
        title="Sort Title",
        track_number=2,
    )

    assert dest.endswith("Sort Artist/Sort Album/02 Sort Title.flac")
    assert db.get_track_local_path("mb-download") == dest
    db.close()


def test_download_ingest_disambiguates_second_disc_path(tmp_path):
    db = DatabaseManager(":memory:")
    src = tmp_path / "raw.flac"
    src.write_bytes(b"audio")
    scanner = FakeScanner(db, Track(
        mbid="mb-disc-two", title="Intro", artist="Artist",
        album="Album", track_number=1, disc_number=2,
    ))

    dest = ingest_downloaded_audio_file(
        db,
        scanner,
        str(tmp_path / "music"),
        str(src),
        artist="Artist",
        album="Album",
        title="Intro",
        track_number=1,
        disc_number=2,
    )

    assert dest.endswith("Artist/Album/02-01 Intro.flac")
    assert db.get_track_local_path("mb-disc-two") == dest
    db.close()


def _quality(bits, sample_rate, bitrate=0):
    return {
        "lossless": True,
        "bits_per_sample": bits,
        "sample_rate": sample_rate,
        "bitrate": bitrate,
    }


def _canonical_meta():
    return {
        "title": "Track",
        "artist": "Artist",
        "album": "Album",
        "album_artist": "Album Artist",
        "date": "2026-07-18",
        "track_number": 1,
        "track_total": 8,
        "disc_number": 1,
        "disc_total": 1,
        "mbid": RECORDING_MBID,
        "release_mbid": RELEASE_MBID,
        "release_track_mbid": RELEASE_TRACK_MBID,
    }


def _write_real_flac(
    path: Path,
    *,
    bits: int,
    sample_rate: int,
    frequency: int,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for real-FLAC ingest coverage")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-v", "error",
        "-f", "lavfi",
        "-i", (
            f"sine=frequency={frequency}:sample_rate={sample_rate}:duration=0.08"
        ),
        "-c:a", "flac",
        "-sample_fmt", "s16" if bits == 16 else "s32",
    ]
    if bits == 24:
        command.extend(("-bits_per_raw_sample", "24"))
    command.extend(("-y", str(path)))
    subprocess.run(command, check=True, capture_output=True)


def _encoded_flac_frame_digest(path: Path) -> str:
    """Independently hash the encoded frames after all FLAC metadata."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        assert handle.read(4) == b"fLaC"
        while True:
            header = handle.read(4)
            assert len(header) == 4
            block_size = int.from_bytes(header[1:4], "big")
            assert len(handle.read(block_size)) == block_size
            if header[0] & 0x80:
                break
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_real_primary_collision(tmp_path, *, stale_destination: bool):
    db = DatabaseManager(":memory:")
    source = tmp_path / "downloads" / "candidate.flac"
    destination = tmp_path / "music" / "Artist" / "Album" / "01 Track.flac"
    _write_real_flac(
        source,
        bits=16,
        sample_rate=44100,
        frequency=440,
    )
    _write_real_flac(
        destination,
        bits=24,
        sample_rate=96000,
        frequency=880,
    )
    source_meta = _canonical_meta()
    tag_service.write_tags(str(source), source_meta)
    destination_meta = dict(source_meta)
    if stale_destination:
        destination_meta.update({
            "title": "Stale Title",
            "album": "Stale Album",
            "release_mbid": OTHER_RECORDING_MBID,
            "release_track_mbid": "20000000-0000-4000-8000-000000000002",
        })
    tag_service.write_tags(str(destination), destination_meta)
    tagged_destination = FLAC(str(destination))
    tagged_destination["lyrics"] = "destination-owned lyrics"
    tagged_destination["rating"] = "0.9"
    if stale_destination:
        tagged_destination["musicbrainz_recordingid"] = RECORDING_MBID
        tagged_destination["totaltracks"] = "99"
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.desc = "destination artwork"
    picture.data = b"stable-destination-artwork"
    tagged_destination.add_picture(picture)
    tagged_destination.save()
    os.chmod(destination, 0o640)
    db.add_or_update_track(Track(
        mbid=RECORDING_MBID,
        title="Track",
        artist="Artist",
        album="Album",
        local_path=str(destination),
        track_number=1,
    ))
    return db, source, destination, source_meta, picture


def _collision_ingest(
    tmp_path,
    monkeypatch,
    source_quality,
    existing_quality,
    *,
    equal_identity_satisfies=True,
):
    db = DatabaseManager(":memory:")
    source = tmp_path / "downloads" / "candidate.flac"
    source.parent.mkdir()
    source.write_bytes(b"candidate-audio")
    destination = (
        tmp_path / "music" / "Artist" / "Album" / "01 Track.flac"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing-audio")
    scanner = FakeScanner(db, Track(
        mbid="collision-mbid",
        title="Track",
        artist="Artist",
        album="Album",
        track_number=1,
    ))

    def quality_for(path):
        return source_quality if os.path.samefile(path, source) else existing_quality

    monkeypatch.setattr("src.file_ingest.read_quality", quality_for)
    monkeypatch.setattr(
        "src.file_ingest.equal_quality_destination_satisfies",
        lambda _source, _destination: equal_identity_satisfies,
    )
    result = ingest_downloaded_audio_file_with_result(
        db,
        scanner,
        str(tmp_path / "music"),
        str(source),
        artist="Artist",
        album="Album",
        title="Track",
        track_number=1,
    )
    return db, source, destination, result


def test_download_ingest_does_not_replace_equal_quality_existing_file(
    tmp_path, monkeypatch
):
    equal = _quality(24, 48000, 1500000)

    with patch("src.file_ingest.os.replace") as replace:
        db, source, destination, result = _collision_ingest(
            tmp_path,
            monkeypatch,
            equal,
            equal,
        )

    assert result.changed is False
    assert result.path == str(destination)
    assert destination.read_bytes() == b"existing-audio"
    assert not source.exists()
    assert db.get_track_local_path("collision-mbid") == str(destination)
    replace.assert_not_called()
    db.close()


def test_download_ingest_does_not_downgrade_better_existing_file(
    tmp_path, monkeypatch
):
    db, source, destination, result = _collision_ingest(
        tmp_path,
        monkeypatch,
        _quality(16, 44100, 800000),
        _quality(24, 96000, 3000000),
    )

    assert result.changed is False
    assert destination.read_bytes() == b"existing-audio"
    assert not source.exists()
    assert db.get_track_local_path("collision-mbid") == str(destination)
    db.close()


def test_better_primary_flac_synchronizes_only_picard_tags(tmp_path):
    db, source, destination, source_meta, picture = (
        _prepare_real_primary_collision(tmp_path, stale_destination=True)
    )
    source_tags = tag_service.read_current_tags(str(source))
    frame_digest = _encoded_flac_frame_digest(destination)

    result = ingest_downloaded_audio_file_with_result(
        db,
        DuplicateSkippingScanner(),
        str(tmp_path / "music"),
        str(source),
        artist="Artist",
        album="Album",
        title="Track",
        track_number=1,
        recording_mbid=RECORDING_MBID,
    )

    tagged = FLAC(str(destination))
    assert result.path == str(destination)
    assert result.changed is True
    assert not source.exists()
    assert tag_service.read_current_tags(str(destination)) == source_tags
    assert source_tags["mbid"] == source_meta["mbid"]
    assert _encoded_flac_frame_digest(destination) == frame_digest
    assert tagged["lyrics"] == ["destination-owned lyrics"]
    assert tagged["rating"] == ["0.9"]
    assert [item.write() for item in tagged.pictures] == [picture.write()]
    assert "musicbrainz_recordingid" not in tagged
    assert "totaltracks" not in tagged
    assert os.stat(destination).st_mode & 0o777 == 0o640
    assert db.get_track_local_path(RECORDING_MBID) == str(destination)
    db.close()


def test_better_primary_flac_with_canonical_tags_is_byte_exact_noop(tmp_path):
    db, source, destination, _source_meta, _picture = (
        _prepare_real_primary_collision(tmp_path, stale_destination=False)
    )
    before = destination.read_bytes()

    result = ingest_downloaded_audio_file_with_result(
        db,
        DuplicateSkippingScanner(),
        str(tmp_path / "music"),
        str(source),
        artist="Artist",
        album="Album",
        title="Track",
        track_number=1,
        recording_mbid=RECORDING_MBID,
    )

    assert result.path == str(destination)
    assert result.changed is False
    assert destination.read_bytes() == before
    assert not source.exists()
    assert db.get_track_local_path(RECORDING_MBID) == str(destination)
    db.close()


def test_better_primary_tag_sync_failure_preserves_staged_source(
    tmp_path,
    monkeypatch,
):
    db, source, destination, _source_meta, _picture = (
        _prepare_real_primary_collision(tmp_path, stale_destination=True)
    )
    source_before = source.read_bytes()
    destination_before = destination.read_bytes()
    monkeypatch.setattr(
        tag_service,
        "copy_complete_picard_tags_atomic",
        lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(OSError("tag sync failed")),
    )

    with pytest.raises(OSError, match="tag sync failed"):
        ingest_downloaded_audio_file_with_result(
            db,
            DuplicateSkippingScanner(),
            str(tmp_path / "music"),
            str(source),
            artist="Artist",
            album="Album",
            title="Track",
            track_number=1,
            recording_mbid=RECORDING_MBID,
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before
    assert db.get_track_local_path(RECORDING_MBID) == str(destination)
    db.close()


def test_better_primary_refuses_mismatched_destination_recording(tmp_path):
    db, source, destination, _source_meta, _picture = (
        _prepare_real_primary_collision(tmp_path, stale_destination=True)
    )
    audio = FLAC(str(destination))
    audio["musicbrainz_trackid"] = OTHER_RECORDING_MBID
    del audio["musicbrainz_recordingid"]
    audio.save()
    source_before = source.read_bytes()
    destination_before = destination.read_bytes()

    with pytest.raises(ValueError, match="destination recording identity"):
        ingest_downloaded_audio_file_with_result(
            db,
            DuplicateSkippingScanner(),
            str(tmp_path / "music"),
            str(source),
            artist="Artist",
            album="Album",
            title="Track",
            track_number=1,
            recording_mbid=RECORDING_MBID,
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before
    assert db.get_track_local_path(RECORDING_MBID) == str(destination)
    db.close()


def test_download_ingest_replaces_equal_quality_stale_canonical_identity(
    tmp_path, monkeypatch
):
    equal = _quality(24, 48000, 1500000)

    db, source, destination, result = _collision_ingest(
        tmp_path,
        monkeypatch,
        equal,
        equal,
        equal_identity_satisfies=False,
    )

    assert result.changed is True
    assert destination.read_bytes() == b"candidate-audio"
    assert not source.exists()
    assert db.get_track_local_path("collision-mbid") == str(destination)
    db.close()


def test_download_ingest_atomically_upgrades_lower_quality_existing_file(
    tmp_path, monkeypatch
):
    real_replace = os.replace
    replace_calls = []

    def replace_spy(temp_path, destination_path):
        assert os.path.dirname(temp_path) == os.path.dirname(destination_path)
        assert open(temp_path, "rb").read() == b"candidate-audio"
        assert open(destination_path, "rb").read() == b"existing-audio"
        replace_calls.append((temp_path, destination_path))
        real_replace(temp_path, destination_path)

    monkeypatch.setattr("src.file_ingest.os.replace", replace_spy)
    db, source, destination, result = _collision_ingest(
        tmp_path,
        monkeypatch,
        _quality(24, 96000, 3000000),
        _quality(16, 44100, 800000),
    )

    assert result.changed is True
    assert destination.read_bytes() == b"candidate-audio"
    assert not source.exists()
    assert len(replace_calls) == 1
    assert not list(destination.parent.glob(".*.dapingest-*.tmp"))
    assert db.get_track_local_path("collision-mbid") == str(destination)
    db.close()


def _mbid_duplicate_ingest(
    tmp_path,
    monkeypatch,
    *,
    source_quality,
    canonical_quality,
):
    db = DatabaseManager(":memory:")
    source = tmp_path / "downloads" / "04 MISS U.flac"
    source.parent.mkdir()
    source.write_bytes(b"staged-album-audio")
    canonical = tmp_path / "music" / "Artist" / "Album" / "MISS U.flac"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical-audio")
    recording_mbid = RECORDING_MBID
    db.add_or_update_track(Track(
        mbid=recording_mbid,
        title="MISS U",
        artist="Artist",
        album="Album",
        local_path=str(canonical),
        track_number=4,
    ))
    scanner = DuplicateSkippingScanner()

    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: recording_mbid,
    )

    def quality_for(path):
        if os.path.samefile(path, source):
            return source_quality
        if os.path.samefile(path, canonical):
            return canonical_quality
        raise AssertionError(f"unexpected quality probe: {path}")

    monkeypatch.setattr("src.file_ingest.read_quality", quality_for)
    monkeypatch.setattr(
        "src.file_ingest.equal_quality_destination_satisfies",
        lambda _source, _destination: True,
    )
    result = ingest_downloaded_audio_file_with_result(
        db,
        scanner,
        str(tmp_path / "music"),
        str(source),
        artist="Artist",
        album="Album",
        title="MISS U",
        track_number=4,
        recording_mbid=recording_mbid,
    )
    metadata_destination = canonical.parent / "04 MISS U.flac"
    return db, scanner, source, canonical, metadata_destination, result


def test_album_duplicate_reuses_equal_quality_db_canonical_path(
    tmp_path, monkeypatch
):
    equal = _quality(16, 44100, 900000)
    db, scanner, source, canonical, duplicate, result = _mbid_duplicate_ingest(
        tmp_path,
        monkeypatch,
        source_quality=equal,
        canonical_quality=equal,
    )

    assert scanner.processed == [str(source)]
    assert result.changed is False
    assert result.path == str(canonical)
    assert canonical.read_bytes() == b"canonical-audio"
    assert not source.exists()
    assert not duplicate.exists()
    assert db.get_track_local_path(RECORDING_MBID) == str(canonical)
    db.close()


def test_album_duplicate_upgrades_db_canonical_path_not_metadata_path(
    tmp_path, monkeypatch
):
    db, _scanner, source, canonical, duplicate, result = _mbid_duplicate_ingest(
        tmp_path,
        monkeypatch,
        source_quality=_quality(24, 96000, 3000000),
        canonical_quality=_quality(16, 44100, 900000),
    )

    assert result.changed is True
    assert result.path == str(canonical)
    assert canonical.read_bytes() == b"staged-album-audio"
    assert not source.exists()
    assert not duplicate.exists()
    assert db.get_track_local_path(RECORDING_MBID) == str(canonical)
    db.close()


def _unsafe_canonical_ingest(tmp_path, monkeypatch, canonical):
    db = DatabaseManager(":memory:")
    source = tmp_path / "downloads" / "candidate.flac"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"candidate")
    db.add_or_update_track(Track(
        mbid=RECORDING_MBID,
        title="Track",
        artist="Artist",
        album="Album",
        local_path=str(canonical),
    ))
    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: RECORDING_MBID,
    )
    with pytest.raises(ValueError):
        ingest_downloaded_audio_file_with_result(
            db,
            DuplicateSkippingScanner(),
            str(tmp_path / "music"),
            str(source),
            artist="Artist",
            album="Album",
            title="Track",
            track_number=1,
            recording_mbid=RECORDING_MBID,
        )
    assert source.read_bytes() == b"candidate"
    assert db.get_track_local_path(RECORDING_MBID) == str(canonical)
    db.close()


def test_album_duplicate_refuses_db_canonical_path_outside_library(
    tmp_path, monkeypatch
):
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")

    _unsafe_canonical_ingest(tmp_path, monkeypatch, outside)

    assert outside.read_bytes() == b"outside"


def test_album_duplicate_refuses_symlink_db_canonical_path(
    tmp_path, monkeypatch
):
    music = tmp_path / "music"
    music.mkdir()
    target = music / "target.flac"
    target.write_bytes(b"target")
    canonical_link = music / "canonical.flac"
    canonical_link.symlink_to(target)

    _unsafe_canonical_ingest(tmp_path, monkeypatch, canonical_link)

    assert canonical_link.is_symlink()
    assert target.read_bytes() == b"target"


def test_album_duplicate_refuses_non_regular_db_canonical_path(
    tmp_path, monkeypatch
):
    canonical_directory = tmp_path / "music" / "canonical.flac"
    canonical_directory.mkdir(parents=True)

    _unsafe_canonical_ingest(tmp_path, monkeypatch, canonical_directory)

    assert canonical_directory.is_dir()


def test_album_duplicate_with_stale_inside_path_uses_safe_metadata_path(
    tmp_path, monkeypatch
):
    db = DatabaseManager(":memory:")
    music = tmp_path / "music"
    music.mkdir()
    stale = music / "Old Artist" / "Old Album" / "missing.flac"
    source = tmp_path / "downloads" / "candidate.flac"
    source.parent.mkdir()
    source.write_bytes(b"candidate")
    db.add_or_update_track(Track(
        mbid=RECORDING_MBID,
        title="Track",
        artist="Old Artist",
        album="Old Album",
        local_path=str(stale),
    ))
    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: RECORDING_MBID,
    )

    result = ingest_downloaded_audio_file_with_result(
        db,
        DuplicateSkippingScanner(),
        str(music),
        str(source),
        artist="New Artist",
        album="New Album",
        title="Track",
        track_number=2,
        recording_mbid=RECORDING_MBID,
    )

    expected = music / "New Artist" / "New Album" / "02 Track.flac"
    assert result.path == str(expected)
    assert result.changed is True
    assert expected.read_bytes() == b"candidate"
    assert not stale.exists()
    assert db.get_track_local_path(RECORDING_MBID) == str(expected)
    db.close()


def test_ingest_refuses_explicit_recording_mbid_tag_mismatch(
    tmp_path, monkeypatch
):
    db = DatabaseManager(":memory:")
    source = tmp_path / "candidate.flac"
    source.write_bytes(b"candidate")
    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: RECORDING_MBID,
    )

    with pytest.raises(ValueError, match="does not match"):
        ingest_downloaded_audio_file_with_result(
            db,
            DuplicateSkippingScanner(),
            str(tmp_path / "music"),
            str(source),
            artist="Artist",
            album="Album",
            title="Track",
            track_number=1,
            recording_mbid=OTHER_RECORDING_MBID,
        )

    assert source.exists()
    assert not (tmp_path / "music").exists()
    db.close()


def test_ingest_canonicalizes_uppercase_explicit_mbid_for_db_reuse(
    tmp_path, monkeypatch
):
    db = DatabaseManager(":memory:")
    music = tmp_path / "music"
    source = tmp_path / "downloads" / "candidate.flac"
    source.parent.mkdir()
    source.write_bytes(b"candidate")
    canonical = music / "Artist" / "Album" / "Track.flac"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical")
    db.add_or_update_track(Track(
        mbid=RECORDING_MBID,
        title="Track",
        artist="Artist",
        album="Album",
        local_path=str(canonical),
    ))
    monkeypatch.setattr(
        "src.file_ingest.normalize_embedded_recording_mbid",
        lambda _path: RECORDING_MBID,
    )
    monkeypatch.setattr(
        "src.file_ingest.read_embedded_recording_mbid",
        lambda _path: RECORDING_MBID,
    )
    monkeypatch.setattr(
        "src.file_ingest.read_quality",
        lambda _path: _quality(16, 44100, 900000),
    )
    monkeypatch.setattr(
        "src.file_ingest.equal_quality_destination_satisfies",
        lambda _source, _destination: True,
    )

    result = ingest_downloaded_audio_file_with_result(
        db,
        DuplicateSkippingScanner(),
        str(music),
        str(source),
        artist="Different Sort Artist",
        album="Different Sort Album",
        title="Different Sort Title",
        track_number=7,
        recording_mbid=RECORDING_MBID.upper(),
    )

    assert result.path == str(canonical)
    assert result.changed is False
    assert not source.exists()
    assert db.get_track_local_path(RECORDING_MBID) == str(canonical)
    assert db.get_track_by_mbid(RECORDING_MBID.upper()) is None
    db.close()
