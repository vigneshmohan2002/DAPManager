import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import {
  albumCoverUrl,
  backendUrl,
  fetchHome,
  type Album,
  type Artist,
  type HomePayload,
} from "../lib/api";

type Props = {
  ready: boolean;
  // Each nav handler routes the click into the same screen the
  // sidebar would have opened. The Home screen never opens screens
  // on its own — it always defers to the App-level router.
  onOpenAlbum: (album: Album) => void;
  onOpenArtist: (artist: Artist) => void;
  onOpenPlaylist: (playlistId: string) => void;
  onOpenStats: () => void;
};

// Relative-time formatter matching the Listening screen's
// "Xm ago / Xh ago / Xd ago" shape so users see consistent copy
// across the two surfaces.
function relTime(iso: string): string {
  try {
    const t = Date.parse(iso);
    if (!isFinite(t)) return "";
    const diff = Math.max(0, Date.now() - t);
    const m = Math.floor(diff / 60_000);
    if (m < 1) return "just now";
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  } catch {
    return "";
  }
}

export default function HomeScreen({
  ready,
  onOpenAlbum,
  onOpenArtist,
  onOpenPlaylist,
  onOpenStats,
}: Props) {
  const [data, setData] = useState<HomePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [base, setBase] = useState("");

  useEffect(() => {
    backendUrl().then(setBase);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const payload = await fetchHome();
        if (!cancelled) setData(payload);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Home" />
      <div className="flex-1 overflow-y-auto px-6 pb-8">
        {!ready || loading ? (
          <p className="py-6 text-sm text-[var(--color-text-muted)]">Loading…</p>
        ) : error ? (
          <p className="py-6 text-sm text-[var(--color-accent)]">{error}</p>
        ) : !data ? null : (
          <div className="flex flex-col gap-10 pt-4">
            {data.daily_mixes.length > 0 && (
              <Section title="Daily Mixes">
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                  {data.daily_mixes.map((m) => (
                    <button
                      key={m.playlist_id}
                      onClick={() => onOpenPlaylist(m.playlist_id)}
                      className="text-left group"
                    >
                      <div className="aspect-square rounded-md bg-gradient-to-br from-[var(--color-accent)]/40 via-[var(--color-surface)] to-[var(--color-bg-elevated)] flex items-center justify-center px-3">
                        <span className="text-xs font-semibold uppercase tracking-wider text-center text-[var(--color-text)] line-clamp-3">
                          {m.tag || "Mix"}
                        </span>
                      </div>
                      <div className="mt-1.5 text-xs font-medium truncate">
                        {m.name}
                      </div>
                      <div className="text-[11px] text-[var(--color-text-muted)]">
                        {m.track_count}{" "}
                        {m.track_count === 1 ? "track" : "tracks"}
                      </div>
                    </button>
                  ))}
                </div>
              </Section>
            )}

            {data.jump_back_in.length > 0 && (
              <Section title="Jump back in">
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                  {data.jump_back_in.map((a) => (
                    <button
                      key={a.album_id}
                      onClick={() =>
                        onOpenAlbum({
                          id: a.album_id,
                          title: a.title,
                          artist: a.artist,
                          track_count: 0,
                        })
                      }
                      className="text-left group"
                    >
                      <div className="aspect-square rounded-md overflow-hidden bg-[var(--color-surface)]">
                        {base && (
                          <img
                            src={albumCoverUrl(base, a.album_id)}
                            alt=""
                            className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                            onError={(e) => {
                              (e.currentTarget as HTMLImageElement).style.display =
                                "none";
                            }}
                          />
                        )}
                      </div>
                      <div className="mt-1.5 text-xs font-medium truncate">
                        {a.title}
                      </div>
                      <div className="text-[11px] text-[var(--color-text-muted)] truncate">
                        {a.artist}
                      </div>
                    </button>
                  ))}
                </div>
              </Section>
            )}

            {data.top_artists.length > 0 && (
              <Section
                title="Top artists this month"
                action={{ label: "See all", onClick: onOpenStats }}
              >
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
                  {data.top_artists.map((a) => (
                    <button
                      key={a.artist}
                      onClick={() =>
                        onOpenArtist({
                          name: a.artist,
                          album_count: 0,
                          track_count: a.distinct_tracks,
                        })
                      }
                      className="flex flex-col items-center text-center gap-1.5 group"
                    >
                      <div className="w-20 h-20 rounded-full bg-[var(--color-surface)] flex items-center justify-center text-2xl font-bold text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">
                        {(a.artist[0] ?? "?").toUpperCase()}
                      </div>
                      <div className="text-xs font-medium truncate w-full">
                        {a.artist}
                      </div>
                      <div className="text-[11px] text-[var(--color-text-muted)]">
                        {a.plays} {a.plays === 1 ? "play" : "plays"}
                      </div>
                    </button>
                  ))}
                </div>
              </Section>
            )}

            <Section
              title="Liked Songs"
              action={
                data.liked.total > 0
                  ? {
                      label: `See all (${data.liked.total})`,
                      onClick: () => onOpenPlaylist("liked_songs"),
                    }
                  : undefined
              }
            >
              {data.liked.total === 0 ? (
                <p className="text-sm text-[var(--color-text-muted)]">
                  Tap the ♡ on any track to add it here.
                </p>
              ) : (
                <div className="flex flex-col rounded-md bg-[var(--color-surface)]/40 divide-y divide-[var(--color-border)]/40">
                  {data.liked.preview.map((t) => (
                    <button
                      key={t.mbid}
                      onClick={() =>
                        t.album_id &&
                        onOpenAlbum({
                          id: t.album_id,
                          title: t.album ?? "",
                          artist: t.artist,
                          track_count: 0,
                        })
                      }
                      className="flex items-center gap-3 px-3 py-2 text-left hover:bg-[var(--color-surface)]/80"
                    >
                      <span className="text-rose-400">♥</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm truncate">{t.title}</div>
                        <div className="text-xs text-[var(--color-text-muted)] truncate">
                          {t.artist}
                          {t.album ? ` — ${t.album}` : ""}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Section>

            {data.recent.length > 0 && (
              <Section
                title="Recently played"
                action={{ label: "See all", onClick: onOpenStats }}
              >
                <div className="flex flex-col rounded-md bg-[var(--color-surface)]/40 divide-y divide-[var(--color-border)]/40">
                  {data.recent.map((p) => (
                    <button
                      key={p.id}
                      onClick={() =>
                        p.album_id &&
                        onOpenAlbum({
                          id: p.album_id,
                          title: p.album ?? "",
                          artist: p.artist ?? "",
                          track_count: 0,
                        })
                      }
                      className="flex items-center gap-3 px-3 py-2 text-left hover:bg-[var(--color-surface)]/80"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="text-sm truncate">
                          {p.title ?? "(unknown track)"}
                        </div>
                        <div className="text-xs text-[var(--color-text-muted)] truncate">
                          {p.artist ?? ""}
                          {p.album ? ` — ${p.album}` : ""}
                        </div>
                      </div>
                      <div className="text-xs text-[var(--color-text-muted)] shrink-0">
                        {relTime(p.played_at)}
                      </div>
                    </button>
                  ))}
                </div>
              </Section>
            )}

            {data.recent.length === 0 &&
              data.top_artists.length === 0 &&
              data.liked.total === 0 && (
                <div className="text-sm text-[var(--color-text-muted)] py-12 text-center">
                  Nothing here yet. Play a few tracks and your listening history
                  will appear.
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: { label: string; onClick: () => void };
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-lg font-semibold">{title}</h2>
        {action && (
          <button
            onClick={action.onClick}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            {action.label}
          </button>
        )}
      </div>
      {children}
    </section>
  );
}
