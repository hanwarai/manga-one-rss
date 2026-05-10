"""chapter エントリ抽出のフィルタ仕様。"""

from datetime import datetime, timedelta, timezone

import main

JST = timezone(timedelta(hours=9))


def _entry(
    *,
    chapter_id: int = 344436,
    label: str = "第49話",
    subtitle: str = "平家追討㉘百万一心",
    date: str | None = "2026/04/15",
    locked: bool = False,
) -> list[tuple[int, str, object]]:
    """chapter list 内の1エントリ ({1:1, ...}) と同じ構造を組み立てる。"""
    fields: list[tuple[int, str, object]] = [
        (1, "varint", chapter_id),
        (2, "str", label),
    ]
    if subtitle:
        fields.append((3, "str", subtitle))
    if date is not None:
        fields.append((5, "str", date))
    if locked:
        fields.append((16, "msg", [(1, "varint", 1), (2, "varint", 1)]))
    else:
        fields.append((16, "str", ""))
    return fields


def test_returns_none_when_locked() -> None:
    assert main.parse_chapter(_entry(locked=True)) is None


def test_returns_none_when_missing_required_fields() -> None:
    assert main.parse_chapter(_entry(label="")) is None
    assert main.parse_chapter(_entry(date=None)) is None


def test_returns_none_on_invalid_date() -> None:
    assert main.parse_chapter(_entry(date="not a date")) is None


def test_parses_free_chapter() -> None:
    parsed = main.parse_chapter(_entry())
    assert parsed is not None
    assert parsed["chapter_id"] == 344436
    assert parsed["title"] == "第49話 平家追討㉘百万一心"
    assert parsed["pubdate"] == datetime(2026, 4, 15, tzinfo=JST)


def test_uses_label_only_when_subtitle_missing() -> None:
    parsed = main.parse_chapter(_entry(label="軍装・区分図・人物紹介⑦", subtitle=""))
    assert parsed is not None
    assert parsed["title"] == "軍装・区分図・人物紹介⑦"


def test_treats_missing_field_16_as_free() -> None:
    """一部章では field 16 そのものが省略されている。これも無料扱い。"""
    fields: list[tuple[int, str, object]] = [
        (1, "varint", 100),
        (2, "str", "第1話"),
        (3, "str", "泰平の誓い"),
        (5, "str", "2021/11/24"),
    ]
    parsed = main.parse_chapter(fields)
    assert parsed is not None
    assert parsed["chapter_id"] == 100
