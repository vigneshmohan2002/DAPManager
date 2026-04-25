import { useEffect, useState } from "react";
import {
  createPlaylist,
  deletePlaylist,
  fetchPlaylists,
  renamePlaylist,
  type Playlist,
} from "../lib/api";
import ContextMenu, { type ContextMenuEntry } from "./ContextMenu";
import { useToast } from "./Toast";

type SidebarItem = {
  id: string;
  label: string;
  count?: number;
  // Attached only to playlist items so right-click can open the
  // rename/delete menu. Static items leave this unset.
  playlistId?: string;
};

type SidebarSection = {
  title: string;
  items: SidebarItem[];
  accessory?: React.ReactNode;
};

type Props = {
  activeId: string;
  onSelect: (id: string) => void;
  onOpenSearch: () => void;
  ready: boolean;
  playlistsVersion: number;
  onPlaylistsChanged: () => void;
  onPlaylistCreated: (pid: string) => void;
  onPlaylistDeleted: (pid: string) => void;
};

const STATIC_SECTIONS: SidebarSection[] = [
  {
    title: "Library",
    items: [
      { id: "albums", label: "Albums" },
      { id: "artists", label: "Artists" },
      { id: "songs", label: "Songs" },
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
      { id: "fleet", label: "Fleet" },
      { id: "sync", label: "Sync" },
      { id: "settings", label: "Settings" },
    ],
  },
];

// Sidebar id convention: static screens use their own id ("albums"),
// playlists use "playlist:<playlist_id>" so App.tsx can route the click
// to SongsScreen with the right scope.
export const playlistSidebarId = (pid: string) => `playlist:${pid}`;

export default function Sidebar({
  activeId,
  onSelect,
  onOpenSearch,
  ready,
  playlistsVersion,
  onPlaylistsChanged,
  onPlaylistCreated,
  onPlaylistDeleted,
}: Props) {
  const isMac =
    typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [playlistError, setPlaylistError] = useState<string | null>(null);
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    pid: string;
    name: string;
  } | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchPlaylists();
        if (!cancelled) {
          setPlaylists(data);
          setPlaylistError(null);
        }
      } catch (e) {
        if (!cancelled) setPlaylistError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistsVersion]);

  const handleNewPlaylist = async () => {
    const name = (window.prompt("New playlist name:") ?? "").trim();
    if (!name) return;
    const result = await createPlaylist(name);
    if (!result.success || !result.playlist_id) {
      toast.show(result.message ?? "Failed to create playlist", "err");
      return;
    }
    toast.show(`Created "${result.name ?? name}".`);
    onPlaylistCreated(result.playlist_id);
  };

  const handleRename = async (pid: string, currentName: string) => {
    const next = (
      window.prompt("Rename playlist to:", currentName) ?? ""
    ).trim();
    if (!next || next === currentName) return;
    const result = await renamePlaylist(pid, next);
    if (!result.success) {
      toast.show(result.message || "Rename failed", "err");
      return;
    }
    toast.show(`Renamed to "${next}".`);
    onPlaylistsChanged();
  };

  const handleDelete = async (pid: string, name: string) => {
    if (
      !window.confirm(
        `Soft-delete playlist "${name}"? It becomes an orphan — restore from the web /orphans page if needed.`,
      )
    )
      return;
    const result = await deletePlaylist(pid);
    if (!result.success) {
      toast.show(result.message || "Delete failed", "err");
      return;
    }
    toast.show(`Deleted "${name}".`);
    onPlaylistDeleted(pid);
  };

  const playlistSection: SidebarSection = {
    title: "Playlists",
    accessory: (
      <button
        onClick={handleNewPlaylist}
        disabled={!ready}
        title="New playlist"
        className="text-lg leading-none px-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
      >
        +
      </button>
    ),
    items: playlists.map((p) => ({
      id: playlistSidebarId(p.playlist_id),
      label: p.name,
      count: p.track_count,
      playlistId: p.playlist_id,
    })),
  };

  const menuEntries: ContextMenuEntry[] | null = menu
    ? [
        { kind: "label", text: menu.name },
        { kind: "separator" },
        {
          kind: "item",
          label: "Rename…",
          onSelect: () => handleRename(menu.pid, menu.name),
        },
        {
          kind: "item",
          label: "Delete (soft)",
          danger: true,
          onSelect: () => handleDelete(menu.pid, menu.name),
        },
      ]
    : null;

  return (
    <aside className="w-60 shrink-0 bg-[var(--color-bg-sidebar)] border-r border-[var(--color-border)] flex flex-col">
      <div className="titlebar-drag h-10 shrink-0" />
      <div className="px-3 pb-3 titlebar-nodrag">
        <button
          onClick={onOpenSearch}
          className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md bg-[var(--color-surface)]/70 hover:bg-[var(--color-surface)] text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <span>Search</span>
          <span className="ml-auto text-xs tracking-wide border border-[var(--color-border)] rounded px-1.5 py-0.5">
            {isMac ? "⌘K" : "Ctrl K"}
          </span>
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {STATIC_SECTIONS.map((section) => (
          <Section
            key={section.title}
            section={section}
            activeId={activeId}
            onSelect={onSelect}
          />
        ))}
        <Section
          section={playlistSection}
          activeId={activeId}
          onSelect={onSelect}
          onContextMenuItem={(item, e) => {
            if (!item.playlistId) return;
            e.preventDefault();
            setMenu({
              x: e.clientX,
              y: e.clientY,
              pid: item.playlistId,
              name: item.label,
            });
          }}
          footer={
            playlistError ? (
              <div
                className="px-3 py-1 text-xs text-[var(--color-accent)] truncate"
                title={playlistError}
              >
                Failed to load
              </div>
            ) : playlists.length === 0 ? (
              <div className="px-3 py-1 text-xs text-[var(--color-text-muted)] italic">
                No playlists yet.
              </div>
            ) : null
          }
        />
      </nav>
      {menuEntries && menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          entries={menuEntries}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </aside>
  );
}

function Section({
  section,
  activeId,
  onSelect,
  onContextMenuItem,
  footer,
}: {
  section: SidebarSection;
  activeId: string;
  onSelect: (id: string) => void;
  onContextMenuItem?: (item: SidebarItem, e: React.MouseEvent) => void;
  footer?: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <div className="px-3 pb-2 flex items-center justify-between text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        <span>{section.title}</span>
        {section.accessory ?? null}
      </div>
      <ul className="space-y-0.5">
        {section.items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <button
                onClick={() => onSelect(item.id)}
                onContextMenu={(e) => onContextMenuItem?.(item, e)}
                className={`w-full flex items-center text-left px-3 py-1.5 rounded-md text-sm transition-colors ${
                  active
                    ? "bg-[var(--color-surface)] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/50"
                }`}
              >
                <span className="truncate">{item.label}</span>
                {item.count !== undefined ? (
                  <span className="ml-auto shrink-0 text-xs text-[var(--color-text-muted)]">
                    {item.count}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
      {footer}
    </div>
  );
}
