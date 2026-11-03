"""Offline paging regressions using the complete CLI and real dependencies.

Synthetic responses follow the documented one-based paging contract:
https://api.smugmug.com/api/v2/doc/tutorial/paging.html
No API, credentials, OAuth flow, or user files are accessed.
"""

from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests


CLI_PATH = Path(__file__).resolve().parents[1] / "smugmug_downloader" / "cli.py"


class PagedSession:
    """Serve stable synthetic records under SmugMug's documented page rules."""

    def __init__(self, locator, records, maximum=100, absolute_links=False):
        self.locator = locator
        self.records = records
        self.maximum = maximum
        self.absolute_links = absolute_links
        self.requests = []

    def get(self, url, headers=None, params=None, allow_redirects=True):
        prepared = requests.Request("GET", url, params=params).prepare()
        parsed = urlsplit(prepared.url)
        if parsed.scheme != "https" or parsed.netloc != "api.smugmug.com":
            raise AssertionError("Unexpected API destination")
        query = parse_qs(parsed.query)
        start = int(query.get("start", ["1"])[0])
        count = min(int(query.get("count", ["100"])[0]), self.maximum)
        self.requests.append(prepared.url)
        if len(self.requests) > 20:
            raise AssertionError("Paging did not terminate")
        selected = self.records[start - 1:start - 1 + count]
        pages = {
            "Start": start, "Count": len(selected),
            "RequestedCount": count, "Total": len(self.records),
        }
        if start - 1 + count < len(self.records):
            prefix = "https://api.smugmug.com" if self.absolute_links else ""
            pages["NextPage"] = f"{prefix}{parsed.path}?count={count}&start={start + count}"
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response._content = json.dumps({
            "Code": 200, "Message": "Ok",
            "Response": {self.locator: selected, "Pages": pages},
        }).encode()
        return response


class PaginationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temporary = Path(stack.enter_context(TemporaryDirectory()))
        previous = Path.cwd()
        stack.callback(os.chdir, previous)
        os.chdir(temporary)
        stack.enter_context(patch.dict(os.environ, {
            "HOME": str(temporary),
            "SMUGMUG_API_KEY": "synthetic-test-key",
            "SMUGMUG_API_SECRET": "synthetic-test-secret",
        }, clear=True))
        stack.enter_context(patch.object(Path, "home", return_value=temporary))
        stack.enter_context(patch(
            "requests.sessions.Session.send",
            side_effect=AssertionError("Network transport forbidden"),
        ))
        stack.enter_context(patch(
            "requests_oauthlib.OAuth1Session.__init__",
            side_effect=AssertionError("OAuth construction forbidden"),
        ))
        spec = importlib.util.spec_from_file_location("_smug_pagination_test", CLI_PATH)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        stack.enter_context(patch.object(
            self.cli, "load_tokens", side_effect=AssertionError("Token reads forbidden"),
        ))
        stack.enter_context(patch.object(
            self.cli, "authenticate", side_effect=AssertionError("Authentication forbidden"),
        ))

    def collect(self, locator, records, maximum=100, absolute_links=False):
        session = PagedSession(locator, records, maximum, absolute_links)
        downloader = self.cli.SmugMugDownloader(session, "downloads")
        if locator == "Node":
            result = downloader.get_node_children("/api/v2/node/synthetic")
        else:
            result = downloader.get_album_images("/api/v2/album/synthetic")
        return result, session

    def assert_complete_pages(self, locator, maximum=100, absolute_links=False):
        records = [{"ID": str(index)} for index in range(1, 206)]
        result, session = self.collect(locator, records, maximum, absolute_links)
        self.assertEqual(records, result)
        self.assertEqual(3 if maximum == 100 else 5, len(session.requests))

    def test_node_pages_do_not_repeat_boundary_record(self):
        self.assert_complete_pages("Node")

    def test_image_pages_do_not_repeat_boundary_record(self):
        self.assert_complete_pages("AlbumImage")

    def test_node_pages_respect_server_clamped_page_size(self):
        self.assert_complete_pages("Node", maximum=50)

    def test_image_pages_respect_server_clamped_page_size(self):
        self.assert_complete_pages("AlbumImage", maximum=50)

    def test_absolute_api_next_page_links(self):
        for locator in ("Node", "AlbumImage"):
            with self.subTest(locator=locator):
                self.assert_complete_pages(locator, absolute_links=True)

    def test_one_page_and_empty_collections_stop_without_another_request(self):
        for locator in ("Node", "AlbumImage"):
            for records in ([], [{"ID": "only"}]):
                with self.subTest(locator=locator, records=records):
                    result, session = self.collect(locator, records)
                    self.assertEqual(records, result)
                    self.assertEqual(1, len(session.requests))

    def test_single_object_response_remains_a_list(self):
        for locator, method in (("Node", "get_node_children"),
                                ("AlbumImage", "get_album_images")):
            with self.subTest(locator=locator):
                downloader = self.cli.SmugMugDownloader(None, "downloads")
                with patch.object(downloader, "api_get", return_value={
                    "Response": {locator: {"ID": "only"}},
                }):
                    self.assertEqual([{"ID": "only"}], getattr(downloader, method)("/synthetic"))

    def test_album_not_found_preserves_previously_collected_images(self):
        downloader = self.cli.SmugMugDownloader(None, "downloads")
        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError(response=response)
        with patch.object(downloader, "api_get", side_effect=[{
            "Response": {"AlbumImage": [{"ID": "first"}], "Pages": {
                "NextPage": "/api/v2/album/synthetic!images?start=101&count=100",
            }},
        }, error]):
            self.assertEqual([{"ID": "first"}], downloader.get_album_images("/synthetic"))

    def test_other_http_errors_are_not_swallowed(self):
        for method, status in (("get_node_children", 404),
                               ("get_node_children", 500),
                               ("get_album_images", 500)):
            with self.subTest(method=method, status=status):
                downloader = self.cli.SmugMugDownloader(None, "downloads")
                response = requests.Response()
                response.status_code = status
                error = requests.HTTPError(response=response)
                with patch.object(downloader, "api_get", side_effect=error):
                    with self.assertRaises(requests.HTTPError) as caught:
                        getattr(downloader, method)("/synthetic")
                    self.assertIs(error, caught.exception)


if __name__ == "__main__":
    unittest.main()
