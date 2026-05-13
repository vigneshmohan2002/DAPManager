import { useCallback, useEffect, useState } from "react";
import MiniPlayer from "./components/MiniPlayer";
import PlayerBar from "./components/PlayerBar";
import QueuePanel from "./components/QueuePanel";
import SearchOverlay from "./components/SearchOverlay";
import Sidebar from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import AlbumsScreen from "./screens/AlbumsScreen";
import AlbumDetailScreen from "./screens/AlbumDetailScreen";
import ArtistsScreen from "./screens/ArtistsScreen";
import ArtistDetailScreen from "./screens/ArtistDetailScreen";
import AuditScreen from "./screens/AuditScreen";
import DownloadsScreen from "./screens/DownloadsScreen";
import DuplicatesScreen from "./screens/DuplicatesScreen";
import FleetScreen from "./screens/FleetScreen";
import HomeScreen from "./screens/HomeScreen";
import OrphansScreen from "./screens/OrphansScreen";
import ReleasesScreen from "./screens/ReleasesScreen";
import SetupScreen from "./screens/SetupScreen";
import SettingsScreen from "./screens/SettingsScreen";
import SongsScreen from "./screens/SongsScreen";
import StatsScreen from "./screens/StatsScreen";
import SuggestScreen from "./screens/SuggestScreen";
import SyncScreen from "./screens/SyncScreen";
import WrappedScreen from "./screens/WrappedScreen";
import { fetchSetupStatus, waitForBackend, type Album, type Artist } from "./lib/api";
import { PlayerProvider } from "./player/PlayerContext";

type BackendStatus = "booting" | "ready" | "failed";

