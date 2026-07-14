import { useState, type Dispatch, type SetStateAction } from "react";
import { useToast } from "../../components/Toast";
import {
  addTrackToPlaylist,
  applyTrackTags,
  contributeTrack,
  fetchConfig,
  identifyTrack,
  postSuggestions,
  queueCatalogDownload,
  setTrackLiked,
  softDeleteTrack,
  SUGGESTION_HOST_KEY,
  suggestionHostFromConfig,
  type IdentifyCandidate,
  type LibraryTrack,
  type Playlist,
  type TagMeta,
} from "../../lib/api";
import { createPlayableQueue } from "../../lib/songList";
import { usePlayer } from "../../player/PlayerContext";
import type { SongMenuActions } from "./menu";

type IdentifyDialogState = {
  mbid: string;
  candidate: IdentifyCandidate;
  localPath: string;
};

type UseSongsActionsOptions = {
  visibleTracks: readonly LibraryTrack[];
  setRows: Dispatch<SetStateAction<LibraryTrack[]>>;
  allPlaylists: readonly Playlist[];
  playlistId?: string | null;
  reloadTable: () => void;
  onPlaylistsChanged: () => void;
  onOpenSettings: (focusKey?: string) => void;
};

export type SongsActionsController = {
  currentMbid: string | null;
  isPlaying: boolean;
  identify: IdentifyDialogState | null;
  identifying: boolean;
  applyingTag: boolean;
  contributingMbid: string | null;
  suggestingMbid: string | null;
  menuActions: SongMenuActions;
  playFrom: (startIndex: number) => void;
  toggle: () => void;
  handleApplyTags: (meta: TagMeta) => Promise<void>;
  closeIdentifyDialog: () => void;
};

