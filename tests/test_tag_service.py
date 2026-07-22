"""Tests for src.tag_service — identify (fingerprint+lookup) and write_tags."""

import os
import shutil
from inspect import signature
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import FLAC, Picture, StreamInfo

from src import tag_service


def test_public_tag_service_signature_contract():
    identify = signature(tag_service.identify_file).parameters
    assert tuple(identify) == ("filepath", "api_key", "contact")
    assert identify["contact"].default == ""

    write = signature(tag_service.write_tags).parameters
    assert tuple(write) == ("filepath", "meta")

    update_album = signature(tag_service.update_album_tags).parameters
    assert tuple(update_album) == (
        "filepath",
        "album",
        "album_artist",
        "release_mbid",
    )
    assert update_album["album_artist"].default is None
    assert update_album["release_mbid"].default is None

    atomic_write = signature(tag_service.write_tags_atomic).parameters
    assert tuple(atomic_write) == ("filepath", "meta")

    atomic_copy = signature(
        tag_service.copy_complete_picard_tags_atomic
    ).parameters
    assert tuple(atomic_copy) == (
        "source_path",
        "destination_path",
        "expected_destination_snapshot",
    )

    release_identify = signature(
        tag_service.identify_file_for_release
    ).parameters
    assert tuple(release_identify) == (
        "filepath", "api_key", "release_mbid", "recording_mbid", "contact",
    )


# ---------------------------------------------------------------------------
# _tier
# ---------------------------------------------------------------------------

def test_tier_green_at_threshold():
    assert tag_service._tier(0.90) == "green"
    assert tag_service._tier(0.99) == "green"


def test_tier_yellow_between():
    assert tag_service._tier(0.50) == "yellow"
    assert tag_service._tier(0.89) == "yellow"


def test_tier_red_below_half():
    assert tag_service._tier(0.49) == "red"
    assert tag_service._tier(0.0) == "red"
    assert tag_service._tier(float("inf")) == "red"
    assert tag_service._tier(float("nan")) == "red"


# ---------------------------------------------------------------------------
# Pure identification helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.90, "green"),
        (0.50, "yellow"),
        (0.499, "red"),
    ],
)
def test_classify_confidence_threshold_edges(score, expected):
    assert tag_service._classify_confidence(score) == expected


def test_select_best_acoustid_result_uses_score_and_keeps_first_tie():
    low = {"id": "low", "score": 0.4}
    first_high = {"id": "first-high", "score": 0.9}
    second_high = {"id": "second-high", "score": 0.9}

    selected = tag_service._select_best_acoustid_result(
        [low, first_high, second_high]
    )

    assert selected is first_high
    assert tag_service._select_best_acoustid_result([]) is None


def test_select_recording_identity_uses_first_recording_and_release():
    result = {
        "recordings": [
            {
                "id": "recording-1",
                "releases": [{"id": "release-1"}, {"id": "release-2"}],
            },
            {"id": "recording-2", "releases": [{"id": "release-3"}]},
        ]
    }

    assert tag_service._select_recording_identity(result) == (
        "recording-1",
        "release-1",
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"recordings": []},
        {"recordings": [{"id": "recording-1", "releases": []}]},
        {"recordings": [{"id": "", "releases": [{"id": "release-1"}]}]},
        {"recordings": [{"id": "recording-1", "releases": [{}]}]},
    ],
)
def test_select_recording_identity_rejects_incomplete_context(result):
    assert tag_service._select_recording_identity(result) is None


def test_select_musicbrainz_track_finds_first_match_across_media():
    matching_track = {
        "number": "7",
        "recording": {"id": "recording-1", "title": "Song"},
    }
    release = {
        "medium-list": [
            {
                "position": 1,
                "track-list": [
                    {"recording": {"id": "another-recording"}}
                ],
            },
            {"position": 2, "track-list": [matching_track]},
        ]
    }

    selected = tag_service._select_musicbrainz_track(release, "recording-1")

    assert selected == (matching_track, 2)


