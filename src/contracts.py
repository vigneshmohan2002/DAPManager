"""Shared, dependency-free type contracts for DAP Manager.

The project passes JSON-shaped dictionaries between the Python services, web
routes, sync jobs, and desktop backend.  These contracts document those
boundaries without introducing runtime validation or changing the serialized
payloads.  They intentionally use only :mod:`typing` features available in
Python 3.10.
"""

from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    TypeAlias,
    TypedDict,
    Union,
)


JSONScalar: TypeAlias = Union[str, int, float, bool, None]
JSONValue: TypeAlias = Union[
    JSONScalar,
    List["JSONValue"],
    Dict[str, "JSONValue"],
]
JSONObject: TypeAlias = Dict[str, JSONValue]
ConfigValue: TypeAlias = JSONValue
ConfigData: TypeAlias = Dict[str, ConfigValue]
ConfigMapping: TypeAlias = Mapping[str, Any]
MutableConfigMapping: TypeAlias = MutableMapping[str, Any]


DeviceRole: TypeAlias = Literal["master", "satellite", "standalone"]
AuthorityDeviceRole: TypeAlias = Literal["master", "standalone"]


class ConfigReader(Protocol):
    """Smallest interface consumed by code that reads configuration."""

    def get(self, key: str, default: Any = None) -> Any:
        """Return a configured value or ``default`` when the key is absent."""
        ...


class InitialConfigBase(TypedDict):
    """Fields emitted for every first-run role."""

    database_file: str
    music_library_path: str
    downloads_path: str
    ffmpeg_path: str
    slsk_cmd_base: List[str]
    dap_mount_point: str
    dap_music_dir_name: str
    dap_playlist_dir_name: str
    conversion_sample_rate: int
    conversion_bit_depth: int
    fast_search: bool
    remove_ft: bool
    desperate_mode: bool
    strict_quality: bool
    auto_tag_downloads: bool
    is_master: bool
    device_role: DeviceRole
    acoustid_api_key: str
    contact_email: str
    artist_tag_max_age_days: int
    library_maintenance_interval_seconds: int
    library_maintenance_on_startup: bool
    api_token: str


class InitialConfig(InitialConfigBase, total=False):
    """Role-dependent fields emitted by ``build_initial_config``."""

    device_name: str
    master_url: str
    public_master_url: str
    report_inventory_to_host: bool
    contribute_to_host: bool
    slsk_username: str
    slsk_password: str
    jellyfin_url: str
    jellyfin_api_key: str
    jellyfin_user_id: str
    jellyfin_music_library_path: str
    lidarr_enabled: bool
    lidarr_acquisition_handoff_enabled: bool
    lidarr_url: str
    lidarr_api_key: str


class ProgressEventBase(TypedDict):
    message: str


class ProgressEvent(ProgressEventBase, total=False):
    detail: str
    current: int
    total: int


class ProgressReporter(Protocol):
    """Callable accepted by long-running jobs for progress events."""

    def __call__(self, event: ProgressEvent) -> None:
        """Consume one progress event."""
        ...


ProgressCallback: TypeAlias = Callable[[ProgressEvent], None]
MessageReporter: TypeAlias = Callable[[str], None]


CatalogApplyAction: TypeAlias = Literal["inserted", "updated", "stale", "skipped"]
CatalogRow: TypeAlias = Dict[str, Any]
CatalogApplyCallback: TypeAlias = Callable[[CatalogRow], CatalogApplyAction]
SyncStepStatus: TypeAlias = Literal["ok", "skipped", "error"]


class DeltaSyncResult(TypedDict, total=False):
    received: int
    inserted: int
    updated: int
    stale: int
    skipped: int
    pushed: int
    since: Optional[str]
    as_of: Optional[str]


class PlaylistPushResult(TypedDict):
    sent: int
    accepted: int
    stale: int
    skipped: int
    since: Optional[str]
    as_of: Optional[str]


class InventoryItem(TypedDict):
    """One device-local track advertised to an authority device."""

    mbid: str
    local_path: str


class InventoryReportResult(TypedDict):
    """Stable summary returned by the inventory sync entry point."""

    mode: Literal["local", "remote"]
    device_id: str
    items: int
    written: int


class SyncStepBase(TypedDict):
    name: str
    status: SyncStepStatus


class SyncStepResult(SyncStepBase, total=False):
    message: str
    summary: JSONObject


class SyncAllResult(TypedDict):
    steps: List[SyncStepResult]


class SyncOperation(Protocol):
    """No-argument operation used by the Sync All step runner."""

    def __call__(self) -> Mapping[str, Any]:
        """Run a sync step and return its existing JSON-shaped summary."""
        ...


ContributionStatus: TypeAlias = Literal[
    "attempting",
    "have_better",
    "satisfied",
    "needs_upload",
    "ingested",
]


class ContributionRunBase(TypedDict):
    offered: int
    uploaded: int
    satisfied: int
    errors: int


class ContributionRunResult(ContributionRunBase, total=False):
    skipped: str


class ContributionActionBase(TypedDict):
    success: bool
    mbid: str


class ContributionActionResult(ContributionActionBase, total=False):
    status: Optional[ContributionStatus]
    message: str


class ContributionResponse(TypedDict, total=False):
    success: bool
    status: ContributionStatus
    contribution_id: int
    want_upload: bool
    message: str


ConfidenceTier: TypeAlias = Literal["green", "yellow", "red"]


class TagMetadata(TypedDict, total=False):
    artist: str
    album_artist: str
    album: str
    title: str
    date: str
    track_number: Union[str, int]
    track_total: Union[str, int]
    disc_number: Union[str, int]
    disc_total: Union[str, int]
    mbid: str
    release_mbid: str
    release_track_mbid: str


class TagCandidate(TypedDict):
    score: float
    tier: ConfidenceTier
    meta: TagMetadata
    current: TagMetadata
