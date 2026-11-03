"""Reject unsafe or looping page navigation before a real signed request leaves.

OAuth credentials are synthetic. A local Requests adapter records prepared
requests; real HTTP transport, sockets, token reads and authentication are blocked.
"""

from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter
from requests_oauthlib import OAuth1Session


CLI_PATH = Path(__file__).resolve().parents[1] / "smugmug_downloader" / "cli.py"
KINDS = (("Node", "get_node_children", "/api/v2/node/synthetic!children"),
         ("AlbumImage", "get_album_images", "/api/v2/album/synthetic!images"))


class OfflineAdapter(BaseAdapter):
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        if len(self.calls) > 6:
            raise RuntimeError("Offline test request limit reached")
        status, payload, headers = self.handler(request, len(self.calls))
        response = requests.Response()
        response.status_code = status
        response.request = request
        response.url = request.url
        response.headers.update(headers)
        response._content = json.dumps(payload).encode()
        return response

    def close(self):
        pass


class PaginationBoundaryTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temporary = Path(stack.enter_context(TemporaryDirectory()))
        previous = Path.cwd()
        stack.callback(os.chdir, previous)
        os.chdir(temporary)
        stack.enter_context(patch.dict(os.environ, {
            "HOME": str(temporary), "SMUGMUG_API_KEY": "synthetic-key",
            "SMUGMUG_API_SECRET": "synthetic-secret",
        }, clear=True))
        stack.enter_context(patch.object(Path, "home", return_value=temporary))
        stack.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network forbidden"),
        ))
        stack.enter_context(patch.object(
            requests.adapters.HTTPAdapter, "send",
            side_effect=AssertionError("Live HTTP transport forbidden"),
        ))
        spec = importlib.util.spec_from_file_location("_smug_boundary_test", CLI_PATH)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        stack.enter_context(patch.object(
            self.cli, "load_tokens", side_effect=AssertionError("Token reads forbidden"),
        ))
        stack.enter_context(patch.object(
            self.cli, "authenticate", side_effect=AssertionError("Authentication forbidden"),
        ))

    def collect(self, kind, handler):
        _, method, endpoint = kind
        adapter = OfflineAdapter(handler)
        session = OAuth1Session(
            "synthetic-consumer", client_secret="synthetic-consumer-secret",
            resource_owner_key="synthetic-token",
            resource_owner_secret="synthetic-token-secret",
        )
        self.addCleanup(session.close)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        downloader = self.cli.SmugMugDownloader(session, "downloads")
        value = error = None
        try:
            value = getattr(downloader, method)(endpoint.split("!")[0])
        except Exception as caught:
            error = caught
        return value, error, adapter.calls

    def test_untrusted_next_page_is_rejected_before_signing_another_request(self):
        invalid = (
            "https://outside.invalid/collect", "//outside.invalid/collect",
            "http://api.smugmug.com/api/v2/page",
            "https://api.smugmug.com.outside.invalid/collect",
            "https://api.smugmug.com@outside.invalid/collect",
            "https://user@api.smugmug.com/api/v2/page",
            "https://api.smugmug.com:444/api/v2/page",
            "https://api.smugmug.com:invalid/api/v2/page",
            17, True, False, {}, [], {"Uri": "/api/v2/page"},
        )
        for kind in KINDS:
            for next_page in invalid:
                with self.subTest(kind=kind[0], next_page=next_page):
                    def handler(request, number):
                        payload = {kind[0]: [{"ID": "first"}]}
                        if number == 1:
                            payload["Pages"] = {"NextPage": next_page}
                        return 200, {"Response": payload}, {}
                    _, error, calls = self.collect(kind, handler)
                    self.assertIsInstance(error, ValueError)
                    self.assertEqual(1, len(calls))

    def test_valid_relative_and_same_origin_links_preserve_query(self):
        for kind in KINDS:
            path = kind[2] + "?count=2&start=3&filter=https%3A%2F%2Foutside.invalid"
            for link in (path, "https://api.smugmug.com" + path,
                         "//api.smugmug.com" + path,
                         "https://API.SMUGMUG.COM:443" + path):
                with self.subTest(kind=kind[0], link=link):
                    def handler(request, number):
                        payload = {kind[0]: [{"ID": str(number)}]}
                        if number == 1:
                            payload["Pages"] = {"NextPage": link}
                        return 200, {"Response": payload}, {}
                    value, error, calls = self.collect(kind, handler)
                    self.assertIsNone(error)
                    self.assertEqual([{"ID": "1"}, {"ID": "2"}], value)
                    actual = urlsplit(calls[1].url)
                    self.assertEqual("https", actual.scheme)
                    self.assertEqual("api.smugmug.com", actual.hostname)
                    self.assertIn(actual.port, (None, 443))
                    self.assertEqual(urlsplit(path).query, actual.query)
                    self.assertIn("Authorization", calls[1].headers)

    def test_repeated_next_page_stops_before_a_duplicate_request(self):
        for kind in KINDS:
            with self.subTest(kind=kind[0]):
                next_page = kind[2] + "?count=100&start=101"
                def handler(request, number):
                    return 200, {"Response": {kind[0]: [{"ID": str(number)}],
                        "Pages": {"NextPage": next_page}}}, {}
                _, error, calls = self.collect(kind, handler)
                self.assertIsInstance(error, ValueError)
                self.assertEqual(2, len(calls))

    def test_cycle_and_equivalent_absolute_link_stop_before_repeat(self):
        for kind in KINDS:
            first = kind[2] + "?count=100&start=101"
            second = kind[2] + "?count=100&start=201"
            for links, expected_count in (
                ([first, second, first], 3),
                ([first, "https://API.SMUGMUG.COM:443" + first + "#ignored"], 2),
                ([kind[2] + "?count=100"], 1),
            ):
                with self.subTest(kind=kind[0], links=links):
                    def handler(request, number):
                        return 200, {"Response": {kind[0]: [], "Pages": {
                            "NextPage": links[(number - 1) % len(links)],
                        }}}, {}
                    _, error, calls = self.collect(kind, handler)
                    self.assertIsInstance(error, ValueError)
                    self.assertEqual(expected_count, len(calls))

    def test_page_redirects_are_not_followed(self):
        for kind in KINDS:
            for status in (301, 302, 303, 307, 308):
                for location in ("https://outside.invalid/collect",
                                 "http://api.smugmug.com/api/v2/page",
                                 "/api/v2/redirected"):
                    for redirect_at in (1, 2):
                        with self.subTest(kind=kind[0], status=status,
                                          location=location, redirect_at=redirect_at):
                            def handler(request, number):
                                if number == redirect_at:
                                    return status, {}, {"Location": location}
                                payload = {kind[0]: []}
                                if number == 1:
                                    payload["Pages"] = {"NextPage": kind[2] + "?start=101"}
                                return 200, {"Response": payload}, {}
                            _, error, calls = self.collect(kind, handler)
                            self.assertIsInstance(error, ValueError)
                            self.assertEqual(redirect_at, len(calls))


if __name__ == "__main__":
    unittest.main()