def test_select_musicbrainz_track_returns_none_without_match():
    release = {
        "medium-list": [
            {"position": 1, "track-list": [{"recording": {"id": "other"}}]}
        ]
    }

    assert tag_service._select_musicbrainz_track(release, "recording-1") is None


def test_build_tag_metadata_preserves_flat_payload_shape():
    release = {
        "title": "Album",
        "date": "2024-01-01",
        "artist-credit": [{"artist": {"name": "Artist"}}],
    }
    track = {
        "number": "4",
        "recording": {"id": "recording-1", "title": "Title"},
    }

    metadata = tag_service._build_tag_metadata(
        release, track, "recording-1", "release-1", 2
    )

    assert metadata == {
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "title": "Title",
        "date": "2024-01-01",
        "track_number": "4",
        "track_total": "",
        "disc_number": 2,
        "disc_total": "",
        "mbid": "recording-1",
        "release_mbid": "release-1",
        "release_track_mbid": "",
    }


def test_build_tag_metadata_defaults_missing_artist_title_and_disc():
    metadata = tag_service._build_tag_metadata(
        {"title": "Album", "artist-credit": []},
        {"recording": {}},
        "recording-1",
        "release-1",
        None,
    )

    assert metadata["artist"] == ""
    assert metadata["album_artist"] == ""
    assert metadata["title"] == ""
    assert metadata["disc_number"] == ""


# ---------------------------------------------------------------------------
# identify_file
# ---------------------------------------------------------------------------

def test_identify_file_returns_none_without_api_key(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    assert tag_service.identify_file(str(f), api_key="") is None


def test_identify_file_returns_none_when_file_missing():
    assert tag_service.identify_file("/nonexistent", api_key="k") is None


def test_identify_file_returns_none_when_acoustid_empty(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(120.0, "FINGERPRINT")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value={"status": "ok", "results": []}):
            result = tag_service.identify_file(str(f), api_key="k")
    assert result is None


def test_identify_file_assembles_candidate_on_success(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")

    acoustid_result = {
        "status": "ok",
        "results": [
            {
                "score": 0.97,
                "recordings": [
                    {
                        "id": "rec-1",
                        "releases": [{"id": "rel-1"}],
                    }
                ],
            }
        ],
    }
    mb_release = {
        "release": {
            "title": "The Album",
            "date": "2020-01-01",
            "artist-credit": [{"artist": {"name": "The Artist"}}],
            "medium-list": [
                {
                    "position": 1,
                    "track-list": [
                        {
                            "number": "3",
                            "recording": {"id": "rec-1", "title": "The Song"},
                        }
                    ],
                }
            ],
        }
    }

    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(180.0, "FP")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value=acoustid_result):
            with patch("src.tag_service.mb.get_release_by_id",
                       return_value=mb_release):
                with patch("src.tag_service.mb.configure"):
                    with patch("src.tag_service.read_current_tags",
                               return_value={"title": "old title"}):
                        candidate = tag_service.identify_file(str(f), api_key="k")

    assert candidate is not None
    assert candidate["score"] == 0.97
    assert candidate["tier"] == "green"
    assert candidate["meta"]["artist"] == "The Artist"
    assert candidate["meta"]["title"] == "The Song"
    assert candidate["meta"]["album"] == "The Album"
    assert candidate["meta"]["mbid"] == "rec-1"
    assert candidate["meta"]["release_mbid"] == "rel-1"
    assert candidate["current"] == {"title": "old title"}


def test_identify_file_none_when_recording_has_no_releases(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(120.0, "FP")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value={"results": [
                       {"score": 0.9, "recordings": [{"id": "r1", "releases": []}]}
                   ]}):
            result = tag_service.identify_file(str(f), api_key="k")
    assert result is None


def _release_with_recording(release_id, recording_id):
    return {"release": {
        "id": release_id,
        "title": "Selected Edition",
        "date": "2026-07-18",
        "artist-credit": [{"artist": {"name": "Album Artist"}}],
        "medium-list": [{
            "position": 1,
            "track-count": 1,
            "track-list": [{
                "id": "10000000-0000-4000-8000-000000000001",
                "position": 1,
                "title": "Canonical Track",
                "artist-credit": [{"artist": {"name": "Track Artist"}}],
                "recording": {
                    "id": recording_id,
                    "title": "Recording Title",
                },
            }],
        }],
    }}


def test_release_bound_identification_accepts_expected_recording_when_acoustid_omits_release(
    tmp_path,
):
    path = tmp_path / "selected.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    recording_id = "00000000-0000-4000-8000-000000000002"
    other_release_id = "461eac33-7edd-481a-a7d1-089ec6fc01af"
    response = {"results": [{
        "score": 0.96,
        "recordings": [{
            "id": recording_id,
            "releases": [{"id": other_release_id}],
        }],
    }]}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
        return_value=_release_with_recording(release_id, recording_id),
    ) as get_release, patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path), "key", release_id, recording_id,
        )

    assert candidate is not None
    assert candidate["score"] == 0.96
    assert candidate["meta"]["mbid"] == recording_id
    assert candidate["meta"]["release_mbid"] == release_id
    get_release.assert_called_once_with(
        release_id,
        includes=["artists", "recordings", "release-groups"],
    )


