"""feed.csv パース仕様（title_id,chapter_id の2列）。"""

from pathlib import Path

import pytest

import main


def _write(tmp_path: Path, content: str) -> Path:
    csv_path = tmp_path / "feed.csv"
    csv_path.write_text(content)
    return csv_path


def test_reads_valid_pairs(tmp_path: Path) -> None:
    path = _write(tmp_path, "1924,344436\n659,353965\n")
    assert list(main.read_feed_ids(path)) == [(1924, 344436), (659, 353965)]


def test_skips_empty_and_whitespace_lines(tmp_path: Path) -> None:
    path = _write(tmp_path, "1924,344436\n\n   \n659,353965\n")
    assert list(main.read_feed_ids(path)) == [(1924, 344436), (659, 353965)]


def test_skips_invalid_rows(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """非数値・列不足・パストラバーサル試行は無視。"""
    path = _write(
        tmp_path,
        "1924,344436\nfoo,bar\n../etc/passwd\n42\n659,353965\n",
    )
    with caplog.at_level("WARNING", logger="manga-one-rss"):
        assert list(main.read_feed_ids(path)) == [
            (1924, 344436),
            (659, 353965),
        ]
    assert any("invalid feed row" in rec.message for rec in caplog.records)


def test_deduplicates_by_title_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(
        tmp_path,
        "1924,344436\n659,353965\n1924,999999\n",
    )
    with caplog.at_level("WARNING", logger="manga-one-rss"):
        result = list(main.read_feed_ids(path))
    assert result == [(1924, 344436), (659, 353965)]
    assert any("duplicate title id" in rec.message for rec in caplog.records)


def test_strips_whitespace(tmp_path: Path) -> None:
    path = _write(tmp_path, "  1924 ,  344436 \n")
    assert list(main.read_feed_ids(path)) == [(1924, 344436)]
