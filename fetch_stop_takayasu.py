# -*- coding: utf-8 -*-
"""Fetch Kabutan stop-high and stop-low warning tables.

Outputs:
  history/YYYY-MM-DD.json
  output/stop_takayasu_YYYY-MM-DD.csv
  docs/data/YYYY-MM-DD.json
  docs/data/index.json
"""
import csv
import html
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
HISTORY = BASE / "history"
OUTPUT = BASE / "output"
DOCS_DATA = BASE / "docs" / "data"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SOURCES = [
    {
        "group": "stop_high",
        "group_label": "本日のストップ高銘柄",
        "url": "https://kabutan.jp/warning/?mode=3_1&market=0&capitalization=-1&dispmode=normal",
        "sign": 1,
    },
    {
        "group": "stop_low",
        "group_label": "本日のストップ安銘柄",
        "url": "https://kabutan.jp/warning/?mode=3_2&market=0&capitalization=-1&dispmode=normal",
        "sign": -1,
    },
]
REQUEST_SLEEP = 1.5

TABLE_RE = re.compile(r'<table class="stock_table st_market">(?P<table>[\s\S]*?)</table>')
AS_OF_RE = re.compile(
    r"<li>(?P<date>[0-9]{4}年[0-9]{2}月[0-9]{2}日)</li>\s*"
    r"<li>(?P<time>[0-9]{1,2}:[0-9]{2})現在</li>"
)
COUNT_RE = re.compile(r"<li>(?P<count>[0-9,]+)銘柄</li>")

CELL = r"<td[^>]*>(?P<{name}>(?:(?!</td>)[\s\S])*)</td>"
ICON_CELL = r"<td[^>]*>(?:(?!</td>)[\s\S])*</td>"
ROW_RE = re.compile(
    r"<tr>\s*"
    r'<td class="tac"><a href="/stock/\?code=(?P<code>[0-9A-Z]+)">[0-9A-Z]+</a></td>\s*'
    r'<th scope="row" class="tal">(?P<name>(?:(?!</th>)[\s\S])*)</th>\s*'
    r'<td class="tac">(?P<market>(?:(?!</td>)[\s\S])*)</td>\s*'
    r'<td class="gaiyou_icon">(?:(?!</td>)[\s\S])*</td>\s*'
    r'<td class="chart_icon">(?:(?!</td>)[\s\S])*</td>\s*'
    + CELL.format(name="price")
    + r"\s*"
    + CELL.format(name="status")
    + r"\s*"
    + CELL.format(name="change")
    + r"\s*"
    + CELL.format(name="change_pct")
    + r"\s*"
    + ICON_CELL
    + r"\s*"
    + CELL.format(name="per")
    + r"\s*"
    + CELL.format(name="pbr")
    + r"\s*"
    + CELL.format(name="yld"),
)


def clean_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_number(value: str):
    if value in ("", "－", "-"):
        return None
    try:
        return float(value.replace(",", "").replace("+", "").replace("%", ""))
    except ValueError:
        return None


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def paged_url(url: str, page: int) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page={page}"


def parse_page(html_text: str, source: dict) -> tuple[list[dict], str | None, int | None]:
    as_of = None
    m = AS_OF_RE.search(html_text)
    if m:
        yyyy_mm_dd = (
            m.group("date")
            .replace("年", "-")
            .replace("月", "-")
            .replace("日", "")
        )
        as_of = f"{yyyy_mm_dd} {m.group('time')}"

    expected_count = None
    cm = COUNT_RE.search(html_text)
    if cm:
        expected_count = int(cm.group("count").replace(",", ""))

    tm = TABLE_RE.search(html_text)
    if not tm:
        raise RuntimeError(f"stock table not found: {source['group']}")

    rows = []
    for rm in ROW_RE.finditer(tm.group("table")):
        d = {k: clean_cell(v or "") for k, v in rm.groupdict().items()}
        d["group"] = source["group"]
        d["group_label"] = source["group_label"]
        d["key"] = f"{source['group']}:{d['code']}"
        d["change_pct_num"] = parse_number(d["change_pct"])
        rows.append(d)
    return rows, as_of, expected_count


