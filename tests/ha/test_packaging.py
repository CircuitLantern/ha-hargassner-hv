# SPDX-License-Identifier: Apache-2.0
"""Import an installed component in a fresh process with no checkout/SDK fallback."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("poison_external_sdk", [False, True])
def test_component_only_import_ignores_external_sdk(tmp_path, poison_external_sdk):
    source = ROOT / "custom_components/hargassner_hv"
    component = tmp_path / "custom_components/hargassner_hv"
    shutil.copytree(source, component, ignore=shutil.ignore_patterns(
        "__pycache__", "*.egg-info", "*.pyc", ".pytest_cache", "wheels",
    ))
    script = r'''
import importlib
import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import homeassistant

config = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(config))
try:
    importlib.metadata.distribution("hargassner-hvclient")
except importlib.metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError("Isolation test needs an environment without the standalone SDK")
assert importlib.util.find_spec("hvclient") is None
if sys.argv[2] == "True":
    # Any accidentally absolute import must fail instead of silently using a
    # globally installed or cached SDK from the former packaging arrangement.
    sys.modules["hvclient"] = ModuleType("hvclient")
integration = importlib.import_module("custom_components.hargassner_hv")
sdk = importlib.import_module("custom_components.hargassner_hv.hvclient")
assert Path(sdk.__file__).resolve().is_relative_to(config)
assert integration.AsyncHargassnerClient is sdk.AsyncHargassnerClient
for name in ("climate", "config_flow", "coordinator", "diagnostics", "entity",
             "identity", "number", "select", "sensor", "services", "telemetry"):
    module = importlib.import_module("custom_components.hargassner_hv." + name)
    assert Path(module.__file__).resolve().is_relative_to(config)
assert "hvclient" not in sys.modules or sys.argv[2] == "True"
assert (config / "custom_components/hargassner_hv/LICENSE").is_file()
assert (config / "custom_components/hargassner_hv/LICENSING.md").is_file()
'''
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(tmp_path), str(poison_external_sdk)],
        check=False, cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_component_licenses_and_sdk_ownership():
    component = ROOT / "custom_components/hargassner_hv"
    assert (component / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert "agent" in (component / "LICENSING.md").read_text().casefold()
    assert not (ROOT / "src/hvclient").exists()
