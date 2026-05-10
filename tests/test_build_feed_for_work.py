"""build_feed_for_work のエンドツーエンド（HTTP モック / 実 fixture）テスト。"""

from pathlib import Path

import pytest
import requests_mock as rm_module

import main

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def feeds_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(main, "FEEDS_DIR", tmp_path)
    return tmp_path


def _api_url(title_id: int, chapter_id: int) -> str:
    return main.VIEWER_API_URL_TEMPLATE.format(
        title_id=title_id, chapter_id=chapter_id
    )


def test_real_fixture_1924_yields_only_free_chapters(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    """1924『日本三國』(70章中23章が無料) の実バイナリスナップショット。"""
    fixture = (FIXTURES / "1924_344436.bin").read_bytes()
    requests_mock.post(_api_url(1924, 344436), content=fixture)

    result = main.build_feed_for_work(main.create_session(), 1924, 344436)

    assert result is not None
    assert result["id"] == "1924"
    assert result["title"] == "日本三國"

    xml = (feeds_dir / "1924.xml").read_text(encoding="utf-8")
    # 無料章は含まれる
    assert "344436" in xml  # 第49話
    assert "第1話" in xml
    assert "第2話" in xml
    # 有料章は含まれない
    assert "342813" not in xml  # 第48話 (LOCKED)
    assert "平家追討㉗贋書の計" not in xml


def test_real_fixture_659_yields_only_free_chapters(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    fixture = (FIXTURES / "659_353965.bin").read_bytes()
    requests_mock.post(_api_url(659, 353965), content=fixture)

    result = main.build_feed_for_work(main.create_session(), 659, 353965)

    assert result is not None
    assert result["id"] == "659"
    assert result["title"] == "ケンガンオメガ"

    xml = (feeds_dir / "659.xml").read_text(encoding="utf-8")
    # 第352話は無料
    assert "353965" in xml
    # 第353話は有料
    assert "354409" not in xml


def test_emits_chapters_in_descending_pubdate_order(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    """日付降順。chapter_id ではなく pubdate でソート。"""
    fixture = (FIXTURES / "1924_344436.bin").read_bytes()
    requests_mock.post(_api_url(1924, 344436), content=fixture)

    main.build_feed_for_work(main.create_session(), 1924, 344436)
    xml = (feeds_dir / "1924.xml").read_text(encoding="utf-8")
    # 第49話 (2026/04/15) は 7巻コミックPR (2026/04/01) より先頭に来る
    # — chapter_id だと 353341 > 344436 で逆転していた
    pos_49 = xml.index("344436")
    pos_pr = xml.index("353341")
    pos_old = xml.index("174463")  # 第1話 (2021/11/24)
    assert pos_49 < pos_pr < pos_old


def test_returns_none_on_404(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    requests_mock.post(_api_url(1924, 344436), status_code=404)
    assert main.build_feed_for_work(main.create_session(), 1924, 344436) is None
    assert not (feeds_dir / "1924.xml").exists()


def test_returns_none_on_unparseable_body(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    requests_mock.post(_api_url(1924, 344436), content=b"\xff\xff garbage")
    assert main.build_feed_for_work(main.create_session(), 1924, 344436) is None
