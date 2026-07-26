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
    <div className="titlebar-nodrag shrink-0 border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)]/65 px-5 py-1.5">
      <div className="mx-auto flex w-full max-w-[1180px] items-center gap-1.5">
        <span className="mr-1 text-[9px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          Include
        </span>
        <FilterToggle
          checked={catalogOnly}
          label="Catalog-only"
          onChange={onCatalogOnlyChange}
        />
        <FilterToggle
          checked={showOrphans}
          label="Orphans"
          onChange={onShowOrphansChange}
        />
      </div>
    </div>
  );
}

function FilterToggle({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label
      className={`relative flex h-6 cursor-pointer select-none items-center gap-1.5 rounded-md px-2 text-[10px] font-medium ${
        checked
          ? "doppler-selection text-[var(--color-accent)]"
          : "doppler-control"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${
          checked
            ? "bg-[var(--color-accent)]"
            : "bg-[var(--color-text-muted)]/45"
        }`}
      />
      {label}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 rounded-md peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-1 peer-focus-visible:outline-[var(--color-accent)]"
      />
    </label>
  );
}
