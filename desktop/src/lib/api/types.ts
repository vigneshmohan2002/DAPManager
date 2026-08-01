export type Album = {
  id: string;
  title: string;
  artist: string;
  track_count: number;
  primary_artist?: string | null;
  credited_artists?: string[];
};

export type Artist = {
  name: string;
  album_count: number;
  track_count: number;
};

export type Track = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  track_number: number | null;
  disc_number: number | null;
  // Optional because not every consumer of Track carries the hearted
  // state (search results, ad-hoc constructed rows). Present on rows
  // coming from /api/library/tracks and /api/library/albums/.../tracks.
  is_liked?: boolean;
};

export type Availability = "local" | "drive" | "remote" | "unavailable";

export type LibraryTrack = Track & {
  album_id: string | null;
  availability: Availability;
  // Server-side hearted state. Present on every row served from
  // /api/library/tracks; the heart toggle flips this through
  // setTrackLiked and the consumer expects the update to surface
  // optimistically.
  is_liked: boolean;
  // Only present when the caller passed include_orphans=1; absent
  // rows are implicitly not orphans.
  orphan?: boolean;
};

export type BackendStartupResult =
  | { ok: true }
  | { ok: false; error: string };

export type BackendRestartResult = {
  success: boolean;
  message: string;
  bind_host: string;
  backend_running: boolean;
};

export type SearchTrackResult = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  path: string | null;
};

export type FetchTracksOptions = {
  playlistId?: string;
  localOnly?: boolean;
  includeOrphans?: boolean;
};

export type SmartField =
  | "artist"
  | "album"
  | "title"
  | "tag_tier"
  | "tag_score"
  // Existing web-created rules can contain these server-supported fields.
  // The desktop editor does not offer them yet, but the API boundary must
  // retain them so it does not drop an otherwise valid smart playlist.
  | "genre"
  | "is_liked";

export type SmartOp =
  | "contains"
  | "equals"
  | "starts_with"
  | "ends_with"
  | "gt"
  | "lt";

export type SmartRule = {
  field: SmartField;
  op: SmartOp;
  value: string | number | boolean;
};

export type SmartRuleset = {
  match: "all" | "any";
  rules: SmartRule[];
};

export type Playlist = {
  playlist_id: string;
  name: string;
  track_count: number;
  updated_at: string;
  // Decoded by the GET endpoint; null means "static playlist". Static
  // and smart are distinguished only by truthiness of this field.
  smart_rules: SmartRuleset | null;
};

export type ArtistInfo = {
  summary: string;
  source_url: string | null;
  image_url: string | null;
  title: string;
};

export type ConfigGroup = { label: string; keys: string[] };

export type ConfigValue = string | number | boolean | null;

export type ConfigPayload = {
  config: Record<string, ConfigValue>;
  editable_keys: string[];
  secret_keys: string[];
  bool_keys: string[];
  groups: ConfigGroup[];
};

export type TagTier = "green" | "yellow" | "red";

export type TagMeta = {
  artist?: string;
  album_artist?: string;
  album?: string;
  title?: string;
  date?: string;
  track_number?: string;
  disc_number?: string;
  mbid?: string;
  release_mbid?: string;
};

export type IdentifyCandidate = {
  score: number;
  tier: TagTier;
  meta: TagMeta;
  current: TagMeta;
};

export type IdentifyResult =
  | { kind: "match"; candidate: IdentifyCandidate; localPath: string }
  | { kind: "no_match"; current: TagMeta }
  | { kind: "needs_config"; key: string; message: string }
  | { kind: "error"; message: string };

export type SaveConfigResult = {
  success: boolean;
  message: string;
  changed: string[];
};

export type OrphanTrack = {
  mbid: string;
  artist: string | null;
  title: string | null;
  album: string | null;
  deleted_at: string | null;
  local_path: string | null;
};

export type OrphanPlaylist = {
  playlist_id: string;
  name: string;
  deleted_at: string | null;
  track_count: number;
};

export type PlayStatsTrack = {
  mbid: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  plays: number;
};

export type PlayStatsArtist = {
  artist: string;
  plays: number;
  distinct_tracks: number;
};

export type PlayStatsRecent = {
  id: number;
  mbid: string;
  played_at: string;
  source: string | null;
  title: string | null;
  artist: string | null;
  album: string | null;
  album_id: string | null;
};

