"""
Unit tests for the pure logic in src/split_album_detector.py.

These cover the normalization / edition-ranking / superset / incident-key
helpers without a database or any heavy dependency, so they run with a plain
host `python -m unittest` (and in the pre-commit hook).
"""

import unittest

from src import split_album_detector as sad


class TestAlbumNameNormalization(unittest.TestCase):
    def test_strips_parenthetical_editions(self):
        self.assertEqual(sad._norm_album_base("The Melodic Blue (Deluxe)"), "the melodic blue")
        self.assertEqual(sad._norm_album_base("Ctrl (Deluxe)"), "ctrl")
        self.assertEqual(sad._norm_album_base("Animal (Expanded Edition)"), "animal")
        self.assertEqual(
            sad._norm_album_base("Future Nostalgia (The Moonlight Edition)"),
            "future nostalgia",
        )

    def test_strips_bracketed_and_remaster(self):
        self.assertEqual(sad._norm_album_base("Album [Remastered]"), "album")
        self.assertEqual(sad._norm_album_base("Album (2011 Remaster)"), "album")

    def test_strips_trailing_separator_edition(self):
        self.assertEqual(sad._norm_album_base("Album - Deluxe"), "album")
        self.assertEqual(sad._norm_album_base("Album: Special Edition"), "album")

    def test_leaves_non_edition_parentheticals(self):
        # "(Live)" / "(Acoustic)" are not edition keywords — keep them.
        self.assertEqual(sad._norm_album_base("Album (Live)"), "album (live)")

    def test_collapses_whitespace_and_case(self):
        self.assertEqual(sad._norm_album_base("  The   COOL  Album "), "the cool album")

    def test_pure_edition_name_becomes_empty(self):
        # A title that is *only* an edition tag normalizes to empty (caller skips).
        self.assertEqual(sad._norm_album_base("(Deluxe)"), "")


class TestArtistNormalization(unittest.TestCase):
    def test_strips_feat(self):
        self.assertEqual(sad._norm_artist_base("Baby Keem feat. Brent Faiyaz"), "baby keem")
        self.assertEqual(sad._norm_artist_base("A$AP Ferg ft. Meek Mill"), "a$ap ferg")
        self.assertEqual(sad._norm_artist_base("X featuring Y"), "x")

    def test_plain_artist_unchanged(self):
        self.assertEqual(sad._norm_artist_base("Kendrick Lamar"), "kendrick lamar")


class TestEditionRank(unittest.TestCase):
    def test_ranking_order(self):
        self.assertGreater(sad._edition_rank("X (Super Deluxe)"), sad._edition_rank("X (Deluxe)"))
        self.assertGreater(sad._edition_rank("X (Deluxe)"), sad._edition_rank("X"))
        self.assertGreater(sad._edition_rank("X (Expanded Edition)"), sad._edition_rank("X"))
        self.assertEqual(sad._edition_rank("Plain Album"), 0)


class TestAlbumsMatch(unittest.TestCase):
    def _g(self, album):
        return {"album": album}

    def test_exact_and_substring(self):
        self.assertTrue(sad._albums_match(self._g("Cool Album"), self._g("Cool Album")))
        self.assertTrue(sad._albums_match(self._g("Cool Album"), self._g("Cool Album (Deluxe)")))

    def test_dissimilar_albums_do_not_match(self):
        # The Mac Miller / Alchemist false-positive class: different albums.
        self.assertFalse(sad._albums_match(self._g("The Divine Feminine"), self._g("Bread")))

    def test_empty_never_matches(self):
        self.assertFalse(sad._albums_match(self._g(""), self._g("Anything")))


class TestPickCanonical(unittest.TestCase):
    def test_prefers_deluxe_over_more_tracks(self):
        base = {"album": "X", "track_count": 12, "album_id": "b"}
        deluxe = {"album": "X (Deluxe)", "track_count": 3, "album_id": "d"}
        self.assertEqual(sad._pick_canonical([base, deluxe])["album_id"], "d")

    def test_falls_back_to_most_tracks_when_no_edition(self):
        a = {"album": "X", "track_count": 5, "album_id": "a"}
        b = {"album": "X", "track_count": 15, "album_id": "b"}
        self.assertEqual(sad._pick_canonical([a, b])["album_id"], "b")


class TestIncidentKey(unittest.TestCase):
    def test_order_independent_and_stable(self):
        inc1 = {"groups": [{"album_id": "a"}, {"album_id": "b"}]}
        inc2 = {"groups": [{"album_id": "b"}, {"album_id": "a"}]}
        self.assertEqual(sad.incident_key(inc1), sad.incident_key(inc2))

    def test_different_groups_differ(self):
        inc1 = {"groups": [{"album_id": "a"}, {"album_id": "b"}]}
        inc3 = {"groups": [{"album_id": "a"}, {"album_id": "c"}]}
        self.assertNotEqual(sad.incident_key(inc1), sad.incident_key(inc3))


class TestIncidentFinalization(unittest.TestCase):
    def test_folder_incident_wins_without_mutating_detection_results(self):
        groups = [{"album_id": "a"}, {"album_id": "b"}]
        folder = {"type": "folder", "directory": "/music", "groups": groups}
        name = {"type": "name", "similarity": 0.99, "groups": groups}

        result = sad._finalize_incidents([folder, name], set())

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "folder")
        self.assertIn("key", result[0])
        self.assertNotIn("key", folder)
        self.assertNotIn("key", name)

    def test_dismissed_key_is_filtered(self):
        incident = {
            "type": "name",
            "similarity": 0.9,
            "groups": [{"album_id": "a"}, {"album_id": "b"}],
        }
        key = sad.incident_key(incident)
        self.assertEqual(sad._finalize_incidents([incident], {key}), [])


class TestEditionPlanning(unittest.TestCase):
    def test_consolidation_plan_freezes_ordered_source_moves(self):
        canonical = {
            "album_id": "release-deluxe",
            "album": "Album (Deluxe)",
            "artist": "Artist",
            "release_mbid": "release-deluxe",
            "track_count": 10,
        }
        standard = {
            "album_id": "Album|Artist",
            "album": "Album",
            "artist": "Artist",
            "release_mbid": None,
            "track_count": 2,
        }

        plans = sad._build_edition_consolidation_plans([
            {"canonical": canonical, "others": [standard]}
        ])

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].target_album, "Album (Deluxe)")
        self.assertEqual(plans[0].moves[0].source_album_id, "Album|Artist")
        self.assertEqual(plans[0].moves[0].planned_track_count, 2)
        self.assertTrue(plans[0].moves[0].write_file_tags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
