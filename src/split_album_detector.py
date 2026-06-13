"""Detect albums that have been fragmented into multiple groups.

Two detection strategies:
  1. Folder-based: tracks in the same directory assigned to different album groups.
  2. Name-similarity: different album groups from the same artist whose titles
     are very similar (difflib ratio > 0.82), suggesting a metadata mismatch.

Plus a library-wide consolidation pass that folds standard/base editions of an
album into their superset edition (e.g. "X" → "X (Deluxe)").
"""

import os
import re
import hashlib
import logging
from difflib import SequenceMatcher
from collections import defaultdict
from typing import List, Optional

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.82

# Parenthetical / bracketed edition tags we strip to find an album's base name.
_EDITION_PAREN_RE = re.compile(
    r"[\(\[][^\)\]]*\b("
    r"deluxe|expanded|extended|anniversary|special|collector'?s?|complete|"
    r"super\s*deluxe|platinum|bonus|remaster(?:ed)?|edition|version)"
    r"\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
# Trailing " - Deluxe", " : Deluxe Edition", etc.
_EDITION_TAIL_RE = re.compile(
    r"\s*[-–—:]\s*("
    r"deluxe|expanded|extended|anniversary|special edition|complete edition|"
    r"super deluxe|platinum|remaster(?:ed)?)\b.*$",
    re.IGNORECASE,
)
# Featured-artist suffix on the album-level artist string.
_FEAT_RE = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring|with)\b.*$", re.IGNORECASE
)


def _norm_album_base(title: str) -> str:
    """Album title with edition tags removed, lowercased + whitespace-collapsed."""
    t = title or ""
    t = _EDITION_PAREN_RE.sub("", t)
    t = _EDITION_TAIL_RE.sub("", t)
    return " ".join(t.lower().split()).strip()


def _norm_artist_base(artist: str) -> str:
    """Artist with any 'feat. …' suffix stripped, lowercased."""
    a = _FEAT_RE.sub("", artist or "")
    return " ".join(a.lower().split()).strip()


def _edition_rank(title: str) -> int:
    """Higher = more likely the superset edition."""
    t = (title or "").lower()
    if "super deluxe" in t:
        return 4
    if "deluxe" in t:
        return 3
    if any(k in t for k in ("expanded", "extended", "anniversary", "complete", "collector", "platinum")):
        return 3
    if "bonus" in t:
        return 1
    return 0


