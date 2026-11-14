# SPDX-License-Identifier: Apache-2.0
"""Verify the standalone distribution builds the sole bundled SDK source tree."""

import subprocess
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "custom_components/hargassner_hv/hvclient"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    destination = tmp_path_factory.mktemp("sdk-distributions")
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(destination), str(ROOT)],
        check=True, capture_output=True, text=True,
    )
    return destination


def test_wheel_contains_only_sdk_code_and_matches_canonical_sources(distributions):
    with zipfile.ZipFile(next(distributions.glob("*.whl"))) as wheel:
        assert wheel.testzip() is None
        names = wheel.namelist()
        assert all(name.startswith(("hvclient/", "hargassner_hvclient-")) for name in names)
        assert not any(name.startswith(("custom_components/", "homeassistant/", "tests/")) for name in names)
        expected = {path.name for path in SDK.glob("*.py")} | {"py.typed"}
        assert {name.removeprefix("hvclient/") for name in names if name.startswith("hvclient/")} == expected
        for name in expected:
            assert wheel.read("hvclient/" + name) == (SDK / name).read_bytes()
        info = next(name for name in names if name.endswith(".dist-info/METADATA"))
        prefix = info.removesuffix("METADATA")
        metadata = BytesParser().parsebytes(wheel.read(info))
        assert metadata["License-Expression"] == "Apache-2.0"
        assert not any("homeassistant" in value.casefold() for value in metadata.get_all("Requires-Dist", []))
        assert wheel.read(prefix + "licenses/LICENSE") == (ROOT / "LICENSE").read_bytes()
        assert wheel.read(prefix + "licenses/LICENSING.md") == (ROOT / "LICENSING.md").read_bytes()
        entry_points = wheel.read(prefix + "entry_points.txt").decode()
        assert entry_points.strip() == "[console_scripts]\nhvctl = hvclient.cli:main"


def test_sdist_contains_sdk_not_adapter_or_ha_tests(distributions):
    with tarfile.open(next(distributions.glob("*.tar.gz"))) as archive:
        members = archive.getnames()
        prefix = members[0].split("/")[0] + "/"
        names = {name.removeprefix(prefix) for name in members}
        assert "custom_components/hargassner_hv/hvclient/client.py" in names
        assert "custom_components/hargassner_hv/climate.py" not in names
        assert not any(name.startswith("tests/ha/") for name in names)
        assert not any(name.startswith("src/hvclient/") for name in names)
        assert archive.extractfile(prefix + "LICENSE").read() == (ROOT / "LICENSE").read_bytes()
        assert archive.extractfile(prefix + "custom_components/hargassner_hv/hvclient/client.py").read() == (SDK / "client.py").read_bytes()
        assert all(member.uname == "root" and member.gname == "root" for member in archive.getmembers())


def test_standalone_sdk_import_does_not_load_home_assistant():
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; import hvclient; assert not any(name == 'homeassistant' or "
         "name.startswith(('homeassistant.', 'custom_components.')) for name in sys.modules); "
         "assert hvclient.AsyncHargassnerClient.__module__ == 'hvclient.client'"],
        check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
