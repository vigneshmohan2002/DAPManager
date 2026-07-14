import { useCallback, useEffect, useState } from "react";
import {
  createPlaylist,
  deletePlaylist,
  fetchPlaylists,
  renamePlaylist,
  updatePlaylistSmartRules,
  type Playlist,
  type SmartRuleset,
} from "../../lib/api";
import type { ContextMenuEntry } from "../ContextMenu";
import {
  playlistMenuEntries,
  playlistSidebarItems,
  type PlaylistDialog,
  type PlaylistMenu,
  type SidebarItem,
} from "./model";

type ToastVariant = "ok" | "err";
type ShowToast = (message: string, variant?: ToastVariant) => void;

type Options = {
  ready: boolean;
  playlistsVersion: number;
  onPlaylistsChanged: () => void;
  onPlaylistCreated: (pid: string) => void;
  onPlaylistDeleted: (pid: string) => void;
  showToast: ShowToast;
};

export type PlaylistSidebarController = {
  playlists: Playlist[];
  playlistItems: SidebarItem[];
  playlistError: string | null;
  menu: PlaylistMenu | null;
  menuEntries: ContextMenuEntry[] | null;
  dialog: PlaylistDialog | null;
  saving: boolean;
  openCreateDialog: () => void;
  openMenu: (item: SidebarItem, x: number, y: number) => void;
  closeMenu: () => void;
  cancelDialog: () => void;
  saveDialog: (name: string, rules: SmartRuleset | null) => Promise<void>;
};

export function usePlaylistSidebarController({
  ready,
  playlistsVersion,
  onPlaylistsChanged,
  onPlaylistCreated,
  onPlaylistDeleted,
  showToast,
}: Options): PlaylistSidebarController {
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [playlistError, setPlaylistError] = useState<string | null>(null);
  const [menu, setMenu] = useState<PlaylistMenu | null>(null);
  const [dialog, setDialog] = useState<PlaylistDialog | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    const load = async () => {
      try {
        const data = await fetchPlaylists();
        if (cancelled) return;
        setPlaylists(data);
        setPlaylistError(null);
      } catch (loadError) {
        if (!cancelled) setPlaylistError(String(loadError));
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistsVersion]);

  const handleCreate = useCallback(
    async (name: string, rules: SmartRuleset | null) => {
      setSaving(true);
      try {
        const result = await createPlaylist(name, rules);
        if (!result.success || !result.playlist_id) {
          showToast(result.message ?? "Failed to create playlist", "err");
          return;
        }
        showToast(
          `Created ${rules ? "smart " : ""}playlist "${result.name ?? name}".`,
        );
        onPlaylistCreated(result.playlist_id);
        setDialog(null);
      } finally {
        setSaving(false);
      }
    },
    [onPlaylistCreated, showToast],
  );

  const handleEditRules = useCallback(
    async (playlistId: string, rules: SmartRuleset | null) => {
      setSaving(true);
      try {
        const result = await updatePlaylistSmartRules(playlistId, rules);
        if (!result.success) {
          showToast(result.message || "Save rules failed", "err");
          return;
        }
        showToast(rules ? "Rules updated." : "Rules cleared.");
        onPlaylistsChanged();
        setDialog(null);
      } finally {
        setSaving(false);
      }
    },
    [onPlaylistsChanged, showToast],
  );

  const handleRename = useCallback(
    async (pid: string, currentName: string) => {
      const next = (
        window.prompt("Rename playlist to:", currentName) ?? ""
      ).trim();
      if (!next || next === currentName) return;
      const result = await renamePlaylist(pid, next);
      if (!result.success) {
        showToast(result.message || "Rename failed", "err");
        return;
      }
      showToast(`Renamed to "${next}".`);
      onPlaylistsChanged();
    },
    [onPlaylistsChanged, showToast],
  );

  const handleDelete = useCallback(
    async (pid: string, name: string) => {
      if (
        !window.confirm(
          `Soft-delete playlist "${name}"? It becomes an orphan — restore from the web /orphans page if needed.`,
        )
      ) {
        return;
      }
      const result = await deletePlaylist(pid);
      if (!result.success) {
        showToast(result.message || "Delete failed", "err");
        return;
      }
      showToast(`Deleted "${name}".`);
      onPlaylistDeleted(pid);
    },
    [onPlaylistDeleted, showToast],
  );

  const openMenu = useCallback((item: SidebarItem, x: number, y: number) => {
    if (!item.playlistId) return;
    setMenu({
      x,
      y,
      pid: item.playlistId,
      name: item.label,
      smartRules: item.smartRules ?? null,
    });
  }, []);

  const menuEntries = playlistMenuEntries(menu, {
    editRules: (selected) =>
      setDialog({
        kind: "edit",
        playlistId: selected.pid,
        name: selected.name,
        rules: selected.smartRules,
      }),
    rename: (pid, name) => {
      handleRename(pid, name);
    },
    remove: (pid, name) => {
      handleDelete(pid, name);
    },
  });

  const saveDialog = useCallback(
    async (name: string, rules: SmartRuleset | null) => {
      if (!dialog) return;
      if (dialog.kind === "edit") {
        await handleEditRules(dialog.playlistId, rules);
        return;
      }
      await handleCreate(name, rules);
    },
    [dialog, handleCreate, handleEditRules],
  );

  return {
    playlists,
    playlistItems: playlistSidebarItems(playlists),
    playlistError,
    menu,
    menuEntries,
    dialog,
    saving,
    openCreateDialog: () => setDialog({ kind: "create" }),
    openMenu,
    closeMenu: () => setMenu(null),
    cancelDialog: () => {
      if (!saving) setDialog(null);
    },
    saveDialog,
  };
}
