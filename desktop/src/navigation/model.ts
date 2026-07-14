import type { Album, Artist } from "../lib/api";

export const SCREEN_IDS = [
  "home",
  "albums",
  "songs",
  "artists",
  "audit",
  "downloads",
  "duplicates",
  "sync",
  "contributions",
  "suggest",
  "fleet",
  "stats",
  "wrapped",
  "orphans",
  "releases",
  "settings",
] as const;

export type ScreenId = (typeof SCREEN_IDS)[number];
export type ContentScreenId = Exclude<ScreenId, "settings">;

export type BaseRoute =
  | { kind: "screen"; screen: ContentScreenId }
  | { kind: "playlist"; playlistId: string }
  | { kind: "artist"; artist: Artist }
  | { kind: "settings"; focusKey: string | null }
  | { kind: "unknown"; screen: string };

export type NavigationState =
  | BaseRoute
  | { kind: "album"; album: Album; returnTo: BaseRoute };

export type NavigationAction =
  | { type: "selectSidebar"; id: string }
  | { type: "openScreen"; screen: ContentScreenId }
  | { type: "openPlaylist"; playlistId: string }
  | { type: "playlistDeleted"; playlistId: string }
  | { type: "openAlbum"; album: Album }
  | { type: "closeAlbum" }
  | { type: "openArtist"; artist: Artist }
  | { type: "closeArtist" }
  | { type: "openSettings"; focusKey?: string }
  | { type: "consumeSettingsFocus" };

export const INITIAL_NAVIGATION_STATE: NavigationState = {
  kind: "screen",
  screen: "home",
};

const PLAYLIST_PREFIX = "playlist:";

const SCREEN_ID_SET: ReadonlySet<string> = new Set(SCREEN_IDS);

export function isScreenId(value: string): value is ScreenId {
  return SCREEN_ID_SET.has(value);
}

export function routeFromSidebarId(id: string): BaseRoute {
  if (id.startsWith(PLAYLIST_PREFIX)) {
    return { kind: "playlist", playlistId: id.slice(PLAYLIST_PREFIX.length) };
  }
  if (id === "settings") {
    return { kind: "settings", focusKey: null };
  }
  if (isScreenId(id) && id !== "settings") {
    return { kind: "screen", screen: id };
  }
  return { kind: "unknown", screen: id };
}

export function navigationReducer(
  state: NavigationState,
  action: NavigationAction,
): NavigationState {
  switch (action.type) {
    case "selectSidebar":
      return routeFromSidebarId(action.id);
    case "openScreen":
      return { kind: "screen", screen: action.screen };
    case "openPlaylist":
      return { kind: "playlist", playlistId: action.playlistId };
    case "playlistDeleted":
      return removePlaylistScope(state, action.playlistId);
    case "openAlbum":
      return {
        kind: "album",
        album: action.album,
        returnTo: state.kind === "album" ? state.returnTo : state,
      };
    case "closeAlbum":
      return state.kind === "album" ? state.returnTo : state;
    case "openArtist":
      return { kind: "artist", artist: action.artist };
    case "closeArtist":
      return state.kind === "artist"
        ? { kind: "screen", screen: "artists" }
        : state;
    case "openSettings":
      return { kind: "settings", focusKey: action.focusKey ?? null };
    case "consumeSettingsFocus":
      return state.kind === "settings"
        ? { ...state, focusKey: null }
        : state;
  }
}

function removePlaylistScope(
  state: NavigationState,
  playlistId: string,
): NavigationState {
  if (state.kind === "playlist") {
    return state.playlistId === playlistId
      ? { kind: "screen", screen: "songs" }
      : state;
  }
  if (state.kind !== "album" || state.returnTo.kind !== "playlist") {
    return state;
  }
  if (state.returnTo.playlistId !== playlistId) {
    return state;
  }
  return {
    ...state,
    returnTo: { kind: "screen", screen: "songs" },
  };
}

export function activeSidebarId(state: NavigationState): string {
  const route = state.kind === "album" ? state.returnTo : state;
  switch (route.kind) {
    case "playlist":
      return `${PLAYLIST_PREFIX}${route.playlistId}`;
    case "artist":
      return "artists";
    case "settings":
      return "settings";
    case "screen":
    case "unknown":
      return route.screen;
  }
}

export type BackendStatus = "booting" | "ready" | "failed";

export type AppSurface =
  | { kind: "setup" }
  | { kind: "booting"; showDependencyHint: boolean }
  | { kind: "miniPlayer" }
  | { kind: "main" };

type AppSurfaceInput = {
  needsSetup: boolean | null;
  status: BackendStatus;
  isMini: boolean;
  bootingSlowly: boolean;
};

export function selectAppSurface({
  needsSetup,
  status,
  isMini,
  bootingSlowly,
}: AppSurfaceInput): AppSurface {
  if (needsSetup === true) return { kind: "setup" };
  if (status === "booting") {
    return { kind: "booting", showDependencyHint: bootingSlowly };
  }
  if (isMini) return { kind: "miniPlayer" };
  return { kind: "main" };
}
