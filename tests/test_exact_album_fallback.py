import copy
import hashlib
import inspect
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import exact_album_fallback as fallback


RELEASE = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
RECORDINGS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
)
RELEASE_TRACKS = (
    "10000000-0000-4000-8000-000000000001",
    "10000000-0000-4000-8000-000000000002",
)
TRACK_LENGTHS = (120_000, 240_000)
ISRCS = ("QM24S1926720", "QM24S1927163")


class _FakeInfo:
    def __init__(self, length_seconds):
        self.length = length_seconds
        self.sample_rate = 44_100
        self.channels = 2
        self.bits_per_sample = 24


class _FakeFlac(dict):
    def __init__(self, tags, length_ms):
        super().__init__({
            key: value if isinstance(value, list) else [str(value)]
            for key, value in tags.items()
        })
        self.info = _FakeInfo(length_ms / 1000.0)


def _manifest():
    tracks = []
    for position, (recording, release_track) in enumerate(
        zip(RECORDINGS, RELEASE_TRACKS),
        start=1,
    ):
        tracks.append({
            "position": position,
            "medium_position": 1,
            "track_position": position,
            "track_number": str(position),
            "recording_mbid": recording,
            "title": "Opening (intro)" if position == 1 else "Closing (outro)",
            "artist": "Album Artist",
            "date": "2020-02-07",
            "track_total": 2,
            "disc_total": 1,
            "release_track_mbid": release_track,
        })
    return {
        "release_mbid": RELEASE,
        "artist": "Album Artist",
        "title": "Exact Album",
        "track_count": 2,
        "recording_mbids": RECORDINGS,
        "tracks": tuple(tracks),
    }


def _release_response():
    tracks = []
    for position, (recording, release_track, length_ms) in enumerate(
        zip(RECORDINGS, RELEASE_TRACKS, TRACK_LENGTHS),
        start=1,
    ):
        tracks.append({
            "id": release_track,
            "position": position,
            "number": str(position),
            "title": "Opening (intro)" if position == 1 else "Closing (outro)",
            "length": length_ms,
            "artist-credit": [{"artist": {"name": "Album Artist"}}],
            "recording": {
                "id": recording,
                "title": "Opening (intro)" if position == 1 else "Closing (outro)",
                "artist-credit": [{"artist": {"name": "Album Artist"}}],
                "isrc-list": [ISRCS[position - 1]],
            },
        })
    return {"release": {
        "id": RELEASE,
        "title": "Exact Album",
        "artist-credit": [{"artist": {"name": "Album Artist"}}],
        "track-count": 2,
        "medium-list": [{
            "position": 1,
            "track-count": 2,
            "track-list": tracks,
        }],
        "date": "2020-02-07",
        "country": "XW",
        "status": "Official",
        "release-group": {"primary-type": "Album"},
    }}


def _tags(position):
    return {
        "title": "Opening" if position == 1 else "Closing",
        "artist": "  ALBUM   artist ",
        "album": "exact album",
        "albumartist": "Album Artist",
        "date": "2020-02-07",
        "isrc": ISRCS[position - 1],
        "tracknumber": str(position),
        "tracktotal": "2",
        "discnumber": "1",
        "disctotal": "1",
    }


def test_complete_source_tags_bind_one_exact_manifest_recording():
    tags = {
        "title": "Closing",
        "artist": " album ARTIST ",
        "album": "Exact Album (EP)",
        "album_artist": "Album Artist",
        "date": "2020-02-07",
        "track_number": "2",
        "track_total": "2",
        "disc_number": "1",
        "disc_total": "1",
        "mbid": "",
        "release_mbid": "",
        "release_track_mbid": "",
    }

    assert fallback.match_exact_manifest_recording(_manifest(), tags) == (
        RECORDINGS[1]
    )
    assert fallback.match_exact_manifest_recording(
        _manifest(),
        {**tags, "title": "Different Track"},
    ) == ""


