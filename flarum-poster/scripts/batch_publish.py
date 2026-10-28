#!/usr/bin/env python3
"""批量转发投递器（独立于任何定时任务）

把「文章要点截图 → 裁剪白边 → COS 图床 → Flarum 发帖 → 核验 → 写发帖日志」
整条机械流程固化成一个脚本。内容提炼仍由人/LLM 预先写进稿件文件，
脚本只负责可靠执行，避免每篇手工重复。

用法:
    # 预览队列（不做事）
    python batch_publish.py --queue queue --list

    # 空跑：只渲染截图+上传，不发帖（用来核对版面）
    python batch_publish.py --queue queue --dry-run

    # 正式发（本次最多 3 篇，篇间隔 20 秒±5）
    python batch_publish.py --queue queue --limit 3 --interval 20

稿件格式（queue/*.json，一篇一个文件，按文件名排序执行）:
{
  "title": "【转载】xxx",                  // 必填，帖子标题
  "tag_id": "30",                          // 必填，板块 id（见 /api/tags）
  "tag_ids": ["59","30"],                   // 可选，多板块；给了就以它为准（本论坛子板块会自动追加父板块）
  "source_file": "/abs/path/原文.md",       // 必填，用于判重（按文件名匹配日志）
  "shot_html": "/tmp/xxx.html",            // 必填，要点截图 HTML
  "body": "/tmp/xxx_body.md",              // 必填，帖子正文（首行图片会被替换为本次图床链接）
  "source_url": "http://...",              // 选填，原文链接（写进日志）
  "author_note": "鹤医声（高鹤丽教授）微信原文",  // 选填，日志里的来源说明
  "image_key": "xyb/hys/20250918-tumor-marker.png"  // 选填，COS 对象键；省略则自动生成
}
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image, ImageChops

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
PROJECT_DIR = SKILL_DIR.parent.parent
CONFIG = SKILL_DIR / "config.env"
LOG_PATH = PROJECT_DIR / "flarum-post-log.md"
BASE = "https://bbs.xiaoyibao.com.cn"
TZ8 = timezone(timedelta(hours=8))

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


# ---------------------------------------------------------------- 基础工具

def load_env(path):
    cfg = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError("找不到 Chrome/Chromium，可用环境变量 CHROME_BIN 指定")


def get_token(cfg):
    r = requests.post(
        BASE + "/api/token",
        json={"identification": cfg["FORUM_USER"], "password": cfg["FORUM_PASS"]},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["token"]


def fetch_tags(token):
    r = requests.get(BASE + "/api/tags", headers={"Authorization": "Token " + token}, timeout=30)
    r.raise_for_status()
    out = {}
    for t in r.json().get("data", []):
        a = t["attributes"]
        out[str(t["id"])] = {"name": a.get("name"), "slug": a.get("slug")}
    return out


def posted_source_names():
    """从发帖日志里提取已发过的源文件名（含工作区外的旧记录）。"""
    if not LOG_PATH.exists():
        return set()
    text = LOG_PATH.read_text(encoding="utf-8")
    names = set()
    for m in re.findall(r"^-\s*源文件:\s*(.+?)\s*$", text, re.M):
        names.add(Path(m.strip().strip("`")).name)
    return names


# ---------------------------------------------------------------- 截图流水线

def render(html_path, out_png, css_width=750, scale=2):
    """渲染并裁剪底部白边；若内容触底（疑似截断）则自动加高重试。"""
    chrome = find_chrome()
    html_uri = Path(html_path).resolve().as_uri()
    for css_h in (3000, 6000, 12000):
        raw = out_png.with_suffix(".raw.png")
        if raw.exists():
            raw.unlink()
        cmd = [
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--window-size={css_width},{css_h}",
            f"--screenshot={raw}", html_uri,
        ]
        subprocess.run(cmd, capture_output=True, timeout=180)
        if not raw.exists() or raw.stat().st_size < 2000:
            raise RuntimeError("Chrome 截图失败（检查 Chrome 是否可用、HTML 是否可读）")

        im = Image.open(raw).convert("RGB")
        bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
        if not bbox:
            raise RuntimeError("截图全白：HTML 没有渲染出内容")
        bottom = bbox[3]
        truncated = bottom >= im.height - 2
        pad = 48
        cropped = im.crop((0, 0, im.width, min(im.height, bottom + pad)))
        cropped.save(out_png)
        if not truncated:
            raw.unlink(missing_ok=True)
            return out_png, cropped.size, css_h
        raw.unlink(missing_ok=True)
        # 触底 -> 加高再来
    return out_png, cropped.size, css_h  # 用最高档的结果兜底


def upload(png_path, key):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "upload_image.py"), str(png_path), key],
        capture_output=True, text=True,
    )
    urls = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("http")]
    if r.returncode != 0 or not urls:
        raise RuntimeError(f"COS 上传失败: {r.stdout[-300:]} {r.stderr[-300:]}")
    return urls[-1]


# ---------------------------------------------------------------- 发帖与核验

def attach_image(body, url):
    """正文首行图片统一替换成本次图床链接；没有则前置。"""
    b = body.strip()
    if b.startswith("!["):
        parts = b.split("\n", 1)
        rest = parts[1].lstrip("\n") if len(parts) > 1 else ""
        return f"![]({url})\n\n{rest}".strip() + "\n"
    return f"![]({url})\n\n{b}\n"


def resolve_tags(spec):
    """兼容单 tag_id 与多 tag_ids。"""
    ids = spec.get("tag_ids")
    if ids:
        return [str(i) for i in ids]
    if spec.get("tag_id"):
        return [str(spec["tag_id"])]
    return []


def publish(token, title, content, tag_ids):
    if isinstance(tag_ids, str):
        tag_ids = [tag_ids]
    payload = {
        "data": {
            "type": "discussions",
            "attributes": {"title": title, "content": content},
            "relationships": {"tags": {"data": [{"type": "tags", "id": str(t)} for t in tag_ids]}},
        }
    }
    r = requests.post(
        BASE + "/api/discussions",
        headers={"Authorization": "Token " + token},
        json=payload, timeout=60,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"发帖失败 HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()["data"]
    return d["id"], d["attributes"]["slug"]


def verify(token, disc_id, tag_ids, expect_img):
    h = {"Authorization": "Token " + token}
    d = requests.get(f"{BASE}/api/discussions/{disc_id}", headers=h, timeout=30).json()
    tags = [i["attributes"]["name"] for i in d.get("included", []) if i.get("type") == "tags"]
    p = requests.get(f"{BASE}/api/posts", params={"filter[discussion]": disc_id}, headers=h, timeout=30).json()
    items = p.get("data", [])
    content = items[0]["attributes"]["content"] if items else ""
    return {
        "tags": tags,
        "tag_ok": any(str(t) for t in tags),
        "len": len(content),
        "has_image": expect_img in content,
    }


def display_path(path):
    """日志里统一存相对路径（优先项目内，其次 ~/Downloads 下），便于人读与判重。"""
    p = Path(path)
    for root in (SKILL_DIR.parent.parent.parent, Path.home() / "Downloads"):
        try:
            return str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            continue
    return str(p)


def append_log(seq, spec, tag_names, post_url, body_len, img_url):
    entry = [
        "",
        f"### {seq}. {spec['title']}",
        f"- 板块: {'、'.join(tag_names)}",
        f"- 链接: {post_url}",
        f"- 首帖字数: {body_len}",
        f"- 配图: {img_url}",
    ]
    if spec.get("source_url"):
        entry.append(f"- 来源: {spec.get('author_note', '原始出处')} {spec['source_url']}")
    entry.append(f"- 源文件: {display_path(spec['source_file'])}")
    entry.append(f"- 时间: {datetime.now(TZ8).strftime('%Y-%m-%d %H:%M')} (GMT+8)")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n")


def next_seq():
    if not LOG_PATH.exists():
        return 1
    nums = [int(n) for n in re.findall(r"^###\s+(\d+)\.", LOG_PATH.read_text(encoding="utf-8"), re.M)]
    return (max(nums) + 1) if nums else 1


def default_image_key(source_file):
    stem = Path(source_file).stem
    date = re.match(r"(\d{8})", stem)
    digest = hashlib.md5(stem.encode("utf-8")).hexdigest()[:6]
    return f"xyb/hys/{date.group(1) if date else 'undated'}-{digest}.png"


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="批量转发投递器")
    ap.add_argument("--queue", required=True, help="稿件目录（内含 *.json）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多发几篇（0=不限）")
    ap.add_argument("--interval", type=float, default=16, help="篇间隔秒数（会加 ±5 抖动）")
    ap.add_argument("--dry-run", action="store_true", help="只渲染截图并上传，不发帖")
    ap.add_argument("--list", action="store_true", help="只列出队列与判重结果")
    ap.add_argument("--force", action="store_true", help="忽略判重，强制发送（慎用）")
    ap.add_argument("--width", type=int, default=750, help="截图 CSS 宽度，默认 750")
    args = ap.parse_args()

    queue = Path(args.queue)
    if not queue.is_dir():
        sys.exit(f"队列目录不存在: {queue}")
    files = sorted(queue.glob("*.json"))
    if not files:
        sys.exit(f"队列为空: {queue}")

    posted = posted_source_names()

    if args.list:
        print(f"队列 {queue}  共 {len(files)} 篇\n")
        for f in files:
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [坏稿件] {f.name}: {e}")
                continue
            name = Path(spec.get("source_file", "")).name
            flag = "已发过·跳过" if name in posted else "待发"
            print(f"  [{flag}] {f.name}  →  {spec.get('title', '(无标题)')}")
        return

    cfg = load_env(CONFIG)
    token = get_token(cfg)
    tags = fetch_tags(token)

    done, skipped, failed = [], [], []
    seq = next_seq()
    sent = 0

    for f in files:
        if args.limit and sent >= args.limit:
            print(f"\n已达本次上限 {args.limit} 篇，停止。")
            break

        spec = json.loads(f.read_text(encoding="utf-8"))
        tag_ids = resolve_tags(spec)
        missing = [n for n in ("title", "source_file", "shot_html", "body") if not spec.get(n)]
        if not tag_ids:
            missing.append("tag_id/tag_ids")
        if missing:
            failed.append((f.name, f"缺字段 {','.join(missing)}"))
        else:
            name = Path(spec["source_file"]).name
            if name in posted and not args.force:
                print(f"跳过（已发过）: {name}")
                skipped.append(name)
                continue

            unknown = [t for t in tag_ids if t not in tags]
            if unknown:
                failed.append((f.name, f"板块 id 不存在: {unknown}"))
                continue
            tag_names = [tags[t]["name"] for t in tag_ids]

            print(f"\n[{f.name}] {spec['title']}")
            try:
                png = Path(spec.get("png_out") or f"/tmp/{f.stem}.png")
                shot, size, css_h = render(spec["shot_html"], png, css_width=args.width)
                print(f"  截图 {size[0]}x{size[1]}（css 高度 {css_h}）")

                key = spec.get("image_key") or default_image_key(spec["source_file"])
                img_url = upload(shot, key)
                print(f"  图床 {img_url}")

                body = Path(spec["body"]).read_text(encoding="utf-8")
                content = attach_image(body, img_url)

                # 本站 Flarum 1.x 默认不支持 md 表格（原始 HTML 也会被转义），
                # 因此默认把表格转成图片嵌入；站点若装了 PipeTables 扩展，
                # 可在 config.env 把 TABLE_MODE 设为 md 直接发原生表格。
                if cfg.get("TABLE_MODE", "image").lower() != "md":
                    from md_table_image import convert_tables
                    content, ntab = convert_tables(content)
                    if ntab:
                        print(f"  表格 {ntab} 个 → 图片")

                if args.dry_run:
                    print(f"  [空跑] 未发帖 板块={tag_names}")
                    done.append((spec["title"], "dry-run", size, img_url))
                    sent += 1
                    continue

                disc_id, slug = publish(token, spec["title"], content, tag_ids)
                post_url = f"{BASE}/d/{slug}"
                chk = verify(token, disc_id, tag_ids, img_url)
                ok = chk["has_image"] and chk["len"] > 200
                print(f"  发帖 id={disc_id} 板块={chk['tags']} 字数={chk['len']} 含图={chk['has_image']} -> {'OK' if ok else '需复查'}")

                append_log(seq, spec, tag_names, post_url, chk["len"], img_url)
                posted.add(name)
                done.append((spec["title"], post_url, size, img_url))
                seq += 1
                sent += 1

                if args.limit and sent >= args.limit:
                    continue
                wait = max(1.0, args.interval + random.uniform(-5, 5))
                time.sleep(wait)
            except Exception as e:
                print(f"  ✗ 失败: {e}")
                failed.append((f.name, str(e)))

    print("\n" + "=" * 60)
    print(f"成功 {len(done)} / 跳过 {len(skipped)} / 失败 {len(failed)}")
    for t, u, size, img in done:
        print(f"  ✓ {t}\n    {u}\n    图 {img}  ({size[0]}x{size[1]})")
    for n, why in failed:
        print(f"  ✗ {n}: {why}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
