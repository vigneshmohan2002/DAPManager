import { useEffect, useState } from "react";
import PlayerBar from "./components/PlayerBar";
import Sidebar from "./components/Sidebar";
import AlbumsScreen from "./screens/AlbumsScreen";
import AlbumDetailScreen from "./screens/AlbumDetailScreen";
import ArtistsScreen from "./screens/ArtistsScreen";
import ArtistDetailScreen from "./screens/ArtistDetailScreen";
import SongsScreen from "./screens/SongsScreen";
import { waitForBackend, type Album, type Artist } from "./lib/api";
import { PlayerProvider } from "./player/PlayerContext";

type BackendStatus = "booting" | "ready" | "failed";

function App() {
  const [status, setStatus] = useState<BackendStatus>("booting");
  const [screen, setScreen] = useState<string>("albums");
  const [openAlbum, setOpenAlbum] = useState<Album | null>(null);
  const [openArtist, setOpenArtist] = useState<Artist | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await waitForBackend();
      if (!cancelled) setStatus(ok ? "ready" : "failed");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSidebarSelect = (id: string) => {
    setScreen(id);
    setOpenAlbum(null);
    setOpenArtist(null);
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
    if (screen === "albums") {
      return <AlbumsScreen ready={status === "ready"} onOpen={setOpenAlbum} />;
    }
    if (screen === "songs") {
      return <SongsScreen ready={status === "ready"} />;
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
    return <Placeholder name={screen} />;
  };

  return (
    <PlayerProvider>
      <div className="h-screen w-screen flex flex-col">
        <div className="flex-1 flex min-h-0">
          <Sidebar activeId={screen} onSelect={handleSidebarSelect} />
          <main className="flex-1 flex flex-col min-w-0">{renderScreen()}</main>
        </div>
        <PlayerBar />
      </div>
    </PlayerProvider>
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
