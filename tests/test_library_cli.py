# SPDX-License-Identifier: Apache-2.0

import io
import json
import subprocess
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from hvclient import (
    AsyncHargassnerClient,
    DailySchedule,
    TimeRange,
    ValidationError,
    cli,
)
from hvclient.models import WriteResult, parameter_from_json
from tests.library_support import mode_catalog, schedule_catalog


class CLIAndConfigurationTests(unittest.TestCase):
    def test_distribution_declares_apache_and_ships_provenance_notice(self):
        package = distribution("hargassner-hvclient")
        metadata = package.metadata
        self.assertEqual(metadata["License-Expression"], "Apache-2.0")
        self.assertEqual(set(metadata.get_all("License-File", [])), {"LICENSE", "LICENSING.md"})
        self.assertIn("## Licence", metadata.get_payload())
        self.assertIn("LICENSING.md", metadata.get_payload())
        notice = package.read_text("licenses/LICENSING.md")
        self.assertIsInstance(notice, str, "packaged provenance notice is missing")
        self.assertIn("## Agent-generated development", notice)
        self.assertIn("Apache License", notice)

    def test_distributed_readme_leads_with_capabilities_not_project_instructions(self):
        readme = distribution("hargassner-hvclient").metadata.get_payload()
        overview = readme.split("## Install", 1)[0]
        self.assertIn("## Capabilities", overview)
        for capability in ("Runtime channels", "Device information", "PCB diagnostics",
                           "11 USER temperatures", "6 heating-curve slopes",
                           "3 circuit-mode parameters", "6 daily/weekly comfort programs",
                           "Other USER parameters"):
            with self.subTest(capability=capability):
                self.assertIn(capability, overview)
        self.assertLess(readme.index("## Capabilities"), readme.index("## Quick start"))
        self.assertLess(readme.index("## Quick start"), readme.index("## Licence"))
        self.assertIn("## API at a glance", readme)
        self.assertNotIn("## Publication privacy", readme)
        self.assertNotIn("## Copyright and enforceability", readme)
        self.assertNotIn("## Licence and development provenance", readme)

    def test_library_modules_carry_spdx_notices(self):
        import hvclient

        for path in Path(hvclient.__file__).parent.glob("*.py"):
            with self.subTest(module=path.name):
                self.assertTrue(path.read_text().startswith("# SPDX-License-Identifier: Apache-2.0\n"))

    def test_packaged_licensing_text_avoids_personal_biography_and_home_paths(self):
        import re

        package = distribution("hargassner-hvclient")
        self.assertTrue(
            all(not package.metadata.get(field) for field in
                ("Author", "Author-email", "Maintainer", "Maintainer-email")),
            "package identity/contact metadata present; details suppressed",
        )
        notice = package.read_text("licenses/LICENSING.md")
        self.assertIsInstance(notice, str, "packaged provenance notice is missing")
        biography = re.compile(
            r"\bmaintainer\b[^.\n]{0,80}\b(?:based|located|resides|lives)\b", re.IGNORECASE
        )
        home_path = re.compile(r"(?:/home/|/Users/|[a-z]:\\Users\\)[^/\\\s]+", re.IGNORECASE)
        for label, text in (("package README", package.metadata.get_payload()),
                            ("provenance notice", notice)):
            with self.subTest(document=label):
                self.assertFalse(bool(biography.search(text)), "personal location context; details suppressed")
                self.assertFalse(bool(home_path.search(text)), "private home path; details suppressed")
        self.assertIn("## Copyright and enforceability", notice)
        self.assertIn("## Publication privacy", notice)

    def test_hvctl_is_the_only_console_entry_point(self):
        scripts = {
            entry.name: entry.value
            for entry in distribution("hargassner-hvclient").entry_points
            if entry.group == "console_scripts"
        }
        self.assertEqual(scripts, {"hvctl": "hvclient.cli:main"})

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
                ["127.0.0.1", "info", "--apply"],
                ["127.0.0.1", "channels", "--full"],
                ["127.0.0.1", "pcb", "--allow-service-mode"],
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
            "assert callable(hvclient.AsyncHargassnerClient.async_get_channels); "
            "assert callable(hvclient.AsyncHargassnerClient.async_get_info); "
            "assert callable(hvclient.AsyncHargassnerClient.async_get_pcb); "
            "assert not hasattr(hvclient.AsyncHargassnerClient, 'async_set_channels'); "
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

    def test_snapshot_cli_commands_only_call_read_methods_with_readonly_policy(self):
        for command, method, document in (
            ("channels", "async_get_channels", {"example.channel": 0}),
            ("info", "async_get_info", {"softwareVersion": "test"}),
            ("pcb", "async_get_pcb", {"internalTemperature": 20, "internalVoltage": 24}),
        ):
            with self.subTest(command=command):
                result = unittest.mock.Mock()
                result.to_dict.return_value = document
                instance = AsyncMock()
                instance.__aenter__.return_value = instance
                getattr(instance, method).return_value = result
                with (
                    patch("hvclient.cli.AsyncHargassnerClient", return_value=instance) as constructor,
                    redirect_stdout(io.StringIO()) as out,
                ):
                    code = cli.main(["127.0.0.1", command])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out.getvalue()), document)
                getattr(instance, method).assert_awaited_once_with()
                self.assertFalse(constructor.call_args.kwargs["write_policy"].allowed_names)
                instance.async_set_parameter.assert_not_awaited()
                instance.async_preview_parameter_write.assert_not_awaited()

    def test_mode_cli_is_thin_and_preserves_zero_id_and_expected(self):
        before = parameter_from_json(mode_catalog()["A1z"])
        after = parameter_from_json(dict(mode_catalog()["A1z"], value={"id": 0, "name": "Off"}))
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_set_parameter.return_value = WriteResult(
            "A1z", 0, before, after, after, "verified", 200
        )
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance) as constructor,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["127.0.0.1", "set", "A1z", "0", "--apply", "--expect", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["requested"], 0)
        self.assertTrue(json.loads(out.getvalue())["read_back_matches"])
        self.assertEqual(constructor.call_args.kwargs["write_policy"].allowed_names, {"A1z"})
        instance.async_set_parameter.assert_awaited_once_with("A1z", 0, expected=3)

    def test_mode_cli_preview_prints_configured_circuit_context_without_write(self):
        parameter = parameter_from_json(mode_catalog()["A1z"])
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_preview_parameter_write.return_value = Namespace(
            parameter=parameter, requested=1, circuit_type_id=2
        )
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["127.0.0.1", "set", "A1z", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["circuit_type_id"], 2)
        instance.async_set_parameter.assert_not_awaited()

    def test_mode_cli_expected_zero_is_not_missing(self):
        before = parameter_from_json(dict(mode_catalog()["A1z"], value={"id": 0, "name": "Off"}))
        after = parameter_from_json(dict(mode_catalog()["A1z"], value={"id": 1, "name": "Auto"}))
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_set_parameter.return_value = WriteResult(
            "A1z", 1, before, after, after, "verified", 200
        )
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(["127.0.0.1", "set", "A1z", "1", "--apply", "--expect", "0"]), 0)
        instance.async_set_parameter.assert_awaited_once_with("A1z", 1, expected=0)

    def test_schedule_cli_accepts_json_program_and_expected_arrays(self):
        current = schedule_catalog()["3"]
        value = [{"start": "07:00", "end": "09:00"}]
        requested = DailySchedule((TimeRange("07:00", "09:00"),))
        before = parameter_from_json(current)
        after = parameter_from_json(dict(current, value=requested.to_json()))
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.async_set_parameter.return_value = WriteResult(
            "3", requested, before, after, after, "verified", 200
        )
        with (
            patch("hvclient.cli.AsyncHargassnerClient", return_value=instance),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["127.0.0.1", "set", "3", json.dumps(value),
                             "--apply", "--expect", json.dumps(current["value"])])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["requested"], requested.to_json())
        instance.async_set_parameter.assert_awaited_once_with("3", value, expected=current["value"])
