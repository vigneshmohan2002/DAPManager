"""Database-level coverage for authoritative artist-tag delta snapshots."""

from src.db_manager import DatabaseManager


def test_get_artist_tags_since_groups_rows_and_preserves_empty_sentinel():
    db = DatabaseManager(":memory:")
    try:
        db.conn.executemany(
            "INSERT INTO artist_tags "
            "(artist_name, mbid, tag, weight, fetched_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("Fresh", "mb-f", "rock", 10, "2026-06-02 10:00:00"),
                ("Fresh", "mb-f", "indie", 4, "2026-06-02 10:00:00"),
                ("No Match", None, "", 0, "2026-06-03 10:00:00"),
                ("Old", "mb-o", "jazz", 8, "2026-05-01 10:00:00"),
            ],
        )
        db.conn.commit()

        rows = db.get_artist_tags_since("2026-06-01 00:00:00")

        assert [row["artist_name"] for row in rows] == ["Fresh", "No Match"]
        assert rows[0] == {
            "artist_name": "Fresh",
            "mbid": "mb-f",
            "fetched_at": "2026-06-02 10:00:00",
            "tags": [
                {"tag": "rock", "weight": 10},
                {"tag": "indie", "weight": 4},
            ],
        }
        assert rows[1]["tags"] == []
    finally:
        db.close()


def test_apply_artist_tags_row_replaces_snapshot_and_rejects_stale_update():
    db = DatabaseManager(":memory:")
    try:
        inserted = db.apply_artist_tags_row({
            "artist_name": "Artist",
            "mbid": "mb-a",
            "fetched_at": "2026-06-02 10:00:00",
            "tags": [
                {"tag": "rock", "weight": 10},
                {"tag": "pop", "weight": 5},
            ],
        })
        assert inserted == "inserted"
        assert db.get_top_tags_for_artist("artist") == [
            {"tag": "rock", "weight": 10},
            {"tag": "pop", "weight": 5},
        ]

        stale = db.apply_artist_tags_row({
            "artist_name": "Artist",
            "fetched_at": "2026-06-01 10:00:00",
            "tags": [{"tag": "stale", "weight": 99}],
        })
        assert stale == "stale"
        assert db.get_top_tags_for_artist("Artist")[0]["tag"] == "rock"

        updated = db.apply_artist_tags_row({
            "artist_name": "ARTIST",
            "mbid": None,
            "fetched_at": "2026-06-03 10:00:00",
            "tags": [],
        })
        assert updated == "updated"
        assert db.get_top_tags_for_artist("Artist") == []
        sentinel = db.conn.execute(
            "SELECT tag, weight, fetched_at FROM artist_tags "
            "WHERE artist_name = ? COLLATE NOCASE",
            ("Artist",),
        ).fetchone()
        assert dict(sentinel) == {
            "tag": "",
            "weight": 0,
            "fetched_at": "2026-06-03 10:00:00",
        }
    finally:
        db.close()


def test_apply_artist_tags_row_skips_missing_artist():
    db = DatabaseManager(":memory:")
    try:
        assert db.apply_artist_tags_row({"tags": []}) == "skipped"
    finally:
        db.close()


def test_same_second_snapshot_after_cursor_is_replayed_and_applied():
    """A write after the master's query can share its second-level cursor.

    The inclusive boundary must return the changed snapshot, and the replica
    must distinguish it from an identical retry even though both versions
    carry exactly the same ``fetched_at``.
    """
    master = DatabaseManager(":memory:")
    replica = DatabaseManager(":memory:")
    timestamp = "2026-06-04 12:00:00"
    try:
        first = {
            "artist_name": "Boundary Artist",
            "mbid": "artist-mbid",
            "fetched_at": timestamp,
            "tags": [{"tag": "old tag", "weight": 5}],
        }
        assert master.apply_artist_tags_row(first) == "inserted"
        initial = master.get_artist_tags_since()
        assert replica.apply_artist_tags_row(initial[0]) == "inserted"

        # Simulate a second refresh committing after the pull snapshot query
        # but inside the same SQLite CURRENT_TIMESTAMP second.
        second = {
            "artist_name": "Boundary Artist",
            "mbid": "artist-mbid",
            "fetched_at": timestamp,
            "tags": [{"tag": "new tag", "weight": 11}],
        }
        assert master.apply_artist_tags_row(second) == "updated"

        boundary_delta = master.get_artist_tags_since(timestamp)
        assert len(boundary_delta) == 1
        assert boundary_delta[0]["tags"] == [
            {"tag": "new tag", "weight": 11},
        ]
        assert replica.apply_artist_tags_row(boundary_delta[0]) == "updated"
        assert replica.get_top_tags_for_artist("Boundary Artist") == [
            {"tag": "new tag", "weight": 11},
        ]

        # A network retry replays the boundary once more, but identical
        # content remains idempotent and does not rewrite the database.
        assert replica.apply_artist_tags_row(boundary_delta[0]) == "stale"
    finally:
        master.close()
        replica.close()
