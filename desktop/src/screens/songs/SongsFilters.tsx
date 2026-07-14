type Props = {
  catalogOnly: boolean;
  showOrphans: boolean;
  onCatalogOnlyChange: (value: boolean) => void;
  onShowOrphansChange: (value: boolean) => void;
};

export default function SongsFilters({
  catalogOnly,
  showOrphans,
  onCatalogOnlyChange,
  onShowOrphansChange,
}: Props) {
  return (
    <div className="titlebar-nodrag flex items-center gap-4 px-6 py-2 border-b border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
      <label className="flex items-center gap-2 cursor-pointer select-none hover:text-[var(--color-text)]">
        <input
          type="checkbox"
          checked={catalogOnly}
          onChange={(event) => onCatalogOnlyChange(event.target.checked)}
        />
        Show catalog-only
      </label>
      <label className="flex items-center gap-2 cursor-pointer select-none hover:text-[var(--color-text)]">
        <input
          type="checkbox"
          checked={showOrphans}
          onChange={(event) => onShowOrphansChange(event.target.checked)}
        />
        Show orphans
      </label>
    </div>
  );
}
