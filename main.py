"""マンガワン（manga-one.com）の無料章を取得する Atom RSS ジェネレータ。"""

from __future__ import annotations

import csv
import logging
import re
import struct
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedgenerator
import requests
from jinja2 import Environment, FileSystemLoader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("manga-one-rss")

BASE_URL = "https://manga-one.com"
VIEWER_API_URL_TEMPLATE = (
    BASE_URL
    + "/api/client?rq=viewer_v2"
    + "&title_id={title_id}&chapter_id={chapter_id}"
    + "&page=1&limit=500&sort_type=desc&list_type=chapter"
    + "&free_point=0&event_point=0&paid_point=0"
)
CHAPTER_URL_TEMPLATE = BASE_URL + "/manga/{title_id}/chapter/{chapter_id}"
WORK_URL_TEMPLATE = BASE_URL + "/manga/{title_id}/chapter/{chapter_id}"

REQUEST_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
JST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
ID_RE = re.compile(r"^\d+$")

FEEDS_DIR = Path("feeds")
FEED_LIST_PATH = Path("feed.csv")
TEMPLATE_DIR = Path("templates")

# viewer_v2 protobuf 内の field 番号
WORK_INFO_FIELD = 5            # トップ: work メタ情報
WORK_INNER_FIELD = 1           #   work_info.1: 作品本体
WORK_TITLE_FIELD = 2           #     work.2: タイトル
WORK_DESC_FIELD = 4            #     work.4: あらすじ
WORK_AUTHOR_FIELD = 5          #     work.5: 著者
WORK_THUMB_FIELD = 6           #     work.6: サムネイル

CHAPTER_LIST_FIELD = 11        # トップ: 全章リスト
CHAPTER_ENTRY_FIELD = 1        #   chapter_list.1: 各章 (repeated)

CHAPTER_ID_FIELD = 1
CHAPTER_LABEL_FIELD = 2
CHAPTER_SUBTITLE_FIELD = 3
CHAPTER_DATE_FIELD = 5
CHAPTER_LOCK_FIELD = 16        # サブメッセージなら有料、空文字列／欠落なら無料


# ---------------------------------------------------------------------------
# protobuf wire-format decoder (schema-less, recursive)
# ---------------------------------------------------------------------------

ProtoNode = list[tuple[int, str, Any]]


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    v = 0
    s = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint truncated")
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << s
        if not (b & 0x80):
            return v, i
        s += 7
        if s > 64:
            raise ValueError("varint too long")


def proto_decode(buf: bytes) -> ProtoNode | None:
    """生 protobuf を field, wire, value のリストに展開する。

    wire 種別:
      - "varint" — int
      - "fixed64"/"fixed32" — int
      - "msg" — 入れ子の ProtoNode（再帰）
      - "str" — UTF-8 文字列（length-delimited で msg 化に失敗したフォールバック）
      - "bytes" — 上記すべてに失敗した生バイト列
    バッファが消費しきれない／壊れている場合は ``None``。
    """
    out: ProtoNode = []
    i = 0
    while i < len(buf):
        try:
            tag, i = _read_varint(buf, i)
        except ValueError:
            return None
        wire = tag & 7
        field = tag >> 3
        if wire == 0:
            try:
                v, i = _read_varint(buf, i)
            except ValueError:
                return None
            out.append((field, "varint", v))
        elif wire == 1:
            if i + 8 > len(buf):
                return None
            (v64,) = struct.unpack_from("<Q", buf, i)
            i += 8
            out.append((field, "fixed64", v64))
        elif wire == 2:
            try:
                ln, i = _read_varint(buf, i)
            except ValueError:
                return None
            if i + ln > len(buf):
                return None
            sub = buf[i : i + ln]
            i += ln
            inner = proto_decode(sub)
            if inner:  # 非空かつ None でない
                out.append((field, "msg", inner))
            else:
                # 空メッセージ（[]）または非メッセージ。文字列化を試みる。
                try:
                    out.append((field, "str", sub.decode("utf-8")))
                except UnicodeDecodeError:
                    out.append((field, "bytes", sub))
        elif wire == 5:
            if i + 4 > len(buf):
                return None
            (v32,) = struct.unpack_from("<I", buf, i)
            i += 4
            out.append((field, "fixed32", v32))
        else:
            # SGROUP/EGROUP は manga-one では未使用
            return None
    return out


def _find_field(node: ProtoNode, field: int) -> tuple[str, Any] | None:
    for f, w, v in node:
        if f == field:
            return w, v
    return None


# ---------------------------------------------------------------------------
# domain logic
# ---------------------------------------------------------------------------


def parse_chapter(entry: ProtoNode) -> dict[str, Any] | None:
    """1章エントリ（chapter_list.1 の中身）を整形。有料章・不正データは ``None``。"""
    chapter_id: int | None = None
    label = ""
    subtitle = ""
    date_str: str | None = None

    for f, w, v in entry:
        if f == CHAPTER_ID_FIELD and w == "varint":
            chapter_id = int(v)
        elif f == CHAPTER_LABEL_FIELD and w == "str":
            label = v
        elif f == CHAPTER_SUBTITLE_FIELD and w == "str":
            subtitle = v
        elif f == CHAPTER_DATE_FIELD and w == "str":
            date_str = v
        elif f == CHAPTER_LOCK_FIELD and w == "msg":
            # 有料章: サブメッセージ ({1:1, 2:1} など)
            return None

    if chapter_id is None or not label or not date_str:
        return None

    m = DATE_RE.match(date_str)
    if not m:
        return None
    try:
        pubdate = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=JST
        )
    except ValueError:
        return None

    title = f"{label} {subtitle}" if subtitle else label
    return {
        "chapter_id": chapter_id,
        "title": title,
        "pubdate": pubdate,
    }


