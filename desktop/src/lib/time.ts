// Backend timestamps come from SQLite CURRENT_TIMESTAMP: UTC, space-
// separated "YYYY-MM-DD HH:MM:SS". Normalize to ISO before parsing
// so Date knows it's UTC, not local.
export function relativeTime(stamp: string | null): string {
  if (!stamp) return "never";
  const iso = stamp.includes("T") ? stamp : stamp.replace(" ", "T") + "Z";
  const then = Date.parse(iso);
  if (isNaN(then)) return stamp;
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}
