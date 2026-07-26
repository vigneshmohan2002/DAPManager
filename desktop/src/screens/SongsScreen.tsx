import { useMemo, useState } from "react";
import ContextMenu from "../components/ContextMenu";
import IdentifyTagDialog from "../components/IdentifyTagDialog";
import TopBar from "../components/TopBar";
import type { LibraryTrack } from "../lib/api";
import {
  filterAndSortTracks,
  type SongSortDirection,
  type SongSortKey,
} from "../lib/songList";
import SongsFilters from "./songs/SongsFilters";
import SongsTable from "./songs/SongsTable";
import { buildSongContextMenu } from "./songs/menu";
import { useSongsActions } from "./songs/useSongsActions";
import { useSongsLibrary } from "./songs/useSongsLibrary";

type Props = {
  ready: boolean;
  // When set, Songs is scoped to this playlist's membership and the
  // TopBar title reflects the playlist name. null means "all tracks".
  playlistId?: string | null;
  // Bumped by App when any playlist mutation happens; the Add-to-
  // Playlist submenu needs to re-fetch so new playlists show up.
  playlistsVersion: number;
  // SongsScreen itself mutates playlists (add track to playlist,
  // soft-delete a track that affects playlist counts). Call this to
  // tell the rest of the app to refresh.
  onPlaylistsChanged: () => void;
  // Route to Settings scoped to a missing key. Used by Identify &
  // Tag when acoustid_api_key isn't configured.
  onOpenSettings: (focusKey?: string) => void;
};

type SongMenuState = {
  x: number;
  y: number;
  track: LibraryTrack;
};

export default function SongsScreen({
  ready,
  playlistId,
  playlistsVersion,
  onPlaylistsChanged,
  onOpenSettings,
}: Props) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SongSortKey>("artist");
  const [direction, setDirection] = useState<SongSortDirection>("asc");
  const [catalogOnly, setCatalogOnly] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);
  const [menu, setMenu] = useState<SongMenuState | null>(null);

  const library = useSongsLibrary({
    ready,
    playlistId,
    playlistsVersion,
    catalogOnly,
    showOrphans,
  });
  const visible = useMemo(
    () => filterAndSortTracks(library.rows, search, sort, direction),
    [library.rows, search, sort, direction],
  );
  const actions = useSongsActions({
    visibleTracks: visible,
    setRows: library.setRows,
    allPlaylists: library.allPlaylists,
    playlistId,
    reloadTable: library.reloadTable,
    onPlaylistsChanged,
    onOpenSettings,
  });

  const clickHeader = (key: SongSortKey) => {
    if (key === sort) {
      setDirection(direction === "asc" ? "desc" : "asc");
      return;
    }
    setSort(key);
    setDirection("asc");
  };

  const menuEntries = menu
    ? buildSongContextMenu({
        track: menu.track,
        playlists: library.allPlaylists,
        canContributeToMaster: library.canContributeToMaster,
        contributingMbid: actions.contributingMbid,
        suggestingMbid: actions.suggestingMbid,
        identifying: actions.identifying,
        actions: actions.menuActions,
      })
    : null;
  const songCount = `${visible.length} ${
    visible.length === 1 ? "song" : "songs"
  }`;
  const countSummary =
    visible.length === library.rows.length
      ? songCount
      : `${visible.length} of ${library.rows.length} songs`;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title={playlistId ? library.playlistName ?? "Playlist" : "Songs"}
        subtitle={playlistId ? `${countSummary} · playlist` : countSummary}
        search={search}
        onSearch={setSearch}
      />
      <SongsFilters
        catalogOnly={catalogOnly}
        showOrphans={showOrphans}
        onCatalogOnlyChange={setCatalogOnly}
        onShowOrphansChange={setShowOrphans}
      />
      <div className="flex-1 overflow-y-auto px-5 pb-6">
        <div className="mx-auto w-full max-w-[1180px]">
          <SongsTable
            ready={ready}
            loading={library.loading}
            error={library.error}
            tracks={visible}
            sort={sort}
            direction={direction}
            currentMbid={actions.currentMbid}
            isPlaying={actions.isPlaying}
            onSort={clickHeader}
            onPlayFrom={actions.playFrom}
            onTogglePlayback={actions.toggle}
            onLikeToggle={actions.menuActions.onLikeToggle}
            onContextMenu={(event, track) => {
              event.preventDefault();
              setMenu({ x: event.clientX, y: event.clientY, track });
            }}
          />
        </div>
      </div>
      {menuEntries && menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          entries={menuEntries}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {actions.identify ? (
        <IdentifyTagDialog
          candidate={actions.identify.candidate}
          localPath={actions.identify.localPath}
          applying={actions.applyingTag}
          onApply={actions.handleApplyTags}
          onCancel={actions.closeIdentifyDialog}
        />
      ) : null}
    </div>
  );
}
