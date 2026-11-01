"""Offline regression tests for issue 3; load the complete real CLI module.

Fixtures are synthetic Node contract projections, not live API captures.
Run with the project's supported Python and its real declared dependencies.
"""

from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch


CLI_PATH = Path(__file__).resolve().parents[1] / "smugmug_downloader" / "cli.py"


class FolderTraversalTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temporary = Path(stack.enter_context(TemporaryDirectory()))
        old_cwd = Path.cwd()
        stack.callback(os.chdir, old_cwd)
        os.chdir(temporary)
        stack.enter_context(patch.dict(os.environ, {
            "SMUGMUG_API_KEY": "unit-test-not-a-real-key",
            "SMUGMUG_API_SECRET": "unit-test-not-a-real-secret",
            "HOME": str(temporary),
        }, clear=True))
        stack.enter_context(patch.object(Path, "home", return_value=temporary))

        # These patch targets import REAL dependencies, never fabricated modules.
        # Block transport and OAuth construction before executing the CLI module.
        stack.enter_context(patch(
            "requests.sessions.Session.send",
            side_effect=AssertionError("Network transport forbidden in unit tests"),
        ))
        stack.enter_context(patch(
            "requests_oauthlib.OAuth1Session.__init__",
            side_effect=AssertionError("OAuth construction forbidden in unit tests"),
        ))
        spec = importlib.util.spec_from_file_location("_smugmug_cli_under_test", CLI_PATH)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        # No insertion into sys.modules: no cached CLI module leaks between tests.
        stack.enter_context(patch.object(
            cli, "authenticate", side_effect=AssertionError("Authentication forbidden"),
        ))
        stack.enter_context(patch.object(
            cli, "load_tokens", side_effect=AssertionError("Token loading forbidden"),
        ))
        session = Mock(spec=["get"])
        session.get.side_effect = AssertionError("Session requests forbidden")
        self.downloader = cli.SmugMugDownloader(session, "downloads")
        self.progress = Mock(spec=["update"])
        self.download_album = stack.enter_context(patch.object(
            self.downloader, "download_album",
        ))
        self.get_children = stack.enter_context(patch.object(
            self.downloader, "get_node_children",
        ))

    def traverse(self):
        self.downloader.traverse_and_download(
            "/api/v2/node/root", Path("downloads/test-user"), self.progress, 7,
        )

    def test_folder_top_level_uri_reaches_album_in_folder(self):
        # Old Uris.Node lookup silently skips this folder and loses its album.
        self.get_children.side_effect = {
            "/api/v2/node/root": [{
                "Type": "Folder", "Name": "Trips", "UrlName": "Trips",
                "Uri": "/api/v2/node/trips", "Uris": {},
            }],
            "/api/v2/node/trips": [{
                "Type": "Album", "Name": "Summer", "UrlName": "Summer",
                "Uri": "/api/v2/node/summer",
                "Uris": {"Album": {"Uri": "/api/v2/album/summer"}},
            }],
        }.__getitem__
        self.traverse()
        self.download_album.assert_called_once_with(
            "/api/v2/album/summer", Path("downloads/test-user/Trips/Summer"),
            "Summer", self.progress,
        )
        self.assertEqual(self.get_children.call_args_list, [
            call("/api/v2/node/root"), call("/api/v2/node/trips"),
        ])

    def test_multiple_folder_levels_preserve_album_destination(self):
        self.get_children.side_effect = {
            "/api/v2/node/root": [{
                "Type": "Folder", "Name": "Trips", "UrlName": "Trips",
                "Uri": "/api/v2/node/trips", "Uris": {},
            }],
            "/api/v2/node/trips": [{
                "Type": "Folder", "Name": "2026", "UrlName": "2026",
                "Uri": "/api/v2/node/year", "Uris": {},
            }],
            "/api/v2/node/year": [{
                "Type": "Album", "Name": "Summer", "UrlName": "Summer",
                "Uri": "/api/v2/node/summer",
                "Uris": {"Album": {"Uri": "/api/v2/album/summer"}},
            }],
        }.__getitem__
        self.traverse()
        self.download_album.assert_called_once_with(
            "/api/v2/album/summer", Path("downloads/test-user/Trips/2026/Summer"),
            "Summer", self.progress,
        )
        self.assertEqual(self.get_children.call_args_list, [
            call("/api/v2/node/root"), call("/api/v2/node/trips"),
            call("/api/v2/node/year"),
        ])

    def test_top_level_album_uses_album_resource_not_node_uri(self):
        self.get_children.return_value = [{
            "Type": "Album", "Name": "Portraits", "UrlName": "Portraits",
            "Uri": "/api/v2/node/portraits",
            "Uris": {"Album": {"Uri": "/api/v2/album/portraits"}},
        }]
        self.traverse()
        self.download_album.assert_called_once_with(
            "/api/v2/album/portraits", Path("downloads/test-user/Portraits"),
            "Portraits", self.progress,
        )
        self.get_children.assert_called_once_with("/api/v2/node/root")

    def test_folder_without_uri_is_skipped(self):
        self.get_children.side_effect = {
            "/api/v2/node/root": [{
                "Type": "Folder", "Name": "No URI", "UrlName": "No-URI",
                "Uris": {},
            }],
        }.__getitem__
        self.traverse()
        self.get_children.assert_called_once_with("/api/v2/node/root")
        self.download_album.assert_not_called()

    def test_folder_with_empty_uri_is_skipped(self):
        for uri in (None, ""):
            with self.subTest(uri=uri):
                self.get_children.reset_mock()
                self.get_children.side_effect = {
                    "/api/v2/node/root": [{
                        "Type": "Folder", "Name": "Empty", "UrlName": "Empty",
                        "Uri": uri, "Uris": {},
                    }],
                }.__getitem__
                self.traverse()
                self.get_children.assert_called_once_with("/api/v2/node/root")
                self.download_album.assert_not_called()


if __name__ == "__main__":
    unittest.main()
