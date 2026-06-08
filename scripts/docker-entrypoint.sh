#!/bin/sh
set -e

CONFIG_DIR="${CONFIG_DIR:-/config}"
DATA_DIR="${DATA_DIR:-/data}"
CONFIG_FILE="${CONFIG_DIR}/config.json"

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$DATA_DIR/music" "$DATA_DIR/downloads"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] No config.json at $CONFIG_FILE — seeding master stub."
    cat > "$CONFIG_FILE" <<EOF
{
    "database_file": "${DATA_DIR}/dap_library.db",
    "music_library_path": "${DATA_DIR}/music",
    "downloads_path": "${DATA_DIR}/downloads",
    "ffmpeg_path": "ffmpeg",
    "slsk_cmd_base": ["sldl"],
    "dap_mount_point": "",
    "dap_music_dir_name": "Music",
    "dap_playlist_dir_name": "Playlists",
    "is_master": true,
    "device_role": "master",
    "slsk_username": "",
    "slsk_password": "",
    "acoustid_api_key": "",
    "contact_email": "",
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_user_id": "",
    "lidarr_enabled": false,
    "lidarr_url": "",
    "lidarr_api_key": "",
    "lidarr_quality_profile_id": null,
    "lidarr_root_folder_path": ""
}
EOF
fi

if ! command -v sldl >/dev/null 2>&1; then
    echo "[entrypoint] WARNING: 'sldl' not on PATH — downloads will fail. Rebuild the image or reinstall sldl." >&2
fi

cd "$CONFIG_DIR"
exec "$@"
