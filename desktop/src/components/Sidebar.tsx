type SidebarItem = {
  id: string;
  label: string;
};

type SidebarSection = {
  title: string;
  items: SidebarItem[];
};

type Props = {
  activeId: string;
  onSelect: (id: string) => void;
};

const SECTIONS: SidebarSection[] = [
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

export default function Sidebar({ activeId, onSelect }: Props) {
  return (
    <aside className="w-60 shrink-0 bg-[var(--color-bg-sidebar)] border-r border-[var(--color-border)] flex flex-col">
      <div className="titlebar-drag h-10 shrink-0" />
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {SECTIONS.map((section) => (
          <div key={section.title} className="mb-6">
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
                      className={`w-full text-left px-3 py-1.5 rounded-md text-sm transition-colors ${
                        active
                          ? "bg-[var(--color-surface)] text-[var(--color-text)]"
                          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/50"
                      }`}
                    >
                      {item.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