def fetch_source(source: dict) -> tuple[list[dict], str | None, int | None]:
    rows = []
    as_of = None
    expected_count = None
    seen_codes = set()
    page = 1

    while True:
        html_text = fetch(source["url"] if page == 1 else paged_url(source["url"], page))
        page_rows, page_as_of, page_expected = parse_page(html_text, source)
        if page_as_of:
            as_of = page_as_of
        if page_expected is not None:
            expected_count = page_expected

        before = len(rows)
        for row in page_rows:
            if row["code"] not in seen_codes:
                seen_codes.add(row["code"])
                rows.append(row)

        if expected_count is None or len(rows) >= expected_count:
            break
        if len(rows) == before or not page_rows:
            break
        page += 1
        time.sleep(REQUEST_SLEEP)

    return rows, as_of, expected_count


def load_previous(today: str) -> tuple[str | None, dict]:
    files = sorted(p for p in HISTORY.glob("*.json") if p.stem < today)
    if not files:
        return None, {}
    prev_date = files[-1].stem
    payload = json.loads(files[-1].read_text(encoding="utf-8"))
    stocks = payload["stocks"] if isinstance(payload, dict) else payload
    return prev_date, {s["key"]: s for s in stocks}


def add_rank_and_moves(stocks: list[dict], prev_data: dict):
    for group in ("stop_high", "stop_low"):
        group_rows = [s for s in stocks if s["group"] == group]
        group_rows.sort(
            key=lambda s: (
                s["change_pct_num"] is None,
                -(s["change_pct_num"] or 0),
            )
        )
        if group == "stop_low":
            group_rows.sort(
                key=lambda s: (
                    s["change_pct_num"] is None,
                    s["change_pct_num"] or 0,
                )
            )
        for i, s in enumerate(group_rows, 1):
            s["rank"] = i
            prev = prev_data.get(s["key"])
            if prev:
                diff = int(prev["rank"]) - i
                s["prev_rank"] = prev["rank"]
                s["move_num"] = diff
                s["move"] = f"↑{diff}" if diff > 0 else f"↓{-diff}" if diff < 0 else "→"
                s["is_new"] = False
            else:
                s["prev_rank"] = ""
                s["move_num"] = None
                s["move"] = "NEW" if prev_data else ""
                s["is_new"] = bool(prev_data)


def main() -> int:
    today = date.today().isoformat()
    HISTORY.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    all_rows = []
    as_of_values = []
    source_counts = {}
    expected_counts = {}
    for idx, source in enumerate(SOURCES):
        if idx:
            time.sleep(REQUEST_SLEEP)
        rows, as_of, expected_count = fetch_source(source)
        if as_of:
            as_of_values.append(as_of)
        source_counts[source["group"]] = len(rows)
        expected_counts[source["group"]] = expected_count
        if expected_count is not None and expected_count != len(rows):
            raise RuntimeError(
                f"{source['group']} expected {expected_count}, parsed {len(rows)}"
            )
        all_rows.extend(rows)

    deduped = []
    seen = set()
    for row in all_rows:
        if row["key"] not in seen:
            seen.add(row["key"])
            deduped.append(row)
    all_rows = deduped

    prev_date, prev_data = load_previous(today)
    add_rank_and_moves(all_rows, prev_data)
    all_rows.sort(key=lambda s: (s["group"], s["rank"]))

    as_of = max(as_of_values) if as_of_values else None
    payload = {
        "date": today,
        "as_of": as_of,
        "prev_date": prev_date,
        "count": len(all_rows),
        "source_counts": source_counts,
        "expected_counts": expected_counts,
        "stocks": all_rows,
    }

    (HISTORY / f"{today}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DOCS_DATA / f"{today}.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    dates = sorted((p.stem for p in DOCS_DATA.glob("????-??-??.json")), reverse=True)
    (DOCS_DATA / "index.json").write_text(
        json.dumps({"dates": dates}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_path = OUTPUT / f"stop_takayasu_{today}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "種別",
                "順位",
                "順位変動",
                "前回順位",
                "コード",
                "銘柄名",
                "市場",
                "株価",
                "状態",
                "前日比",
                "前日比率",
                "PER",
                "PBR",
                "利回り",
                "as_of",
            ]
        )
        for s in all_rows:
            writer.writerow(
                [
                    s["group_label"],
                    s["rank"],
                    s["move"],
                    s["prev_rank"],
                    s["code"],
                    s["name"],
                    s["market"],
                    s["price"],
                    s["status"],
                    s["change"],
                    s["change_pct"],
                    s["per"],
                    s["pbr"],
                    s["yld"],
                    as_of or "",
                ]
            )

    print(csv_path)
    print(
        f"count={len(all_rows)} high={source_counts.get('stop_high', 0)} "
        f"low={source_counts.get('stop_low', 0)} as_of={as_of}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