export type PlayStats = {
  total: number;
  // Sum of listened ms across the same window the play count uses.
  // Legacy rows (Stage 12a migration boundary) have NULL listened_ms
  // server-side and contribute 0; tooltip copy on the Stats screen
  // notes the partial-history caveat.
  listening_time_ms: number;
  top_tracks: PlayStatsTrack[];
  top_artists: PlayStatsArtist[];
  recent: PlayStatsRecent[];
  // 24-element fixed-width array, index = UTC hour (0..23).
  // Backend pads zeros so the heatmap can render directly.
  hour_of_day: number[];
};

export type FetchPlayStatsOptions = {
  // ISO-8601 cutoff (inclusive); omit for all-time.
  since?: string;
  limit?: number;
};

export type WrappedTopTrack = {
  mbid: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  plays: number;
};

export type WrappedTopArtist = {
  artist: string;
  plays: number;
  distinct_tracks: number;
};

export type WrappedTopAlbum = {
  album_id: string;
  album: string | null;
  artist: string | null;
  plays: number;
};

export type WrappedPayload = {
  year: number;
  total_plays: number;
  total_listening_time_ms: number;
  has_legacy_rows: boolean;
  top_track: WrappedTopTrack | null;
  top_artist: WrappedTopArtist | null;
  top_album: WrappedTopAlbum | null;
  busiest_day: { date: string; plays: number } | null;
  top_hour: number | null;
  first_play: { played_at: string; title: string | null; artist: string | null } | null;
  longest_streak_days: number;
};

export type HomeLikedPreview = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  album_id: string | null;
};

export type HomeJumpBackIn = {
  album_id: string;
  title: string;
  artist: string;
};

export type HomeDailyMix = {
  playlist_id: string;
  name: string;
  tag: string;
  track_count: number;
};

export type HomePayload = {
  recent: PlayStatsRecent[];
  top_artists: PlayStatsArtist[];
  liked: { total: number; preview: HomeLikedPreview[] };
  jump_back_in: HomeJumpBackIn[];
  daily_mixes: HomeDailyMix[];
};

export type ArtistRadio = {
  tracks: LibraryTrack[];
  top_tag: string | null;
  seed_count: number;
  related_count: number;
};

export type LyricsResponse = {
  lrc: string | null;
  synced: boolean;
  source: "lrclib" | "manual" | null;
  fetched_at: string | null;
  // True when the server returned a stale cached row because the live
  // LRCLIB fetch hit a transient network error. UI shows a quiet note;
  // contents are still rendered.
  stale?: boolean;
};

export type SuggestionItem =
  | { artist: string; title: string; mbid?: string }
  | { search_query: string; mbid?: string };

export type SuggestionResult = {
  success: boolean;
  message: string;
  received: number;
  queued: number;
  skipped: number;
};

export type PublicUrlDetection = {
  source: "env" | "tailscale" | "none";
  url?: string;
};

export type SetupPayload = {
  role: "master" | "satellite" | "standalone";
  music_library_path: string;
  downloads_path: string;
  dap_mount_point?: string;
  master_url?: string;
  public_master_url?: string;
  device_name?: string;
  slsk_username?: string;
  slsk_password?: string;
  jellyfin_url?: string;
  jellyfin_api_key?: string;
  jellyfin_user_id?: string;
  lidarr_url?: string;
  lidarr_api_key?: string;
  lidarr_enabled?: boolean;
  acoustid_api_key?: string;
  contact_email?: string;
  api_token?: string;
};

export type DuplicateCandidate = {
  path: string;
  score: number;
  is_recommended?: boolean;
  exists?: boolean;
  is_safe_file?: boolean;
  identity_status?: "match" | "mismatch" | "unknown";
  release_mbid?: string;
};

export type DuplicateGroup = {
  mbid: string;
  artist: string;
  title: string;
  release_conflict?: boolean;
  candidates: DuplicateCandidate[];
};

export type ResolveDuplicateResult = {
  success: boolean;
  message: string;
  deleted: string[];
  errors: string[];
  missing: string[];
  remaining: string[];
  resolved: boolean;
};

export type IncompleteAlbum = {
  artist: string;
  album: string;
  mbid: string;
  have: number;
  total: number;
  missing: number;
  cover_art: string;
};

export type SyncState = {
  catalog_pull: string | null;
  playlist_pull: string | null;
  playlist_push: string | null;
  inventory_report: string | null;
};

export type BackendStatus = {
  running: boolean;
  task: string | null;
  message: string | null;
  detail: string | null;
};

export type AudioQuality = {
  ext?: string;
  lossless?: boolean;
  bits_per_sample?: number;
  sample_rate?: number;
  bitrate?: number;
  channels?: number;
  length_ms?: number;
  size_bytes?: number;
};

