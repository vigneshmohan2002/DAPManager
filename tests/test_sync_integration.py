
import pytest
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch
from src.db_manager import DatabaseManager, Track, Playlist
from src.sync_ipod import EnhancedIpodSyncer, SyncMode

# Mock SUPPORTED_EXTENSIONS if needed by patching the module that uses it
# OR just ensure we use standard extensions.

@pytest.fixture
def temp_dirs():
    """Creates a temporary workspace with PC and iPod folders."""
    with tempfile.TemporaryDirectory() as temp_dir:
        pc_lib = os.path.join(temp_dir, "MusicLibrary")
        ipod_mount = os.path.join(temp_dir, "IPOD")
        ipod_music = os.path.join(ipod_mount, "Music")
        ipod_playlists = os.path.join(ipod_mount, "Playlists")
        
        os.makedirs(pc_lib)
        os.makedirs(ipod_music)
        os.makedirs(ipod_playlists)
        
        # Create a dummy ffmpeg executable
        ffmpeg_path = os.path.join(temp_dir, "ffmpeg")
        with open(ffmpeg_path, "w") as f:
            f.write("dummy")
        os.chmod(ffmpeg_path, 0o755)
        
        yield {
            "root": temp_dir,
            "pc": pc_lib,
            "ipod": ipod_mount,
            "ipod_music": ipod_music,
            "ffmpeg": ffmpeg_path
        }

@pytest.fixture
def db(temp_dirs):
    """Creates a persistent DB file for the test session."""
    db_path = os.path.join(temp_dirs["root"], "dap_library.db")
    manager = DatabaseManager(db_path)
    yield manager
    manager.close()

@pytest.fixture
def syncer(db, temp_dirs):
    """Returns an EnhancedIpodSyncer instance."""
    # We need to mock subprocess.run so we don't actually try to transcode
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        
        syncer_instance = EnhancedIpodSyncer(
            db=db,
            downloader=MagicMock(),
            ffmpeg_path=temp_dirs["ffmpeg"],
            ipod_mount=temp_dirs["ipod"],
            ipod_music_dir="Music",
            ipod_playlist_dir="Playlists"
        )
        # Monkey patch _convert_and_copy to just copy the file if we want to verify content,
        # OR just mock it to touch the output file.
        # Let's mock it to simple copy for verification
        
        original_convert = syncer_instance._convert_and_copy
        
        def mock_convert_and_copy(track):
            # Simulate conversion by just creating the destination file
            # Logic borrowed from original but simplified
            output_path = os.path.join(
                syncer_instance.ipod_music_path, 
                "Unknown Artist", "Unknown Album", f"{track.title}.flac"
            )
            # Replicate the path logic roughly or just use what the method would do?
            # It's safer to let the real method run but mock the subprocess call.
            # But the real method does complex path building.
            # Let's try to trust the real method with the mocked subprocess.
            # BUT: The real method uses ffmpeg to write the file. If we mock subprocess, no file is written!
            # So we MUST create the file manually after "subprocess" runs.
            
            # Let's side_effect the subprocess.run to create the file?
            # Or simplified: just override _convert_and_copy to simple copy.
            
            safe_title = track.title or "Unknown"
            # Just put it in the root of music for simplicity of test, or match structure
            dest = os.path.join(syncer_instance.ipod_music_path, f"{safe_title}.flac")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write("TRANSCODED_CONTENT")
            
            syncer_instance.db.mark_track_synced(track.mbid, dest)
            
        syncer_instance._convert_and_copy = mock_convert_and_copy
        
        yield syncer_instance

def test_downstream_sync_pc_to_ipod(db, syncer, temp_dirs):
    """Test standard sync from PC to iPod."""
    # 1. Add track to DB
    track = Track(
        mbid="123", title="Test Song", artist="Test Actor", album="Test Album",
        local_path=os.path.join(temp_dirs["pc"], "song.flac"), synced_to_ipod=False
    )
    # Create the source file
    with open(track.local_path, "w") as f:
        f.write("ORIGINAL_CONTENT")
        
    db.add_or_update_track(track)
    
    # 2. Run Sync (Full Library)
    syncer.run_sync(mode=SyncMode.FULL_LIBRARY, skip_downloads=True)
    
    # 3. Verify
    # Check DB was updated
    updated_track = db.get_track_by_mbid("123")
    assert updated_track.synced_to_ipod is True
    assert updated_track.ipod_path is not None
    assert os.path.exists(updated_track.ipod_path)

def test_db_backup(db, syncer, temp_dirs):
    """Test that the database is backed up to iPod root."""
    # 1. Run Sync (can be empty)
    syncer.run_sync(mode=SyncMode.FULL_LIBRARY, skip_downloads=True)
    
    # 2. Verify DB file at iPod root
    expected_path = os.path.join(temp_dirs["ipod"], "dap_library.db")
    assert os.path.exists(expected_path)
    
def test_upstream_sync_ipod_to_pc(db, syncer, temp_dirs):
    """Test that missing tracks on iPod are restored to PC."""
    # 1. Create a "ghost" track on iPod (simulating lost local file or new file)
    ghost_file = os.path.join(temp_dirs["ipod_music"], "GhostTrack.flac")
    with open(ghost_file, "w") as f:
        f.write("GHOST_CONTENT")
        
    # We need to tag it (or have logic handle untagged). 
    # The current logic uses get_mbid_from_tags. If that returns None, it STILL imports it.
    # So we don't strictly need tags for this test if we trust the "untagged implies import" logic.
    
    # 2. Run Sync
    syncer.run_sync(mode=SyncMode.FULL_LIBRARY, skip_downloads=True)
    
    # 3. Verify it was copied to Restored_From_iPod
    # The restore path is relative to the DB path
    restore_dir = os.path.join(temp_dirs["root"], "Restored_From_iPod")
    
    # We need to find the file recursively or check specific path
    # The logic keeps the relative path from iPod root
    expected_restored_file = os.path.join(restore_dir, "GhostTrack.flac")
    
    assert os.path.exists(expected_restored_file)
    with open(expected_restored_file, "r") as f:
        assert f.read() == "GHOST_CONTENT"

def test_no_deletion_safety(db, syncer, temp_dirs):
    """Verify that neither PC nor iPod files are deleted."""
    # 1. Setup PC file
    pc_file = os.path.join(temp_dirs["pc"], "StayOnPC.flac")
    with open(pc_file, "w") as f:
        f.write("PC_CONTENT")
        
    # 2. Setup iPod file
    ipod_file = os.path.join(temp_dirs["ipod_music"], "StayOnIpod.flac")
    with open(ipod_file, "w") as f:
        f.write("IPOD_CONTENT")
        
    # 3. Run Sync
    syncer.run_sync(mode=SyncMode.FULL_LIBRARY, skip_downloads=True)
    
    # 4. Verify presence
    assert os.path.exists(pc_file)
    assert os.path.exists(ipod_file)
