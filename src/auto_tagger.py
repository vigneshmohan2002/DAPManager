import os
import shutil
import logging
import acoustid
import musicbrainzngs
import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TPOS
from mutagen.easyid3 import EasyID3

logger = logging.getLogger(__name__)

class AutoTagger:
    def __init__(self, acoustid_api_key: str):
        self.api_key = acoustid_api_key
        musicbrainzngs.set_useragent("DAPManager", "0.1", "http://github.com/dapmanager")
        
        if not self.api_key:
            logger.warning("No AcoustID API Key provided. Auto-tagging will fail.")

    def identify_and_tag(self, filepath: str) -> dict:
        """
        Identifies a track, fetches metadata, tags it, and returns the new metadata.
        Returns dict with keys: 'artist', 'album', 'title', 'mbid', 'release_mbid'
        """
        if not self.api_key:
            return None

        try:
            # 1. Fingerprint
            duration, fingerprint = acoustid.fingerprint_file(filepath)
            
            # 2. Lookup
            results = acoustid.lookup(self.api_key, fingerprint, duration, meta=['recordings', 'releases', 'tracks', 'usermeta', 'releasegroups'])
            
            if not results or not results.get('results'):
                logger.warning(f"No AcoustID match for {filepath}")
                return None
                
            # Get best match (highest score)
            best_match = max(results['results'], key=lambda x: x.get('score', 0))
            if best_match['score'] < 0.5:
                 logger.warning(f"Low confidence score ({best_match['score']}) for {filepath}")
                 # Proceed with caution or abort?
            
            # Extract MBIDs
            recording_id = best_match['recordings'][0]['id']
            # We need a release (album) context. Ideally finding the release that matches best?
            # Or just pick the first one?
            release = best_match['recordings'][0].get('releases', [{}])[0]
            release_id = release.get('id')
            
            if not release_id:
                logger.warning(f"No release ID found for {recording_id}")
                return None

            # 3. Fetch Full Metadata from MusicBrainz
            # We use identifying info from AcoustID but fetch details from MB for strictness
            try:
                mb_data = musicbrainzngs.get_release_by_id(release_id, includes=['artists', 'recordings', 'release-groups'])
                release_info = mb_data['release']
                
                # Find our track in this release to get track number
                track_info = None
                for media in release_info['medium-list']:
                    for track in media['track-list']:
                        if track['recording']['id'] == recording_id:
                            track_info = track
                            # Also grab disc number
                            disc_num = media['position']
                            break
                    if track_info: break
                
                if not track_info:
                    logger.warning("Could not find recording in release tracklist")
                    return None

                artist = release_info['artist-credit'][0]['artist']['name']
                album_artist = artist # Simplify
                album = release_info['title']
                title = track_info['recording']['title']
                date = release_info.get('date', '')
                track_num = track_info['number']
                total_tracks = len(media['track-list']) # Approximation
                
                meta = {
                    'artist': artist,
                    'album_artist': album_artist,
                    'album': album,
                    'title': title,
                    'date': date,
                    'track_number': track_num,
                    'disc_number': disc_num,
                    'mbid': recording_id,
                    'release_mbid': release_id,
                    'genre': '' 
                }
                
                # 4. Apply Tags
                self._apply_tags(filepath, meta)
                return meta
                
            except musicbrainzngs.WebServiceError as e:
                logger.error(f"MusicBrainz API error: {e}")
                return None
                
        except acoustid.WebServiceError as e:
             logger.error(f"AcoustID API error: {e}")
             return None
        except Exception as e:
            logger.error(f"Auto-tagging failed: {e}", exc_info=True)
            return None

    def _apply_tags(self, filepath: str, meta: dict):
        """Applies metadata to FLAC/MP3 files."""
        try:
            if filepath.lower().endswith('.flac'):
                audio = FLAC(filepath)
                audio['title'] = meta['title']
                audio['artist'] = meta['artist']
                audio['album'] = meta['album']
                audio['albumartist'] = meta['album_artist']
                audio['date'] = meta['date']
                audio['tracknumber'] = str(meta['track_number'])
                audio['discnumber'] = str(meta['disc_number'])
                audio['musicbrainz_recordingid'] = meta['mbid']
                audio['musicbrainz_albumid'] = meta['release_mbid']
                audio.save()
            # Add MP3 support if needed (EasyID3)
        except Exception as e:
            logger.error(f"Failed to write tags: {e}")
