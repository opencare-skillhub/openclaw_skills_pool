#!/usr/bin/env python3
"""把 markdown 表格转成图片，用于 Flarum 发帖。

背景（2026-10-19 实测 bbs.xiaoyibao.com.cn）：
  - Flarum 的 s9e/Litedown 格式化器 **不支持 GFM 管道表格**，`| a | b |` 会原样变文字；
  - 该站 **不允许原始 HTML**，`<table>` 会被整体转义成源码。
  => 表格只能用图片承载（与本项目「文章要点长图」同一条渲染管线）。

用法（库）：
    from md_table_image import convert_tables
    body2, n = convert_tables(body_md)          # 转图 + 传 COS，返回新正文
    body2, n = convert_tables(body_md, upload=False)   # 只本地渲染，发帖前预览

用法（命令行，单文件试跑）：
    python md_table_image.py body.md --out body_new.md --no-upload
"""
import argparse
import hashlib
import html
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_publish import render, upload  # noqa: E402

TAG_COLOR = "#5c4a7a"
TAG_DARK = "#3a2f50"
ZEBRA = "#f7f4fb"
BORDER = "#ddd3ea"
WORK = Path(tempfile.gettempdir()) / "flarum-tables"

SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_sep(line):
    return bool(SEP_RE.match(line.strip()))


def _aligns(sep_line):
    out = []
    for c in _cells(sep_line):
        left, right = c.startswith(":"), c.endswith(":")
        out.append("center" if left and right else "right" if right else "left")
    return out


def find_tables(text):
    """返回 [{start,end,header,rows,align}]，end 为表格最后一行的下标（含）。"""
    lines = text.split("\n")
    tables, i = [], 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _is_sep(lines[i + 1]):
            header = _cells(lines[i])
            align = _aligns(lines[i + 1])
            j, rows = i + 2, []
            while j < len(lines) and lines[j].strip() and "|" in lines[j]:
                rows.append(_cells(lines[j]))
                j += 1
            tables.append({"start": i, "end": j - 1, "header": header, "rows": rows, "align": align})
            i = j
        else:
            i += 1
    return tables


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    return s.replace("\n", "<br>")


def _font_size(ncol):
    return {1: 15, 2: 15, 3: 14.5, 4: 14, 5: 12.5, 6: 11.5}.get(ncol, 10.5)


def _disp_len(s):
    return sum(2 if ord(c) > 127 else 1 for c in s)


def _col_widths(header, rows, ncol):
    """按每列最长内容分配宽度（固定布局，避免 NCT/链接把列撑爆）。"""
    if ncol <= 2:
        return [30.0, 70.0] if ncol == 2 else [100.0]
    scores = []
    for k in range(ncol):
        vals = [header[k] if k < len(header) else ""] + [r[k] if k < len(r) else "" for r in rows]
        scores.append(min(max(max((_disp_len(v) for v in vals), default=4), 4), 30))
    # 长文本列给更高权重，避免短列（期别/申办方）占位过宽
    weighted = [s ** 1.25 for s in scores]
    tot = sum(weighted) or 1
    return [round(w / tot * 100, 2) for w in weighted]


def build_html(header, rows, align, width=750):
    ncol = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
    fs = _font_size(ncol)
    al = list(align) + ["left"] * (ncol - len(align))
    cols = "".join(f'<col style="width:{w}%">' for w in _col_widths(header, rows, ncol))

    def pad(r):
        return (list(r) + [""] * ncol)[:ncol]

    ths = "".join(f'<th style="text-align:{al[k]}">{_inline(v)}</th>' for k, v in enumerate(pad(header)))
    body = []
    for n, r in enumerate(rows):
        tds = "".join(f'<td style="text-align:{al[k]}">{_inline(v)}</td>' for k, v in enumerate(pad(r)))
        body.append(f'<tr class="{"odd" if n % 2 else "even"}">{tds}</tr>')

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: #fff; }}
  body {{ font-family: "PingFang SC", "Helvetica Neue", "Microsoft YaHei", sans-serif; }}
  .wrap {{ width: {width}px; padding: 14px 14px 10px; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed;
           font-size: {fs}px; line-height: 1.55; color: #48565A; }}
  th {{ background: {TAG_COLOR}; color: #fff; font-weight: 600; text-align: left;
        padding: 10px 9px; border: 1px solid {TAG_COLOR};
        word-break: break-word; overflow-wrap: break-word; }}
  td {{ padding: 9px 9px; border: 1px solid {BORDER}; vertical-align: top;
        word-break: break-word; overflow-wrap: break-word; }}
  tr.odd td {{ background: {ZEBRA}; }}
  tr.even td {{ background: #fff; }}
  td:first-child {{ color: {TAG_DARK}; }}
  strong {{ color: {TAG_DARK}; }}
  code {{ font-family: "SF Mono", Menlo, monospace; font-size: 0.94em;
          background: #f1ecf7; color: {TAG_DARK}; padding: 0 3px; border-radius: 3px; }}
</style></head><body><div class="wrap"><table>
<colgroup>{cols}</colgroup>
<thead><tr>{ths}</tr></thead>
<tbody>{''.join(body)}</tbody>
</table></div></body></html>"""


def table_key(header, rows):
    raw = "|".join(header) + "||" + "||".join("|".join(r) for r in rows)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def render_table(header, rows, align, width=750, out_png=None):
    WORK.mkdir(parents=True, exist_ok=True)
    key = table_key(header, rows)
    out_png = Path(out_png) if out_png else WORK / f"{key}.png"
    html_path = WORK / f"{key}.html"
    html_path.write_text(build_html(header, rows, align, width), encoding="utf-8")
    png, size, css_h = render(html_path, out_png, css_width=width)
    return png, size, key


def convert_tables(body, upload_=True, width=750, key_prefix="xyb/tables"):
    """把正文里的 md 表格逐块换成图片，返回 (新正文, 替换数量)。"""
    tables = find_tables(body)
    if not tables:
        return body, 0
    lines = body.split("\n")
    for t in reversed(tables):
        png, size, key = render_table(t["header"], t["rows"], t["align"], width)
        if upload_:
            url = upload(png, f"{key_prefix}/{key}.png")
        else:
            url = png.as_uri()
        lines[t["start"]:t["end"] + 1] = [f"![]({url})"]
    return "\n".join(lines), len(tables)


def main():
    ap = argparse.ArgumentParser(description="markdown 表格转图片")
    ap.add_argument("body")
    ap.add_argument("--out")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--width", type=int, default=750)
    a = ap.parse_args()
    body = Path(a.body).read_text(encoding="utf-8")
    new, n = convert_tables(body, upload_=not a.no_upload, width=a.width)
    print(f"表格 {n} 个已转图")
    if a.out:
        Path(a.out).write_text(new, encoding="utf-8")
        print("写出", a.out)
    else:
        print(new)


if __name__ == "__main__":
    main()
