from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.services.tag_application_service import (
    ApplyTagRequest,
    TagApplicationResult,
    apply_track_tags,
    identify_track,
    prepare_tag_apply,
)


@dataclass
class FakeTrack:
    mbid: str
    title: str
    artist: str
    album: str | None = None
    local_path: str | None = None


class FakeContext:
    def __init__(self, db, events):
        self.db = db
        self.events = events

    def __enter__(self):
        self.events.append("db-enter")
        return self.db

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("db-exit")


class FakeStore:
    def __init__(self, track, events):
        self.track = track
        self.events = events

    def get_track_by_mbid(self, mbid):
        self.events.append(("lookup", mbid))
        return self.track

    def soft_delete_track(self, mbid):
        self.events.append(("soft-delete", mbid))
        return True

    def add_or_update_track(self, track):
        self.events.append(("upsert", track))

    def set_track_tag_tier(self, mbid, tier, score):
        self.events.append(("tier", mbid, tier, score))
        return True


def _factory(db, events):
    return lambda _path: FakeContext(db, events)


def test_identify_closes_database_before_contact_and_fingerprint():
    events = []
    track = FakeTrack(
        mbid="track-1",
        title="Old",
        artist="Artist",
        local_path="/music/track.flac",
    )
    db = FakeStore(track, events)
    candidate = {
        "score": 0.95,
        "tier": "green",
        "meta": {"title": "New"},
        "current": {"title": "Old"},
    }

    def contact_provider():
        events.append("contact")
        return "person@example.test"

    def identifier(path, api_key, contact):
        events.append(("identify", path, api_key, contact))
        return candidate

    result = identify_track(
        db_path="library.db",
        database_factory=_factory(db, events),
        mbid="track-1",
        api_key="key",
        contact_provider=contact_provider,
        identify_file=identifier,
        read_current_tags=MagicMock(),
    )

    assert result.payload["candidate"] == candidate
    assert events == [
        "db-enter",
        ("lookup", "track-1"),
        "db-exit",
        "contact",
        ("identify", "/music/track.flac", "key", "person@example.test"),
    ]


def test_identify_no_match_reads_current_tags_after_identifier():
    events = []
    track = FakeTrack(
        mbid="track-1",
        title="Old",
        artist="Artist",
        local_path="/music/track.flac",
    )
    db = FakeStore(track, events)

    def identify(*_args):
        events.append("identify")
        return None

    def read_current(path):
        events.append(("read-current", path))
        return {"title": "Old"}

    result = identify_track(
        db_path="library.db",
        database_factory=_factory(db, events),
        mbid="track-1",
        api_key="key",
        contact_provider=lambda: "contact",
        identify_file=identify,
        read_current_tags=read_current,
    )

    assert result.payload == {
        "success": True,
        "candidate": None,
        "message": "no match",
        "current": {"title": "Old"},
    }
    assert events[-2:] == [
        "identify",
        ("read-current", "/music/track.flac"),
    ]


def test_identify_translates_only_identifier_failures():
    events = []
    db = FakeStore(
        FakeTrack("track-1", "Old", "Artist", local_path="/track.flac"),
        events,
    )
    event_logger = MagicMock()

    result = identify_track(
        db_path="library.db",
        database_factory=_factory(db, events),
        mbid="track-1",
        api_key="key",
        contact_provider=lambda: "contact",
        identify_file=MagicMock(side_effect=RuntimeError("fingerprint failed")),
        read_current_tags=MagicMock(),
        event_logger=event_logger,
    )

    assert result == TagApplicationResult(
        {"success": False, "message": "fingerprint failed"},
        500,
    )
    event_logger.error.assert_called_once_with(
        "tag_identify failed for track-1: fingerprint failed",
        exc_info=True,
    )


