from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture

from src import library_mirror, tag_service
from src.library_mirror import mirror_imported_file


RECORDING_MBID = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
RELEASE_MBID = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
RELEASE_TRACK_MBID = "10000000-0000-4000-8000-000000000002"


def _quality(*, bits: int, rate: int, bitrate: int = 0) -> dict:
    return {
        "lossless": True,
        "bits_per_sample": bits,
        "sample_rate": rate,
        "bitrate": bitrate,
    }


def _canonical_meta() -> dict:
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
        pytest.skip("ffmpeg is required for real-FLAC mirror coverage")
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


def _prepare_real_mirror_collision(tmp_path: Path, *, stale_destination: bool):
    source_root = tmp_path / "dap-music"
    mirror_root = tmp_path / "jellyfin-music"
    source = source_root / "Artist" / "Album" / "01 Track.flac"
    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
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
            "release_mbid": "cfba04e9-aadc-4e45-ae8d-3177e5e6f463",
            "release_track_mbid": "20000000-0000-4000-8000-000000000002",
        })
    tag_service.write_tags(str(destination), destination_meta)
    tagged_destination = FLAC(str(destination))
    tagged_destination["lyrics"] = "mirror-owned lyrics"
    tagged_destination["custom-field"] = "mirror-owned value"
    if stale_destination:
        tagged_destination["musicbrainz_recordingid"] = RECORDING_MBID
        tagged_destination["totaldiscs"] = "9"
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.desc = "mirror artwork"
    picture.data = b"stable-mirror-artwork"
    tagged_destination.add_picture(picture)
    tagged_destination.save()
    os.chmod(destination, 0o640)
    return source_root, mirror_root, source, destination, picture