def test_release_bound_identification_rejects_wrong_expected_recording(
    tmp_path,
):
    path = tmp_path / "wrong-recording.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    expected_recording = "00000000-0000-4000-8000-000000000002"
    wrong_recording = "00000000-0000-4000-8000-000000000003"
    response = {"results": [{
        "score": 0.99,
        "recordings": [{
            "id": wrong_recording,
            "releases": [{"id": release_id}],
        }],
    }]}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
    ) as get_release, patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path), "key", release_id, expected_recording,
        )

    assert candidate is None
    get_release.assert_not_called()


def test_release_bound_identification_rejects_recording_absent_from_exact_release(
    tmp_path,
):
    path = tmp_path / "wrong-edition.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    expected_recording = "00000000-0000-4000-8000-000000000002"
    other_recording = "00000000-0000-4000-8000-000000000003"
    response = {"results": [{
        "score": 0.96,
        "recordings": [{"id": expected_recording, "releases": []}],
    }]}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
        return_value=_release_with_recording(release_id, other_recording),
    ) as get_release, patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path), "key", release_id, expected_recording,
        )

    assert candidate is None
    get_release.assert_called_once()


def test_release_bound_identification_without_expected_recording_requires_release_mapping(
    tmp_path,
):
    path = tmp_path / "unbound.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    recording_id = "00000000-0000-4000-8000-000000000002"
    response = {"results": [{
        "score": 0.96,
        "recordings": [{"id": recording_id, "releases": []}],
    }]}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
    ) as get_release, patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path), "key", release_id,
        )

    assert candidate is None
    get_release.assert_not_called()


