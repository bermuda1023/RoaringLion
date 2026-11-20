"""Hermetic pytest fixture: isolate SQLite + Parquet state into a temporary directory per test session."""

from __future__ import annotations

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="roaring_lion_pytest_")
os.environ["ROARING_LION_DATA_DIR"] = _tmp_dir
