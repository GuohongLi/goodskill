#!/usr/bin/env python3
"""GoodSkill 共享的 GitHub API 鉴权.

两种模式:
  1. GITHUB_TOKEN 环境变量存在 → Bearer（GitHub Actions 内）
  2. 否则 → custom.github surrogate（本地 / 定时任务）
"""
import json
import os
import sys
import urllib.request
import urllib.error


def api(method, path, data=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "goodskill-bot",
        },
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    else:
        sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
        from dynamic_credentials import add_surrogate_to_request
        add_surrogate_to_request(req, "custom.github", allowed_hosts=["api.github.com"])
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"message": f"HTTP {e.code}"}
