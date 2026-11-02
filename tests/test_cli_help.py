"""Offline CLI startup checks against the complete real module and Typer.

All credentials are synthetic; home/cwd are temporary and network/token paths
are blocked. No live SmugMug API or installed console script is exercised.
"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import typer
from typer.testing import CliRunner


CLI_PATH = Path(__file__).resolve().parents[1] / 'smugmug_downloader' / 'cli.py'
KEY = {'SMUGMUG_API_KEY': 'synthetic-test-key'}
SECRET = {'SMUGMUG_API_SECRET': 'synthetic-test-secret'}


class CLIHelpTests(unittest.TestCase):
    def invoke(self, credentials, arguments):
        with ExitStack() as stack:
            temporary = Path(stack.enter_context(TemporaryDirectory()))
            previous = Path.cwd()
            stack.callback(os.chdir, previous)
            os.chdir(temporary)
            stack.enter_context(patch.dict(os.environ, {
                'HOME': str(temporary), 'NO_COLOR': '1', 'TERM': 'dumb',
                'COLUMNS': '160', **credentials,
            }, clear=True))
            stack.enter_context(patch.object(Path, 'home', return_value=temporary))
            transport = stack.enter_context(patch(
                'requests.sessions.Session.send',
                side_effect=AssertionError('Network transport forbidden'),
            ))
            oauth = stack.enter_context(patch(
                'requests_oauthlib.OAuth1Session.__init__',
                side_effect=AssertionError('OAuth construction forbidden'),
            ))
            spec = importlib.util.spec_from_file_location('_smug_cli_help_test', CLI_PATH)
            cli = importlib.util.module_from_spec(spec)
            capture = io.StringIO()
            try:
                with redirect_stdout(capture), redirect_stderr(capture):
                    spec.loader.exec_module(cli)
            except typer.Abort:
                # Turn the pre-parser abort into an observed command failure,
                # so RED is a behavioral assertion rather than a setup error.
                result = SimpleNamespace(exit_code=1, output=capture.getvalue(),
                                         imported=False, exception='import-time Abort')
            else:
                tokens = stack.enter_context(patch.object(
                    cli, 'load_tokens', side_effect=AssertionError('Token loading forbidden'),
                ))
                result = CliRunner().invoke(cli.app, arguments)
                result.imported = True
                tokens.assert_not_called()
            transport.assert_not_called()
            oauth.assert_not_called()
            return result

    def assert_help(self, credentials):
        result = self.invoke(credentials, ['--help'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(result.imported)
        self.assertIn('Usage:', result.output)
        self.assertIn('--output', result.output)
        self.assertNotIn('Set SMUGMUG_API_KEY', result.output)

    def test_help_without_credentials(self):
        self.assert_help({})

    def test_help_without_secret(self):
        self.assert_help(KEY)

    def test_help_without_key(self):
        self.assert_help(SECRET)

    def test_help_with_synthetic_credentials(self):
        self.assert_help({**KEY, **SECRET})

    def test_actual_download_still_requires_both_credentials(self):
        for credentials in ({}, KEY, SECRET):
            with self.subTest(credentials=list(credentials)):
                result = self.invoke(credentials, ['synthetic-user'])
                self.assertNotEqual(0, result.exit_code)
                self.assertIn('Set SMUGMUG_API_KEY and SMUGMUG_API_SECRET', result.output)
                self.assertNotIn('Token loading forbidden', result.output)
                self.assertNotIn('OAuth construction forbidden', result.output)


if __name__ == '__main__':
    unittest.main()
