import {
  useCallback,
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  fetchAllTracks,
  fetchConfig,
  fetchPlaylists,
  type LibraryTrack,
  type Playlist,
} from "../../lib/api";

type UseSongsLibraryOptions = {
  ready: boolean;
  playlistId?: string | null;
  playlistsVersion: number;
  catalogOnly: boolean;
  showOrphans: boolean;
};

export type SongsLibraryState = {
  rows: LibraryTrack[];
  setRows: Dispatch<SetStateAction<LibraryTrack[]>>;
  loading: boolean;
  error: string | null;
  playlistName: string | null;
  allPlaylists: Playlist[];
  canContributeToMaster: boolean;
  reloadTable: () => void;
};

export function useSongsLibrary({
  ready,
  playlistId,
  playlistsVersion,
  catalogOnly,
  showOrphans,
}: UseSongsLibraryOptions): SongsLibraryState {
  const [rows, setRows] = useState<LibraryTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playlistName, setPlaylistName] = useState<string | null>(null);
  const [allPlaylists, setAllPlaylists] = useState<Playlist[]>([]);
  const [canContributeToMaster, setCanContributeToMaster] = useState(false);
  const [tableVersion, setTableVersion] = useState(0);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    fetchConfig()
      .then((payload) => {
        if (cancelled) return;
        const role = String(payload.config.device_role ?? "satellite");
        const masterUrl = String(payload.config.master_url ?? "").trim();
        setCanContributeToMaster(
          role !== "master" && role !== "standalone" && Boolean(masterUrl),
        );
      })
      .catch(() => {
        if (!cancelled) setCanContributeToMaster(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ready]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    const loadTracks = async () => {
      try {
        const data = await fetchAllTracks({
          playlistId: playlistId ?? undefined,
          localOnly: !catalogOnly,
          includeOrphans: showOrphans,
        });
        if (!cancelled) setRows(data);
      } catch (error) {
        if (!cancelled) setError(String(error));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadTracks();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistId, catalogOnly, showOrphans, tableVersion]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    const loadPlaylists = async () => {
      try {
        const playlists = await fetchPlaylists();
        if (!cancelled) setAllPlaylists(playlists);
      } catch {
        if (!cancelled) setAllPlaylists([]);
      }
    };

    void loadPlaylists();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistsVersion]);

  useEffect(() => {
    if (!playlistId) {
      setPlaylistName(null);
      return;
    }
    const match = allPlaylists.find(
      (playlist) => playlist.playlist_id === playlistId,
    );
    setPlaylistName(match?.name ?? null);
  }, [playlistId, allPlaylists]);

  const reloadTable = useCallback(() => {
    setTableVersion((version) => version + 1);
  }, []);

  return {
    rows,
    setRows,
    loading,
    error,
    playlistName,
    allPlaylists,
    canContributeToMaster,
    reloadTable,
  };
}
