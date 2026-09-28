#!/usr/bin/env python3
"""把一条 label=new-skill 的 Issue 转成 Pull Request.

Issue 表单字段:
  ### Skill slug / ### 展示名 / ### 一句话描述 / ### 分类 /
  ### 标签（逗号分隔）/ ### SKILL.md 原始链接 / ### 触发词

流程: 下载 SKILL.md → 校验 → 建分支写 skills/<slug>/ → 开 PR → 在 issue 下留言。
环境变量: GITHUB_TOKEN, GOODSKILL_REPO(默认 GuohongLi/goodskill), ISSUE_NUMBER
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

OWNER_REPO = os.environ.get("GOODSKILL_REPO", "GuohongLi/goodskill")
TOKEN = os.environ["GITHUB_TOKEN"]
ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
API = "https://api.github.com"


def api(method, path, data=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "goodskill-newskill-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"message": f"HTTP {e.code}"}


def comment(text):
    api("POST", f"/repos/{OWNER_REPO}/issues/{ISSUE_NUMBER}/comments", {"body": text})


def parse_sections(body):
    sections, cur = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1).strip()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def field(sec, *keywords):
    for k, v in sec.items():
        if any(kw in k for kw in keywords):
            return v
    return ""


def fail(msg):
    comment("❌ 自动转 PR 失败：" + msg + "\n\n请检查表单字段后重新提交，或直接按站内「上传 Skill」页的方式二发 PR。")
    print("FAIL:", msg, file=sys.stderr)
    sys.exit(1)


def main():
    code, issue = api("GET", f"/repos/{OWNER_REPO}/issues/{ISSUE_NUMBER}")
    if code != 200:
        print(f"get issue -> {code}", file=sys.stderr)
        sys.exit(1)
    sec = parse_sections(issue.get("body") or "")

    slug = re.sub(r"[^a-z0-9-]", "", field(sec, "Skill slug", "slug").strip().lower())
    if not slug or len(slug) > 40:
        fail("slug 非法（只允许小写字母、数字、短横线）。")
    display_name = field(sec, "展示名").strip() or slug
    description = field(sec, "一句话描述").strip()
    category = field(sec, "分类").strip() or "未分类"
    tags = field(sec, "标签").strip()
    _url_raw = field(sec, "SKILL.md").strip()
    skill_url = _url_raw.split()[0] if _url_raw else ""
    trigger = field(sec, "触发词").strip()
    if not description:
        fail("「一句话描述」不能为空。")
    if not (skill_url.startswith("https://")):
        fail("SKILL.md 原始链接必须是 https:// 开头的可公开访问链接。")

    # 下载 SKILL.md
    try:
        req = urllib.request.Request(skill_url, headers={"User-Agent": "goodskill-newskill-bot"})
        with urllib.request.urlopen(req, timeout=60) as r:
            skill_md = r.read().decode("utf-8", "replace")
    except Exception as e:
        fail(f"下载 SKILL.md 失败：{e}")
    if len(skill_md) < 300:
        fail("下载到的 SKILL.md 内容过短（<300 字符），请确认链接指向的是原始文件。")

    # 检查是否已存在
    code, _ = api("GET", f"/repos/{OWNER_REPO}/contents/skills/{slug}/SKILL.md")
    if code == 200:
        fail(f"skills/{slug} 已存在，如需更新请直接发 PR。")

    from datetime import date
    skill_yaml = (
        f"slug: {slug}\nname: {slug}\ndisplay_name: {display_name}\n"
        f"description: {description}\ncategory: {category}\n"
        f"tags: [{tags}]\nuploader: {(issue.get('user') or {}).get('login', '')}\n"
        f"version: 1.0.0\ntrigger: {trigger}\nupdated: {date.today().isoformat()}\n"
    )

    # 建分支 + 写文件（Contents API）
    import base64
    code, repo = api("GET", f"/repos/{OWNER_REPO}")
    default_branch = repo.get("default_branch", "main")
    code, ref = api("GET", f"/repos/{OWNER_REPO}/git/ref/heads/{default_branch}")
    base_sha = ref["object"]["sha"]
    branch = f"new-skill/{slug}"
    code, _ = api("POST", f"/repos/{OWNER_REPO}/git/refs",
                  {"ref": f"refs/heads/{branch}", "sha": base_sha})
    if code not in (200, 201):
        fail("创建分支失败。")

    def put_file(path, content, msg):
        payload = {"message": msg, "content": base64.b64encode(content.encode("utf-8")).decode(),
                   "branch": branch}
        code, body = api("PUT", f"/repos/{OWNER_REPO}/contents/{path}", payload)
        if code not in (200, 201):
            fail(f"写入 {path} 失败：{body.get('message')}")

    put_file(f"skills/{slug}/SKILL.md", skill_md, f"new skill: add {slug}/SKILL.md")
    put_file(f"skills/{slug}/skill.yaml", skill_yaml, f"new skill: add {slug}/skill.yaml")

    # 开 PR
    code, pr = api("POST", f"/repos/{OWNER_REPO}/pulls", {
        "title": f"新 Skill 收录：{display_name} ({slug})",
        "head": branch,
        "base": default_branch,
        "body": f"由 #{ISSUE_NUMBER} 自动生成。\n\n- 展示名：{display_name}\n- 描述：{description}\n"
                f"- 分类：{category}\n\n合并后将自动收录进注册表 API。",
    })
    if code not in (200, 201):
        fail("创建 PR 失败。")
    comment(f"✅ 已自动生成 PR：{pr['html_url']}\n\n管理员合并后，你的 Skill 就会上线到 GoodSkill 注册表。")
    print("PR:", pr["html_url"])


if __name__ == "__main__":
    main()