def extract_work_meta(tree: ProtoNode) -> dict[str, str] | None:
    """work タイトル・あらすじ・著者・サムネイル URL を抽出。"""
    work_info = _find_field(tree, WORK_INFO_FIELD)
    if work_info is None or work_info[0] != "msg":
        return None
    inner = _find_field(work_info[1], WORK_INNER_FIELD)
    if inner is None or inner[0] != "msg":
        return None

    fields = inner[1]
    title = description = author = thumbnail = ""
    for f, w, v in fields:
        if w != "str":
            continue
        if f == WORK_TITLE_FIELD:
            title = v
        elif f == WORK_DESC_FIELD:
            description = v
        elif f == WORK_AUTHOR_FIELD:
            author = v
        elif f == WORK_THUMB_FIELD:
            thumbnail = v

    if not title:
        return None
    return {
        "title": title,
        "description": description,
        "author": author,
        "thumbnail": thumbnail,
    }


def extract_free_chapters(tree: ProtoNode) -> list[dict[str, Any]]:
    """章リストから無料章のみ抽出（chapter_id 降順）。"""
    chapter_list = _find_field(tree, CHAPTER_LIST_FIELD)
    if chapter_list is None or chapter_list[0] != "msg":
        return []

    chapters: list[dict[str, Any]] = []
    seen: set[int] = set()
    for f, w, v in chapter_list[1]:
        if f != CHAPTER_ENTRY_FIELD or w != "msg":
            continue
        parsed = parse_chapter(v)
        if parsed is None:
            continue
        if parsed["chapter_id"] in seen:
            continue
        seen.add(parsed["chapter_id"])
        chapters.append(parsed)
    chapters.sort(key=lambda c: (c["pubdate"], c["chapter_id"]), reverse=True)
    return chapters


# ---------------------------------------------------------------------------
# HTTP / feed generation
# ---------------------------------------------------------------------------


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Content-Type"] = "application/json"
    return session


def build_feed_for_work(
    session: requests.Session, title_id: int, chapter_id: int
) -> dict[str, str] | None:
    api_url = VIEWER_API_URL_TEMPLATE.format(
        title_id=title_id, chapter_id=chapter_id
    )
    work_url = WORK_URL_TEMPLATE.format(title_id=title_id, chapter_id=chapter_id)
    logger.info("%s %s", title_id, api_url)

    try:
        response = session.post(api_url, data=b"{}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("request failed for %s: %s", title_id, exc)
        return None
    if not response.ok:
        logger.warning(
            "failed to retrieve %s (status=%s)", title_id, response.status_code
        )
        return None

    tree = proto_decode(response.content)
    if tree is None:
        logger.warning("protobuf decode failed for %s", title_id)
        return None

    work = extract_work_meta(tree)
    if work is None:
        logger.warning("no work meta for %s", title_id)
        return None

    chapters = extract_free_chapters(tree)
    logger.info("%s %s (%d free chapters)", title_id, work["title"], len(chapters))

    rss = feedgenerator.Atom1Feed(
        title=work["title"],
        link=work_url,
        description=work["description"],
        language="ja",
        author_name=work["author"] or None,
        image=work["thumbnail"] or None,
    )
    for ch in chapters:
        link = CHAPTER_URL_TEMPLATE.format(
            title_id=title_id, chapter_id=ch["chapter_id"]
        )
        rss.add_item(
            unique_id=str(ch["chapter_id"]),
            title=ch["title"],
            link=link,
            description="",
            pubdate=ch["pubdate"],
            content="",
        )

    FEEDS_DIR.mkdir(exist_ok=True)
    with (FEEDS_DIR / f"{title_id}.xml").open("w", encoding="utf-8") as fp:
        rss.write(fp, "utf-8")

    return {"id": str(title_id), "title": work["title"]}


def read_feed_ids(path: Path) -> Iterator[tuple[int, int]]:
    """feed.csv の `title_id,chapter_id` 行を生成。

    title_id 重複は最初の行を採用。両方とも数値必須。
    """
    seen: set[int] = set()
    with path.open(encoding="utf-8") as fp:
        for row in csv.reader(fp):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                logger.warning("invalid feed row %r, skipping", row)
                continue
            tid_s = row[0].strip()
            cid_s = row[1].strip()
            if not (ID_RE.fullmatch(tid_s) and ID_RE.fullmatch(cid_s)):
                logger.warning("invalid feed row %r, skipping", row)
                continue
            tid = int(tid_s)
            cid = int(cid_s)
            if tid in seen:
                logger.warning("duplicate title id %d, skipping", tid)
                continue
            seen.add(tid)
            yield (tid, cid)


def render_index(feeds: list[dict[str, str]]) -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("index.html")
    FEEDS_DIR.mkdir(exist_ok=True)
    (FEEDS_DIR / "index.html").write_text(
        template.render(feeds=feeds), encoding="utf-8"
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    session = create_session()
    rendered: list[dict[str, str]] = []
    for title_id, chapter_id in read_feed_ids(FEED_LIST_PATH):
        try:
            result = build_feed_for_work(session, title_id, chapter_id)
        except Exception:
            logger.exception("failed to build feed for %s", title_id)
            continue
        if result:
            rendered.append(result)
    render_index(rendered)


if __name__ == "__main__":
    main()