export function useSongsActions({
  visibleTracks,
  setRows,
  allPlaylists,
  playlistId,
  reloadTable,
  onPlaylistsChanged,
  onOpenSettings,
}: UseSongsActionsOptions): SongsActionsController {
  const [identify, setIdentify] = useState<IdentifyDialogState | null>(null);
  const [identifying, setIdentifying] = useState(false);
  const [applyingTag, setApplyingTag] = useState(false);
  const [contributingMbid, setContributingMbid] = useState<string | null>(null);
  const [suggestingMbid, setSuggestingMbid] = useState<string | null>(null);
  const {
    play,
    current,
    isPlaying,
    toggle,
    playNext,
    addToQueue,
    setTrackLikedInQueue,
  } = usePlayer();
  const toast = useToast();

  const playFrom = (startIndex: number) => {
    const selection = createPlayableQueue(visibleTracks, startIndex);
    if (selection.queue.length) play(selection.queue, selection.startIndex);
  };

  const handleLikeToggle = async (track: LibraryTrack) => {
    const nextLiked = !track.is_liked;
    setRows((rows) =>
      rows.map((row) =>
        row.mbid === track.mbid ? { ...row, is_liked: nextLiked } : row,
      ),
    );
    setTrackLikedInQueue(track.mbid, nextLiked);

    const result = await setTrackLiked(track.mbid, nextLiked);
    if (!result.success) {
      setRows((rows) =>
        rows.map((row) =>
          row.mbid === track.mbid
            ? { ...row, is_liked: track.is_liked }
            : row,
        ),
      );
      setTrackLikedInQueue(track.mbid, track.is_liked);
      toast.show(result.message ?? "Could not save like", "err");
      return;
    }
    if (nextLiked) onPlaylistsChanged();
  };

  const handleQueueDownload = async (mbid: string) => {
    const result = await queueCatalogDownload([mbid]);
    if (!result.success) {
      toast.show(result.message || "Queue failed", "err");
      return;
    }
    if (result.queued > 0) {
      toast.show("Queued for download.");
    } else if (result.skipped_linked > 0) {
      toast.show("Already has a local file — nothing queued.");
    } else if (result.skipped_queued > 0) {
      toast.show("Already in the download queue.");
    } else if (result.not_found > 0) {
      toast.show("Track not found in catalog.", "err");
    } else {
      toast.show("Nothing queued.");
    }
  };

  const handleSoftDelete = async (track: LibraryTrack) => {
    const label = `${track.artist} — ${track.title}`;
    if (
      !window.confirm(
        `Soft-delete "${label}"? It becomes an orphan — restore from the web /orphans page if needed.`,
      )
    ) {
      return;
    }

    const result = await softDeleteTrack(track.mbid);
    if (!result.success) {
      toast.show(result.message || "Delete failed", "err");
      return;
    }
    toast.show(`Deleted "${label}".`);
    onPlaylistsChanged();
    reloadTable();
  };

  const handleContribute = async (track: LibraryTrack) => {
    if (contributingMbid) return;
    setContributingMbid(track.mbid);
    try {
      const payload = await fetchConfig();
      const role = String(payload.config.device_role ?? "satellite");
      const masterUrl = String(payload.config.master_url ?? "").trim();
      if (role === "master" || role === "standalone" || !masterUrl) {
        toast.show("This device has no upstream master to contribute to.", "err");
        return;
      }
      const result = await contributeTrack(track.mbid);
      if (!result.success) {
        toast.show(result.message || "Contribution failed", "err");
        return;
      }
      const labels: Record<string, string> = {
        attempting: "The master is trying to acquire a matching copy.",
        have_better: "The master already has an equal-or-better copy.",
        satisfied: "The master downloaded a matching copy.",
        needs_upload:
          "The master requested an upload; run Contributions again to send it.",
        ingested: "The track was uploaded and ingested by the master.",
      };
      toast.show(
        result.status
          ? labels[result.status] ?? `Contribution status: ${result.status}.`
          : "Track offered to the master.",
      );
    } catch (error) {
      toast.show(`Contribution failed: ${String(error)}`, "err");
    } finally {
      setContributingMbid(null);
    }
  };

  const handleSuggest = async (track: LibraryTrack) => {
    if (suggestingMbid) return;
    setSuggestingMbid(track.mbid);
    try {
      const config = await fetchConfig();
      const host = suggestionHostFromConfig(config.config);
      if (!host) {
        toast.show(
          `${SUGGESTION_HOST_KEY} is not configured. Opening Settings.`,
          "err",
        );
        onOpenSettings(SUGGESTION_HOST_KEY);
        return;
      }

      const result = await postSuggestions([
        { artist: track.artist, title: track.title, mbid: track.mbid },
      ]);
      if (!result.success) {
        toast.show(result.message || "Suggestion failed", "err");
        return;
      }
      const label = `${track.artist} — ${track.title}`;
      if (result.queued > 0) {
        toast.show(`Suggested ${label} to Jellyfin.`);
      } else if (result.skipped > 0) {
        toast.show(`${label} is already queued on the host.`);
      } else {
        toast.show(`Sent ${label} to the host.`);
      }
    } catch (error) {
      toast.show(`Suggestion failed: ${String(error)}`, "err");
    } finally {
      setSuggestingMbid(null);
    }
  };

  const handleIdentify = async (track: LibraryTrack) => {
    if (identifying) return;
    setIdentifying(true);
    try {
      const result = await identifyTrack(track.mbid);
      if (result.kind === "needs_config") {
        toast.show(`${result.message} Opening Settings.`, "err");
        onOpenSettings(result.key);
        return;
      }
      if (result.kind === "error") {
        toast.show(result.message, "err");
        return;
      }
      if (result.kind === "no_match") {
        toast.show("No match found on AcoustID / MusicBrainz.");
        return;
      }
      setIdentify({
        mbid: track.mbid,
        candidate: result.candidate,
        localPath: result.localPath,
      });
    } finally {
      setIdentifying(false);
    }
  };

  const handleApplyTags = async (meta: TagMeta) => {
    if (!identify || applyingTag) return;
    setApplyingTag(true);
    try {
      const result = await applyTrackTags(identify.mbid, meta);
      if (!result.success) {
        toast.show(result.message || "Tag apply failed", "err");
        return;
      }
      toast.show("Tags written.");
      setIdentify(null);
      reloadTable();
      onPlaylistsChanged();
    } finally {
      setApplyingTag(false);
    }
  };

  const handleAddToPlaylist = async (
    targetPlaylistId: string,
    track: LibraryTrack,
  ) => {
    const playlist = allPlaylists.find(
      (item) => item.playlist_id === targetPlaylistId,
    );
    const name = playlist?.name ?? targetPlaylistId;
    const result = await addTrackToPlaylist(targetPlaylistId, track.mbid);
    if (!result.success) {
      toast.show(result.message || "Add failed", "err");
      return;
    }
    if (result.added === 0) {
      toast.show(`Already in "${name}".`);
      return;
    }
    toast.show(
      result.missed > 0
        ? `Added to "${name}" (${result.missed} dropped as unknown).`
        : `Added to "${name}".`,
    );
    onPlaylistsChanged();
    if (playlistId === targetPlaylistId) reloadTable();
  };

  const menuActions: SongMenuActions = {
    onLikeToggle: handleLikeToggle,
    onPlayNext: (track) =>
      playNext({ ...track, albumId: track.album_id }),
    onAddToQueue: (track) =>
      addToQueue({ ...track, albumId: track.album_id }),
    onAddToPlaylist: handleAddToPlaylist,
    onQueueDownload: handleQueueDownload,
    onSuggest: handleSuggest,
    onContribute: handleContribute,
    onIdentify: handleIdentify,
    onSoftDelete: handleSoftDelete,
  };

  return {
    currentMbid: current?.mbid ?? null,
    isPlaying,
    identify,
    identifying,
    applyingTag,
    contributingMbid,
    suggestingMbid,
    menuActions,
    playFrom,
    toggle,
    handleApplyTags,
    closeIdentifyDialog: () => setIdentify(null),
  };
}