@pytest.fixture
def staged_album(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    paths = []
    audio = {}
    for position, length_ms in enumerate(TRACK_LENGTHS, start=1):
        path = staging / f"{position:02d}.flac"
        path.write_bytes(f"untouched-{position}".encode())
        paths.append(str(path))
        audio[os.path.realpath(path)] = _FakeFlac(_tags(position), length_ms)
    return staging, paths, audio


def _plan(
    staged_album,
    *,
    manifest=None,
    response=None,
    coverage_states=("no_results", "no_results"),
):
    staging, paths, audio = staged_album
    states = iter(coverage_states)

    def probe(_path, _api_key):
        return SimpleNamespace(state=next(states))

    with patch(
        "src.exact_album_fallback.FLAC",
        side_effect=lambda path: audio[os.path.realpath(path)],
    ), patch.object(
        fallback.tag_service,
        "probe_acoustid_coverage",
        side_effect=probe,
        create=True,
    ) as coverage, patch.object(
        fallback.mb,
        "configure",
    ) as configure, patch.object(
        fallback.mb,
        "get_release_by_id",
        return_value=response or _release_response(),
    ) as get_release, patch.object(
        fallback.tag_service,
        "write_tags_atomic",
    ) as write_tags, patch.object(
        fallback.tag_service,
        "flac_audio_payload_digest",
        side_effect=lambda path: hashlib.sha256(
            os.path.basename(path).encode()
        ).hexdigest(),
        create=True,
    ):
        result = fallback.build_exact_album_fallback_plan(
            paths,
            str(staging),
            manifest or _manifest(),
            "api-key",
            "owner@example.test",
        )
    return result, coverage, configure, get_release, write_tags


def test_public_plan_contract_is_stable_and_read_only(staged_album):
    parameters = inspect.signature(
        fallback.build_exact_album_fallback_plan
    ).parameters
    assert tuple(parameters) == (
        "file_paths",
        "staging_root",
        "album_manifest",
        "api_key",
        "contact",
    )
    assert parameters["contact"].default == ""

    staging, paths, _ = staged_album
    before = {path: open(path, "rb").read() for path in paths}
    result, coverage, configure, get_release, write_tags = _plan(staged_album)

    assert result.accepted is True
    assert result.reason == ""
    assert dict(result.recording_mbid_by_path) == dict(zip(paths, RECORDINGS))
    assert dict(result.audio_payload_sha256_by_path) == {
        path: hashlib.sha256(os.path.basename(path).encode()).hexdigest()
        for path in paths
    }
    assert set(result.file_snapshot_by_path) == set(paths)
    with pytest.raises(TypeError):
        result.recording_mbid_by_path[paths[0]] = RECORDINGS[1]
    with pytest.raises(TypeError):
        result.audio_payload_sha256_by_path[paths[0]] = "changed"
    assert {path: open(path, "rb").read() for path in paths} == before
    assert coverage.call_count == 2
    configure.assert_called_once_with("owner@example.test")
    get_release.assert_called_once_with(
        RELEASE,
        includes=["artists", "release-groups", "recordings", "isrcs"],
    )
    write_tags.assert_not_called()
    assert staging.is_dir()


@pytest.mark.parametrize("state", ["has_results", "unavailable", "unknown"])
def test_only_explicit_zero_acoustid_coverage_is_eligible(
    staged_album,
    state,
):
    result, coverage, _, get_release, _ = _plan(
        staged_album,
        coverage_states=(state,),
    )

    assert result.accepted is False
    assert not result.recording_mbid_by_path
    assert "AcoustID" in result.reason
    assert coverage.call_count == 1
    get_release.assert_not_called()


def test_acoustid_probe_failure_is_not_treated_as_no_coverage(staged_album):
    staging, paths, audio = staged_album
    with patch(
        "src.exact_album_fallback.FLAC",
        side_effect=lambda path: audio[os.path.realpath(path)],
    ), patch.object(
        fallback.tag_service,
        "probe_acoustid_coverage",
        side_effect=RuntimeError("network down"),
        create=True,
    ), patch.object(fallback.mb, "get_release_by_id") as get_release:
        result = fallback.build_exact_album_fallback_plan(
            paths,
            str(staging),
            _manifest(),
            "api-key",
        )

    assert result.accepted is False
    assert "could not be verified" in result.reason
    get_release.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["tracks"][1].update({
            "recording_mbid": RECORDINGS[0],
        }),
        lambda manifest: manifest["tracks"][1].update({"position": 1}),
        lambda manifest: manifest["tracks"][1].update({"track_position": 1}),
        lambda manifest: manifest["tracks"][1].update({"date": ""}),
        lambda manifest: manifest["tracks"][1].update({"track_total": 3}),
        lambda manifest: manifest.update({
            "recording_mbids": tuple(reversed(RECORDINGS)),
        }),
        lambda manifest: manifest.update({"track_count": 3}),
    ],
)
def test_incomplete_or_non_bijective_persisted_manifests_are_rejected(
    staged_album,
    mutation,
):
    manifest = copy.deepcopy(_manifest())
    mutation(manifest)
    result, coverage, _, get_release, _ = _plan(
        staged_album,
        manifest=manifest,
    )

    assert result.accepted is False
    assert "persisted" in result.reason.lower()
    coverage.assert_not_called()
    get_release.assert_not_called()


