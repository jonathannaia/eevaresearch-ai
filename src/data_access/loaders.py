"""Shared cached seed-data loader. The one place that knows how to read a
JSON file out of data/seed/ — every demo repository goes through this
rather than opening a file directly, so the eventual Phase 2 swap to a
real source only touches src/data_access/container.py, not this file's
callers.

Wrapped in try/except so a missing or malformed seed file degrades to an
empty result (repositories then return empty lists, and pages fall back to
their EmptyState component) instead of crashing the app.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from src.config.settings import Settings


@st.cache_data(show_spinner=False)
def load_seed_json(seed_dir: str, filename: str, default: Any = None) -> Any:
    from pathlib import Path

    path = Path(seed_dir) / filename
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []


def load(settings: Settings, filename: str, default: Any = None) -> Any:
    return load_seed_json(str(settings.seed_data_dir), filename, default)
