import AlbumDetailScreen from "../screens/AlbumDetailScreen";
import AlbumsScreen from "../screens/AlbumsScreen";
import ArtistDetailScreen from "../screens/ArtistDetailScreen";
import ArtistsScreen from "../screens/ArtistsScreen";
import AuditScreen from "../screens/AuditScreen";
import ContributionsScreen from "../screens/ContributionsScreen";
import DownloadsScreen from "../screens/DownloadsScreen";
import DuplicatesScreen from "../screens/DuplicatesScreen";
import FleetScreen from "../screens/FleetScreen";
import HomeScreen from "../screens/HomeScreen";
import OrphansScreen from "../screens/OrphansScreen";
import ReleasesScreen from "../screens/ReleasesScreen";
import SettingsScreen from "../screens/SettingsScreen";
import SongsScreen from "../screens/SongsScreen";
import StatsScreen from "../screens/StatsScreen";
import SuggestScreen from "../screens/SuggestScreen";
import SyncScreen from "../screens/SyncScreen";
import WrappedScreen from "../screens/WrappedScreen";
import type {
  BackendStatus,
  ContentScreenId,
  NavigationAction,
  NavigationState,
} from "./model";

type Props = {
  route: NavigationState;
  status: BackendStatus;
  backendError: string | null;
  playlistsVersion: number;
  onNavigate: (action: NavigationAction) => void;
  onPlaylistsChanged: () => void;
};

export default function ScreenRenderer({
  route,
  status,
  backendError,
  playlistsVersion,
  onNavigate,
  onPlaylistsChanged,
}: Props) {
  if (status === "failed") {
    return <BackendFailure error={backendError} />;
  }

  const ready = status === "ready";
  switch (route.kind) {
    case "album":
      return (
        <AlbumDetailScreen
          album={route.album}
          onBack={() => onNavigate({ type: "closeAlbum" })}
          onPlaylistsChanged={onPlaylistsChanged}
        />
      );
    case "artist":
      return (
        <ArtistDetailScreen
          artist={route.artist}
          onBack={() => onNavigate({ type: "closeArtist" })}
          onOpenAlbum={(album) =>
            onNavigate({ type: "openAlbum", album })
          }
        />
      );
    case "playlist":
      return (
        <SongsScreen
          ready={ready}
          playlistId={route.playlistId}
          playlistsVersion={playlistsVersion}
          onPlaylistsChanged={onPlaylistsChanged}
          onOpenSettings={(focusKey) =>
            onNavigate({ type: "openSettings", focusKey })
          }
        />
      );
    case "settings":
      return (
        <SettingsScreen
          ready={ready}
          focusKey={route.focusKey}
          onConsumedFocusKey={() =>
            onNavigate({ type: "consumeSettingsFocus" })
          }
        />
      );
    case "unknown":
      return <UnknownScreen name={route.screen} />;
    case "screen":
      return renderContentScreen({
        screen: route.screen,
        ready,
        playlistsVersion,
        onNavigate,
        onPlaylistsChanged,
      });
  }
}

type ContentScreenProps = {
  screen: ContentScreenId;
  ready: boolean;
  playlistsVersion: number;
  onNavigate: (action: NavigationAction) => void;
  onPlaylistsChanged: () => void;
};

function renderContentScreen({
  screen,
  ready,
  playlistsVersion,
  onNavigate,
  onPlaylistsChanged,
}: ContentScreenProps) {
  const openSettings = (focusKey?: string) =>
    onNavigate({ type: "openSettings", focusKey });

  switch (screen) {
    case "home":
      return (
        <HomeScreen
          ready={ready}
          onOpenAlbum={(album) =>
            onNavigate({ type: "openAlbum", album })
          }
          onOpenArtist={(artist) =>
            onNavigate({ type: "openArtist", artist })
          }
          onOpenPlaylist={(playlistId) =>
            onNavigate({ type: "openPlaylist", playlistId })
          }
          onOpenStats={() =>
            onNavigate({ type: "openScreen", screen: "stats" })
          }
        />
      );
    case "albums":
      return (
        <AlbumsScreen
          ready={ready}
          onOpen={(album) => onNavigate({ type: "openAlbum", album })}
        />
      );
    case "songs":
      return (
        <SongsScreen
          ready={ready}
          playlistId={null}
          playlistsVersion={playlistsVersion}
          onPlaylistsChanged={onPlaylistsChanged}
          onOpenSettings={openSettings}
        />
      );
    case "artists":
      return (
        <ArtistsScreen
          ready={ready}
          onOpen={(artist) => onNavigate({ type: "openArtist", artist })}
        />
      );
    case "audit":
      return <AuditScreen ready={ready} />;
    case "downloads":
      return <DownloadsScreen ready={ready} />;
    case "duplicates":
      return <DuplicatesScreen ready={ready} />;
    case "sync":
      return <SyncScreen ready={ready} />;
    case "contributions":
      return (
        <ContributionsScreen ready={ready} onOpenSettings={openSettings} />
      );
    case "suggest":
      return <SuggestScreen ready={ready} onOpenSettings={openSettings} />;
    case "fleet":
      return <FleetScreen ready={ready} />;
    case "stats":
      return (
        <StatsScreen
          ready={ready}
          onOpenWrapped={() =>
            onNavigate({ type: "openScreen", screen: "wrapped" })
          }
        />
      );
    case "wrapped":
      return (
        <WrappedScreen
          ready={ready}
          onBack={() =>
            onNavigate({ type: "openScreen", screen: "stats" })
          }
        />
      );
    case "orphans":
      return (
        <OrphansScreen
          ready={ready}
          onPlaylistsChanged={onPlaylistsChanged}
        />
      );
    case "releases":
      return <ReleasesScreen ready={ready} onOpenSettings={openSettings} />;
  }
}

function BackendFailure({ error }: { error: string | null }) {
  return (
    <div className="flex-1 flex items-center justify-center px-8">
      <div className="max-w-2xl rounded-lg border border-red-900/70 bg-red-950/20 px-5 py-4">
        <h1 className="text-base font-semibold text-red-300">
          Backend failed to start
        </h1>
        <p className="mt-2 whitespace-pre-line text-sm text-[var(--color-text-muted)]">
          {error ??
            "DAPManager could not start its local Python backend. Relaunch the app after checking your Python installation."}
        </p>
      </div>
    </div>
  );
}

function UnknownScreen({ name }: { name: string }) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <header className="titlebar-drag h-14 shrink-0 border-b border-[var(--color-border)] flex items-center px-6">
        <h1 className="text-lg font-semibold capitalize">{name}</h1>
      </header>
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)] text-sm">
        Unknown screen
      </div>
    </div>
  );
}