def test_release_bound_identification_ignores_higher_scoring_wrong_edition(
    tmp_path,
):
    path = tmp_path / "selected.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    recording_id = "00000000-0000-4000-8000-000000000002"
    release_track_id = "10000000-0000-4000-8000-000000000002"
    response = {"results": [
        {
            "score": 0.99,
            "recordings": [{
                "id": "00000000-0000-4000-8000-000000000001",
                "releases": [{"id": "461eac33-7edd-481a-a7d1-089ec6fc01af"}],
            }],
        },
        {
            "score": 0.96,
            "recordings": [{
                "id": recording_id,
                "releases": [
                    {"id": "461eac33-7edd-481a-a7d1-089ec6fc01af"},
                    {"id": release_id},
                ],
            }],
        },
    ]}
    release = {"release": {
        "id": release_id,
        "title": "Selected Edition",
        "date": "2026-07-18",
        "artist-credit": [{"artist": {"name": "Album Artist"}}],
        "medium-list": [
            {
                "position": 1,
                "track-count": 1,
                "track-list": [{
                    "id": "10000000-0000-4000-8000-000000000001",
                    "position": 1,
                    "recording": {
                        "id": "00000000-0000-4000-8000-000000000009",
                        "title": "Disc One",
                    },
                }],
            },
            {
                "position": 2,
                "track-count": 3,
                "track-list": [{
                    "id": release_track_id,
                    "position": 2,
                    "number": "2",
                    "title": "Canonical Track",
                    "artist-credit": [{"artist": {"name": "Track Artist"}}],
                    "recording": {"id": recording_id, "title": "Recording Title"},
                }],
            },
        ],
    }}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
        return_value=release,
    ), patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path), "key", release_id, recording_id,
        )

    assert candidate is not None
    assert candidate["score"] == 0.96
    assert candidate["tier"] == "green"
    assert candidate["meta"] == {
        "artist": "Track Artist",
        "album_artist": "Album Artist",
        "album": "Selected Edition",
        "title": "Canonical Track",
        "date": "2026-07-18",
        "track_number": 2,
        "track_total": 3,
        "disc_number": 2,
        "disc_total": 2,
        "mbid": recording_id,
        "release_mbid": release_id,
        "release_track_mbid": release_track_id,
    }
    assert tag_service.is_safe_auto_candidate(
        candidate,
        expected_release_mbid=release_id,
        expected_recording_mbid=recording_id,
    )


def test_release_bound_identification_rejects_ambiguous_recordings(tmp_path):
    path = tmp_path / "ambiguous.flac"
    path.write_bytes(b"fingerprint input")
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    first_recording = "00000000-0000-4000-8000-000000000001"
    second_recording = "00000000-0000-4000-8000-000000000002"
    response = {"results": [
        {
            "score": 0.98,
            "recordings": [{
                "id": first_recording,
                "releases": [{"id": release_id}],
            }],
        },
        {
            "score": 0.96,
            "recordings": [{
                "id": second_recording,
                "releases": [{"id": release_id}],
            }],
        },
    ]}

    with patch(
        "src.tag_service.acoustid.fingerprint_file",
        return_value=(180.0, "FP"),
    ), patch(
        "src.tag_service.acoustid.lookup",
        return_value=response,
    ), patch(
        "src.tag_service.mb.get_release_by_id",
    ) as get_release, patch("src.tag_service.mb.configure"):
        candidate = tag_service.identify_file_for_release(
            str(path),
            "key",
            release_id,
        )

    assert candidate is None
    get_release.assert_not_called()


@pytest.mark.parametrize("unsafe_score", [float("inf"), float("nan"), -0.1, 1.1])
def test_safe_auto_candidate_rejects_nonfinite_or_out_of_range_score(
    unsafe_score,
):
    candidate = {
        "score": unsafe_score,
        "tier": "green",
        "meta": {
            "title": "Track",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Artist",
            "track_number": 1,
            "track_total": 1,
            "disc_number": 1,
            "disc_total": 1,
            "mbid": "00000000-0000-4000-8000-000000000001",
            "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
            "release_track_mbid": (
                "10000000-0000-4000-8000-000000000001"
            ),
        },
    }

    assert not tag_service.is_safe_auto_candidate(candidate)


# ---------------------------------------------------------------------------
# write_tags — format coverage
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_flac(tmp_path):
    """Write a minimal, empty-tagged FLAC so we can exercise the writer."""
    # We can't synthesize a real FLAC without audio data, but we can take a
    # shortcut: use mutagen to write a FLAC header and rely on the writer
    # operating on tags only. A byte-identical playable file isn't needed.
    path = tmp_path / "t.flac"
    # Minimal FLAC magic + metadata block. Easiest: copy from a fixture if
    # one exists; otherwise, skip this test on CI. We'll use a small
    # bundled-on-the-fly FLAC via mutagen's low-level API.
    # For safety, mark as xfail if we can't build one.
    path.write_bytes(b"")  # placeholder; see test_write_tags_flac below
    return str(path)


