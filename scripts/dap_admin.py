#!/usr/bin/env python3
"""
DAPManager admin CLI - all major GUI operations as shell commands.

Usage:
    python dap_admin.py [--server URL] [--token TOKEN] <command> [args]

Server defaults to http://localhost:5001. Pass --token when api_token is set.

Commands:
    status                        Show server task status
    healthz                       Liveness/readiness check
    scan                          Run library scan
    download                      Run the download queue
    download-request <query...>   Queue a track for download (satellite->master)
    sync [--mode MODE]            Run sync (default mode: playlists)
    jellyfin-pull                 Pull Jellyfin library into DB
    catalog-pull                  Pull master catalog (satellite)

    duplicates list               List duplicate track groups
    duplicates resolve <mbid> --keep <path>   Resolve one group
    duplicates resolve-all        Resolve all groups (keep recommended)

    split-albums list             Detect split-album incidents (with keys)
    split-albums merge --primary ID --secondary ID --album T --artist N [--release-mbid M]
    split-albums dismiss <key> [--undismiss]  Hide/unhide a false positive

    library albums                List albums
    library tracks [--limit N]    List tracks
    library artists               List artists
    library consolidate [--apply] Fold editions into superset (dry-run default)
    library retag [--all]         Sync on-disk tags to DB (mismatched only)
    library scrub-dangling        Clear local_path for files missing from disk

See docs/agent-operations.md for workflows and safety notes.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

DEFAULT_SERVER = "http://localhost:5001"


def _req(method, url, body=None, token=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"message": e.reason}
        return e.code, body
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _pp(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_status(server, token, _args):
    code, data = _req("GET", f"{server}/api/status", token=token)
    print(f"  running : {data.get('running')}")
    print(f"  task    : {data.get('task')}")
    print(f"  message : {data.get('message')}")
    if data.get("detail"):
        print(f"  detail  : {data['detail']}")


def cmd_healthz(server, token, _args):
    code, data = _req("GET", f"{server}/api/healthz", token=token)
    print(f"  ok          : {data.get('ok')}")
    print(f"  initialized : {data.get('initialized')}")


def cmd_scan(server, token, _args):
    code, data = _req("POST", f"{server}/api/scan", token=token)
    print(f"  success : {data.get('success')}")
    print(f"  message : {data.get('message')}")


def cmd_download(server, token, args):
    code, data = _req("POST", f"{server}/api/download", token=token)
    print(f"  success : {data.get('success')}")
    print(f"  message : {data.get('message')}")


def cmd_download_request(server, token, args):
    query = " ".join(args.query)
    code, data = _req("POST", f"{server}/api/download/request",
                       {"search_query": query}, token=token)
    print(f"  success : {data.get('success')}")
    print(f"  queued  : {data.get('queued')}")
    print(f"  message : {data.get('message')}")


def cmd_sync(server, token, args):
    mode = getattr(args, "mode", "playlists") or "playlists"
    code, data = _req("POST", f"{server}/api/sync", {"mode": mode}, token=token)
    print(f"  success : {data.get('success')}")
    print(f"  message : {data.get('message')}")


def cmd_jellyfin_pull(server, token, _args):
    code, data = _req("POST", f"{server}/api/jellyfin/pull", token=token)
    print(f"  success : {data.get('success')}")
    print(f"  message : {data.get('message')}")


def cmd_catalog_pull(server, token, _args):
    code, data = _req("POST", f"{server}/api/catalog/pull", token=token)
    print(f"  success : {data.get('success')}")
    print(f"  message : {data.get('message')}")


# -- Duplicates ----------------------------------------------------------------

def cmd_duplicates_list(server, token, _args):
    code, data = _req("GET", f"{server}/api/duplicates", token=token)
    dupes = data.get("duplicates") or []
    if not dupes:
        print("  No duplicates found.")
        return
    for g in dupes:
        print(f"\n  {g['artist']} - {g['title']}  (mbid={g['mbid']})")
        for c in g.get("candidates", []):
            rec = " [RECOMMENDED]" if c.get("is_recommended") else ""
            print(f"    score={c['score']}{rec}  {c['path']}")


def cmd_duplicates_resolve(server, token, args):
    mbid = args.mbid
    keep = args.keep
    code, data = _req("GET", f"{server}/api/duplicates", token=token)
    dupes = data.get("duplicates") or []
    group = next((g for g in dupes if g["mbid"] == mbid), None)
    if not group:
        print(f"  ERROR: mbid {mbid} not found in duplicates list.")
        sys.exit(1)
    delete_paths = [c["path"] for c in group["candidates"] if c["path"] != keep]
    code, resp = _req("POST", f"{server}/api/duplicates/resolve",
                      {"mbid": mbid, "keep_path": keep, "delete_paths": delete_paths},
                      token=token)
    print(f"  success : {resp.get('success')}")
    if resp.get("result"):
        print(f"  deleted : {resp['result'].get('deleted')}")
        if resp['result'].get("errors"):
            print(f"  errors  : {resp['result']['errors']}")
    elif resp.get("message"):
        print(f"  message : {resp.get('message')}")


def cmd_duplicates_resolve_all(server, token, _args):
    code, data = _req("GET", f"{server}/api/duplicates", token=token)
    dupes = data.get("duplicates") or []
    if not dupes:
        print("  No duplicates to resolve.")
        return
    resolved = 0
    failed = 0
    for g in dupes:
        mbid = g["mbid"]
        candidates = g.get("candidates", [])
        keep = next((c["path"] for c in candidates if c.get("is_recommended")), None)
        if not keep and candidates:
            keep = candidates[0]["path"]
        if not keep:
            print(f"  SKIP {mbid} - no candidates")
            failed += 1
            continue
        delete_paths = [c["path"] for c in candidates if c["path"] != keep]
        _, resp = _req("POST", f"{server}/api/duplicates/resolve",
                       {"mbid": mbid, "keep_path": keep, "delete_paths": delete_paths},
                       token=token)
        if resp.get("success"):
            print(f"  RESOLVED {g['artist']} - {g['title']}")
            resolved += 1
        else:
            print(f"  FAILED   {g['artist']} - {g['title']} : {resp.get('message')}")
            failed += 1
    print(f"\n  Resolved: {resolved}  Failed: {failed}")


# -- Split albums --------------------------------------------------------------

def cmd_split_albums_list(server, token, _args):
    code, data = _req("GET", f"{server}/api/library/split-albums", token=token)
    incidents = data.get("incidents") or []
    if not incidents:
        print("  No split album incidents detected.")
        return
    print(f"  {len(incidents)} incident(s) found:\n")
    for i, inc in enumerate(incidents, 1):
        t = inc["type"]
        key = inc.get("key", "?")
        if t == "folder":
            print(f"  [{i}] FOLDER SPLIT  key={key}  dir={inc['directory']}")
        else:
            print(f"  [{i}] NAME SIMILARITY  key={key}  ({inc['similarity']:.0%} similar)")
        for g in inc.get("groups", []):
            print(f"       album_id={g['album_id']}")
            print(f"         album={g['album']}  artist={g['artist']}  tracks={g['track_count']}")
            if g.get("release_mbid"):
                print(f"         release_mbid={g['release_mbid']}")


def cmd_split_albums_dismiss(server, token, args):
    body = {"key": args.key, "undismiss": bool(getattr(args, "undismiss", False))}
    code, data = _req("POST", f"{server}/api/library/split-albums/dismiss", body, token=token)
    print(f"  success   : {data.get('success')}")
    print(f"  dismissed : {data.get('dismissed')}")
    if data.get("message"):
        print(f"  message   : {data['message']}")


def cmd_split_albums_merge(server, token, args):
    body = {
        "primary_album_id": args.primary,
        "secondary_album_id": args.secondary,
        "target_album": args.album,
        "target_artist": args.artist,
        "target_release_mbid": getattr(args, "release_mbid", "") or "",
    }
    code, data = _req("POST", f"{server}/api/library/split-albums/merge", body, token=token)
    print(f"  success : {data.get('success')}")
    print(f"  merged  : {data.get('merged')}")
    if data.get("message"):
        print(f"  message : {data['message']}")


# -- Library -------------------------------------------------------------------

def cmd_library_consolidate(server, token, args):
    dry_run = not getattr(args, "apply", False)
    if not dry_run:
        print("  Applying merge and rewriting file tags (this can take a while)...")
    code, data = _req("POST", f"{server}/api/library/consolidate-editions",
                       {"dry_run": dry_run}, token=token,
                       timeout=30 if dry_run else 1800)
    if not data.get("success"):
        print(f"  ERROR: {data.get('message')}")
        return
    clusters = data.get("clusters") or []
    label = "PLAN (dry-run)" if dry_run else "APPLIED"
    print(f"  {label}: {data.get('albums_merged')} editions, {data.get('tracks_reassigned')} tracks")
    for c in clusters:
        sources = ", ".join(f'"{f["album"]}" ({f["tracks"]})' for f in c["folded"])
        print(f"    -> {c['into']} - {c['artist']}")
        print(f"       absorbs {sources}")
    if dry_run and clusters:
        print("\n  Re-run with --apply to commit.")


def cmd_library_retag(server, token, args):
    only_mismatched = not getattr(args, "all", False)
    print("  Scanning library and rewriting mismatched tags (this can take a while)...")
    code, data = _req("POST", f"{server}/api/library/retag-files",
                       {"only_mismatched": only_mismatched}, token=token,
                       timeout=1800)
    if not data.get("success"):
        print(f"  ERROR: {data.get('message')}")
        return
    print(f"  tagged  : {data.get('tagged')}")
    print(f"  skipped : {data.get('skipped')}")
    errs = data.get("errors") or []
    if errs:
        print(f"  errors  : {len(errs)}")
        for e in errs[:10]:
            print(f"    {e}")


def cmd_library_scrub_dangling(server, token, _args):
    code, data = _req("POST", f"{server}/api/library/scrub-dangling",
                       {}, token=token, timeout=600)
    if not data.get("success"):
        print(f"  ERROR: {data.get('message')}")
        return
    print(f"  scanned : {data.get('scanned')}")
    print(f"  cleared : {data.get('cleared')}")
    for p in (data.get("sample") or []):
        print(f"    {p}")


def cmd_library_albums(server, token, _args):
    code, data = _req("GET", f"{server}/api/library/albums", token=token)
    albums = data.get("albums") or []
    print(f"  {len(albums)} albums")
    for a in albums:
        print(f"  {a['artist']} / {a['title']}  ({a['track_count']} tracks)  id={a['id']}")


def cmd_library_tracks(server, token, args):
    limit = getattr(args, "limit", 50) or 50
    code, data = _req("GET", f"{server}/api/library/tracks?limit={limit}", token=token)
    tracks = data.get("tracks") or []
    print(f"  {len(tracks)} tracks (limit={limit})")
    for t in tracks:
        print(f"  {t['artist']} / {t['album']} / {t['title']}  [{t['availability']}]")


def cmd_library_artists(server, token, _args):
    code, data = _req("GET", f"{server}/api/library/artists", token=token)
    artists = data.get("artists") or []
    print(f"  {len(artists)} artists")
    for a in artists:
        name = a.get("name") or a.get("artist") or "?"
        print(f"  {name}  ({a.get('album_count', '?')} albums, {a.get('track_count', '?')} tracks)")


# -- Argument parser -----------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="DAPManager admin CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"Server URL (default: {DEFAULT_SERVER})")
    p.add_argument("--token", default="", help="API bearer token (if configured)")

    sub = p.add_subparsers(dest="command")

    sub.add_parser("status")
    sub.add_parser("healthz")
    sub.add_parser("scan")
    sub.add_parser("download")
    sub.add_parser("jellyfin-pull")
    sub.add_parser("catalog-pull")

    dl_req = sub.add_parser("download-request")
    dl_req.add_argument("query", nargs="+", help="Track search query")

    sync_p = sub.add_parser("sync")
    sync_p.add_argument("--mode", default="playlists", help="Sync mode (default: playlists)")

    # duplicates
    dup_p = sub.add_parser("duplicates")
    dup_sub = dup_p.add_subparsers(dest="dup_cmd")
    dup_sub.add_parser("list")
    dup_sub.add_parser("resolve-all")
    res_p = dup_sub.add_parser("resolve")
    res_p.add_argument("mbid")
    res_p.add_argument("--keep", required=True, help="Path to keep")

    # split-albums
    sa_p = sub.add_parser("split-albums")
    sa_sub = sa_p.add_subparsers(dest="sa_cmd")
    sa_sub.add_parser("list")
    merge_p = sa_sub.add_parser("merge")
    merge_p.add_argument("--primary", required=True, help="album_id to keep")
    merge_p.add_argument("--secondary", required=True, help="album_id to merge in")
    merge_p.add_argument("--album", required=True, help="Canonical album title")
    merge_p.add_argument("--artist", required=True, help="Canonical artist name")
    merge_p.add_argument("--release-mbid", default="", help="Release MBID (optional)")
    dismiss_p = sa_sub.add_parser("dismiss")
    dismiss_p.add_argument("key", help="Incident key from 'split-albums list'")
    dismiss_p.add_argument("--undismiss", action="store_true", help="Reverse a dismissal")

    # library
    lib_p = sub.add_parser("library")
    lib_sub = lib_p.add_subparsers(dest="lib_cmd")
    lib_sub.add_parser("albums")
    tracks_p = lib_sub.add_parser("tracks")
    tracks_p.add_argument("--limit", type=int, default=50)
    lib_sub.add_parser("artists")
    consolidate_p = lib_sub.add_parser("consolidate")
    consolidate_p.add_argument("--apply", action="store_true",
                               help="Commit the merge (default is dry-run preview)")
    retag_p = lib_sub.add_parser("retag")
    retag_p.add_argument("--all", action="store_true",
                         help="Retag every file (default: only files whose tags differ from DB)")
    lib_sub.add_parser("scrub-dangling")

    args = p.parse_args()
    server = args.server.rstrip("/")
    token = args.token or ""

    dispatch = {
        "status": cmd_status,
        "healthz": cmd_healthz,
        "scan": cmd_scan,
        "download": cmd_download,
        "download-request": cmd_download_request,
        "sync": cmd_sync,
        "jellyfin-pull": cmd_jellyfin_pull,
        "catalog-pull": cmd_catalog_pull,
    }

    if args.command in dispatch:
        dispatch[args.command](server, token, args)
    elif args.command == "duplicates":
        dup_cmd = getattr(args, "dup_cmd", None)
        if dup_cmd == "list":
            cmd_duplicates_list(server, token, args)
        elif dup_cmd == "resolve":
            cmd_duplicates_resolve(server, token, args)
        elif dup_cmd == "resolve-all":
            cmd_duplicates_resolve_all(server, token, args)
        else:
            dup_p.print_help()
    elif args.command == "split-albums":
        sa_cmd = getattr(args, "sa_cmd", None)
        if sa_cmd == "list":
            cmd_split_albums_list(server, token, args)
        elif sa_cmd == "merge":
            cmd_split_albums_merge(server, token, args)
        elif sa_cmd == "dismiss":
            cmd_split_albums_dismiss(server, token, args)
        else:
            sa_p.print_help()
    elif args.command == "library":
        lib_cmd = getattr(args, "lib_cmd", None)
        if lib_cmd == "albums":
            cmd_library_albums(server, token, args)
        elif lib_cmd == "tracks":
            cmd_library_tracks(server, token, args)
        elif lib_cmd == "artists":
            cmd_library_artists(server, token, args)
        elif lib_cmd == "consolidate":
            cmd_library_consolidate(server, token, args)
        elif lib_cmd == "retag":
            cmd_library_retag(server, token, args)
        elif lib_cmd == "scrub-dangling":
            cmd_library_scrub_dangling(server, token, args)
        else:
            lib_p.print_help()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
