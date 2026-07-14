import { useCallback, useEffect, useReducer, useState } from "react";
import LyricsPane from "./components/LyricsPane";
import MiniPlayer from "./components/MiniPlayer";
import PlayerBar from "./components/PlayerBar";
import QueuePanel from "./components/QueuePanel";
import SearchOverlay from "./components/SearchOverlay";
import Sidebar from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { fetchSetupStatus, waitForBackend } from "./lib/api";
import {
  INITIAL_NAVIGATION_STATE,
  activeSidebarId,
  navigationReducer,
  selectAppSurface,
  type BackendStatus,
} from "./navigation/model";
import ScreenRenderer from "./navigation/ScreenRenderer";
import { PlayerProvider } from "./player/PlayerContext";
import SetupScreen from "./screens/SetupScreen";

function App() {
  const [status, setStatus] = useState<BackendStatus>("booting");
  const [backendError, setBackendError] = useState<string | null>(null);
  // After 10 s of booting show a hint so the user doesn't force-quit
  // during the first-launch venv + pip-install phase (can take minutes).
  const [bootingSlowly, setBootingSlowly] = useState(false);
  const [route, navigate] = useReducer(
    navigationReducer,
    INITIAL_NAVIGATION_STATE,
  );
  const [queueOpen, setQueueOpen] = useState(false);
  const [lyricsOpen, setLyricsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  // Mini-player mode is purely a layout switch — same window, same
  // PlayerProvider, same audio element. Triggered by the user
  // shrinking the window via `enterMiniPlayer` (or by hand).
  const [isMini, setIsMini] = useState(
    typeof window !== "undefined" &&
      window.innerWidth <= 220 &&
      window.innerHeight <= 220,
  );
  // Bumped by any playlist mutation (create / rename / delete / add-
  // to-playlist). Sidebar + SongsScreen depend on it so their fetches
  // re-fire without prop-drilling a `refresh()` callback everywhere.
  const [playlistsVersion, setPlaylistsVersion] = useState(0);
  const bumpPlaylists = useCallback(
    () => setPlaylistsVersion((version) => version + 1),
    [],
  );
  // null = not yet checked; true = wizard must be shown
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  // If the scoped playlist was just deleted, drop the scope back to
  // "all tracks" so the Songs screen doesn't keep filtering on a
  // soft-deleted id. The reducer also updates an album's return route.
  const handlePlaylistDeleted = useCallback(
    (playlistId: string) => {
      navigate({ type: "playlistDeleted", playlistId });
      bumpPlaylists();
    },
    [bumpPlaylists],
  );

  const handlePlaylistCreated = useCallback(
    (playlistId: string) => {
      navigate({ type: "openPlaylist", playlistId });
      bumpPlaylists();
    },
    [bumpPlaylists],
  );

  const handleSidebarSelect = useCallback((id: string) => {
    navigate({ type: "selectSidebar", id });
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k"
      ) {
        event.preventDefault();
        setSearchOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const onResize = () => {
      setIsMini(window.innerWidth <= 220 && window.innerHeight <= 220);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Show "Installing dependencies…" hint after 10 s of booting.
  useEffect(() => {
    if (status !== "booting") return;
    const timer = window.setTimeout(() => setBootingSlowly(true), 10_000);
    return () => window.clearTimeout(timer);
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const result = await waitForBackend();
      if (cancelled) return;
      if (!result.ok) {
        setBackendError(result.error);
        setStatus("failed");
        return;
      }
      // Check whether first-run setup is still needed before showing
      // the main UI — avoids all library API calls 302-redirecting to
      // the Flask /setup page on a fresh machine with no config.json.
      try {
        const { needs_setup } = await fetchSetupStatus();
        if (!cancelled) {
          setNeedsSetup(needs_setup);
          if (!needs_setup) setStatus("ready");
        }
      } catch (error) {
        if (!cancelled) {
          setBackendError(
            `The backend started, but DAPManager could not read first-run status: ${String(error)}\n\nRelaunch the app. If this persists, check the Python installation and config file.`,
          );
          setStatus("failed");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const surface = selectAppSurface({
    needsSetup,
    status,
    isMini,
    bootingSlowly,
  });

  // Show setup wizard on fresh installs (no config.json). Checked
  // before the booting guard so the wizard shows even though status
  // never transitions to "ready" on a first-run path.
  if (surface.kind === "setup") {
    return <SetupScreen onDone={() => window.location.reload()} />;
  }

  // Backend is still starting up (or setup check is in flight).
  // Show a minimal spinner rather than rendering the sidebar in an
  // unready state. "failed" falls through to the main layout so the
  // screen renderer can surface the inline error message.
  if (surface.kind === "booting") {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-[var(--color-bg)]">
        <div className="titlebar-drag absolute inset-x-0 top-0 h-10" />
        <div className="w-5 h-5 border-2 border-[var(--color-text-muted)]/30 border-t-[var(--color-text-muted)] rounded-full animate-spin" />
        {surface.showDependencyHint && (
          <p className="mt-3 text-xs text-[var(--color-text-muted)]">
            Installing dependencies on first launch…
          </p>
        )}
      </div>
    );
  }

  return (
    <ToastProvider>
      <PlayerProvider>
        {surface.kind === "miniPlayer" ? (
          <MiniPlayer />
        ) : (
          <div className="h-screen w-screen flex flex-col">
            <div className="flex-1 flex min-h-0">
              <Sidebar
                activeId={activeSidebarId(route)}
                onSelect={handleSidebarSelect}
                onOpenSearch={() => setSearchOpen(true)}
                ready={status === "ready"}
                playlistsVersion={playlistsVersion}
                onPlaylistsChanged={bumpPlaylists}
                onPlaylistCreated={handlePlaylistCreated}
                onPlaylistDeleted={handlePlaylistDeleted}
              />
              <main className="flex-1 flex flex-col min-w-0">
                <ScreenRenderer
                  route={route}
                  status={status}
                  backendError={backendError}
                  playlistsVersion={playlistsVersion}
                  onNavigate={navigate}
                  onPlaylistsChanged={bumpPlaylists}
                />
              </main>
              <LyricsPane
                open={lyricsOpen}
                onClose={() => setLyricsOpen(false)}
              />
              <QueuePanel
                open={queueOpen}
                onClose={() => setQueueOpen(false)}
              />
            </div>
            <PlayerBar
              queueOpen={queueOpen}
              onToggleQueue={() => setQueueOpen((open) => !open)}
              lyricsOpen={lyricsOpen}
              onToggleLyrics={() => setLyricsOpen((open) => !open)}
            />
            <SearchOverlay
              open={searchOpen}
              onClose={() => setSearchOpen(false)}
              onOpenAlbum={(album) =>
                navigate({ type: "openAlbum", album })
              }
              onOpenArtist={(artist) =>
                navigate({ type: "openArtist", artist })
              }
            />
          </div>
        )}
      </PlayerProvider>
    </ToastProvider>
  );
}

export default App;