def _minimal_flac(path):
    """Write just enough FLAC bytes that mutagen can open/tag it.

    STREAMINFO needs a non-zero sample rate or mutagen.flac rejects it.
    We pack 44100 Hz, 1 channel, 16 bps, 0 total samples — bit-packed
    per the FLAC spec's STREAMINFO layout.
    """
    data = bytearray()
    data += b"fLaC"
    # Last-block flag | type 0 (STREAMINFO), length 34
    data += bytes([0x80, 0x00, 0x00, 0x22])
    # min/max block size (2 bytes each), min/max frame size (3 bytes each)
    data += (4096).to_bytes(2, "big")
    data += (4096).to_bytes(2, "big")
    data += (0).to_bytes(3, "big")
    data += (0).to_bytes(3, "big")
    # Pack: sample_rate (20 bits) | channels-1 (3 bits) | bps-1 (5 bits) | total_samples (36 bits)
    sample_rate = 44100
    channels_minus_1 = 0  # 1 channel
    bps_minus_1 = 15  # 16 bps
    total_samples = 0
    packed = (sample_rate << 44) | (channels_minus_1 << 41) | (bps_minus_1 << 36) | total_samples
    data += packed.to_bytes(8, "big")
    # MD5 (16 bytes)
    data += b"\x00" * 16
    with open(path, "wb") as f:
        f.write(data)


def test_write_tags_flac(tmp_path):
    path = tmp_path / "t.flac"
    _minimal_flac(str(path))

    meta = {
        "title": "New Title",
        "artist": "New Artist",
        "album": "New Album",
        "album_artist": "New Artist",
        "date": "2024",
        "track_number": "5",
        "disc_number": "1",
        "mbid": "rec-abc",
        "release_mbid": "rel-xyz",
    }
    container = tag_service.write_tags(str(path), meta)
    assert container == "flac"

    audio = FLAC(str(path))
    assert audio["title"] == ["New Title"]
    assert audio["artist"] == ["New Artist"]
    assert audio["album"] == ["New Album"]
    assert audio["tracknumber"] == ["5"]
    assert audio["musicbrainz_trackid"] == ["rec-abc"]
    assert audio["musicbrainz_albumid"] == ["rel-xyz"]


def _canonical_meta():
    return {
        "title": "Canonical Track",
        "artist": "Track Artist",
        "album": "Canonical Album",
        "album_artist": "Album Artist",
        "date": "2026-07-18",
        "track_number": 2,
        "track_total": 7,
        "disc_number": 2,
        "disc_total": 3,
        "mbid": "00000000-0000-4000-8000-000000000002",
        "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        "release_track_mbid": "10000000-0000-4000-8000-000000000002",
    }


def test_atomic_flac_write_preserves_audio_user_fields_and_artwork(tmp_path):
    path = tmp_path / "safe.flac"
    _minimal_flac(str(path))
    audio = FLAC(str(path))
    audio["lyrics"] = "User-owned lyrics"
    audio["rating"] = "0.8"
    audio["musicbrainz_recordingid"] = "legacy-recording-alias"
    audio["totaltracks"] = "99"
    audio["totaldiscs"] = "9"
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.desc = "front"
    picture.data = b"not-a-real-jpeg-but-stable"
    audio.add_picture(picture)
    audio.save()
    original_audio = tag_service._flac_audio_payload_digest(str(path))

    assert tag_service.write_tags_atomic(str(path), _canonical_meta()) == "flac"

    tagged = FLAC(str(path))
    assert tag_service._flac_audio_payload_digest(str(path)) == original_audio
    assert tagged["lyrics"] == ["User-owned lyrics"]
    assert tagged["rating"] == ["0.8"]
    assert [item.write() for item in tagged.pictures] == [picture.write()]
    assert "musicbrainz_recordingid" not in tagged
    assert "totaltracks" not in tagged
    assert "totaldiscs" not in tagged
    assert tagged["tracknumber"] == ["2"]
    assert tagged["tracktotal"] == ["7"]
    assert tagged["discnumber"] == ["2"]
    assert tagged["disctotal"] == ["3"]
    # Vorbis/Picard mapping: TRACKID is the recording; RELEASETRACKID is the
    # release-specific track entity.
    assert tagged["musicbrainz_trackid"] == [
        "00000000-0000-4000-8000-000000000002"
    ]
    assert tagged["musicbrainz_releasetrackid"] == [
        "10000000-0000-4000-8000-000000000002"
    ]


