#!/usr/bin/env python3
"""GoodSkill API 聚合脚本.

事实来源:
  skills/<slug>/SKILL.md + skill.yaml   # skill 本体与元数据
  GitHub Issues (label=review)           # 后验评价（结构化表单）
  GitHub Releases                        # 下载数（asset 名以 slug 开头）

输出:
  docs/api/v1/skills.json                # 摘要（给 Agent 选型）
  docs/api/v1/skills/<slug>.json         # 详情（含 SKILL.md 全文 + 全部评价）

环境变量:
  GITHUB_TOKEN    # Actions 内用 secrets.GITHUB_TOKEN
  GOODSKILL_REPO  # 默认 GuohongLi/goodskill
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_auth import api as gh_api

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(REPO_ROOT, "skills")
API_DIR = os.path.join(REPO_ROOT, "docs", "api", "v1")

OWNER_REPO = os.environ.get("GOODSKILL_REPO", "GuohongLi/goodskill")
SITE = "https://guohongli.github.io/goodskill"


def gh_get(path):
    """GitHub API GET；失败返回 None（降级为只用本地数据）。"""
    try:
        code, body = gh_api("GET", path)
    except Exception as e:
        print(f"  [warn] GET {path} -> {e}", file=sys.stderr)
        return None
    if code != 200:
        print(f"  [warn] GET {path} -> HTTP {code}", file=sys.stderr)
        return None
    return body


def parse_sections(body):
    """把 Issue 表单正文按 '### 标题' 切成 dict。"""
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


def parse_review(issue):
    """从一条 review issue 解析出结构化评价；解析失败返回 None。"""
    sec = parse_sections(issue.get("body") or "")
    slug_raw = field(sec, "Skill", "skill")
    slug = re.sub(r"[^a-z0-9-]", "", slug_raw.strip().lower().split()[0] if slug_raw else "")
    if not slug:
        return None
    rating_raw = field(sec, "评分")
    m = re.search(r"[1-5]", rating_raw or "")
    rating = int(m.group(0)) if m else None
    tokens_raw = field(sec, "token", "Token", "TOKEN")
    tm = re.search(r"(\d[\d,]*)", (tokens_raw or "").replace("k", "000").replace("K", "000"))
    token_cost = int(tm.group(1).replace(",", "")) if tm else None
    who_raw = field(sec, "评价人", "你是")
    reviewer_type = "AI Agent" if re.search(r"\[x\]\s*AI Agent", who_raw, re.I) else (
        "人类用户" if re.search(r"\[x\]\s*人类用户", who_raw, re.I) else "")
    return {
        "slug": slug,
        "rating": rating,
        "what_it_does": field(sec, "能做什么"),
        "strengths": field(sec, "好的方面", "优点"),
        "weaknesses": field(sec, "不好", "缺点", "不足"),
        "token_cost": token_cost,
        "reviewer": (issue.get("user") or {}).get("login", ""),
        "reviewer_type": reviewer_type,
        "date": (issue.get("created_at") or "")[:10],
        "source_url": issue.get("html_url", ""),
    }


def load_skills():
    skills = {}
    for slug in sorted(os.listdir(SKILLS_DIR)):
        sdir = os.path.join(SKILLS_DIR, slug)
        if not os.path.isdir(sdir):
            continue
        meta_path = os.path.join(sdir, "skill.yaml")
        md_path = os.path.join(sdir, "SKILL.md")
        if not (os.path.exists(meta_path) and os.path.exists(md_path)):
            print(f"  [warn] skip {slug}: 缺少 skill.yaml 或 SKILL.md", file=sys.stderr)
            continue
        meta = {}
        for line in open(meta_path, encoding="utf-8"):
            if ":" in line and not line.startswith((" ", "\t", "#")):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        tags = [t.strip() for t in meta.get("tags", "").strip("[]").split(",") if t.strip()]
        with open(md_path, encoding="utf-8") as f:
            skill_md = f.read()
        skills[slug] = {
            "slug": slug,
            "name": meta.get("name", slug),
            "display_name": meta.get("display_name", slug),
            "description": meta.get("description", ""),
            "category": meta.get("category", "未分类"),
            "tags": tags,
            "uploader": meta.get("uploader", ""),
            "version": meta.get("version", "1.0.0"),
            "trigger": meta.get("trigger", ""),
            "updated": meta.get("updated", ""),
            "skill_md": skill_md,
        }
    return skills


def main():
    print("== goodskill build ==")
    skills = load_skills()
    print(f"skills: {len(skills)}")

    # 1. 拉取后验评价
    reviews_by_slug = {s: [] for s in skills}
    issues = gh_get(f"/repos/{OWNER_REPO}/issues?labels=review&state=all&per_page=100") or []
    n_review = 0
    for issue in issues:
        if "pull_request" in issue:
            continue
        r = parse_review(issue)
        if r and r["slug"] in reviews_by_slug and r["rating"]:
            reviews_by_slug[r["slug"]].append(r)
            n_review += 1
    print(f"reviews: {n_review}")

    # 2. 拉取下载数（releases 的 asset）
    downloads = {s: 0 for s in skills}
    download_url = {s: "" for s in skills}
    releases = gh_get(f"/repos/{OWNER_REPO}/releases?per_page=100") or []
    for rel in releases:
        for a in rel.get("assets") or []:
            name = a.get("name", "")
            for slug in skills:
                if name.startswith(slug + "-") and name.endswith(".zip"):
                    downloads[slug] += a.get("download_count", 0)
                    if not download_url[slug]:
                        download_url[slug] = a.get("browser_download_url", "")
    # 兜底：zip 直接放在 docs/downloads/ 下
    dl_dir = os.path.join(REPO_ROOT, "docs", "downloads")
    for slug in skills:
        if not download_url[slug] and os.path.isdir(dl_dir):
            for fn in os.listdir(dl_dir):
                if fn.startswith(slug + "-") and fn.endswith(".zip"):
                    download_url[slug] = f"{SITE}/downloads/{fn}"
    print(f"releases: {len(releases)}")

    # 3. 聚合 + 写 JSON
    os.makedirs(os.path.join(API_DIR, "skills"), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    catalog = []
    for slug, s in skills.items():
        revs = sorted(reviews_by_slug[slug], key=lambda r: r["date"], reverse=True)
        ratings = [r["rating"] for r in revs if r["rating"]]
        rating_avg = round(sum(ratings) / len(ratings), 2) if ratings else None
        positive_rate = round(sum(1 for x in ratings if x >= 4) / len(ratings), 3) if ratings else None
        token_samples = [r["token_cost"] for r in revs if r["token_cost"]]
        token_avg = int(sum(token_samples) / len(token_samples)) if token_samples else None
        strengths_top = [r["strengths"].split("\n")[0][:60] for r in revs if r["strengths"]][:3]
        weaknesses_top = [r["weaknesses"].split("\n")[0][:60] for r in revs if r["weaknesses"]][:3]

        summary = {
            "slug": slug,
            "name": s["name"],
            "display_name": s["display_name"],
            "description": s["description"],
            "category": s["category"],
            "tags": s["tags"],
            "uploader": s["uploader"],
            "version": s["version"],
            "trigger": s["trigger"],
            "updated": s["updated"],
            "rating_avg": rating_avg,
            "rating_count": len(ratings),
            "positive_rate": positive_rate,
            "downloads": downloads[slug],
            "token_cost_avg": token_avg,
            "token_cost_samples": len(token_samples),
            "strengths_top": strengths_top,
            "weaknesses_top": weaknesses_top,
            "page_url": f"{SITE}/skill.html?s={slug}",
            "detail_url": f"{SITE}/api/v1/skills/{slug}.json",
            "download_url": download_url[slug],
        }
        catalog.append(summary)

        detail = dict(summary)
        detail["skill_md"] = s["skill_md"]
        detail["reviews"] = revs
        with open(os.path.join(API_DIR, "skills", f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)

    with open(os.path.join(API_DIR, "skills.json"), "w", encoding="utf-8") as f:
        json.dump({
            "registry": "goodskill",
            "api_version": "v1",
            "site": SITE,
            "repo": f"https://github.com/{OWNER_REPO}",
            "updated_at": now,
            "count": len(catalog),
            "skills": catalog,
        }, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(catalog)} skills -> {API_DIR}")


if __name__ == "__main__":
    main()
