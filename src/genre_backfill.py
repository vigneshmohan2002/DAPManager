"""
Stage 14a backfill: populate artist_tags from MusicBrainz.

Walks the library's distinct artist names (or the still-stale subset
when ``incremental=True``), resolves each to an MB artist mbid via the
search endpoint, fetches its tag-list, and persists the top-N tags via
``db.record_artist_tags``.

MusicBrainz rate-limits anonymous traffic to 1 request/second, and we
need two requests per artist (search + tags). For a 500-artist library
that's ~17 minutes — slow but acceptable as a one-shot background job.
The TaskManager pattern in ``web_server`` wraps this in a thread and
streams progress via the existing ``/api/status`` poll, same as Audit.

Failures on individual artists (MB returns 404, name resolves to a
totally different artist, network blip) are logged and counted but
don't abort the run — a single bad artist shouldn't waste the 16
minutes we already spent on the others.
"""

import logging
import time
from typing import Callable, List, Optional

from . import musicbrainz_client as mb
from .db_manager import DatabaseManager


logger = logging.getLogger(__name__)


def _extract_tags(artist_payload: dict) -> List[dict]:
    """Map MB's response to ``[{"tag": str, "weight": int}]``.

    MB returns ``tag-list`` as ``[{"name": str, "count": str|int}]``;
    count comes through as a string from the XML-shaped library
    sometimes, so we coerce. Missing or non-numeric counts default to
    1 (treat as low-weight rather than dropping the tag entirely).
    """
    artist = artist_payload.get("artist") or {}
    raw_tags = artist.get("tag-list") or []
    out: List[dict] = []
    for entry in raw_tags:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        try:
            weight = int(entry.get("count") or 1)
        except (TypeError, ValueError):
            weight = 1
        out.append({"tag": name, "weight": weight})
    return out


def _best_artist_match(search_payload: dict, query_name: str) -> Optional[dict]:
    """Pick the most plausible artist from a MB search hit list.

    MB's search returns multiple candidates ranked by their own scoring.
    We accept the top hit when its name matches the query exactly
    (case-insensitive) or its score is comfortably high; otherwise
    return None rather than guess. Wrong-artist tags would poison
    Daily Mix clustering for the user's entire library, so the bar
    for "yes, this is them" is intentionally conservative.
    """
    hits = search_payload.get("artist-list") or []
    if not hits:
        return None
    top = hits[0]
    name = (top.get("name") or "").strip()
    if not name:
        return None
    if name.lower() == (query_name or "").strip().lower():
        return top
    # MB's "ext:score" is a 0-100 string. Below ~90 the match is
    # likely a sound-alike or a featured-on credit.
    try:
        score = int(top.get("ext:score") or 0)
    except (TypeError, ValueError):
        score = 0
    return top if score >= 90 else None


def backfill_artist_tags(
    db: DatabaseManager,
    progress_callback: Optional[Callable[[dict], None]] = None,
    incremental: bool = True,
    max_age_days: int = 30,
    sleep_between: float = 0.0,
) -> dict:
    """Walk the library and populate artist_tags from MusicBrainz.

    Args:
        db: live DatabaseManager.
        progress_callback: optional ``fn({"message": ..., "detail": ...})``
            invoked at the start and after each artist. Matches the
            TaskManager contract.
        incremental: when True (default), skip artists with a fresh
            artist_tags row newer than ``max_age_days``. Pass False to
            force a full re-fetch.
        max_age_days: see incremental.
        sleep_between: optional extra delay between artists. The MB
            client already enforces 1.1s between requests; this is for
            tests that want to drop both calls into a single tick.

    Returns ``{processed, matched, no_match, errors, tagged}``.
    """
    artists = (
        db.get_artists_needing_tags(max_age_days)
        if incremental
        else db.get_distinct_artist_names()
    )

    total = len(artists)
    processed = 0
    matched = 0
    no_match = 0
    errors = 0
    tagged = 0

    def _report(msg: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback({"message": msg, "detail": detail})

    _report(
        "Tag backfill" + (f" ({total} artists)" if total else " (nothing to do)")
    )
    if total == 0:
        return {
            "processed": 0, "matched": 0, "no_match": 0,
            "errors": 0, "tagged": 0,
        }

    for i, artist in enumerate(artists, 1):
        processed += 1
        _report(
            f"Backfilling tags ({i}/{total})",
            detail=f"current: {artist}",
        )
        try:
            search = mb.search_artists(artist, limit=3)
            best = _best_artist_match(search, artist)
            if best is None:
                no_match += 1
                # Persist an empty row anyway so the incremental skip
                # works — otherwise we'd re-query the same un-matchable
                # name forever.
                db.record_artist_tags(artist, None, [])
                continue
            mbid = best.get("id")
            payload = mb.get_artist_tags(mbid)
            tags = _extract_tags(payload)
            persisted = db.record_artist_tags(artist, mbid, tags)
            matched += 1
            tagged += persisted
        except Exception as e:
            # MB occasionally 5xxs; one artist failure shouldn't burn
            # the run. Don't persist anything (no row) so the
            # incremental retry picks it back up on the next pass.
            logger.warning("tag backfill failed for %r: %s", artist, e)
            errors += 1
        if sleep_between > 0:
            time.sleep(sleep_between)

    _report(
        "Tag backfill done",
        detail=(
            f"{matched} matched, {no_match} unmatched, {errors} errors, "
            f"{tagged} tags persisted"
        ),
    )
    return {
        "processed": processed,
        "matched": matched,
        "no_match": no_match,
        "errors": errors,
        "tagged": tagged,
    }