def test_atomic_flac_write_failure_leaves_original_byte_exact(tmp_path):
    path = tmp_path / "rollback.flac"
    _minimal_flac(str(path))
    before = path.read_bytes()

    with patch("src.tag_service.write_tags", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            tag_service.write_tags_atomic(str(path), _canonical_meta())

    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.daptag-*.flac"))


def test_picard_tag_copy_refuses_concurrent_source_inode_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    replacement = tmp_path / "source-replacement.flac"
    _minimal_flac(str(source))
    _minimal_flac(str(destination))
    tag_service.write_tags(str(source), _canonical_meta())
    stale_meta = dict(_canonical_meta())
    stale_meta["title"] = "Stale"
    tag_service.write_tags(str(destination), stale_meta)
    shutil.copyfile(source, replacement)
    destination_before = destination.read_bytes()
    real_verify = tag_service._source_flac_tags_are_verified

    def replace_source(path, meta):
        verified = real_verify(path, meta)
        os.replace(replacement, source)
        return verified

    monkeypatch.setattr(
        tag_service,
        "_source_flac_tags_are_verified",
        replace_source,
    )

    with pytest.raises(OSError, match="source changed"):
        tag_service.copy_complete_picard_tags_atomic(
            str(source),
            str(destination),
        )

    assert destination.read_bytes() == destination_before
    assert not list(tmp_path.glob(".*.daptag-*.flac"))


def test_picard_tag_copy_refuses_concurrent_destination_inode_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    concurrent = tmp_path / "concurrent.flac"
    _minimal_flac(str(source))
    _minimal_flac(str(destination))
    _minimal_flac(str(concurrent))
    tag_service.write_tags(str(source), _canonical_meta())
    stale_meta = dict(_canonical_meta())
    stale_meta["title"] = "Stale"
    tag_service.write_tags(str(destination), stale_meta)
    concurrent_meta = dict(_canonical_meta())
    concurrent_meta["title"] = "Concurrent"
    tag_service.write_tags(str(concurrent), concurrent_meta)
    source_before = source.read_bytes()
    concurrent_bytes = concurrent.read_bytes()
    real_write = tag_service.write_tags

    def write_then_replace(path, meta):
        result = real_write(path, meta)
        os.replace(concurrent, destination)
        return result

    monkeypatch.setattr(tag_service, "write_tags", write_then_replace)

    with pytest.raises(OSError, match="changed concurrently"):
        tag_service.copy_complete_picard_tags_atomic(
            str(source),
            str(destination),
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == concurrent_bytes
    assert not list(tmp_path.glob(".*.daptag-*.flac"))


@pytest.mark.parametrize(
    "identity_state",
    ("missing", "mismatched", "conflicting", "ambiguous"),
)
def test_picard_tag_copy_requires_matching_physical_destination_recording(
    tmp_path,
    identity_state,
):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    _minimal_flac(str(source))
    _minimal_flac(str(destination))
    tag_service.write_tags(str(source), _canonical_meta())
    stale_meta = dict(_canonical_meta())
    stale_meta["title"] = "Stale"
    tag_service.write_tags(str(destination), stale_meta)
    audio = FLAC(str(destination))
    if identity_state == "missing":
        del audio["musicbrainz_trackid"]
    elif identity_state == "mismatched":
        audio["musicbrainz_trackid"] = (
            "30000000-0000-4000-8000-000000000003"
        )
    elif identity_state == "conflicting":
        audio["musicbrainz_recordingid"] = (
            "30000000-0000-4000-8000-000000000003"
        )
    else:
        audio["musicbrainz_trackid"] = [
            _canonical_meta()["mbid"],
            "30000000-0000-4000-8000-000000000003",
        ]
    audio.save()
    source_before = source.read_bytes()
    destination_before = destination.read_bytes()

    with pytest.raises(ValueError, match="destination recording identity"):
        tag_service.copy_complete_picard_tags_atomic(
            str(source),
            str(destination),
        )

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before


def test_picard_tag_copy_accepts_matching_legacy_recordingid_fallback(
    tmp_path,
):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    _minimal_flac(str(source))
    _minimal_flac(str(destination))
    tag_service.write_tags(str(source), _canonical_meta())
    stale_meta = dict(_canonical_meta())
    stale_meta["title"] = "Stale"
    tag_service.write_tags(str(destination), stale_meta)
    audio = FLAC(str(destination))
    del audio["musicbrainz_trackid"]
    audio["musicbrainz_recordingid"] = _canonical_meta()["mbid"].upper()
    audio.save()

    assert tag_service.copy_complete_picard_tags_atomic(
        str(source),
        str(destination),
    ) is True

    tagged = FLAC(str(destination))
    assert tagged["musicbrainz_trackid"] == [_canonical_meta()["mbid"]]
    assert "musicbrainz_recordingid" not in tagged


def test_complete_picard_tags_require_ids_and_ordering_totals(tmp_path):
    path = tmp_path / "complete.flac"
    _minimal_flac(str(path))
    tag_service.write_tags(str(path), _canonical_meta())
    assert tag_service.has_complete_picard_tags(str(path)) is True

    audio = FLAC(str(path))
    del audio["musicbrainz_releasetrackid"]
    audio.save()
    assert tag_service.has_complete_picard_tags(str(path)) is False


def test_complete_picard_tags_require_trackid_not_recordingid_substitute(
    tmp_path,
):
    path = tmp_path / "legacy-recordingid.flac"
    _minimal_flac(str(path))
    tag_service.write_tags(str(path), _canonical_meta())
    audio = FLAC(str(path))
    del audio["musicbrainz_trackid"]
    audio["musicbrainz_recordingid"] = _canonical_meta()["mbid"]
    audio.save()

    assert tag_service.has_complete_picard_tags(str(path)) is False

    audio = FLAC(str(path))
    audio["musicbrainz_trackid"] = _canonical_meta()["mbid"]
    audio.save()
    assert tag_service.has_complete_picard_tags(str(path)) is False

    audio = FLAC(str(path))
    del audio["musicbrainz_recordingid"]
    audio.save()
    assert tag_service.has_complete_picard_tags(str(path)) is True

    audio = FLAC(str(path))
    audio["musicbrainz_recordingid"] = (
        "30000000-0000-4000-8000-000000000003"
    )
    audio.save()
    assert tag_service.has_complete_picard_tags(str(path)) is False


def test_write_tags_unsupported_extension_raises(tmp_path):
    path = tmp_path / "t.wav"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="unsupported"):
        tag_service.write_tags(str(path), {"title": "x"})


# ---------------------------------------------------------------------------
# read_current_tags
# ---------------------------------------------------------------------------

def test_read_current_tags_missing_file_returns_empty():
    assert tag_service.read_current_tags("/nonexistent/file.flac") == {}


def test_read_current_tags_returns_values_after_write(tmp_path):
    path = tmp_path / "t.flac"
    _minimal_flac(str(path))
    tag_service.write_tags(str(path), {
        "title": "Song", "artist": "A", "album": "B",
        "date": "", "track_number": "", "disc_number": "",
        "mbid": "m1", "release_mbid": "",
    })
    tags = tag_service.read_current_tags(str(path))
    assert tags["title"] == "Song"
    assert tags["artist"] == "A"
    assert tags["album"] == "B"
    assert tags["mbid"] == "m1"