def _libraries(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "dap-music"
    mirror_root = tmp_path / "jellyfin-music"
    source = source_root / "Artist" / "Album" / "01 Track.flac"
    source.parent.mkdir(parents=True)
    mirror_root.mkdir()
    source.write_bytes(b"new-flac")
    return source_root, mirror_root, source


def test_missing_destination_is_copied_with_relative_path(tmp_path):
    source_root, mirror_root, source = _libraries(tmp_path)

    result = mirror_imported_file(
        str(source),
        str(source_root),
        str(mirror_root),
    )

    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
    assert result == str(destination)
    assert destination.read_bytes() == b"new-flac"


@pytest.mark.parametrize(
    ("source_quality", "destination_quality", "should_replace"),
    [
        (_quality(bits=24, rate=48000), _quality(bits=24, rate=48000), False),
        (_quality(bits=16, rate=44100), _quality(bits=24, rate=48000), False),
        (_quality(bits=24, rate=96000), _quality(bits=16, rate=44100), True),
    ],
    ids=("equal", "destination-better", "destination-worse"),
)
def test_existing_destination_is_replaced_only_for_strict_quality_upgrade(
    tmp_path,
    monkeypatch,
    source_quality,
    destination_quality,
    should_replace,
):
    source_root, mirror_root, source = _libraries(tmp_path)
    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing-flac")

    def quality_for(path):
        if os.path.samefile(path, source):
            return source_quality
        return destination_quality

    monkeypatch.setattr(library_mirror, "read_quality", quality_for)
    monkeypatch.setattr(
        library_mirror,
        "equal_quality_destination_satisfies",
        lambda _source, _destination: True,
    )

    result = mirror_imported_file(
        str(source),
        str(source_root),
        str(mirror_root),
    )

    if should_replace:
        assert result == str(destination)
        assert destination.read_bytes() == b"new-flac"
    else:
        assert result is None
        assert destination.read_bytes() == b"existing-flac"


def test_better_mirror_flac_synchronizes_only_picard_tags(tmp_path):
    source_root, mirror_root, source, destination, picture = (
        _prepare_real_mirror_collision(tmp_path, stale_destination=True)
    )
    source_before = source.read_bytes()
    source_tags = tag_service.read_current_tags(str(source))
    frame_digest = _encoded_flac_frame_digest(destination)

    result = mirror_imported_file(
        str(source),
        str(source_root),
        str(mirror_root),
    )

    tagged = FLAC(str(destination))
    assert result == str(destination)
    assert source.read_bytes() == source_before
    assert tag_service.read_current_tags(str(destination)) == source_tags
    assert _encoded_flac_frame_digest(destination) == frame_digest
    assert tagged["lyrics"] == ["mirror-owned lyrics"]
    assert tagged["custom-field"] == ["mirror-owned value"]
    assert [item.write() for item in tagged.pictures] == [picture.write()]
    assert "musicbrainz_recordingid" not in tagged
    assert "totaldiscs" not in tagged
    assert os.stat(destination).st_mode & 0o777 == 0o640


def test_better_mirror_flac_with_canonical_tags_is_byte_exact_noop(tmp_path):
    source_root, mirror_root, source, destination, _picture = (
        _prepare_real_mirror_collision(tmp_path, stale_destination=False)
    )
    source_before = source.read_bytes()
    destination_before = destination.read_bytes()

    result = mirror_imported_file(
        str(source),
        str(source_root),
        str(mirror_root),
    )

    assert result is None
    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before


def test_better_mirror_tag_sync_failure_preserves_both_files(
    tmp_path,
    monkeypatch,
):
    source_root, mirror_root, source, destination, _picture = (
        _prepare_real_mirror_collision(tmp_path, stale_destination=True)
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
        mirror_imported_file(
            str(source),
            str(source_root),
            str(mirror_root),
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before


def test_better_mirror_refuses_mismatched_destination_recording(tmp_path):
    source_root, mirror_root, source, destination, _picture = (
        _prepare_real_mirror_collision(tmp_path, stale_destination=True)
    )
    audio = FLAC(str(destination))
    audio["musicbrainz_trackid"] = (
        "cfba04e9-aadc-4e45-ae8d-3177e5e6f463"
    )
    del audio["musicbrainz_recordingid"]
    audio.save()
    source_before = source.read_bytes()
    destination_before = destination.read_bytes()

    with pytest.raises(ValueError, match="destination recording identity"):
        mirror_imported_file(
            str(source),
            str(source_root),
            str(mirror_root),
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before


def test_equal_quality_stale_tags_are_synchronized(tmp_path, monkeypatch):
    source_root, mirror_root, source = _libraries(tmp_path)
    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"stale-tags")
    equal = _quality(bits=24, rate=48000)
    monkeypatch.setattr(library_mirror, "read_quality", lambda _path: equal)
    monkeypatch.setattr(
        library_mirror,
        "equal_quality_destination_satisfies",
        lambda _source, _destination: False,
    )

    result = mirror_imported_file(
        str(source),
        str(source_root),
        str(mirror_root),
    )

    assert result == str(destination)
    assert destination.read_bytes() == b"new-flac"


def test_replacement_is_atomic_and_uses_same_directory_temp_file(
    tmp_path,
    monkeypatch,
):
    source_root, mirror_root, source = _libraries(tmp_path)
    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old-flac")
    source_quality = _quality(bits=24, rate=96000)
    destination_quality = _quality(bits=16, rate=44100)

    monkeypatch.setattr(
        library_mirror,
        "read_quality",
        lambda path: source_quality
        if os.path.samefile(path, source)
        else destination_quality,
    )
    real_replace = os.replace
    replace_calls = []

    def replace_spy(temp_path, destination_path):
        temp = Path(temp_path)
        target = Path(destination_path)
        assert temp.parent == target.parent
        assert temp.read_bytes() == b"new-flac"
        assert target.read_bytes() == b"old-flac"
        replace_calls.append((temp_path, destination_path))
        real_replace(temp_path, destination_path)

    monkeypatch.setattr(library_mirror.os, "replace", replace_spy)

    mirror_imported_file(str(source), str(source_root), str(mirror_root))

    assert len(replace_calls) == 1
    assert destination.read_bytes() == b"new-flac"
    assert not list(destination.parent.glob("*.dapmirror-*.tmp"))


def test_source_traversal_outside_library_is_rejected(tmp_path):
    source_root = tmp_path / "dap-music"
    mirror_root = tmp_path / "jellyfin-music"
    source_root.mkdir()
    mirror_root.mkdir()
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")

    with pytest.raises(ValueError, match="outside"):
        mirror_imported_file(
            str(source_root / ".." / "outside.flac"),
            str(source_root),
            str(mirror_root),
        )

    assert not list(mirror_root.rglob("*.flac"))


def test_source_symlink_escape_is_rejected(tmp_path):
    source_root = tmp_path / "dap-music"
    mirror_root = tmp_path / "jellyfin-music"
    source_root.mkdir()
    mirror_root.mkdir()
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")
    source_link = source_root / "linked.flac"
    try:
        source_link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="outside"):
        mirror_imported_file(
            str(source_link),
            str(source_root),
            str(mirror_root),
        )


@pytest.mark.parametrize(
    "reserved_name",
    [
        ".dap-reconcile-backups",
        ".DAP-RECONCILE-BACKUPS",
        ".dap-reconcile-backups.",
    ],
)
def test_reserved_controller_namespace_is_never_mirrored(
    tmp_path,
    reserved_name,
):
    source_root = tmp_path / "dap-music"
    mirror_root = tmp_path / "jellyfin-music"
    source = source_root / reserved_name / "Album" / "Track.flac"
    source.parent.mkdir(parents=True)
    mirror_root.mkdir()
    source.write_bytes(b"untrusted-flac")

    with pytest.raises(ValueError, match="reserved"):
        mirror_imported_file(str(source), str(source_root), str(mirror_root))

    assert not list(mirror_root.rglob("*.flac"))


def test_reserved_controller_namespace_alias_is_never_mirrored(tmp_path):
    source_root, mirror_root, source = _libraries(tmp_path)
    control = mirror_root / ".dap-reconcile-backups"
    control.mkdir()
    alias = mirror_root / "Artist"
    try:
        alias.symlink_to(control, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="reserved"):
        mirror_imported_file(str(source), str(source_root), str(mirror_root))

    assert not list(control.rglob("*.flac"))


def test_destination_symlink_escape_is_rejected(tmp_path):
    source_root, mirror_root, source = _libraries(tmp_path)
    outside = tmp_path / "outside-library"
    outside.mkdir()
    try:
        (mirror_root / "Artist").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="outside"):
        mirror_imported_file(str(source), str(source_root), str(mirror_root))

    assert not list(outside.rglob("*.flac"))


def test_copy_failure_preserves_destination_and_removes_temp_file(
    tmp_path,
    monkeypatch,
):
    source_root, mirror_root, source = _libraries(tmp_path)
    destination = mirror_root / "Artist" / "Album" / "01 Track.flac"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old-flac")
    source_quality = _quality(bits=24, rate=96000)
    destination_quality = _quality(bits=16, rate=44100)
    monkeypatch.setattr(
        library_mirror,
        "read_quality",
        lambda path: source_quality
        if os.path.samefile(path, source)
        else destination_quality,
    )
    monkeypatch.setattr(
        library_mirror.shutil,
        "copyfileobj",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )

    with pytest.raises(OSError, match="copy failed"):
        mirror_imported_file(str(source), str(source_root), str(mirror_root))

    assert destination.read_bytes() == b"old-flac"
    assert not list(destination.parent.glob(".*.dapmirror-*.tmp"))
