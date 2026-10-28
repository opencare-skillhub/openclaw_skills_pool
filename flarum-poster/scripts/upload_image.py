#!/usr/bin/env python3
"""Upload an image to Tencent COS and print its public URL.

Reads credentials from config.env (same directory as SKILL.md).
The config file is never committed or shared; only config.env.example ships.

Usage:
    python3 scripts/upload_image.py <local-image-path> [object-key]
"""
import os
import sys
import time


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
    if len(sys.argv) < 2:
        print("用法: python3 scripts/upload_image.py <本地图片路径> [对象名]", file=sys.stderr)
        sys.exit(1)
    local_path = sys.argv[1]
    if not os.path.isfile(local_path):
        print("找不到文件: " + local_path, file=sys.stderr)
        sys.exit(1)

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    env_path = os.path.join(root, "config.env")
    if not os.path.isfile(env_path):
        print("找不到 config.env：请先复制 config.env.example 为 config.env 并填入你的 COS 信息", file=sys.stderr)
        sys.exit(1)
    cfg = load_env(env_path)
    for k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET", "COS_REGION"):
        if not cfg.get(k) or cfg[k].startswith("your-"):
            print("config.env 中 %s 未填写" % k, file=sys.stderr)
            sys.exit(1)

    from qcloud_cos import CosConfig, CosS3Client
    config = CosConfig(
        Region=cfg["COS_REGION"],
        SecretId=cfg["COS_SECRET_ID"],
        SecretKey=cfg["COS_SECRET_KEY"],
        Scheme="https",
    )
    client = CosS3Client(config)
    bucket = cfg["COS_BUCKET"]
    region = cfg["COS_REGION"]

    if len(sys.argv) >= 3:
        key = sys.argv[2]
    else:
        key = "uploads/{}/{}".format(time.strftime("%Y-%m-%d"), os.path.basename(local_path))

    ext = os.path.splitext(local_path)[1].lower()
    ctype = {
        ".webp": "image/webp",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")

    with open(local_path, "rb") as f:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f,
            ACL="public-read",
            ContentType=ctype,
        )
    print("https://{}.cos.{}.myqcloud.com/{}".format(bucket, region, key))


if __name__ == "__main__":
    main()
