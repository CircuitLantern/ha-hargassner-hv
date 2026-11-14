# SPDX-License-Identifier: Apache-2.0
"""Private bundle staging with a synthetic SDK wheel, never a live appliance."""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_ha_dist", Path(__file__).resolve().parents[1] / "tools" / "prepare_ha_dist.py",
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def bundle_source(tmp_path, monkeypatch):
    component = tmp_path / "custom_components" / "hargassner_hv"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(json.dumps({
        "version": "0.2.0", "requirements": ["hargassner-hvclient==0.1.0"],
    }))
    (component / "sensor.py").write_text("# synthetic component\n")
    (component / "__pycache__").mkdir()
    (component / "__pycache__" / "private.pyc").write_bytes(b"excluded")
    (component / "wheels").mkdir()
    (component / "wheels" / "old.whl").write_bytes(b"excluded")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "hargassner-hvclient"\nversion = "0.1.0"\n',
    )
    for name in ("LICENSE", "LICENSING.md"):
        (tmp_path / name).write_text("Synthetic licensing\n")

    def build(command, check):
        assert check
        assert command[1:5] == ["-m", "build", "--wheel", "--no-isolation"]
        destination = Path(command[command.index("--outdir") + 1])
        destination.mkdir()
        with zipfile.ZipFile(destination / "hargassner_hvclient-0.1.0-py3-none-any.whl", "w") as wheel:
            wheel.writestr("hvclient/__init__.py", "# synthetic SDK\n")

    monkeypatch.setattr(builder.subprocess, "run", build)
    return tmp_path


def test_bundle_has_local_wheel_checksums_and_unchanged_source(bundle_source):
    source = bundle_source / "custom_components" / "hargassner_hv" / "manifest.json"
    original = source.read_bytes()
    archive = builder.prepare(bundle_source)
    bundle = archive.with_suffix("")
    manifest = json.loads((bundle / "custom_components/hargassner_hv/manifest.json").read_text())
    assert manifest["requirements"] == [
        ("hargassner-hvclient @ file:///config/custom_components/hargassner_hv/wheels/"
         "hargassner_hvclient-0.1.0-py3-none-any.whl"),
    ]
    assert source.read_bytes() == original
    for line in (bundle / "SHA256SUMS").read_text().splitlines():
        if line.startswith("#"):
            continue
        checksum, relative = line.split("  ", 1)
        assert checksum == hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert all("__pycache__" not in name and "old.whl" not in name for name in package.namelist())
        assert "INSTALL.md" in package.namelist()
        assert package.read("INSTALL.md").decode().startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->\n")
    assert archive.with_suffix(".zip.sha256").read_text().startswith(builder.digest(archive))


def test_repeat_build_requires_replace_and_preserves_unrelated_assets(bundle_source):
    archive = builder.prepare(bundle_source)
    unrelated = bundle_source / "dist" / "other.txt"
    unrelated.write_text("Keep this")
    with pytest.raises(ValueError, match="--replace"):
        builder.prepare(bundle_source)
    assert builder.prepare(bundle_source, replace=True) == archive
    assert unrelated.read_text() == "Keep this"


def test_refuses_unmarked_replacement_and_symlinks(bundle_source):
    archive = builder.prepare(bundle_source)
    (archive.with_suffix("") / builder.MARKER).write_text("Not this script\n")
    with pytest.raises(ValueError, match="not marked"):
        builder.prepare(bundle_source, replace=True)
    archive.unlink()
    archive.symlink_to(bundle_source / "LICENSE")
    with pytest.raises(ValueError, match="symlink"):
        builder.prepare(bundle_source, replace=True)


def test_refuses_sdk_pin_mismatch_before_build(bundle_source):
    (bundle_source / "pyproject.toml").write_text(
        '[project]\nname = "hargassner-hvclient"\nversion = "0.9.0"\n',
    )
    with pytest.raises(ValueError, match="pin"):
        builder.prepare(bundle_source)
