# SPDX-License-Identifier: Apache-2.0
"""Run real-HA tests with only the explicitly selected pytest plugins."""

import os
import sys
from pathlib import Path

import homeassistant  # noqa: F401 -- required initialization before pytest plugin loading
import pytest

if __name__ == "__main__":
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    raise SystemExit(pytest.main([
        "-c", str(root / "tests/ha/pytest.ini"), str(root / "tests/ha"), *sys.argv[1:],
    ]))
