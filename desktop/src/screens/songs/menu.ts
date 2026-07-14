import type { ContextMenuEntry } from "../../components/ContextMenu";
import type { LibraryTrack, Playlist } from "../../lib/api";

export type SongMenuActions = {
  onLikeToggle: (track: LibraryTrack) => void;
  onPlayNext: (track: LibraryTrack) => void;
  onAddToQueue: (track: LibraryTrack) => void;
  onAddToPlaylist: (playlistId: string, track: LibraryTrack) => void;
  onQueueDownload: (mbid: string) => void;
  onSuggest: (track: LibraryTrack) => void;
  onContribute: (track: LibraryTrack) => void;
  onIdentify: (track: LibraryTrack) => void;
  onSoftDelete: (track: LibraryTrack) => void;
};

type BuildSongContextMenuOptions = {
  track: LibraryTrack;
  playlists: readonly Playlist[];
  canContributeToMaster: boolean;
  contributingMbid: string | null;
  suggestingMbid: string | null;
  identifying: boolean;
  actions: SongMenuActions;
};

export function buildSongContextMenu({
  track,
  playlists,
  canContributeToMaster,
  contributingMbid,
  suggestingMbid,
  identifying,
  actions,
}: BuildSongContextMenuOptions): ContextMenuEntry[] {
  const unavailable = track.availability === "unavailable";
  const local = track.availability === "local";

  return [
    { kind: "label", text: `${track.artist} — ${track.title}` },
    { kind: "separator" },
    {
      kind: "item",
      label: track.is_liked
        ? "Remove from Liked Songs"
        : "Add to Liked Songs",
      disabled: unavailable,
      onSelect: () => actions.onLikeToggle(track),
    },
    {
      kind: "item",
      label: "Play next",
      disabled: unavailable,
      onSelect: () => actions.onPlayNext(track),
    },
    {
      kind: "item",
      label: "Add to queue",
      disabled: unavailable,
      onSelect: () => actions.onAddToQueue(track),
    },
    { kind: "separator" },
    {
      kind: "list",
      heading: "Add to playlist",
      emptyText: "(no playlists — create one from the sidebar)",
      items: playlists
        .filter((playlist) => !playlist.smart_rules)
        .map((playlist) => ({
          key: playlist.playlist_id,
          label: playlist.name,
          onSelect: () =>
            actions.onAddToPlaylist(playlist.playlist_id, track),
        })),
    },
    { kind: "separator" },
    {
      kind: "item",
      label: "Queue Download",
      onSelect: () => actions.onQueueDownload(track.mbid),
    },
    {
      kind: "item",
      label: suggestingMbid === track.mbid ? "Suggesting…" : "Suggest to Jellyfin",
      disabled: suggestingMbid !== null,
      onSelect: () => actions.onSuggest(track),
    },
    {
      kind: "item",
      label:
        contributingMbid === track.mbid
          ? "Contributing…"
          : "Contribute to master",
      disabled:
        !canContributeToMaster || !local || contributingMbid !== null,
      onSelect: () => actions.onContribute(track),
    },
    {
      kind: "item",
      label: identifying ? "Identifying…" : "Identify & Tag",
      disabled: !local || identifying,
      onSelect: () => actions.onIdentify(track),
    },
    {
      kind: "item",
      label: "Soft-Delete",
      danger: true,
      onSelect: () => actions.onSoftDelete(track),
    },
  ];
}
