import io
import json
import subprocess
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from hvclient import AsyncHargassnerClient, ValidationError, cli


class CLIAndConfigurationTests(unittest.TestCase):
    def test_constructor_does_not_create_session_or_perform_io(self):
        with patch("hvclient.client.aiohttp.ClientSession") as session:
            client = AsyncHargassnerClient("192.0.2.10")
            self.assertIsNone(client._session)
            session.assert_not_called()

    def test_invalid_configuration(self):
        for kwargs in ({"host": "http://127.0.0.1"}, {"host": "user@127.0.0.1"},
                       {"host": "127.0.0.1:80"}, {"host": ""}, {"port": 0},
                       {"port": True}, {"timeout": False}, {"timeout": float("nan")},
                       {"read_retries": 4}, {"authentication_attempts": 0},
                       {"max_response_bytes": 0}):
            args = {"host": "127.0.0.1", **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AsyncHargassnerClient(**args)

    def test_cli_errors_and_service_flags_cannot_connect(self):
        with patch("hvclient.cli.AsyncHargassnerClient") as client:
            for args in (
                ["127.0.0.1", "set", "4", "21", "--apply"],
                ["127.0.0.1", "set", "4", "true"],
                ["127.0.0.1", "set", "4", "NaN"],
                ["127.0.0.1", "set", "4", "21", "--full"],
                ["127.0.0.1", "--allow-service-mode", "list"],
                ["127.0.0.1", "set", "4", "21", "--app"],
            ):
                with self.subTest(args=args), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        cli.main(args)
                    self.assertEqual(caught.exception.code, 2)
            client.assert_not_called()

    def test_cli_only_calls_library_and_is_read_only_for_get(self):
        parameter = unittest.mock.Mock()
        parameter.to_dict.return_value = {"name": "4", "value": 20.5}
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_get_parameters.return_value = {"4": parameter}
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance) as constructor,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["127.0.0.1", "get", "4"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"parameters": [{"name": "4", "value": 20.5}]})
        instance.async_get_parameters.assert_awaited_once_with(["4"])
        self.assertFalse(constructor.call_args.kwargs["write_policy"].allowed_names)
        instance.async_set_parameter.assert_not_awaited()

    def test_cli_set_is_preview_unless_explicitly_applied(self):
        parameter = unittest.mock.Mock()
        parameter.to_dict.return_value = {"name": "4", "value": 20.5}
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_preview_parameter_write.return_value = Namespace(
            requested=21.0, parameter=parameter
        )
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["127.0.0.1", "set", "4", "21"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["outcome"], "dry_run")
        instance.async_preview_parameter_write.assert_awaited_once_with("4", 21)
        instance.async_set_parameter.assert_not_awaited()

    def test_cli_propagates_library_validation_as_refusal(self):
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_preview_parameter_write.side_effect = ValidationError("inactive")
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance),
            redirect_stderr(io.StringIO()) as err,
        ):
            code = cli.main(["127.0.0.1", "set", "4", "21"])
        self.assertEqual(code, 2)
        self.assertIn("inactive", err.getvalue())

    def test_import_and_module_cli_outside_repository(self):
        code = (
            "import hvclient,hvclient.__main__,sys; "
            "assert 'homeassistant' not in sys.modules; "
            "assert 'tools.probe' not in sys.modules; "
            "assert not hasattr(hvclient.AsyncHargassnerClient, 'async_get_channels'); "
            "print(hvclient.AsyncHargassnerClient.__name__)"
        )
        # Prefer the harness-approved scratch area when present; stay portable.
        parent = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
        with TemporaryDirectory(prefix="hvclient-import-", dir=parent) as cwd:
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=cwd,
                                    check=True, capture_output=True, text=True)
            self.assertIn("AsyncHargassnerClient", result.stdout)
            result = subprocess.run([sys.executable, "-B", "-m", "hvclient", "--help"],
                                    cwd=cwd, check=True, capture_output=True, text=True)
            self.assertIn("daqOnly=true", result.stdout)
