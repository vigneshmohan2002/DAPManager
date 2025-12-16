import pytest
import os
from unittest.mock import patch, MagicMock
from src.auto_tagger import AutoTagger

@pytest.fixture
def mock_acoustid_lookup():
    with patch('acoustid.lookup') as mock:
        yield mock

@pytest.fixture
def mock_mb_get_release():
    with patch('musicbrainzngs.get_release_by_id') as mock:
        yield mock

@pytest.fixture
def mock_fingerprint():
    with patch('acoustid.fingerprint_file') as mock:
        yield mock

def test_identify_and_tag_success(mock_fingerprint, mock_acoustid_lookup, mock_mb_get_release):
    # Setup Mocks
    mock_fingerprint.return_value = (180, "fingerprint_hash")
    
    mock_acoustid_lookup.return_value = {
        'results': [{
            'score': 0.9,
            'recordings': [{
                'id': 'rec_mbid',
                'releases': [{'id': 'rel_mbid'}]
            }]
        }]
    }
    
    # Mock MB Release
    mock_mb_get_release.return_value = {
        'release': {
            'title': 'Test Album',
            'artist-credit': [{'artist': {'name': 'Test Artist'}}],
            'date': '2023',
            'medium-list': [{
                'position': 1,
                'track-list': [{
                    'number': '1',
                    'recording': {
                        'id': 'rec_mbid',
                        'title': 'Test Title'
                    }
                }]
            }]
        }
    }
    
    # Mock Mutagen apply_tags (we don't want to write to real file)
    with patch.object(AutoTagger, '_apply_tags') as mock_apply:
        tagger = AutoTagger(acoustid_api_key="fake_key")
        filepath = "dummy.flac"
        
        meta = tagger.identify_and_tag(filepath)
        
        assert meta is not None
        assert meta['artist'] == "Test Artist"
        assert meta['title'] == "Test Title"
        assert meta['mbid'] == "rec_mbid"
        
        mock_apply.assert_called_once()
        args = mock_apply.call_args[0]
        assert args[0] == filepath
        assert args[1] == meta

def test_identify_no_match(mock_fingerprint, mock_acoustid_lookup):
    mock_fingerprint.return_value = (180, "fingerprint_hash")
    mock_acoustid_lookup.return_value = {'results': []}
    
    tagger = AutoTagger(acoustid_api_key="fake_key")
    meta = tagger.identify_and_tag("dummy.flac")
    assert meta is None
