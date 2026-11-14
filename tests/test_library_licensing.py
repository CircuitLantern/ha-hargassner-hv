# SPDX-License-Identifier: Apache-2.0
"""Keep project notices and package declarations consistent without modifying JSON."""

import json
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX = "SPDX-License-Identifier: Apache-2.0"


class LicensingTests(unittest.TestCase):
    def test_project_source_and_documentation_headers(self):
        paths = set()
        for directory in ("tests", "tools", "docs", "custom_components/hargassner_hv", ".github"):
            base = ROOT / directory
            if base.exists():
                for extension in ("*.py", "*.md", "*.yaml", "*.yml", "*.json", "*.license"):
                    paths.update(path for path in base.rglob(extension)
                                 if not any(part.startswith(".") or part == "__pycache__"
                                            for part in path.relative_to(base).parts))
        paths.update(ROOT / name for name in (
            ".editorconfig", ".gitattributes", ".gitignore", ".githooks/pre-commit",
            "README.md", "LICENSING.md", "MANIFEST.in", "setup.cfg", "pyproject.toml",
            "custom_components/hargassner_hv/hvclient/py.typed", "firmware/README.md", "firmware/SHA256SUMS",
            "tests/ha/pytest.ini", "tests/ha/requirements.txt", "hacs.json", "hacs.json.license",
        ) if (ROOT / name).exists())
        for path in sorted(paths):
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                if path.suffix == ".json":
                    json.loads(path.read_text())
                    notice = Path(str(path) + ".license").read_text()
                    self.assertEqual(notice, SPDX + "\n")
                else:
                    if path.name == "LICENSE":
                        continue
                    lines = path.read_text().splitlines()
                    header = lines[1] if lines[0].startswith("#!") else lines[0]
                    expected = (f"<!-- {SPDX} -->" if path.suffix == ".md"
                                else SPDX if path.suffix == ".license" else f"# {SPDX}")
                    self.assertEqual(header, expected)

    def test_standard_license_and_package_expression(self):
        license_text = (ROOT / "LICENSE").read_text()
        self.assertIn("Apache License\n", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("3. Grant of Patent License.", license_text)
        self.assertIn("4. Redistribution.", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        # Source archives do not need to include build metadata to run tests.
        if (ROOT / "pyproject.toml").exists():
            project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
            self.assertEqual(project["license"], "Apache-2.0")
            self.assertEqual(project["license-files"], ["LICENSE", "LICENSING.md"])