def test_staged_set_must_be_exact_regular_contained_flacs(staged_album, tmp_path):
    staging, paths, audio = staged_album
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")
    cases = [
        paths[:1],
        [paths[0], str(outside)],
    ]
    lossy = staging / "02.mp3"
    lossy.write_bytes(b"lossy")
    cases.append([paths[0], str(lossy)])
    duplicate_link = staging / "duplicate.flac"
    duplicate_link.symlink_to(paths[0])
    cases.append([str(duplicate_link), paths[1]])

    for file_paths in cases:
        with patch(
            "src.exact_album_fallback.FLAC",
            side_effect=lambda path: audio[os.path.realpath(path)],
        ), patch.object(
            fallback.tag_service,
            "probe_acoustid_coverage",
            create=True,
        ) as coverage, patch.object(
            fallback.mb,
            "get_release_by_id",
        ) as get_release:
            result = fallback.build_exact_album_fallback_plan(
                file_paths,
                str(staging),
                _manifest(),
                "api-key",
            )
        assert result.accepted is False
        coverage.assert_not_called()
        get_release.assert_not_called()


@pytest.mark.parametrize(
    "extra_name",
    ["bonus.flac", "bonus.mp3", "bonus.opus", "02.flac.incomplete"],
)
def test_unlisted_or_incomplete_audio_prevents_a_whole_set_plan(
    staged_album,
    extra_name,
):
    staging, _, _ = staged_album
    (staging / extra_name).write_bytes(b"unexpected")

    result, coverage, _, get_release, _ = _plan(staged_album)

    assert result.accepted is False
    assert "staging" in result.reason.lower() or "complete audio set" in result.reason
    coverage.assert_not_called()
    get_release.assert_not_called()


@pytest.mark.parametrize(
    ("response_mutation", "reason_fragment"),
    [
        (
            lambda response: response["release"]["medium-list"][0][
                "track-list"
            ][1].update({"title": "Different"}),
            "no longer matches",
        ),
        (
            lambda response: response["release"]["medium-list"][0][
                "track-list"
            ][0].update({"length": 0}),
            "duration or ISRC",
        ),
        (
            lambda response: response["release"]["medium-list"][0][
                "track-list"
            ][0]["recording"].update({"isrc-list": []}),
            "duration or ISRC",
        ),
        (
            lambda response: response["release"].update({"title": "Other"}),
            "no longer matches",
        ),
    ],
)
def test_refetched_release_must_match_signature_and_have_all_lengths(
    staged_album,
    response_mutation,
    reason_fragment,
):
    response = copy.deepcopy(_release_response())
    response_mutation(response)
    result, coverage, _, get_release, _ = _plan(
        staged_album,
        response=response,
    )

    assert result.accepted is False
    assert reason_fragment in result.reason
    assert coverage.call_count == 2
    get_release.assert_called_once()


