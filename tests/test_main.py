"""main() と render_index() のエンドツーエンドテスト。

この 2 つは CI でもテストでも一度も実行されていなかった。特に
templates/index.html は `uv run main.py` (push / schedule 実行) でしか
評価されず、Jinja2 やテンプレート側の破壊的変更をデプロイ時まで検知
できなかったため、ここで固定する。
"""

from pathlib import Path

import pytest
import requests_mock as rm_module

import main

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@pytest.fixture
def feeds_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """FEEDS_DIR と TEMPLATE_DIR を固定する。

    TEMPLATE_DIR は既定が相対パス Path("templates") で、pytest の起動
    ディレクトリに依存してしまうため絶対パスに差し替える。
    """
    monkeypatch.setattr(main, "FEEDS_DIR", tmp_path)
    monkeypatch.setattr(main, "TEMPLATE_DIR", TEMPLATES)
    return tmp_path


def _api_url(title_id: int, chapter_id: int) -> str:
    return main.VIEWER_API_URL_TEMPLATE.format(title_id=title_id, chapter_id=chapter_id)


def _write_feed_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    path = tmp_path / "feed.csv"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(main, "FEED_LIST_PATH", path)


# ---------------------------------------------------------------------------
# render_index
# ---------------------------------------------------------------------------


def test_render_index_writes_one_link_per_feed(feeds_dir: Path) -> None:
    main.render_index(
        [{"id": "1924", "title": "日本三國"}, {"id": "659", "title": "ケンガンオメガ"}]
    )

    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert '<a href="1924.xml">日本三國</a>' in html
    assert '<a href="659.xml">ケンガンオメガ</a>' in html
    assert "https://hanwarai.github.io/manga-one-rss/1924.xml" in html


def test_render_index_with_no_feeds_still_writes_page(feeds_dir: Path) -> None:
    """1 作品も取れなかった回でも index.html は出力する (Pages が 404 にならない)。"""
    main.render_index([])

    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert "<title>manga-one-rss</title>" in html
    assert ".xml" not in html


def test_render_index_escapes_html_in_title(feeds_dir: Path) -> None:
    """作品名は API 由来なので autoescape が効いていることを固定する。"""
    main.render_index([{"id": "1", "title": "<script>alert(1)</script>"}])

    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_writes_feed_xml_and_index(
    requests_mock: rm_module.Mocker,
    feeds_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_feed_csv(tmp_path, monkeypatch, "1924,344436\n659,353965\n")
    requests_mock.post(_api_url(1924, 344436), content=(FIXTURES / "1924_344436.bin").read_bytes())
    requests_mock.post(_api_url(659, 353965), content=(FIXTURES / "659_353965.bin").read_bytes())

    main.main()

    assert (feeds_dir / "1924.xml").exists()
    assert (feeds_dir / "659.xml").exists()
    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert '<a href="1924.xml">日本三國</a>' in html
    assert '<a href="659.xml">' in html


def test_main_omits_work_whose_fetch_fails(
    requests_mock: rm_module.Mocker,
    feeds_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 作品が 500 でも他の作品と index の生成は続行する。"""
    _write_feed_csv(tmp_path, monkeypatch, "1924,344436\n659,353965\n")
    requests_mock.post(_api_url(1924, 344436), content=(FIXTURES / "1924_344436.bin").read_bytes())
    requests_mock.post(_api_url(659, 353965), status_code=500)

    main.main()

    assert (feeds_dir / "1924.xml").exists()
    assert not (feeds_dir / "659.xml").exists()
    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert '<a href="1924.xml">' in html
    assert '<a href="659.xml">' not in html


def test_main_continues_when_one_work_raises(
    requests_mock: rm_module.Mocker,
    feeds_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """想定外の例外でも 1 作品分で止まらない (main の except Exception 経路)。"""
    _write_feed_csv(tmp_path, monkeypatch, "659,353965\n1924,344436\n")
    requests_mock.post(_api_url(1924, 344436), content=(FIXTURES / "1924_344436.bin").read_bytes())

    original = main.build_feed_for_work

    def flaky(session: object, title_id: int, chapter_id: int) -> dict[str, str] | None:
        if title_id == 659:
            raise RuntimeError("boom")
        return original(session, title_id, chapter_id)  # type: ignore[arg-type]

    monkeypatch.setattr(main, "build_feed_for_work", flaky)

    main.main()

    assert (feeds_dir / "1924.xml").exists()
    assert '<a href="1924.xml">' in (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert "failed to build feed for 659" in caplog.text
