"""把打好的包发布成 GitHub Release（tag = v<版本>）。

用法：
    python tools/publish_release.py                      # 用缓存的 git 凭据
    python tools/publish_release.py --token ghp_xxx      # 或显式传 PAT
    python tools/publish_release.py --withdraw           # 撤回同名 Release 与 tag 后重发

做的事：
    1. 取 token（--token / GITHUB_TOKEN / `git credential fill` 读 Windows 凭据管理器）
    2. GET /user 确认身份与仓库访问
    3. 可选 --withdraw：删掉同名 tag 的 Release 与远端 tag（本地 tag 需另外 `git tag -d`）
    4. 从 CHANGELOG.md 抽当前版本小节当发布说明
    5. 建 Release（已存在则 PATCH 覆盖说明），tag 由 git 侧已推好
    6. 上传 release/*.zip 作为资产（同名资产先删后传）
    7. 打印资产列表、大小与 SHA256
token 只在本进程内存里用，不落盘、不写进 .git/config。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.branding import APP_NAME, EXE_STEM, VERSION  # noqa: E402

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def token_from_git() -> str:
    """从 git 凭据管理器取已缓存的 GitHub 凭据（不打印）。"""
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            text=True, capture_output=True, timeout=30, cwd=ROOT,
        ).stdout
    except Exception:                                          # noqa: BLE001
        return ""
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def api(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode() or "{}") if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:300]}


def upload_asset(upload_url: str, token: str, path: Path) -> tuple[int, dict]:
    data = path.read_bytes()
    req = urllib.request.Request(
        f"{upload_url}?name={urllib.parse.quote(path.name)}", data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:300]}


def changelog_section() -> str:
    """取 CHANGELOG.md 里当前版本的小节正文（到下一个二级标题为止）。"""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## v{re.escape(VERSION)}[^\n]*\n(.*?)(?=^## |\Z)", text,
                  re.S | re.M)
    return (m.group(1).strip() if m else "").strip()


def release_body(assets: list[Path]) -> str:
    lines = [f"**{APP_NAME} v{VERSION}** · Windows 10/11 x64 · 免安装单文件，无需驱动与管理员权限", ""]
    sec = changelog_section()
    if sec:
        lines += [sec, ""]
    lines += ["## 下载", "",
              f"- `{assets[0].name}`：解压后双击 `{EXE_STEM}.exe` 即用；"
              "首次运行会在 EXE 旁边生成 `config/ protocols/ assets/ data/`。",
              ]
    for extra in assets[1:]:
        lines.append(f"- `{extra.name}`：源码归档（含 README、构建与自检脚本）。")
    lines += ["", "## 校验", "", "| 文件 | 大小 | SHA256（前 16 位） |", "| --- | --- | --- |"]
    for a in assets:
        size = a.stat().st_size
        size_txt = f"{size / 1048576:.1f} MB" if size > 1048576 else f"{size / 1024:.0f} KB"
        lines.append(f"| `{a.name}` | {size_txt} | `{sha256(a)[:16].upper()}` |")
    lines += ["", "完整变更见 [CHANGELOG.md](https://github.com/StrelitziaZYN/HID-Battery-Hub/blob/main/CHANGELOG.md)，"
              "使用与疑难解答见 [README.md](https://github.com/StrelitziaZYN/HID-Battery-Hub#readme)。"]
    return "\n".join(lines)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--owner", default="StrelitziaZYN")
    ap.add_argument("--repo", default="HID-Battery-Hub")
    ap.add_argument("--tag", default=f"v{VERSION}")
    ap.add_argument("--withdraw", action="store_true",
                    help="先撤回同名版本的 Release 与远端 tag，再按当前代码重新发布")
    args = ap.parse_args()

    token = args.token.strip() or token_from_git()
    if not token:
        print("ERROR: 没有可用 token（--token / GITHUB_TOKEN / git 凭据）")
        return 2
    code, me = api("GET", f"{API}/user", token)
    if code != 200:
        print(f"ERROR: token 不可用（{code}）{me.get('message')}")
        return 3
    print(f"[auth] {me['login']}（token 长度 {len(token)}，不落盘）")

    if args.withdraw:
        code, old = api("GET", f"{API}/repos/{args.owner}/{args.repo}/releases/tags/{args.tag}", token)
        if code == 200:
            code, info = api("DELETE",
                             f"{API}/repos/{args.owner}/{args.repo}/releases/{old['id']}", token)
            print(f"[withdraw] 删除 Release {args.tag} → {code}")
        else:
            print(f"[withdraw] 没有已存在的 Release {args.tag}（{code}）")
        code, info = api("DELETE", f"{API}/repos/{args.owner}/{args.repo}/git/refs/tags/{args.tag}", token)
        print(f"[withdraw] 删除远端 tag {args.tag} → {code}")

    rel_dir = ROOT / "release"
    portable = rel_dir / f"{EXE_STEM}_v{VERSION}_win64.zip"
    srcs = sorted(rel_dir.glob(f"{EXE_STEM}_v{VERSION}_*_src.zip"))
    assets = [p for p in (portable, srcs[-1] if srcs else None) if p and p.exists()]
    if not assets:
        print(f"ERROR: 找不到资产，先跑 build_exe.py / make_release.py")
        return 4

    body = release_body(assets)
    payload = {"tag_name": args.tag, "name": f"{APP_NAME} v{VERSION}",
               "body": body, "draft": False, "prerelease": False,
               "make_latest": "true"}

    code, rel = api("GET", f"{API}/repos/{args.owner}/{args.repo}/releases/tags/{args.tag}", token)
    if code == 200:
        print(f"[release] 已存在 id={rel['id']}，更新说明")
        code, rel = api("PATCH", f"{API}/repos/{args.owner}/{args.repo}/releases/{rel['id']}", token, payload)
    else:
        code, rel = api("POST", f"{API}/repos/{args.owner}/{args.repo}/releases", token, payload)
    if code not in (200, 201):
        print(f"ERROR: 建 Release 失败 {code} {rel.get('message')}")
        return 5
    rel_id, upload_url = rel["id"], rel["upload_url"].split("{")[0]
    print(f"[release] {rel['html_url']}")

    old = {a["name"]: a["id"] for a in rel.get("assets", [])}
    for path in assets:
        if path.name in old:
            api("DELETE", f"{API}/repos/{args.owner}/{args.repo}/releases/assets/{old[path.name]}", token)
            print(f"[asset] 删除同名旧资产 {path.name}")
        code, info = upload_asset(upload_url, token, path)
        if code not in (200, 201):
            print(f"ERROR: 上传 {path.name} 失败 {code} {info.get('message')}")
            return 6
        print(f"[asset] {info['name']}  {info['size'] / 1048576:.1f} MB  "
              f"sha256 {sha256(path)[:16].upper()}  {info['browser_download_url']}")

    code, check = api("GET", f"{API}/repos/{args.owner}/{args.repo}/releases/tags/{args.tag}", token)
    print(f"[verify] {code}  assets={[a['name'] for a in check.get('assets', [])]}  "
          f"published={check.get('published_at')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
