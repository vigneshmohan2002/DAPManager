import type { ReactNode } from "react";
import ContextMenu from "./ContextMenu";
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
        className="text-lg leading-none px-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
      >
        +
      </button>
    ),
    items: playlistItems,
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
    <div className="mb-6">
      {showHeading && (
        <div className="px-3 pb-2 flex items-center justify-between text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          <span>{section.title}</span>
          {section.accessory ?? null}
        </div>
      )}
      <ul className="space-y-0.5">
        {section.items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <button
                onClick={() => onSelect(item.id)}
                onContextMenu={(event) => onContextMenuItem?.(item, event)}
                className={`w-full flex items-center text-left px-3 py-1.5 rounded-md text-sm transition-colors ${
                  active
                    ? "bg-[var(--color-surface)] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/50"
                }`}
              >
                {item.smartRules ? (
                  <span
                    title="Smart playlist (rule-based)"
                    className="mr-1 shrink-0 text-[var(--color-accent)]"
                  >
                    ★
                  </span>
                ) : null}
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
