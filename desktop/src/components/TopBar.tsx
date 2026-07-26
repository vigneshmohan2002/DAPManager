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
    <header className="doppler-toolbar titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4">
      <div className="titlebar-nodrag flex shrink-0 items-center">
        <button
          type="button"
          onClick={() => onBack?.()}
          disabled={!onBack}
          aria-label="Back"
          className="doppler-control grid h-7 w-7 place-items-center rounded"
        >
          <Icon name="back" size={17} />
        </button>
        <span className="mx-1 h-5 w-px bg-[var(--color-border)]" />
        <button
          type="button"
          disabled
          aria-label="Forward"
          className="doppler-control grid h-7 w-7 place-items-center rounded"
        >
          <Icon name="forward" size={17} />
        </button>
      </div>
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
        <div className="titlebar-nodrag flex items-center gap-2">
          <button
            type="button"
            disabled
            aria-label="Sort"
            className="doppler-control grid h-7 w-7 place-items-center rounded"
          >
            <Icon name="sort" size={16} />
          </button>
          <label className="relative block w-[clamp(132px,24vw,324px)]">
            <Icon
              name="search"
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
            />
            <input
              type="search"
              placeholder="Search"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              className="h-7 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-toolbar-control)] py-1 pl-8 pr-2 text-[12px] text-[var(--color-text)] shadow-inner placeholder:text-[var(--color-text-muted)]"
            />
          </label>
        </div>
      ) : null}
    </header>
  );
}
