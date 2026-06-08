from types import SimpleNamespace

from src.audio_quality import (
    quality_tuple,
    meets_target,
    library_path_for_track,
)


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