@pytest.mark.parametrize(
    ("field", "value", "reason_fragment"),
    [
        ("title", "Opening (live)", "title"),
        ("artist", "Another Artist", "artist"),
        ("album", "Another Album", "album"),
        ("albumartist", "Another Artist", "album artist"),
        ("date", "2020", "date"),
        ("isrc", "USAAA0000000", "isrc"),
        ("tracktotal", "3", "track position or total"),
        ("discnumber", "2", "bijection"),
    ],
)
def test_any_strict_source_metadata_mismatch_rejects_the_whole_set(
    staged_album,
    field,
    value,
    reason_fragment,
):
    _, _, audio = staged_album
    first = next(iter(audio.values()))
    first[field] = [value]

    result, _, _, _, write_tags = _plan(staged_album)

    assert result.accepted is False
    assert reason_fragment in result.reason.lower()
    assert not result.recording_mbid_by_path
    write_tags.assert_not_called()


@pytest.mark.parametrize(
    ("key", "value", "reason_fragment"),
    [
        ("musicbrainz_trackid", RECORDINGS[1], "recording identity"),
        (
            "musicbrainz_albumid",
            "461eac33-7edd-481a-a7d1-089ec6fc01af",
            "release identity",
        ),
        (
            "musicbrainz_releasetrackid",
            RELEASE_TRACKS[1],
            "release-track identity",
        ),
    ],
)
def test_embedded_musicbrainz_ids_may_be_absent_or_exact_only(
    staged_album,
    key,
    value,
    reason_fragment,
):
    _, _, audio = staged_album
    first = next(iter(audio.values()))
    first[key] = [value]

    result, _, _, _, _ = _plan(staged_album)

    assert result.accepted is False
    assert reason_fragment in result.reason


def test_matching_embedded_ids_and_composite_totals_are_accepted(staged_album):
    _, _, audio = staged_album
    for position, (recording, release_track, fake) in enumerate(
        zip(RECORDINGS, RELEASE_TRACKS, audio.values()),
        start=1,
    ):
        fake["tracknumber"] = [f"{position}/2"]
        fake.pop("tracktotal")
        fake["discnumber"] = ["1/1"]
        fake.pop("disctotal")
        fake["musicbrainz_trackid"] = [recording.upper()]
        fake["musicbrainz_albumid"] = [RELEASE.upper()]
        fake["musicbrainz_releasetrackid"] = [release_track.upper()]

    result, _, _, _, _ = _plan(staged_album)

    assert result.accepted is True


@pytest.mark.parametrize("qualifier", ["intro", "interlude", "outro", "skit"])
def test_title_omission_is_limited_to_the_four_trailing_qualifiers(qualifier):
    assert fallback._title_matches("Song", f"Song ({qualifier})") is True
    assert fallback._title_matches(f"Song ({qualifier})", "Song") is False
    assert fallback._title_matches("Song", "Song (live)") is False
    assert fallback._title_matches("Different", f"Song ({qualifier})") is False


def test_duration_tolerance_is_bounded_and_a_large_delta_rejects(staged_album):
    assert fallback._duration_tolerance_ms(120_000) == 1500
    assert fallback._duration_tolerance_ms(400_000) == 2000
    assert fallback._duration_tolerance_ms(1_000_000) == 3000

    _, _, audio = staged_album
    first = next(iter(audio.values()))
    first.info.length = (TRACK_LENGTHS[0] + 1501) / 1000.0
    result, _, _, _, _ = _plan(staged_album)

    assert result.accepted is False
    assert "duration" in result.reason


def test_duplicate_staged_positions_reject_before_any_mutation(staged_album):
    _, paths, audio = staged_album
    second = audio[os.path.realpath(paths[1])]
    second["tracknumber"] = ["1"]

    result, _, _, _, write_tags = _plan(staged_album)

    assert result.accepted is False
    assert "bijection" in result.reason
    write_tags.assert_not_called()
