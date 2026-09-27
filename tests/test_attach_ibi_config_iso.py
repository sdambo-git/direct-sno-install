#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.ibi import config_iso_path, live_iso_path  # noqa: E402


class AttachIbiConfigIsoTests(unittest.TestCase):
    def test_default_iso_paths(self) -> None:
        self.assertEqual(live_iso_path(), ROOT / "ibi-iso-workdir" / "rhcos-ibi.iso")
        self.assertEqual(config_iso_path(), ROOT / "ibi-config-iso-workdir" / "imagebasedconfig.iso")


if __name__ == "__main__":
    unittest.main()