def incident_key(incident: dict) -> str:
    """Stable identifier for an incident, derived from its album_ids.

    Independent of detection type and ordering, so the same fragment
    combination produces the same key whether it surfaces as a folder
    split or a name-similarity split — and stays dismissed across rescans.
    """
    ids = sorted(g["album_id"] for g in incident.get("groups", []))
    raw = "||".join(ids)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def detect_split_albums(db, include_dismissed: bool = False) -> List[dict]:
    """Return a list of suspected split-album incidents.

    Each item has:
      type: "folder" | "name"
      key: stable incident identifier (for dismiss/merge)
      directory (folder type only): shared parent directory
      similarity (name type only): float 0–1
      groups: list of album-group summaries, each with
        album_id, album, artist, release_mbid, track_count, tracks[]

    Dismissed incidents are filtered out unless ``include_dismissed`` is True.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT
            mbid, title, artist, album, track_number, disc_number,
            local_path, release_mbid,
            COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
        FROM tracks
        WHERE deleted_at IS NULL
          AND album IS NOT NULL AND album != ''
          AND artist IS NOT NULL AND artist != ''
        ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE,
                 COALESCE(disc_number, 1), COALESCE(track_number, 9999)
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()

    incidents = []
    incidents.extend(_folder_splits(rows))
    incidents.extend(_name_similarity_splits(rows))

    # Deduplicate: if a pair already appears as a folder split, skip name split
    seen_pairs = set()
    for inc in incidents:
        if inc["type"] == "folder":
            ids = tuple(sorted(g["album_id"] for g in inc["groups"]))
            seen_pairs.add(ids)

    deduped = []
    for inc in incidents:
        if inc["type"] == "name":
            ids = tuple(sorted(g["album_id"] for g in inc["groups"]))
            if ids in seen_pairs:
                continue
            seen_pairs.add(ids)
        deduped.append(inc)

    # Stamp each incident with its stable key and drop dismissed ones.
    dismissed = set() if include_dismissed else db.get_dismissed_split_albums()
    result = []
    for inc in deduped:
        inc["key"] = incident_key(inc)
        if inc["key"] in dismissed:
            continue
        result.append(inc)

    return result


# A folder co-locating tracks is only weak evidence on its own — a folder
# can hold unrelated songs. We require the album *names* to also match (or
# nearly) before calling it a split, so "two different albums sharing a
# directory" isn't reported as something to merge. Lower than the global
# name-similarity threshold because co-location adds evidence.
_FOLDER_SIM_THRESHOLD = 0.6


def _albums_match(a: dict, b: dict) -> bool:
    """True when two album groups plausibly refer to the same album."""
    name_a = (a.get("album") or "").lower().strip()
    name_b = (b.get("album") or "").lower().strip()
    if not name_a or not name_b:
        return False
    if name_a == name_b:
        return True
    if name_a in name_b or name_b in name_a:
        return True
    return SequenceMatcher(None, name_a, name_b).ratio() >= _FOLDER_SIM_THRESHOLD


def _cluster_similar_albums(summaries: List[dict]) -> List[List[dict]]:
    """Greedily group album summaries whose names match each other."""
    clusters: List[List[dict]] = []
    for s in summaries:
        for cluster in clusters:
            if any(_albums_match(s, member) for member in cluster):
                cluster.append(s)
                break
        else:
            clusters.append([s])
    return clusters


def _folder_splits(rows: List[dict]) -> List[dict]:
    """Same-folder tracks split across album groups that look like one album.

    Reports a folder only when 2+ of its album groups share a similar album
    name — a folder holding genuinely different albums (different artists,
    unrelated titles) is not a split and won't surface.
    """
    dir_tracks = defaultdict(list)
    for row in rows:
        path = (row.get("local_path") or "").strip()
        if not path:
            continue
        parent = os.path.dirname(path.replace("\\", "/"))
        if not parent:
            continue
        dir_tracks[parent].append(row)

    incidents = []
    for directory, tracks in dir_tracks.items():
        groups_map = defaultdict(list)
        for t in tracks:
            groups_map[t["album_id"]].append(t)
        if len(groups_map) < 2:
            continue
        summaries = _summarise_groups(groups_map)
        for cluster in _cluster_similar_albums(summaries):
            if len(cluster) < 2:
                continue
            incidents.append({
                "type": "folder",
                "directory": directory,
                "groups": cluster,
            })
    return incidents


def _name_similarity_splits(rows: List[dict]) -> List[dict]:
    """Album groups from the same artist with similar titles."""
    # Build artist → { album_id → group info }
    artist_albums = defaultdict(dict)
    for row in rows:
        aid = row["album_id"]
        artist = row["artist"]
        if aid not in artist_albums[artist]:
            artist_albums[artist][aid] = {
                "album_id": aid,
                "album": row["album"] or "",
                "artist": artist,
                "release_mbid": (row.get("release_mbid") or "").strip() or None,
                "tracks": [],
            }
        artist_albums[artist][aid]["tracks"].append({
            "mbid": row["mbid"],
            "title": row["title"],
            "track_number": row.get("track_number"),
            "disc_number": row.get("disc_number"),
        })

    incidents = []
    for artist, albums in artist_albums.items():
        album_list = list(albums.values())
        for i in range(len(album_list)):
            for j in range(i + 1, len(album_list)):
                a, b = album_list[i], album_list[j]
                sim = SequenceMatcher(
                    None,
                    a["album"].lower().strip(),
                    b["album"].lower().strip(),
                ).ratio()
                if sim >= _SIMILARITY_THRESHOLD and a["album_id"] != b["album_id"]:
                    incidents.append({
                        "type": "name",
                        "similarity": round(sim, 3),
                        "groups": [
                            {**a, "track_count": len(a["tracks"])},
                            {**b, "track_count": len(b["tracks"])},
                        ],
                    })
    return incidents


def _summarise_groups(groups_map: dict) -> List[dict]:
    result = []
    for album_id, tracks in groups_map.items():
        result.append({
            "album_id": album_id,
            "album": tracks[0]["album"] or "",
            "artist": tracks[0]["artist"],
            "release_mbid": (tracks[0].get("release_mbid") or "").strip() or None,
            "track_count": len(tracks),
            "tracks": [
                {
                    "mbid": t["mbid"],
                    "title": t["title"],
                    "track_number": t.get("track_number"),
                    "disc_number": t.get("disc_number"),
                }
                for t in tracks
            ],
        })
    return result


def merge_album_groups(
    db,
    primary_album_id: str,
    secondary_album_id: str,
    target_album: str,
    target_artist: str,
    target_release_mbid: Optional[str],
) -> dict:
    """Reassign all tracks in ``secondary_album_id`` to match the primary group.

    Delegates to :func:`_reassign_to_album`, so it shares one code path with
    edition consolidation: per-track artist is preserved when a release MBID is
    present, and the on-disk album tags are rewritten so Jellyfin reflects the
    merge. ``primary_album_id`` is accepted for API symmetry but the target
    fields (album/artist/release_mbid) are what's actually written.

    Returns ``{merged, tagged, tag_errors}``.
    """
    res = _reassign_to_album(
        db, secondary_album_id, target_album, target_artist,
        target_release_mbid, write_file_tags=True,
    )
    return {
        "merged": res["moved"],
        "tagged": res["tagged"],
        "tag_errors": res["tag_errors"],
    }


# ── Edition consolidation ──────────────────────────────────────────────────────

def _load_album_summaries(db) -> List[dict]:
    """All album groups in the library as summaries (album, artist, mbid, tracks)."""
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT
            mbid, title, artist, album, track_number, disc_number, release_mbid,
            COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
        FROM tracks
        WHERE deleted_at IS NULL
          AND album IS NOT NULL AND album != ''
          AND artist IS NOT NULL AND artist != ''
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()
    groups_map = defaultdict(list)
    for r in rows:
        groups_map[r["album_id"]].append(r)
    return _summarise_groups(groups_map)


def _pick_canonical(groups: List[dict]) -> dict:
    """The superset edition: highest edition rank, then most tracks, then longest title."""
    return max(
        groups,
        key=lambda g: (
            _edition_rank(g["album"]),
            g["track_count"],
            len(g["album"] or ""),
        ),
    )


def find_edition_clusters(db) -> List[dict]:
    """Group albums sharing a base name+artist where editions can be consolidated.

    Returns clusters with a ``canonical`` (the superset/deluxe) and ``others``
    (base/standard editions to fold in). Only clusters with 2+ distinct album
    groups are returned.
    """
    summaries = _load_album_summaries(db)
    clusters = defaultdict(list)
    for s in summaries:
        base = _norm_album_base(s["album"])
        if not base:
            continue
        key = (base, _norm_artist_base(s["artist"]))
        clusters[key].append(s)

    result = []
    for (base, _artist), groups in clusters.items():
        if len(groups) < 2:
            continue
        canonical = _pick_canonical(groups)
        others = [g for g in groups if g["album_id"] != canonical["album_id"]]
        if not others:
            continue
        result.append({"canonical": canonical, "others": others})
    return result


def _reassign_to_album(
    db, source_album_id: str, target_album: str, target_artist: str,
    target_release_mbid: Optional[str], write_file_tags: bool = True,
) -> dict:
    """Move every track of source_album_id onto the target album.

    When the target has a release MBID we set album+release_mbid and leave the
    per-track artist untouched (preserving feat. credits) — grouping is by MBID.
    With no MBID we also overwrite artist so the synthetic album||artist key
    converges.

    When ``write_file_tags`` is set, also rewrites the on-disk album-level tags
    (album, albumartist, musicbrainz_albumid) so external readers like Jellyfin
    pick up the change. Returns ``{moved, tagged, tag_errors}``.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT mbid FROM tracks
        WHERE deleted_at IS NULL
          AND COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) = ?
        """,
        (source_album_id,),
    )
    mbids = [r[0] for r in cursor.fetchall()]
    if not mbids:
        cursor.close()
        return {"moved": 0, "tagged": 0, "tag_errors": []}
    ph = ",".join("?" * len(mbids))
    if target_release_mbid:
        cursor.execute(
            f"""
            UPDATE tracks SET album = ?, release_mbid = ?, updated_at = CURRENT_TIMESTAMP
            WHERE mbid IN ({ph})
            """,
            [target_album, target_release_mbid] + mbids,
        )
    else:
        cursor.execute(
            f"""
            UPDATE tracks SET album = ?, artist = ?, release_mbid = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE mbid IN ({ph})
            """,
            [target_album, target_artist] + mbids,
        )
    n = cursor.rowcount
    db.conn.commit()

    tagged = 0
    tag_errors: List[str] = []
    if write_file_tags:
        cursor.execute(
            f"SELECT mbid, local_path FROM tracks WHERE mbid IN ({ph})", mbids
        )
        rows = cursor.fetchall()
        from src.tag_service import update_album_tags, TAGGABLE_EXTENSIONS
        for row in rows:
            path = (dict(row).get("local_path") or "").strip()
            if not path or not os.path.isfile(path):
                continue
            if os.path.splitext(path)[1].lower() not in TAGGABLE_EXTENSIONS:
                continue
            try:
                update_album_tags(
                    path,
                    album=target_album,
                    album_artist=target_artist,
                    release_mbid=target_release_mbid or None,
                )
                tagged += 1
            except Exception as e:  # one bad file shouldn't abort the merge
                tag_errors.append(f"{os.path.basename(path)}: {e}")
                logger.warning("update_album_tags failed for %s: %s", path, e)

    cursor.close()
    return {"moved": n, "tagged": tagged, "tag_errors": tag_errors}


def retag_files_from_db(db, only_mismatched: bool = True) -> dict:
    """Rewrite on-disk album-level tags to match the database.

    For every track with a local file, sets the file's album +
    musicbrainz_albumid to the DB values when they differ (or always, with
    ``only_mismatched=False``). Repairs files whose DB rows were changed by a
    metadata-only operation (e.g. an earlier consolidation that didn't tag).
    Returns ``{tagged, skipped, errors}``.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT mbid, album, artist, release_mbid, local_path FROM tracks
        WHERE deleted_at IS NULL AND local_path IS NOT NULL AND local_path != ''
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()

    from src.tag_service import update_album_tags, read_current_tags, TAGGABLE_EXTENSIONS
    tagged = 0
    skipped = 0
    errors: List[str] = []
    for r in rows:
        path = (r.get("local_path") or "").strip()
        if not path or not os.path.isfile(path):
            skipped += 1
            continue
        # Formats we can't tag (e.g. .wav) are skipped, not errored — otherwise
        # they'd show up as failures on every single run.
        if os.path.splitext(path)[1].lower() not in TAGGABLE_EXTENSIONS:
            skipped += 1
            continue
        db_album = r.get("album") or ""
        db_mbid = (r.get("release_mbid") or "").strip()
        if only_mismatched:
            cur = read_current_tags(path)
            same_album = (cur.get("album") or "") == db_album
            same_mbid = (not db_mbid) or (cur.get("release_mbid") or "") == db_mbid
            if same_album and same_mbid:
                skipped += 1
                continue
        try:
            update_album_tags(path, album=db_album, release_mbid=db_mbid or None)
            tagged += 1
        except Exception as e:
            errors.append(f"{os.path.basename(path)}: {e}")
            logger.warning("retag_files_from_db failed for %s: %s", path, e)
    return {"tagged": tagged, "skipped": skipped, "errors": errors}


def consolidate_editions(db, dry_run: bool = False) -> dict:
    """Fold base/standard album editions into their superset (deluxe) edition.

    Returns a summary: total tracks that would move / moved, albums merged,
    and a per-cluster breakdown for preview.
    """
    clusters = find_edition_clusters(db)
    summary = {
        "albums_merged": 0,
        "tracks_reassigned": 0,
        "files_tagged": 0,
        "tag_errors": [],
        "clusters": [],
    }
    for c in clusters:
        canon = c["canonical"]
        cluster_moved = 0
        folded = []
        for o in c["others"]:
            if dry_run:
                moved = o["track_count"]
            else:
                res = _reassign_to_album(
                    db, o["album_id"], canon["album"], canon["artist"],
                    canon["release_mbid"], write_file_tags=True,
                )
                moved = res["moved"]
                summary["files_tagged"] += res["tagged"]
                summary["tag_errors"].extend(res["tag_errors"])
            cluster_moved += moved
            folded.append({"album": o["album"], "artist": o["artist"], "tracks": moved})
            summary["albums_merged"] += 1
        summary["tracks_reassigned"] += cluster_moved
        summary["clusters"].append({
            "into": canon["album"],
            "artist": canon["artist"],
            "release_mbid": canon["release_mbid"],
            "folded": folded,
            "tracks_reassigned": cluster_moved,
        })
    return summary