function App() {
  const [status, setStatus] = useState<BackendStatus>("booting");
  const [screen, setScreen] = useState<string>("home");
  const [scopedPlaylistId, setScopedPlaylistId] = useState<string | null>(null);
  const [openAlbum, setOpenAlbum] = useState<Album | null>(null);
  const [openArtist, setOpenArtist] = useState<Artist | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  // Mini-player mode is purely a layout switch — same window, same
  // PlayerProvider, same audio element. Triggered by the user
  // shrinking the window via `enterMiniPlayer` (or by hand).
  const [isMini, setIsMini] = useState(
    typeof window !== "undefined" &&
      window.innerWidth <= 220 &&
      window.innerHeight <= 220,
  );
  // When another screen routes to Settings to demand a missing config
  // key (e.g. Identify & Tag needs acoustid_api_key in Stage 7b), it
  // sets this so the Settings screen can scroll + flash the row.
  const [settingsFocusKey, setSettingsFocusKey] = useState<string | null>(null);
  // Bumped by any playlist mutation (create / rename / delete / add-
  // to-playlist). Sidebar + SongsScreen depend on it so their fetches
  // re-fire without prop-drilling a `refresh()` callback everywhere.
  const [playlistsVersion, setPlaylistsVersion] = useState(0);
  const bumpPlaylists = useCallback(
    () => setPlaylistsVersion((v) => v + 1),
    [],
  );
  // null = not yet checked; true = wizard must be shown
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  // If the scoped playlist was just deleted, drop the scope back to
  // "all tracks" so the Songs screen doesn't keep filtering on a
  // soft-deleted id.
  const handlePlaylistDeleted = useCallback(
    (pid: string) => {
      if (scopedPlaylistId === pid) setScopedPlaylistId(null);
      bumpPlaylists();
    },
    [scopedPlaylistId, bumpPlaylists],
  );

  const handlePlaylistCreated = useCallback(
    (pid: string) => {
      setScopedPlaylistId(pid);
      setScreen("songs");
      setOpenAlbum(null);
      setOpenArtist(null);
      bumpPlaylists();
    },
    [bumpPlaylists],
  );

  const handleOpenSettings = useCallback((focusKey?: string) => {
    setScreen("settings");
    setScopedPlaylistId(null);
    setOpenAlbum(null);
    setOpenArtist(null);
    setSettingsFocusKey(focusKey ?? null);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await waitForBackend();
      if (cancelled) return;
      if (!ok) {
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
      } catch {
        if (!cancelled) {
          setNeedsSetup(false);
          setStatus("ready");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSidebarSelect = (id: string) => {
    // Playlist clicks route to the Songs screen with a scope; other ids
    // are plain screen names. Matches the sidebar's id convention.
    if (id.startsWith("playlist:")) {
      setScopedPlaylistId(id.slice("playlist:".length));
      setScreen("songs");
    } else {
      setScopedPlaylistId(null);
      setScreen(id);
    }
    setOpenAlbum(null);
    setOpenArtist(null);
  };

  const activeSidebarId = scopedPlaylistId
    ? `playlist:${scopedPlaylistId}`
    : screen;

  const openArtistFromSearch = (a: Artist) => {
    setScreen("artists");
    setOpenArtist(a);
    setOpenAlbum(null);
  };

  const openAlbumFromSearch = (a: Album) => {
    setOpenAlbum(a);
  };

  const renderScreen = () => {
    if (status === "failed") {
      return (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
          Backend failed to start — check the terminal.
        </div>
      );
    }
    if (openAlbum) {
      return (
        <AlbumDetailScreen album={openAlbum} onBack={() => setOpenAlbum(null)} />
      );
    }
    if (screen === "home") {
      return (
        <HomeScreen
          ready={status === "ready"}
          onOpenAlbum={setOpenAlbum}
          onOpenArtist={(a) => {
            setScreen("artists");
            setOpenArtist(a);
            setOpenAlbum(null);
          }}
          onOpenPlaylist={(pid) => {
            setScopedPlaylistId(pid);
            setScreen("songs");
            setOpenAlbum(null);
            setOpenArtist(null);
          }}
          onOpenStats={() => {
            setScreen("stats");
            setOpenAlbum(null);
            setOpenArtist(null);
          }}
        />
      );
    }
    if (screen === "albums") {
      return <AlbumsScreen ready={status === "ready"} onOpen={setOpenAlbum} />;
    }
    if (screen === "songs") {
      return (
        <SongsScreen
          ready={status === "ready"}
          playlistId={scopedPlaylistId}
          playlistsVersion={playlistsVersion}
          onPlaylistsChanged={bumpPlaylists}
          onOpenSettings={handleOpenSettings}
        />
      );
    }
    if (screen === "artists") {
      if (openArtist) {
        return (
          <ArtistDetailScreen
            artist={openArtist}
            onBack={() => setOpenArtist(null)}
            onOpenAlbum={setOpenAlbum}
          />
        );
      }
      return <ArtistsScreen ready={status === "ready"} onOpen={setOpenArtist} />;
    }
    if (screen === "audit") {
      return <AuditScreen ready={status === "ready"} />;
    }
    if (screen === "downloads") {
      return <DownloadsScreen ready={status === "ready"} />;
    }
    if (screen === "duplicates") {
      return <DuplicatesScreen ready={status === "ready"} />;
    }
    if (screen === "sync") {
      return <SyncScreen ready={status === "ready"} />;
    }
    if (screen === "suggest") {
      return (
        <SuggestScreen
          ready={status === "ready"}
          onOpenSettings={handleOpenSettings}
        />
      );
    }
    if (screen === "fleet") {
      return <FleetScreen ready={status === "ready"} />;
    }
    if (screen === "stats") {
      return (
        <StatsScreen
          ready={status === "ready"}
          onOpenWrapped={() => setScreen("wrapped")}
        />
      );
    }
    if (screen === "wrapped") {
      return (
        <WrappedScreen
          ready={status === "ready"}
          onBack={() => setScreen("stats")}
        />
      );
    }
    if (screen === "orphans") {
      return (
        <OrphansScreen
          ready={status === "ready"}
          onPlaylistsChanged={bumpPlaylists}
        />
      );
    }
    if (screen === "releases") {
      return (
        <ReleasesScreen
          ready={status === "ready"}
          onOpenSettings={handleOpenSettings}
        />
      );
    }
    if (screen === "settings") {
      return (
        <SettingsScreen
          ready={status === "ready"}
          focusKey={settingsFocusKey}
          onConsumedFocusKey={() => setSettingsFocusKey(null)}
        />
      );
    }
    return <Placeholder name={screen} />;
  };

  // Show setup wizard on fresh installs (no config.json). Checked
  // before the booting guard so the wizard shows even though status
  // never transitions to "ready" on a first-run path.
  if (needsSetup === true) {
    return <SetupScreen onDone={() => window.location.reload()} />;
  }

  // Backend is still starting up (or setup check is in flight).
  // Show a minimal spinner rather than rendering the sidebar in an
  // unready state. "failed" falls through to the main layout so
  // renderScreen() can surface the inline error message.
  if (status === "booting") {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-[var(--color-bg)]">
        <div className="titlebar-drag absolute inset-x-0 top-0 h-10" />
        <div className="w-5 h-5 border-2 border-[var(--color-text-muted)]/30 border-t-[var(--color-text-muted)] rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <ToastProvider>
      <PlayerProvider>
        {isMini ? (
          <MiniPlayer />
        ) : (
          <div className="h-screen w-screen flex flex-col">
            <div className="flex-1 flex min-h-0">
              <Sidebar
                activeId={activeSidebarId}
                onSelect={handleSidebarSelect}
                onOpenSearch={() => setSearchOpen(true)}
                ready={status === "ready"}
                playlistsVersion={playlistsVersion}
                onPlaylistsChanged={bumpPlaylists}
                onPlaylistCreated={handlePlaylistCreated}
                onPlaylistDeleted={handlePlaylistDeleted}
              />
              <main className="flex-1 flex flex-col min-w-0">
                {renderScreen()}
              </main>
              <QueuePanel
                open={queueOpen}
                onClose={() => setQueueOpen(false)}
              />
            </div>
            <PlayerBar
              queueOpen={queueOpen}
              onToggleQueue={() => setQueueOpen((q) => !q)}
            />
            <SearchOverlay
              open={searchOpen}
              onClose={() => setSearchOpen(false)}
              onOpenAlbum={openAlbumFromSearch}
              onOpenArtist={openArtistFromSearch}
            />
          </div>
        )}
      </PlayerProvider>
    </ToastProvider>
  );
}

function Placeholder({ name }: { name: string }) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <header className="titlebar-drag h-14 shrink-0 border-b border-[var(--color-border)] flex items-center px-6">
        <h1 className="text-lg font-semibold capitalize">{name}</h1>
      </header>
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)] text-sm">
        Coming soon
      </div>
    </div>
  );
}

export default App;
