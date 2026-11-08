#!/usr/bin/env python3
"""Publish invoicesnap.work: push dist/ to branch gh-pages and verify production (NIK-545, 8 Nov 2026).

Usage:
  python3 publish.py                 run from a clean, pushed main
  python3 publish.py --from-branch   allow another branch (only for the first cutover, before main has the generator)
Needs: git push access and the gh CLI. It runs the build, the crawl and the tests first and stops on any failure.
Steps: checks -> commit dist/ on gh-pages -> Pages source = gh-pages -> wait for the Pages build -> wait until production
       serves the same bytes as dist/ -> crawl production -> tell IndexNow about new and changed pages.
A failure at any step leaves the last good deployment live. Fix the cause and run it again: it requests a build if needed.

Why a local script and not GitHub Actions (checked 8 Nov 2026): since 28 Sep 2026 every Actions job stops with "account is
locked due to a billing issue". Branch builds of Pages still ran during the lock (29 Sep, 1 Oct, 2 Oct), so this path
works today. `gh api repos/Nikita-Guzenko/invoicesnap-landing/actions/runs?per_page=1` shows the live state.

Gotchas:
- main holds no site files at its root. Pages source on main would publish an empty site, so the script sets gh-pages.
- gh-pages only gets new commits: the push is never forced. To roll back, check out an older commit that has
  publish.py and publish it. To go back before the generator:
  gh api -X PUT repos/Nikita-Guzenko/invoicesnap-landing/pages -f build_type=legacy -f 'source[branch]=main' -f 'source[path]=/'
  That works only on a main commit that still holds index.html at its root.
- src/static/.nojekyll stays: without it the branch build runs Jekyll, which drops files that start with "_".
- The CDN can serve an old file for up to 10 minutes (cache-control: max-age=600), so it compares for up to 15 minutes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "repos/Nikita-Guzenko/invoicesnap-landing"
BRANCH = "gh-pages"
LIVE = "https://invoicesnap.work"


class PublishError(Exception):
    pass


def run(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired as e:
        raise PublishError(f"{' '.join(args)} ran over {e.timeout} s") from e
    if check and result.returncode != 0:
        raise PublishError(f"{' '.join(args)} failed:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")
    return result.stdout.strip()


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    return run("git", *args, cwd=cwd, check=check)


def check_source_tree(from_branch: bool) -> str:
    if git("status", "--porcelain"):
        raise PublishError("the working tree has changes: commit them first")
    git("fetch", "-q", "origin")
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    if current != "main" and not from_branch:
        raise PublishError(f"on branch {current}: publish from main, or pass --from-branch for the first cutover")
    head = git("rev-parse", "HEAD")
    if not git("branch", "-r", "--contains", head):
        raise PublishError("HEAD is not on the remote: push it first, so each published commit has a source")
    if not from_branch and head != git("rev-parse", "origin/main"):
        raise PublishError("HEAD is not origin/main: pull or push first, so an old site cannot replace a newer one")
    return head


def commit_dist(source: str) -> str:
    """Return the commit on gh-pages that holds dist/. It adds a commit only when the output changed."""
    work = Path(tempfile.mkdtemp(dir=Path.home() / ".cache", prefix="invoicesnap-publish-"))  # /tmp is RAM on this machine.
    try:
        exists = git("ls-remote", "--heads", "origin", BRANCH)
        git("worktree", "add", "-q", "--detach", str(work))
        if exists:
            git("fetch", "-q", "origin", BRANCH)
            git("checkout", "-q", "--detach", f"origin/{BRANCH}", cwd=work)
        else:
            git("checkout", "-q", "--orphan", f"{BRANCH}-new", cwd=work)
        for item in work.iterdir():
            if item.name != ".git":
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        shutil.copytree(ROOT / "dist", work, dirs_exist_ok=True)
        git("add", "-A", cwd=work)
        if not git("status", "--porcelain", cwd=work):
            print("gh-pages already has this output")
            return git("rev-parse", "HEAD", cwd=work)
        git("commit", "-q", "-m", f"Publish {source[:12]}", cwd=work)
        git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=work)
        return git("rev-parse", "HEAD", cwd=work)
    finally:
        git("worktree", "remove", "--force", str(work), check=False)
        git("branch", "-q", "-D", f"{BRANCH}-new", check=False)
        shutil.rmtree(work, ignore_errors=True)


def set_source() -> None:
    pages = json.loads(run("gh", "api", f"{REPO}/pages"))
    if pages["source"] == {"branch": BRANCH, "path": "/"} and pages["build_type"] == "legacy":
        return
    print(f"Pages source was {pages['source']}, build type {pages['build_type']}: setting {BRANCH}")
    run("gh", "api", "-X", "PUT", f"{REPO}/pages", "-f", "build_type=legacy", "-f", f"source[branch]={BRANCH}", "-f", "source[path]=/")


def wait_for_build(commit: str) -> None:
    requested = False
    for attempt in range(40):
        try:
            latest = json.loads(run("gh", "api", f"{REPO}/pages/builds/latest"))
        except PublishError as e:  # One failed API call must not stop a publish that already pushed.
            print(f"could not read the Pages build, trying again: {str(e)[:200]}")
            latest = {}
        current = latest.get("commit") == commit
        if current and latest.get("status") == "built":
            print(f"Pages build done for {commit[:12]}")
            return
        errored = latest.get("status") == "errored"
        if not requested and (errored or (latest and not current and attempt >= 4)):
            print(f"latest build: {latest.get('status')} for {str(latest.get('commit'))[:12]}: {latest.get('error')}. Requesting a build")
            run("gh", "api", "-X", "POST", f"{REPO}/pages/builds")
            requested = True
        elif requested and errored and current:
            raise PublishError(f"Pages build failed: {latest.get('error')}. The site keeps its last good deployment")
        time.sleep(15)
    raise PublishError(f"no finished Pages build for {commit[:12]} after 10 minutes: "
                       f"gh api {REPO}/pages/builds/latest. If Pages started to need Actions, see the header of this file")


def differs(path: Path) -> str | None:
    """The URL path if production does not serve the bytes of this dist/ file."""
    relative = path.relative_to(ROOT / "dist").as_posix()
    url = f"{LIVE}/{relative.removesuffix('index.html')}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=60) as r:
            return None if r.read() == path.read_bytes() else url
    except OSError:
        return url


def wait_for_live_bytes() -> None:
    """A green crawl alone cannot tell the old deployment from the new one: the old site is valid too."""
    files = [p for p in (ROOT / "dist").rglob("*") if p.suffix in (".html", ".xml", ".txt") and p.is_file()]
    for attempt in range(15):
        with ThreadPoolExecutor(16) as pool:
            old = [u for u in pool.map(differs, files) if u]
        if not old:
            print(f"production serves the same bytes as dist/ for {len(files)} files")
            return
        print(f"{len(old)} of {len(files)} files are not new yet, first: {old[0]}")
        time.sleep(60)
    raise PublishError(f"production still serves old files after 15 minutes, first: {old[0]}")


def crawl_live() -> None:
    result = subprocess.run([sys.executable, "check_site.py", "--base", LIVE], cwd=ROOT, capture_output=True, text=True)
    print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-300:])
    if result.returncode != 0:
        raise PublishError(f"production crawl failed:\n{result.stdout[-3000:]}\n{result.stderr[-500:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-branch", action="store_true", help="allow a branch other than main (first cutover)")
    args = parser.parse_args()
    try:
        source = check_source_tree(args.from_branch)
        run(sys.executable, "build.py", "--check")
        print(run(sys.executable, "check_site.py", "--dir", "dist"))
        print(run(sys.executable, "tests/test_site.py").splitlines()[-1])
        commit = commit_dist(source)
        set_source()
        wait_for_build(commit)
        wait_for_live_bytes()
        crawl_live()
        print(run(sys.executable, "indexnow.py"))
        print(f"published {source[:12]} as {BRANCH} {commit[:12]}")
        return 0
    except PublishError as e:
        print(f"publish stopped: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
