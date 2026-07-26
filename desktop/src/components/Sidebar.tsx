import type { ReactNode } from "react";
import ContextMenu from "./ContextMenu";
import Icon, { type IconName } from "./Icon";
import {
  STATIC_SECTIONS,
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
  const isMac =
    typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  const toast = useToast();
  const {
    playlists,
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

  const playlistSection: RenderSection = {
    title: "Playlists",
    accessory: (
      <button
        onClick={openCreateDialog}
        disabled={!ready}
        title="New playlist"
        aria-label="New playlist"
        className="doppler-control grid h-5 w-5 place-items-center rounded"
      >
        <span className="text-base leading-none">+</span>
      </button>
    ),
    items: playlistItems,
  };

  return (
    <aside className="doppler-sidebar w-[196px] shrink-0 border-r border-[var(--color-border)] flex flex-col">
      <div className="titlebar-drag h-11 shrink-0" />
      <div className="px-2.5 pb-2 titlebar-nodrag">
        <button
          onClick={onOpenSearch}
          className="doppler-control w-full flex h-7 items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)]/70 px-2 text-[11px] shadow-inner"
        >
          <Icon name="search" size={13} />
          <span>Search</span>
          <span className="ml-auto text-[9px] tracking-wide text-[var(--color-text-muted)]">
            {isMac ? "⌘K" : "Ctrl K"}
          </span>
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2.5 pb-3">
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
            ) : playlists.length === 0 ? (
              <div className="px-2 py-1 text-[10px] text-[var(--color-text-muted)] italic">
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
          onClose={closeMenu}
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
        <div className="px-2 pb-1 flex items-center justify-between text-[10px] font-medium text-[var(--color-text-muted)]">
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
                className={`w-full flex h-6 items-center gap-1.5 rounded px-2 text-left text-[11px] transition-colors ${
                  active
                    ? "doppler-selection font-medium"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/55"
                }`}
              >
                <Icon
                  name={icon}
                  size={13}
                  className={active ? "text-[var(--color-accent)]" : ""}
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
