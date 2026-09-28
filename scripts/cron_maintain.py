#!/usr/bin/env python3
"""GoodSkill 定期维护（由 Muse 的定时任务每 6 小时执行）.

1. 运行 build_api.py 聚合 skills.json（含 Issue 后验评价 + Release 下载数）
2. 把 docs/api/v1/*.json 推到仓库（GitHub Pages 即时生效）
3. 处理 label=new-skill 且尚未处理过的提交 issue（自动转 PR）
"""
import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_auth import api

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_REPO = os.environ.get("GOODSKILL_REPO", "GuohongLi/goodskill")


def main():
    print("== goodskill maintain ==")
    # 1. 聚合
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "build_api.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if r.returncode != 0:
        print("build_api failed", file=sys.stderr)
        return 1

    # 2. 上传 JSON
    api_dir = os.path.join(ROOT, "docs", "api", "v1")
    pushed = 0
    for dirpath, _, fns in os.walk(api_dir):
        for fn in sorted(fns):
            if not fn.endswith(".json"):
                continue
            local = os.path.join(dirpath, fn)
            rel = os.path.relpath(local, ROOT).replace(os.sep, "/")
            with open(local, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            code, body = api("GET", f"/repos/{OWNER_REPO}/contents/{rel}")
            payload = {"message": f"chore: rebuild api ({rel})", "content": content}
            if code == 200 and isinstance(body, dict) and body.get("sha"):
                payload["sha"] = body["sha"]
            code, _ = api("PUT", f"/repos/{OWNER_REPO}/contents/{rel}", payload)
            print(f"  push {rel}: {code}")
            if code in (200, 201):
                pushed += 1
    print(f"pushed {pushed} json files")

    # 3. 处理 new-skill 提交
    code, issues = api("GET", f"/repos/{OWNER_REPO}/issues?labels=new-skill&state=open&per_page=20")
    for issue in (issues if code == 200 else []) or []:
        if "pull_request" in issue:
            continue
        num = issue["number"]
        code, comments = api("GET", f"/repos/{OWNER_REPO}/issues/{num}/comments?per_page=20")
        done = any(
            ("已自动生成 PR" in (c.get("body") or "") or "自动转 PR 失败" in (c.get("body") or ""))
            for c in ((comments if code == 200 else []) or [])
        )
        if done:
            continue
        print(f"  new-skill #{num}: converting to PR")
        env = dict(os.environ, ISSUE_NUMBER=str(num), GOODSKILL_REPO=OWNER_REPO)
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "new_skill_from_issue.py")],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        print(r.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
