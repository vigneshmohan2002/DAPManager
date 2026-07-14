import type { Playlist, SmartRuleset } from "../../lib/api";
import type { ContextMenuEntry } from "../ContextMenu";

export type SidebarItem = {
  id: string;
  label: string;
  count?: number;
  playlistId?: string;
  smartRules?: SmartRuleset | null;
};

export type SidebarSection = {
  title: string;
  items: SidebarItem[];
};

export type PlaylistMenu = {
  x: number;
  y: number;
  pid: string;
  name: string;
  smartRules: SmartRuleset | null;
};

export type PlaylistDialog =
  | { kind: "create" }
  | {
      kind: "edit";
      playlistId: string;
      name: string;
      rules: SmartRuleset | null;
    };

export const STATIC_SECTIONS: SidebarSection[] = [
  {
    // Home is the launch surface rather than another library browser.
    title: "",
    items: [{ id: "home", label: "Home" }],
  },
  {
    title: "Library",
    items: [
      { id: "albums", label: "Albums" },
      { id: "artists", label: "Artists" },
      { id: "songs", label: "Songs" },
      { id: "stats", label: "Listening" },
    ],
  },
  {
    title: "Discover",
    items: [
      { id: "downloads", label: "Downloads" },
      { id: "releases", label: "New Releases" },
    ],
  },
  {
    title: "Manage",
    items: [
      { id: "audit", label: "Audit" },
      { id: "duplicates", label: "Duplicates" },
      { id: "orphans", label: "Orphans" },
      { id: "fleet", label: "Fleet" },
      { id: "contributions", label: "Contributions" },
      { id: "sync", label: "Sync" },
      { id: "suggest", label: "Suggest" },
      { id: "settings", label: "Settings" },
    ],
  },
];

// Static screens use their own id; playlists retain the exact encoded prefix
// consumed by App.tsx.
export const playlistSidebarId = (pid: string): string => `playlist:${pid}`;

export function playlistSidebarItems(
  playlists: readonly Playlist[],
): SidebarItem[] {
  const ordered = [...playlists].sort((left, right) => {
    if (left.playlist_id === "liked_songs") return -1;
    if (right.playlist_id === "liked_songs") return 1;
    return 0;
  });

  return ordered.map((playlist) => ({
    id: playlistSidebarId(playlist.playlist_id),
    label:
      playlist.playlist_id === "liked_songs"
        ? `♥ ${playlist.name}`
        : playlist.name,
    count: playlist.track_count,
    playlistId: playlist.playlist_id,
    smartRules: playlist.smart_rules,
  }));
}

type PlaylistMenuActions = {
  editRules: (menu: PlaylistMenu) => void;
  rename: (pid: string, name: string) => void;
  remove: (pid: string, name: string) => void;
};

export function playlistMenuEntries(
  menu: PlaylistMenu | null,
  actions: PlaylistMenuActions,
): ContextMenuEntry[] | null {
  if (!menu) return null;

  const isSmart = Boolean(menu.smartRules);
  const isSystem = menu.pid === "liked_songs";
  return [
    { kind: "label", text: menu.name },
    { kind: "separator" },
    ...(isSmart
      ? [
          {
            kind: "item" as const,
            label: "Edit rules…",
            disabled: isSystem,
            onSelect: () => actions.editRules(menu),
          },
        ]
      : []),
    {
      kind: "item",
      label: "Rename…",
      disabled: isSystem,
      onSelect: () => actions.rename(menu.pid, menu.name),
    },
    {
      kind: "item",
      label: "Delete (soft)",
      danger: true,
      disabled: isSystem,
      onSelect: () => actions.remove(menu.pid, menu.name),
    },
  ];
}
