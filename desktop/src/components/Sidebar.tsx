import { useMemo, useState, type ReactNode } from "react";
import ContextMenu, { type ContextMenuEntry } from "./ContextMenu";
import Icon, { type IconName } from "./Icon";
import {
  STATIC_SECTIONS,
  TOOL_ITEMS,
  type SidebarItem,
  type SidebarSection,
} from "./sidebar/model";
import { usePlaylistSidebarController } from "./sidebar/usePlaylistSidebarController";
import SmartPlaylistDialog from "./SmartPlaylistDialog";
import { useToast } from "./Toast";

export { playlistSidebarId } from "./sidebar/model";

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

type RenderSection = SidebarSection & {
  accessory?: ReactNode;
};

const STATIC_ICONS: Record<string, IconName> = {
  home: "home",
  albums: "albums",
  artists: "artists",
  songs: "songs",
  stats: "listening",
  downloads: "downloads",
  releases: "releases",
  audit: "audit",
  duplicates: "duplicates",
  orphans: "orphans",
  fleet: "fleet",
  contributions: "contributions",
  sync: "sync",
  suggest: "suggest",
  settings: "settings",
};

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
  const toast = useToast();
  const [toolsMenu, setToolsMenu] = useState<{ x: number; y: number } | null>(
    null,
  );
  const {
    playlistItems,
    playlistError,
    menu,
    menuEntries,
    dialog,
    saving,
    openCreateDialog,
    openMenu,
    closeMenu,
    cancelDialog,
    saveDialog,
  } = usePlaylistSidebarController({
    ready,
    playlistsVersion,
    onPlaylistsChanged,
    onPlaylistCreated,
    onPlaylistDeleted,
    showToast: toast.show,
  });

  const likedPlaylist = playlistItems.find(
    (item) => item.playlistId === "liked_songs",
  );
  const regularPlaylistItems = playlistItems.filter(
    (item) => item.playlistId !== "liked_songs",
  );
  const primarySections = STATIC_SECTIONS.map((section) =>
    section.title === "Presets" && likedPlaylist
      ? { ...section, items: [...section.items, likedPlaylist] }
      : section,
  );

  const playlistSection: RenderSection = {
    title: "Playlists",
    items: regularPlaylistItems,
  };

  const toolsMenuEntries = useMemo<ContextMenuEntry[]>(
    () => [
      { kind: "label", text: "DAPManager" },
      {
        kind: "item",
        label: "Search…",
        onSelect: onOpenSearch,
      },
      { kind: "separator" },
      ...TOOL_ITEMS.map((item) => ({
        kind: "item" as const,
        label: item.label,
        onSelect: () => onSelect(item.id),
      })),
    ],
    [onOpenSearch, onSelect],
  );
  const toolActive = TOOL_ITEMS.some((item) => item.id === activeId);

  return (
    <aside className="doppler-sidebar flex h-full w-[220px] shrink-0 flex-col border-r border-[var(--color-border)]">
      <div className="titlebar-drag h-[52px] shrink-0" />
      <nav className="flex-1 overflow-y-auto px-2.5 pb-3">
        {primarySections.map((section) => (
          <Section
            key={section.title}
            section={section}
            activeId={activeId}
            onSelect={onSelect}
            onContextMenuItem={(item, event) => {
              if (!item.playlistId) return;
              event.preventDefault();
              openMenu(item, event.clientX, event.clientY);
            }}
          />
        ))}
        <Section
          section={{ title: "Collections", items: [] }}
          activeId={activeId}
          onSelect={onSelect}
        />
        <Section
          section={playlistSection}
          activeId={activeId}
          onSelect={onSelect}
          onContextMenuItem={(item, event) => {
            if (!item.playlistId) return;
            event.preventDefault();
            openMenu(item, event.clientX, event.clientY);
          }}
          footer={
            playlistError ? (
              <div
                className="px-2 py-1 text-[10px] text-[var(--color-danger)] truncate"
                title={playlistError}
              >
                Failed to load
              </div>
            ) : regularPlaylistItems.length === 0 ? (
              <div className="px-2 py-1 text-[10px] text-[var(--color-text-muted)] italic">
                No playlists yet.
              </div>
            ) : null
          }
        />
      </nav>
      <div className="group titlebar-nodrag relative flex h-[38px] shrink-0 items-center border-t border-[var(--color-border)] px-2">
        <button
          type="button"
          onClick={openCreateDialog}
          disabled={!ready}
          title="New playlist"
          aria-label="New Playlist/Collection"
          className="doppler-control flex h-7 min-w-0 flex-1 items-center gap-2 rounded px-1.5 text-left text-[12px] font-medium"
        >
          <span className="text-[19px] font-light leading-none">+</span>
          <span className="truncate">New Playlist/Collection</span>
        </button>
        <button
          type="button"
          aria-label="Open DAPManager tools"
          aria-haspopup="menu"
          aria-expanded={toolsMenu !== null}
          onClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            setToolsMenu({ x: rect.right - 4, y: rect.top });
          }}
          className={`doppler-control absolute right-2 grid h-7 w-7 shrink-0 place-items-center rounded bg-[var(--color-bg-sidebar)] transition-opacity ${
            toolActive || toolsMenu
              ? "opacity-100"
              : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
          }`}
        >
          <Icon name="settings" size={14} />
        </button>
      </div>
      {menuEntries && menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          entries={menuEntries}
          onClose={closeMenu}
        />
      ) : null}
      {toolsMenu ? (
        <ContextMenu
          x={toolsMenu.x}
          y={toolsMenu.y}
          entries={toolsMenuEntries}
          onClose={() => setToolsMenu(null)}
        />
      ) : null}
      {dialog ? (
        <SmartPlaylistDialog
          mode={
            dialog.kind === "edit"
              ? { kind: "edit", playlistId: dialog.playlistId, nameLocked: true }
              : { kind: "create" }
          }
          initialName={dialog.kind === "edit" ? dialog.name : ""}
          initialRules={dialog.kind === "edit" ? dialog.rules : null}
          saving={saving}
          onSave={saveDialog}
          onCancel={cancelDialog}
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
  section: RenderSection;
  activeId: string;
  onSelect: (id: string) => void;
  onContextMenuItem?: (item: SidebarItem, event: React.MouseEvent) => void;
  footer?: ReactNode;
}) {
  // Empty-title sections (the top-level Home anchor) retain the same rhythm
  // without rendering a heading row.
  const showHeading = section.title.length > 0 || section.accessory;
  return (
    <div className="mb-3.5">
      {showHeading && (
        <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-semibold text-[var(--color-text-subtle)]">
          <span>{section.title}</span>
          {section.accessory ?? null}
        </div>
      )}
      <ul className="space-y-px">
        {section.items.map((item) => {
          const active = item.id === activeId;
          const icon =
            STATIC_ICONS[item.id] ??
            (item.id.startsWith("playlist:")
              ? item.id === "playlist:liked_songs"
                ? "heart"
                : "playlist"
              : "playlist");
          return (
            <li key={item.id}>
              <button
                onClick={() => onSelect(item.id)}
                onContextMenu={(event) => onContextMenuItem?.(item, event)}
                className={`flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] transition-colors ${
                  active
                    ? "doppler-selection font-medium"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/55"
                }`}
              >
                <Icon
                  name={icon}
                  size={15}
                  className={
                    section.title === "Library" || active
                      ? "text-[var(--color-accent)]"
                      : ""
                  }
                />
                {item.smartRules ? (
                  <span
                    title="Smart playlist (rule-based)"
                    className="shrink-0 text-[8px] text-[var(--color-accent)]"
                  >
                    ◆
                  </span>
                ) : null}
                <span className="truncate">{item.label}</span>
                {item.count !== undefined ? (
                  <span className="ml-auto shrink-0 text-[9px] tabular-nums text-[var(--color-text-muted)]">
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
