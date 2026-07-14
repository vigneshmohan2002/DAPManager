"""Unit tests for src/first_run.build_initial_config role shaping."""

import unittest

from src import first_run


class TestBuildInitialConfig(unittest.TestCase):
    BASE = dict(music_library_path="/data/music", downloads_path="/data/downloads")

    def test_master_role(self):
        cfg = first_run.build_initial_config(
            "master", jellyfin_url="http://jf:8096", public_master_url="http://m:5001/",
            **self.BASE)
        self.assertTrue(cfg["is_master"])
        self.assertEqual(cfg["device_role"], "master")
        self.assertEqual(cfg["master_url"], "")
        self.assertEqual(cfg["public_master_url"], "http://m:5001")  # trailing / stripped

    def test_satellite_role(self):
        cfg = first_run.build_initial_config(
            "satellite", master_url="http://m:5001/", **self.BASE)
        self.assertFalse(cfg["is_master"])
        self.assertEqual(cfg["device_role"], "satellite")
        self.assertEqual(cfg["master_url"], "http://m:5001")
        # satellites leave Soulseek blank (downloads forward to master)
        self.assertEqual(cfg["slsk_username"], "")

    def test_standalone_role(self):
        cfg = first_run.build_initial_config(
            "standalone", slsk_username="u", **self.BASE)
        self.assertTrue(cfg["is_master"])
        self.assertEqual(cfg["device_role"], "standalone")
        self.assertEqual(cfg["master_url"], "")
        self.assertEqual(cfg["slsk_username"], "u")

    def test_unknown_role_raises(self):
        with self.assertRaises(ValueError):
            first_run.build_initial_config("overlord", **self.BASE)

    def test_missing_required_paths_raises(self):
        with self.assertRaises(ValueError):
            first_run.build_initial_config("master", music_library_path="", downloads_path="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