export type ContributionStatus =
  | "attempting"
  | "have_better"
  | "satisfied"
  | "needs_upload"
  | "ingested";

export type Contribution = {
  id: number;
  contribution_id?: number | null;
  device_id: string | null;
  mbid: string | null;
  isrc?: string | null;
  artist: string | null;
  title: string | null;
  album: string | null;
  target_quality: AudioQuality | null;
  acquired_quality: AudioQuality | null;
  status: ContributionStatus | string;
  download_id?: number | null;
  created_at?: string | null;
  updated_at: string | null;
};

export type FleetDevice = {
  device_id: string;
  track_count: number;
  last_reported_at: string | null;
};

export type FleetHolder = {
  device_id: string;
  local_path: string | null;
  reported_at: string;
};

export type FleetSearchResult = {
  mbid: string;
  artist: string;
  title: string;
  album: string | null;
  device_count: number;
  holders: FleetHolder[];
};

export type ActionResult = { success: boolean; message: string };

export type ContributeTrackResult = ActionResult & {
  mbid?: string;
  status?: ContributionStatus | string | null;
};

export type QueueDownloadResult = {
  success: boolean;
  message?: string;
  queued: number;
  skipped_linked: number;
  skipped_queued: number;
  not_found: number;
};

export type CreatePlaylistResult = {
  success: boolean;
  message?: string;
  playlist_id?: string;
  name?: string;
};

export type AddToPlaylistResult = {
  success: boolean;
  message: string;
  added: number;
  missed: number;
};

export type WantedRelease = {
  mbid: string;
  artist: string;
  title: string;
  release_date: string | null;
  cover_url: string;
  queued: boolean;
  downloaded: boolean;
};

export type WantedReleasesResult =
  | { kind: "ok"; last_tick: string | null; items: WantedRelease[] }
  | { kind: "disabled" }
  | { kind: "unavailable" }
  | { kind: "error"; message: string };

export type DownloadQueueItem = {
  id: number;
  query: string;
  // Backend status vocabulary is "pending" / "failed" / "success".
  // The UI labels "success" as "Completed" — keep names aligned with
  // the schema so logs / DB / API don't translate three different ways.
  status: "pending" | "failed" | "success" | string;
  last_attempt: string | null;
  // Retry metadata was added after the original queue API. Keep each field
  // optional so desktop clients remain compatible with older masters while a
  // rolling upgrade is in progress.
  attempt_count?: number;
  max_attempts?: number;
  next_attempt_at?: string | null;
  is_paused?: boolean;
  is_quarantined?: boolean;
  last_error?: string | null;
  retained_bytes?: number;
  retained_directories?: number;
  retained_files?: number;
  retained_kinds?: Array<"attempt" | "quarantine">;
  target_key?: string;
  phase?: string;
  failure_class?: string | null;
  blocked_reason?: string | null;
  strategy_index?: number;
};

export type DownloadWorkerState = {
  is_paused: boolean;
  state: string;
  current_item_id: number | null;
  detail: string;
  heartbeat_at: string | null;
  next_wake_at: string | null;
};

export type ClearCompletedResult = ActionResult & { removed: number };
export type RemoveDownloadResidueResult = ActionResult & {
  removed_bytes: number;
  removed_directories: number;
  removed_files: number;
};

export type AlbumReleaseCandidate = {
  release_mbid: string;
  title: string;
  artist: string;
  track_count: number;
  date: string;
  country: string;
  status: string;
  disambiguation: string;
  primary_type: string;
  format: string;
  label: string;
  catalog_number: string;
  barcode: string;
  cover_url: string;
  musicbrainz_url: string;
  score?: number;
};

export type AlbumDownloadStage =
  | "queued"
  | "downloading"
  | "importing"
  | "success"
  | "failed";

export type AlbumDownloadRequest = {
  id: number;
  release_mbid: string;
  title: string;
  artist: string;
  track_count: number;
  stage: AlbumDownloadStage;
  detail: string;
  completed_tracks: number;
  queue_status: string | null;
  last_attempt: string | null;
  created_at: string | null;
  updated_at: string | null;
  cover_url: string;
};

export type AlbumReleaseSearchResult = {
  query: string;
  ambiguous: boolean;
  candidates: AlbumReleaseCandidate[];
};

export type AlbumDownloadRequestResult = {
  success: boolean;
  queued: boolean;
  message: string;
  request?: AlbumDownloadRequest;
};
