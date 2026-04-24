import { useEffect, useState } from "react";
import { fetchPlaylists, type Playlist } from "../lib/api";

type SidebarItem = {
  id: string;
  label: string;
  count?: number;
};

type SidebarSection = {
  title: string;
  items: SidebarItem[];
};

type Props = {
  activeId: string;
  onSelect: (id: string) => void;
  onOpenSearch: () => void;
  ready: boolean;
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
}: Props) {
  const isMac =
    typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [playlistError, setPlaylistError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchPlaylists();
        if (!cancelled) setPlaylists(data);
      } catch (e) {
        if (!cancelled) setPlaylistError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  const playlistSection: SidebarSection = {
    title: "Playlists",
    items: playlists.map((p) => ({
      id: playlistSidebarId(p.playlist_id),
      label: p.name,
      count: p.track_count,
    })),
  };

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
        {playlistSection.items.length > 0 || playlistError ? (
          <Section
            section={playlistSection}
            activeId={activeId}
            onSelect={onSelect}
            footer={
              playlistError ? (
                <div
                  className="px-3 py-1 text-xs text-[var(--color-accent)] truncate"
                  title={playlistError}
                >
                  Failed to load
                </div>
              ) : null
            }
          />
        ) : null}
      </nav>
    </aside>
  );
}

function Section({
  section,
  activeId,
  onSelect,
  footer,
}: {
  section: SidebarSection;
  activeId: string;
  onSelect: (id: string) => void;
  footer?: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <div className="px-3 pb-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        {section.title}
      </div>
      <ul className="space-y-0.5">
        {section.items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <button
                onClick={() => onSelect(item.id)}
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
