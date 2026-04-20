type Props = {
  title: string;
  subtitle?: string;
  search: string;
  onSearch: (v: string) => void;
};

export default function TopBar({ title, subtitle, search, onSearch }: Props) {
  return (
    <header className="titlebar-drag h-14 shrink-0 border-b border-[var(--color-border)] flex items-center gap-4 px-6">
      <div className="flex-1 min-w-0">
        <h1 className="text-lg font-semibold truncate">{title}</h1>
        {subtitle ? (
          <div className="text-xs text-[var(--color-text-muted)]">
            {subtitle}
          </div>
        ) : null}
      </div>
      <div className="titlebar-nodrag">
        <input
          type="search"
          placeholder="Search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          className="w-56 bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] rounded-md px-3 py-1.5 outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
        />
      </div>
    </header>
  );
}
