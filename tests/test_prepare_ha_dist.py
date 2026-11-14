# SPDX-License-Identifier: Apache-2.0
"""Private source-only bundle staging; no package installer or live appliance."""

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
def bundle_source(tmp_path):
    component = tmp_path / "custom_components" / "hargassner_hv"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(json.dumps({
        "version": "0.4.0", "requirements": [],
    }))
    (component / "sensor.py").write_text("# synthetic component\n")
    (component / "__pycache__").mkdir()
    (component / "__pycache__" / "private.pyc").write_bytes(b"excluded")
    (component / "wheels").mkdir()
    (component / "wheels" / "old.whl").write_bytes(b"excluded")
    (component / "hvclient").mkdir()
    (component / "hvclient/__init__.py").write_text("# synthetic SDK\n")
    (component / "hvclient/client.py").write_text("# synthetic client\n")
    (component / "synthetic.egg-info").mkdir()
    (component / "synthetic.egg-info/PKG-INFO").write_text("Excluded build metadata\n")
    for name in ("LICENSE", "LICENSING.md"):
        (tmp_path / name).write_text("Synthetic licensing\n")

    return tmp_path


def test_bundle_has_sdk_sources_checksums_and_unchanged_manifest(bundle_source):
    source = bundle_source / "custom_components" / "hargassner_hv" / "manifest.json"
    original = source.read_bytes()
    archive = builder.prepare(bundle_source)
    bundle = archive.with_suffix("")
    manifest = json.loads((bundle / "custom_components/hargassner_hv/manifest.json").read_text())
    assert manifest["requirements"] == []
    assert (bundle / "custom_components/hargassner_hv/manifest.json").read_bytes() == original
    assert (bundle / "custom_components/hargassner_hv/hvclient/client.py").read_text() == "# synthetic client\n"
    assert source.read_bytes() == original
    for line in (bundle / "SHA256SUMS").read_text().splitlines():
        if line.startswith("#"):
            continue
        checksum, relative = line.split("  ", 1)
        assert checksum == hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert all("__pycache__" not in name and ".egg-info" not in name and not name.endswith(".whl")
                   for name in package.namelist())
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


def test_refuses_external_sdk_requirement(bundle_source):
    source = bundle_source / "custom_components/hargassner_hv/manifest.json"
    manifest = json.loads(source.read_text())
    manifest["requirements"] = ["hargassner-hvclient==0.1.0"]
    source.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="external SDK"):
        builder.prepare(bundle_source)


def test_refuses_missing_sdk_sources(bundle_source):
    (bundle_source / "custom_components/hargassner_hv/hvclient/client.py").unlink()
    with pytest.raises(ValueError, match="SDK sources"):
        builder.prepare(bundle_source)


def test_refuses_symlink_inputs(bundle_source):
    (bundle_source / "custom_components/hargassner_hv/linked.py").symlink_to(bundle_source / "LICENSE")
    with pytest.raises(ValueError, match="symlink input"):
        builder.prepare(bundle_source)
