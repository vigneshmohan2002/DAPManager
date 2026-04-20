import { useEffect, useState } from "react";
import PlayerBar from "./components/PlayerBar";
import Sidebar from "./components/Sidebar";
import AlbumsScreen from "./screens/AlbumsScreen";
import AlbumDetailScreen from "./screens/AlbumDetailScreen";
import { waitForBackend, type Album } from "./lib/api";
import { PlayerProvider } from "./player/PlayerContext";

type BackendStatus = "booting" | "ready" | "failed";

function App() {
  const [status, setStatus] = useState<BackendStatus>("booting");
  const [screen, setScreen] = useState<string>("albums");
  const [openAlbum, setOpenAlbum] = useState<Album | null>(null);

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
  };

  return (
    <PlayerProvider>
      <div className="h-screen w-screen flex flex-col">
        <div className="flex-1 flex min-h-0">
          <Sidebar activeId={screen} onSelect={handleSidebarSelect} />
          <main className="flex-1 flex flex-col min-w-0">
            {status === "failed" ? (
              <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
                Backend failed to start — check the terminal.
              </div>
            ) : screen === "albums" && openAlbum ? (
              <AlbumDetailScreen
                album={openAlbum}
                onBack={() => setOpenAlbum(null)}
              />
            ) : screen === "albums" ? (
              <AlbumsScreen ready={status === "ready"} onOpen={setOpenAlbum} />
            ) : (
              <Placeholder name={screen} />
            )}
          </main>
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
