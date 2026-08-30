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


# ---------------------------------------------------------------------------
# read_existing_feed_title (前回デプロイ分からの復旧)
# ---------------------------------------------------------------------------


def test_read_existing_feed_title_reads_generated_feed(
    requests_mock: rm_module.Mocker, feeds_dir: Path
) -> None:
    """自分が生成した Atom XML から作品名を読み戻せる (feedgenerator との往復)。"""
    requests_mock.post(_api_url(1924, 344436), content=(FIXTURES / "1924_344436.bin").read_bytes())
    main.build_feed_for_work(main.create_session(), 1924, 344436)

    assert main.read_existing_feed_title(1924) == "日本三國"


def test_read_existing_feed_title_returns_none_when_missing(feeds_dir: Path) -> None:
    assert main.read_existing_feed_title(9999) is None


def test_read_existing_feed_title_returns_none_for_broken_xml(
    feeds_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (feeds_dir / "1.xml").write_text("<feed><unclosed>", encoding="utf-8")

    assert main.read_existing_feed_title(1) is None
    assert "could not parse existing feed for 1" in caplog.text


def test_read_existing_feed_title_returns_none_without_title(feeds_dir: Path) -> None:
    (feeds_dir / "1.xml").write_text(
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><id>x</id></feed>',
        encoding="utf-8",
    )

    assert main.read_existing_feed_title(1) is None


def test_main_keeps_failed_work_in_index_when_seeded(
    requests_mock: rm_module.Mocker,
    feeds_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得に失敗しても、seed 済みの XML があれば index に残す。

    gh-pages.yaml が公開中のフィードを seed してから main.py を回すので、
    一時的な API 障害で作品が一覧から消えない。
    """
    _write_feed_csv(tmp_path, monkeypatch, "1924,344436\n659,353965\n")
    # 659 は「前回デプロイ分」として seed 済みの状態にする
    requests_mock.post(_api_url(659, 353965), content=(FIXTURES / "659_353965.bin").read_bytes())
    main.build_feed_for_work(main.create_session(), 659, 353965)
    seeded = (feeds_dir / "659.xml").read_text(encoding="utf-8")

    requests_mock.reset()
    requests_mock.post(_api_url(1924, 344436), content=(FIXTURES / "1924_344436.bin").read_bytes())
    requests_mock.post(_api_url(659, 353965), status_code=500)

    main.main()

    html = (feeds_dir / "index.html").read_text(encoding="utf-8")
    assert '<a href="1924.xml">日本三國</a>' in html
    assert '<a href="659.xml">' in html, "seed 済みなら index に残るはず"
    # seed した XML は上書きされずそのまま残る (購読者の feed URL が 404 にならない)
    assert (feeds_dir / "659.xml").read_text(encoding="utf-8") == seeded
