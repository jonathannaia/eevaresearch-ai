"""Gated Japan/Korea source expansion (design/DECISIONS.md) — the
read-only dry-run harness's own source-selection logic. The harness's
real reporting work (fetch -> match -> materiality -> admission) is
already exhaustively covered by tests/test_editorial_pipeline.py's own
Business Korea / Japan Times Business sections — run_editorial_
discovery() is reused unmodified, never duplicated here. These tests
cover only _select_sources(), with no real network call."""
from __future__ import annotations

import pytest

from scripts.daily_news_jp_kr_dry_run import _select_sources
from src.data_access.daily_news.source_registry import GATED_JP_KR_SOURCE_REGISTRY


def test_no_source_arg_selects_every_gated_source():
    assert _select_sources(None) == GATED_JP_KR_SOURCE_REGISTRY


def test_named_source_arg_selects_only_that_entry():
    result = _select_sources("businesskorea-science-tech-rss")

    assert len(result) == 1
    assert result[0].source_id == "businesskorea-science-tech-rss"


def test_unknown_source_arg_exits_with_a_clear_error():
    with pytest.raises(SystemExit) as exc_info:
        _select_sources("not-a-real-source-id")

    assert exc_info.value.code == 2
