"""
Stage 14d Daily Mixes: cluster the user's top artists by their
MusicBrainz top tag and persist each cluster as a system playlist.

Daily Mixes are *static* playlists with reserved ids (``daily_mix_1``
through ``daily_mix_N``). Each mix is a 40-track shuffled pool drawn
from the artists in its cluster, regenerated on demand from this
module. The persistence shape mirrors Liked Songs (Stage 11a) — a
playlist row with a reserved id, plus normal playlist_tracks
membership — so the existing read paths (sidebar listing, scoped
Songs screen) just work.

Clustering for v1 is intentionally simple: each top artist's #1 tag
is the cluster key. Two artists land in the same mix iff their
strongest tag matches. More sophisticated approaches (Jaccard on full
tag sets, k-means in a tag-weight space) wait until the v1 grouping
proves too narrow in practice.

Cold start: until the user has at least 8 distinct artists with ≥3
plays each in the last 90 days, regenerate returns
``{"mixes": 0, "reason": "cold_start"}`` and the Home tile renders
the "play more tracks to unlock Daily Mixes" empty state.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db_manager import DatabaseManager


logger = logging.getLogger(__name__)

# Display copy uses a human title-case version of the tag — "indie
# rock mix" instead of "indie-rock mix". The transformation lives
# here so the Home renderer doesn't carry tag-prettification logic.
def _format_mix_name(index: int, tag: str) -> str:
    pretty = tag.replace("-", " ").title() if tag else "Mix"
    return f"Daily Mix {index}: {pretty}"


DAILY_MIX_ID_PREFIX = "daily_mix_"

# Cold-start guard. The cluster step needs a real signal — N plays
# across N distinct artists — or it'll generate one mix dominated by
# a single tag and call it Daily Mix 1. The thresholds are
# intentionally conservative; users with thin history see the
# "unlock Daily Mixes" empty state until they've listened more.
_COLD_START_MIN_ARTISTS = 8
_COLD_START_MIN_PLAYS_PER_ARTIST = 3


def regenerate_daily_mixes(
    db: DatabaseManager,
    target_mixes: int = 6,
    tracks_per_mix: int = 40,
    since_days: int = 90,
) -> dict:
    """Cluster top artists by their top MB tag, build N mixes, persist.

    Returns ``{"mixes": int, "reason": str|None, "details": [...]}``.
    ``reason`` is set on a non-shipping outcome (``cold_start`` /
    ``no_tags``) so the API layer can surface a specific empty-state
    string rather than guess.
    """
    since_iso = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
    ).strftime("%Y-%m-%d %H:%M:%S")

    top_artists = db.top_artists_since(since_iso, limit=30)
    qualifying = [
        a for a in top_artists
        if int(a.get("plays") or 0) >= _COLD_START_MIN_PLAYS_PER_ARTIST
    ]
    if len(qualifying) < _COLD_START_MIN_ARTISTS:
        # Wipe any stale prior mixes so the UI's empty state really is
        # empty. Without this, an install that *used to* qualify and
        # then went quiet would show last week's mixes forever.
        _purge_daily_mixes(db)
        return {
            "mixes": 0,
            "reason": "cold_start",
            "details": {
                "qualifying_artists": len(qualifying),
                "needed": _COLD_START_MIN_ARTISTS,
            },
        }

    # For each qualifying artist, look up their top tag (skipping
    # sentinel '' rows). Untagged artists are excluded from the
    # cluster step — they'll come back in once the tag backfill
    # covers them.
    by_tag: dict = defaultdict(list)
    for a in qualifying:
        tags = db.get_top_tags_for_artist(a["artist"], limit=1)
        if not tags:
            continue
        top_tag = (tags[0]["tag"] or "").strip()
        if not top_tag:
            continue
        by_tag[top_tag].append(a["artist"])

    if not by_tag:
        _purge_daily_mixes(db)
        return {
            "mixes": 0,
            "reason": "no_tags",
            "details": {"qualifying_artists": len(qualifying)},
        }

    # Take the top N clusters by member count; tie-break alphabetic
    # on the tag so the order is deterministic between regenerations.
    ranked = sorted(
        by_tag.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )[:max(1, int(target_mixes))]

    # Persist. Wipe any extra mixes from a prior larger generation so
    # the sidebar doesn't carry stale "Daily Mix 6" entries when the
    # current run only produced 4.
    _purge_daily_mixes(db)

    details = []
    for i, (tag, artists) in enumerate(ranked, 1):
        pid = f"{DAILY_MIX_ID_PREFIX}{i}"
        name = _format_mix_name(i, tag)
        db.ensure_system_playlist(pid, name)
        pool = db.get_random_tracks_for_artists(
            artists, limit=int(tracks_per_mix)
        )
        mbids = [t["mbid"] for t in pool]
        persisted = db.replace_playlist_membership(pid, mbids)
        details.append({
            "playlist_id": pid,
            "name": name,
            "tag": tag,
            "artist_count": len(artists),
            "track_count": persisted,
        })

    return {"mixes": len(details), "reason": None, "details": details}


def _purge_daily_mixes(db: DatabaseManager) -> None:
    """Hard-delete every daily_mix_* playlist row (and cascade
    membership). Used both before regen and on a cold-start fall-back
    so the UI never shows stale mixes."""
    db.conn.execute(
        "DELETE FROM playlists WHERE playlist_id LIKE ?",
        (f"{DAILY_MIX_ID_PREFIX}%",),
    )
    db.conn.commit()


def list_daily_mixes(db: DatabaseManager) -> list:
    """Return the current daily mixes for the Home tile.

    Each entry carries the cluster tag (parsed back out of the name)
    so the UI can render "Indie Rock mix" copy without re-querying
    artist_tags. Ordered by the mix index in the id, so a regen
    that shuffles cluster contents doesn't reorder the tiles on
    Home.
    """
    cur = db.conn.execute(
        "SELECT p.playlist_id, p.name, "
        "       COUNT(pt.track_mbid) AS track_count "
        "FROM playlists p "
        "LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id "
        "WHERE p.playlist_id LIKE ? AND p.deleted_at IS NULL "
        "GROUP BY p.playlist_id "
        "ORDER BY p.playlist_id",
        (f"{DAILY_MIX_ID_PREFIX}%",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    out = []
    for r in rows:
        # Name shape is "Daily Mix N: TagName" — pull the tag back out
        # for the tile subtitle.
        name = r["name"]
        tag = ""
        if ":" in name:
            tag = name.split(":", 1)[1].strip()
        out.append({
            "playlist_id": r["playlist_id"],
            "name": name,
            "tag": tag,
            "track_count": int(r["track_count"] or 0),
        })
    return out


def _ignored() -> Optional[None]:
    """Placeholder to keep typing imports honest in tests."""
    return None