def test_identify_does_not_translate_contact_provider_failures():
    events = []
    db = FakeStore(
        FakeTrack("track-1", "Old", "Artist", local_path="/track.flac"),
        events,
    )
    identify_file = MagicMock()

    with pytest.raises(RuntimeError, match="contact unavailable"):
        identify_track(
            db_path="library.db",
            database_factory=_factory(db, events),
            mbid="track-1",
            api_key="key",
            contact_provider=MagicMock(
                side_effect=RuntimeError("contact unavailable")
            ),
            identify_file=identify_file,
            read_current_tags=MagicMock(),
        )

    identify_file.assert_not_called()


def test_prepare_apply_keeps_score_lookup_until_after_catalog_upsert():
    events = []

    class TrackingBody(dict):
        def get(self, key, default=None):
            events.append(("body-get", key))
            return super().get(key, default)

    body = TrackingBody(
        meta={"title": "New", "mbid": "new-mbid"},
        score="0.72",
    )
    prepared = prepare_tag_apply(body)

    assert isinstance(prepared, ApplyTagRequest)
    assert prepared.data is body
    assert events == [("body-get", "meta")]

    track = FakeTrack(
        mbid="old-mbid",
        title="Old",
        artist="Old Artist",
        album="Old Album",
        local_path="/music/track.flac",
    )
    db = FakeStore(track, events)

    def write(path, meta):
        events.append(("write", path, meta))
        return "flac"

    def track_factory(**values):
        events.append(("build-track", values))
        return FakeTrack(**values)

    result = apply_track_tags(
        db_path="library.db",
        database_factory=_factory(db, events),
        mbid="old-mbid",
        prepared=prepared,
        write_tags=write,
        track_factory=track_factory,
    )

    assert result.payload == {
        "success": True,
        "container": "flac",
        "mbid": "new-mbid",
        "previous_mbid": "old-mbid",
    }
    event_names = [
        item[0] if isinstance(item, tuple) else item
        for item in events
    ]
    assert event_names == [
        "body-get",
        "db-enter",
        "lookup",
        "write",
        "build-track",
        "soft-delete",
        "upsert",
        "body-get",
        "tier",
        "db-exit",
    ]
    # Score remains the final request-body read, after the catalog upsert.
    assert events[-3] == ("body-get", "score")
    assert events[-2] == ("tier", "new-mbid", "green", 0.72)


def test_apply_value_error_returns_400_without_catalog_mutation():
    events = []
    db = FakeStore(
        FakeTrack("track-1", "Old", "Artist", local_path="/track.wav"),
        events,
    )
    prepared = ApplyTagRequest(
        meta={"title": "New"},
        data={"meta": {"title": "New"}},
    )

    result = apply_track_tags(
        db_path="library.db",
        database_factory=_factory(db, events),
        mbid="track-1",
        prepared=prepared,
        write_tags=MagicMock(side_effect=ValueError("unsupported")),
        track_factory=MagicMock(),
    )

    assert result == TagApplicationResult(
        {"success": False, "message": "unsupported"},
        400,
    )
    assert events == [
        "db-enter",
        ("lookup", "track-1"),
        "db-exit",
    ]


def test_apply_does_not_translate_catalog_persistence_failures():
    events = []

    class FailingStore(FakeStore):
        def add_or_update_track(self, track):
            super().add_or_update_track(track)
            raise RuntimeError("catalog unavailable")

    db = FailingStore(
        FakeTrack("track-1", "Old", "Artist", local_path="/track.flac"),
        events,
    )
    prepared = ApplyTagRequest(
        meta={"title": "New"},
        data={"meta": {"title": "New"}},
    )

    with pytest.raises(RuntimeError, match="catalog unavailable"):
        apply_track_tags(
            db_path="library.db",
            database_factory=_factory(db, events),
            mbid="track-1",
            prepared=prepared,
            write_tags=lambda _path, _meta: "flac",
            track_factory=FakeTrack,
        )

    assert [event[0] if isinstance(event, tuple) else event for event in events] == [
        "db-enter",
        "lookup",
        "upsert",
        "db-exit",
    ]
