from types import SimpleNamespace

from mutagen.flac import FLAC

from src.audio_quality import (
    equal_quality_destination_satisfies,
    quality_tuple,
    meets_target,
    library_path_for_track,
)


def _write_identity_flac(path, *, recording_mbid, title="Track"):
    data = bytearray(b"fLaC")
    data += bytes([0x80, 0x00, 0x00, 0x22])
    data += (4096).to_bytes(2, "big") * 2
    data += (0).to_bytes(3, "big") * 2
    data += ((44100 << 44) | (15 << 36)).to_bytes(8, "big")
    data += b"\x00" * 16
    path.write_bytes(data)
    audio = FLAC(path)
    audio["musicbrainz_trackid"] = recording_mbid
    audio["musicbrainz_albumid"] = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    audio["musicbrainz_releasetrackid"] = (
        "10000000-0000-4000-8000-000000000001"
    )
    audio["title"] = title
    audio["artist"] = "Artist"
    audio["album"] = "Album"
    audio["albumartist"] = "Artist"
    audio["date"] = "2026"
    audio["tracknumber"] = "1"
    audio["tracktotal"] = "10"
    audio["discnumber"] = "1"
    audio["disctotal"] = "1"
    audio.save()


def q(lossless=False, bits=0, sr=0, br=0):
    return {
        "lossless": lossless,
        "bits_per_sample": bits,
        "sample_rate": sr,
        "bitrate": br,
    }


def test_quality_tuple_orders_lossless_above_lossy():
    flac = q(lossless=True, bits=16, sr=44100, br=900000)
    mp3 = q(lossless=False, bits=0, sr=44100, br=320000)
    assert quality_tuple(flac) > quality_tuple(mp3)


def test_quality_tuple_orders_by_bit_depth_then_sample_rate():
    cd = q(lossless=True, bits=16, sr=44100, br=900000)
    hires = q(lossless=True, bits=24, sr=96000, br=4000000)
    assert quality_tuple(hires) > quality_tuple(cd)


def test_quality_tuple_orders_lossy_by_bitrate():
    assert quality_tuple(q(br=320000)) > quality_tuple(q(br=128000))


def test_empty_descriptor_sorts_lowest():
    assert quality_tuple(None) == (0, 0, 0, 0)
    assert quality_tuple(None) < quality_tuple(q(br=64000))


def test_meets_target_equal_quality_is_good_enough():
    a = q(lossless=True, bits=16, sr=44100, br=900000)
    assert meets_target(a, dict(a))


def test_meets_target_worse_quality_fails():
    target = q(lossless=True, bits=16, sr=44100, br=900000)
    candidate = q(lossless=False, bits=0, sr=44100, br=320000)
    assert not meets_target(candidate, target)


def test_meets_target_better_quality_passes():
    target = q(lossless=False, bits=0, sr=44100, br=320000)
    candidate = q(lossless=True, bits=24, sr=96000, br=4000000)
    assert meets_target(candidate, target)


def test_equal_quality_flac_requires_canonical_tags_to_match(tmp_path):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    recording = "00000000-0000-4000-8000-000000000001"
    _write_identity_flac(source, recording_mbid=recording)
    _write_identity_flac(destination, recording_mbid=recording)

    assert equal_quality_destination_satisfies(
        str(source),
        str(destination),
    )

    stale = FLAC(destination)
    stale["musicbrainz_trackid"] = (
        "00000000-0000-4000-8000-000000000002"
    )
    stale.save()

    assert not equal_quality_destination_satisfies(
        str(source),
        str(destination),
    )


def test_equal_quality_flac_with_same_audio_converges_display_tags(tmp_path):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    recording = "00000000-0000-4000-8000-000000000001"
    _write_identity_flac(source, recording_mbid=recording, title="Correct")
    _write_identity_flac(destination, recording_mbid=recording, title="Stale")

    assert not equal_quality_destination_satisfies(
        str(source),
        str(destination),
    )


def test_library_path_for_track_sanitises_and_numbers():
    track = SimpleNamespace(
        artist="AC/DC", album="Back: In Black", title='Hells "Bells"',
        track_number=1,
    )
    path = library_path_for_track("/music", track)
    assert path == "/music/AC_DC/Back_ In Black/01 Hells _Bells_.flac"


def test_library_path_for_track_defaults_missing_fields():
    track = SimpleNamespace(artist=None, album=None, title=None, track_number=None)
    path = library_path_for_track("/music", track)
    assert path == "/music/Unknown Artist/Unknown Album/Unknown Title.flac"


def test_library_path_for_track_disambiguates_later_discs():
    disc_one = SimpleNamespace(
        artist="Artist", album="Album", title="Intro",
        track_number=1, disc_number=1,
    )
    disc_two = SimpleNamespace(
        artist="Artist", album="Album", title="Intro",
        track_number=1, disc_number=2,
    )

    first = library_path_for_track("/music", disc_one)
    second = library_path_for_track("/music", disc_two)

    assert first == "/music/Artist/Album/01 Intro.flac"
    assert second == "/music/Artist/Album/02-01 Intro.flac"
    assert first != second
