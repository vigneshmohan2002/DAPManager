import Icon from "./Icon";

type Props = {
  title: string;
  subtitle?: string;
  search?: string;
  onSearch?: (v: string) => void;
  onBack?: () => void;
};

export default function TopBar({
  title,
  subtitle,
  search,
  onSearch,
  onBack,
}: Props) {
  const hasSearch = search !== undefined && onSearch !== undefined;
  return (
    <header className="doppler-toolbar titlebar-drag h-12 shrink-0 border-b border-[var(--color-border)] flex items-center gap-2 px-3">
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          aria-label="Back"
          className="doppler-control titlebar-nodrag grid h-7 w-7 place-items-center rounded-md"
        >
          <Icon name="back" size={15} />
        </button>
      ) : (
        <span className="w-1" />
      )}
      <div className="flex-1 min-w-0">
        <h1 className="text-[13px] font-medium leading-tight truncate">
          {title}
        </h1>
        {subtitle ? (
          <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-muted)] truncate">
            {subtitle}
          </div>
        ) : null}
      </div>
      {hasSearch ? (
        <label className="titlebar-nodrag relative block">
          <Icon
            name="search"
            size={13}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
          />
          <input
            type="search"
            placeholder="Search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            className="h-7 w-52 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] py-1 pl-7 pr-2 text-[11px] text-[var(--color-text)] shadow-inner placeholder:text-[var(--color-text-muted)]"
          />
        </label>
      ) : null}
    </header>
  );
}
