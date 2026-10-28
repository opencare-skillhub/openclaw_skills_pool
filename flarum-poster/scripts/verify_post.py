import os

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


cfg = load_env(os.path.join(SKILL_DIR, "config.env"))
r = requests.post(
    BASE + "/api/token",
    json={"identification": cfg["FORUM_USER"], "password": cfg["FORUM_PASS"]},
    timeout=30,
)
token = r.json()["token"]
h = {"Authorization": "Token " + token}

d = requests.get(BASE + "/api/discussions/67", headers=h, timeout=30).json()
tags = [i["attributes"]["name"] for i in d.get("included", []) if i.get("type") == "tags"]
posts = [i for i in d.get("included", []) if i.get("type") == "posts"]
print("tags=", tags)
print("included_posts=", len(posts))

p = requests.get(BASE + "/api/posts", params={"filter[discussion]": 67}, headers=h, timeout=30).json()
items = p.get("data", [])
print("posts_api_count=", len(items))
if items:
    c = items[0]["attributes"].get("content", "")
    print("first_post_len=", len(c))
    print("has_image=", "cos.ap-guangzhou.myqcloud.com" in c)
    print("head=", c[:90].replace("\n", " "))
