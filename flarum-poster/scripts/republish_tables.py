#!/usr/bin/env python3
"""把已发布帖子里「显示不出来」的 markdown 表格换成表格图片，并回写正文。

Flarum 本站点实测：md 管道表格不渲染、原始 HTML 被转义 => 表格只能走图片。
用法:
    python republish_tables.py --scan                 # 扫全站，列出含表格的帖子
    python republish_tables.py --ids 137,138 --dry-run  # 只渲染预览，不改线上
    python republish_tables.py --ids 137,138            # 正式替换并核验
    python republish_tables.py --all --yes              # 批量修全部含表格的帖子
"""
import argparse
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_publish import BASE, CONFIG, load_env  # noqa: E402
from md_table_image import convert_tables, find_tables  # noqa: E402


def client():
    cfg = load_env(CONFIG)
    r = requests.post(BASE + "/api/token",
                      json={"identification": cfg["FORUM_USER"], "password": cfg["FORUM_PASS"]}, timeout=30)
    r.raise_for_status()
    return {"Authorization": "Token " + r.json()["token"]}


def first_post(h, did):
    d = requests.get(f"{BASE}/api/posts", headers=h, params={"filter[discussion]": did}, timeout=30).json()
    items = d.get("data", [])
    return (items[0]["id"], items[0]["attributes"]["content"]) if items else (None, "")


def scan(h):
    hits, offset = [], 0
    while True:
        r = requests.get(f"{BASE}/api/discussions", headers=h,
                         params={"page[offset]": offset, "page[limit]": 50}, timeout=60).json()
        data = r.get("data", [])
        if not data:
            break
        hits += [(int(d["id"]), d["attributes"]["title"]) for d in data]
        offset += 50
        if offset > 800:
            break
    out = []
    for did, title in sorted(hits):
        _, content = first_post(h, did)
        n = len(find_tables(content))
        if n:
            out.append((did, title, n))
    return out


def main():
    ap = argparse.ArgumentParser(description="表格转图后回写帖子")
    ap.add_argument("--ids", help="讨论 id，逗号分隔")
    ap.add_argument("--all", action="store_true", help="处理全站所有含表格的帖子")
    ap.add_argument("--scan", action="store_true", help="只扫描不改")
    ap.add_argument("--dry-run", action="store_true", help="只本地渲染，不上传不发帖")
    ap.add_argument("--width", type=int, default=750)
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    a = ap.parse_args()

    h = client()
    targets = []
    if a.scan or a.all:
        found = scan(h)
        print(f"含 md 表格的帖子 {len(found)} 篇：")
        for did, title, n in found:
            print(f"  id={did}  表格 {n} 个  {title[:44]}")
        if a.scan or not a.all:
            return
        targets = [did for did, _, _ in found]
    elif a.ids:
        targets = [int(x) for x in a.ids.replace(" ", "").split(",") if x]
    else:
        sys.exit("需要 --ids 或 --all / --scan")

    if not a.dry_run and not a.yes:
        print(f"\n将改写 {len(targets)} 篇帖子正文（id={targets}），确认请加 --yes")
        return

    ok = bad = 0
    for did in targets:
        pid, content = first_post(h, did)
        if not pid:
            print(f"✗ id={did} 没有首帖，跳过")
            bad += 1
            continue
        n = len(find_tables(content))
        if not n:
            print(f"- id={did} 无表格，跳过")
            continue
        new, n2 = convert_tables(content, upload_=not a.dry_run, width=a.width)
        if a.dry_run:
            print(f"  [干跑] id={did} 表格 {n2} 个已渲染（未上传）")
            continue
        r = requests.patch(f"{BASE}/api/posts/{pid}",
                           headers={**h, "Content-Type": "application/vnd.api+json"},
                           json={"data": {"type": "posts", "id": str(pid), "attributes": {"content": new}}},
                           timeout=60)
        if r.status_code != 200:
            print(f"✗ id={did} PATCH {r.status_code} {r.text[:200]}")
            bad += 1
            continue
        html = r.json()["data"]["attributes"].get("contentHtml", "")
        leftover = len(re.findall(r"<p>\s*\|", html))
        imgs = len(re.findall(r"<img ", html))
        good = leftover == 0
        print(f"  {'✓' if good else '⚠'} id={did} 表格{n2}个→图片  残留md表格={leftover}  正文图片={imgs}")
        ok += 1 if good else 0
        bad += 0 if good else 1
    print(f"\n完成 {len(targets)} 篇：正常 {ok}，异常/失败 {bad}")


if __name__ == "__main__":
    main()
