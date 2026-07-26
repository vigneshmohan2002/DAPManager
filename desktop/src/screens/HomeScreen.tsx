import { useEffect, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import Artwork from "../components/Artwork";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  albumCoverUrl,
  backendUrl,
  fetchHome,
  type Album,
  type Artist,
  type HomePayload,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

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
  const { playAlbum } = usePlayer();
  const toast = useToast();

  const handlePlayAlbum = async (album: Album) => {
    try {
      const count = await playAlbum(album.id);
      if (count === 0) toast.show("No playable tracks in this album.", "err");
    } catch (e) {
      toast.show(`Could not play album: ${e}`, "err");
    }
  };

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
      <div className="flex-1 overflow-y-auto px-5 pb-10">
        {!ready || loading ? (
          <p className="py-6 text-sm text-[var(--color-text-muted)]">Loading…</p>
        ) : error ? (
          <p className="py-6 text-sm text-[var(--color-danger)]">{error}</p>
        ) : !data ? null : (
          <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-9 pt-5">
            {data.daily_mixes.length > 0 && (
              <Section title="Daily Mixes">
                <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-x-4 gap-y-5">
                  {data.daily_mixes.map((m) => (
                    <button
                      type="button"
                      key={m.playlist_id}
                      onClick={() => onOpenPlaylist(m.playlist_id)}
                      aria-label={`Open playlist ${m.name}`}
                      className="group min-w-0 text-left focus-visible:rounded-md"
                    >
                      <div className="relative flex aspect-square items-end overflow-hidden rounded-[5px] bg-[linear-gradient(145deg,color-mix(in_srgb,var(--color-accent)_78%,#25293a),color-mix(in_srgb,var(--color-accent)_24%,#b08a9d))] p-3 shadow-[var(--shadow-artwork)] transition-transform duration-150 group-hover:-translate-y-px">
                        <Icon
                          name="playlist"
                          size={58}
                          className="absolute -right-2 -top-1 rotate-6 text-white/20"
                        />
                        <div className="relative min-w-0 text-white">
                          <span className="block text-[9px] font-semibold uppercase tracking-[0.14em] text-white/70">
                            Daily Mix
                          </span>
                          <span className="mt-1 block line-clamp-3 text-[15px] font-semibold leading-tight">
                            {m.tag || "Mix"}
                          </span>
                        </div>
                      </div>
                      <div className="mt-1.5 truncate text-[11px] font-medium">
                        {m.name}
                      </div>
                      <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
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
                <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-x-4 gap-y-5">
                  {data.jump_back_in.map((a) => {
                    const album: Album = {
                      id: a.album_id,
                      title: a.title,
                      artist: a.artist,
                      track_count: 0,
                    };
                    return (
                      <AlbumCard
                        key={a.album_id}
                        album={album}
                        coverUrl={base ? albumCoverUrl(base, a.album_id) : ""}
                        onClick={() => onOpenAlbum(album)}
                        onDoubleClick={() => void handlePlayAlbum(album)}
                      />
                    );
                  })}
                </div>
              </Section>
            )}

            {data.top_artists.length > 0 && (
              <Section
                title="Top artists this month"
                action={{ label: "See all", onClick: onOpenStats }}
              >
                <div className="grid grid-cols-1 gap-x-5 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] sm:grid-cols-2">
                  {data.top_artists.map((a) => (
                    <button
                      type="button"
                      key={a.artist}
                      onClick={() =>
                        onOpenArtist({
                          name: a.artist,
                          album_count: 0,
                          track_count: a.distinct_tracks,
                        })
                      }
                      aria-label={`Open artist ${a.artist}`}
                      className="group flex min-w-0 items-center gap-3 border-b border-[var(--color-border)] px-3 py-2.5 text-left last:border-b-0 hover:bg-[var(--color-surface)]/55 sm:[&:nth-last-child(-n+2)]:border-b-0"
                    >
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--color-surface)] text-[12px] font-semibold text-[var(--color-text-muted)] shadow-sm transition-colors group-hover:text-[var(--color-text)]">
                        {(a.artist[0] ?? "?").toUpperCase()}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12px] font-medium">
                          {a.artist}
                        </span>
                        <span className="mt-0.5 block truncate text-[10px] text-[var(--color-text-muted)]">
                          {a.distinct_tracks}{" "}
                          {a.distinct_tracks === 1 ? "track" : "tracks"}
                        </span>
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-text-muted)]">
                        {a.plays} {a.plays === 1 ? "play" : "plays"}
                      </span>
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
                <EmptyCollection
                  icon="heart"
                  text="Use the heart on any track to add it here."
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
                  {data.liked.preview.map((t) => (
                    <CompactTrackRow
                      key={t.mbid}
                      title={t.title}
                      subtitle={`${t.artist}${t.album ? ` — ${t.album}` : ""}`}
                      artworkUrl={
                        base && t.album_id
                          ? albumCoverUrl(base, t.album_id)
                          : undefined
                      }
                      leading={<Icon name="heart" size={14} />}
                      disabled={!t.album_id}
                      onClick={() => {
                        t.album_id &&
                        onOpenAlbum({
                          id: t.album_id,
                          title: t.album ?? "",
                          artist: t.artist,
                          track_count: 0,
                        });
                      }}
                    />
                  ))}
                </div>
              )}
            </Section>

            {data.recent.length > 0 && (
              <Section
                title="Recently played"
                action={{ label: "See all", onClick: onOpenStats }}
              >
                <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
                  {data.recent.map((p) => (
                    <CompactTrackRow
                      key={p.id}
                      title={p.title ?? "(unknown track)"}
                      subtitle={`${p.artist ?? ""}${p.album ? ` — ${p.album}` : ""}`}
                      artworkUrl={
                        base && p.album_id
                          ? albumCoverUrl(base, p.album_id)
                          : undefined
                      }
                      trailing={relTime(p.played_at)}
                      disabled={!p.album_id}
                      onClick={() => {
                        p.album_id &&
                        onOpenAlbum({
                          id: p.album_id,
                          title: p.album ?? "",
                          artist: p.artist ?? "",
                          track_count: 0,
                        });
                      }}
                    />
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
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-[13px] font-semibold tracking-[-0.01em]">{title}</h2>
        {action && (
          <button
            type="button"
            onClick={action.onClick}
            className="rounded px-1 py-0.5 text-[10px] font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
          >
            {action.label}
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

function CompactTrackRow({
  title,
  subtitle,
  artworkUrl,
  leading,
  trailing,
  disabled,
  onClick,
}: {
  title: string;
  subtitle: string;
  artworkUrl?: string;
  leading?: React.ReactNode;
  trailing?: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="group flex w-full items-center gap-2.5 border-b border-[var(--color-border)] px-2.5 py-1.5 text-left last:border-b-0 hover:bg-[var(--color-surface)]/55 disabled:cursor-default disabled:opacity-70"
    >
      <Artwork
        src={artworkUrl}
        alt=""
        fallbackLabel=""
        className="h-9 w-9 shrink-0 rounded-[3px] shadow-sm"
      />
      {leading ? (
        <span className="shrink-0 text-[var(--color-liked)]">{leading}</span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[11px] font-medium">{title}</span>
        <span className="mt-0.5 block truncate text-[10px] text-[var(--color-text-muted)]">
          {subtitle}
        </span>
      </span>
      {trailing ? (
        <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-text-muted)]">
          {trailing}
        </span>
      ) : null}
    </button>
  );
}

function EmptyCollection({
  icon,
  text,
}: {
  icon: "heart";
  text: string;
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-4 text-[11px] text-[var(--color-text-muted)]">
      <Icon name={icon} size={16} />
      {text}
    </div>
  );
}
