import json
import os
import sys

import requests

SKILL_DIR = "/Users/qinxiaoqiang/Downloads/bbs-aiop/.workbuddy/skills/flarum-poster"
BASE = "https://bbs.xiaoyibao.com.cn"


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


def main():
    body_path, title, tag_id = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(body_path, encoding="utf-8") as f:
        content = f.read().strip()

    cfg = load_env(os.path.join(SKILL_DIR, "config.env"))
    user, pwd = cfg["FORUM_USER"], cfg["FORUM_PASS"]

    r = requests.post(
        BASE + "/api/token",
        json={"identification": user, "password": pwd},
        timeout=30,
    )
    if r.status_code != 200:
        print("AUTH FAIL", r.status_code, r.text[:300])
        sys.exit(1)
    token = r.json()["token"]

    payload = {
        "data": {
            "type": "discussions",
            "attributes": {"title": title, "content": content},
            "relationships": {
                "tags": {"data": [{"type": "tags", "id": str(tag_id)}]}
            },
        }
    }
    r = requests.post(
        BASE + "/api/discussions",
        headers={"Authorization": "Token " + token},
        json=payload,
        timeout=60,
    )
    print("HTTP", r.status_code)
    data = r.json()
    if "data" in data:
        a = data["data"]["attributes"]
        print("id=", data["data"]["id"])
        print("slug=", a.get("slug"))
        print("title=", a.get("title"))
        print("URL=", BASE + "/d/" + str(a.get("slug")))
    else:
        print("ERROR", json.dumps(data, ensure_ascii=False)[:600])
        sys.exit(1)


if __name__ == "__main__":
    main()
