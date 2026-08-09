"""Build and publish a release.

    py release.py 1.1.0            # bump, build, verify, tag, publish
    py release.py 1.1.0 --dry-run  # do everything except push and publish
    py release.py --check          # verify the current state, build nothing

The parts worth automating here are not the build -- that is one command --
but the four things that silently break an update for everyone:

* ``version.__version__`` not matching the tag, so the updater never offers
  the release (it compares the tag against the version baked into the exe);
* the asset being uploaded under a different name, which breaks both the
  updater and the download page's permanent ``latest/download/<name>`` link;
* a tag that already exists, or a working tree with uncommitted changes, so
  the exe does not correspond to the tag it is published under;
* releasing without the previous version having a release at all, which is
  fine but worth saying out loud once.

Everything that touches GitHub goes through the ``gh`` CLI so there is no
token handling here.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "poehelper" / "version.py"

sys.path.insert(0, str(ROOT))
from poehelper import version as V  # noqa: E402  (needs ROOT on the path first)

DIST_EXE = ROOT / "dist" / V.ASSET_NAME


class Failed(Exception):
    """A check that must stop the release, with a message worth reading."""


def run(*args: str, capture: bool = False, check: bool = True) -> str:
    proc = subprocess.run(
        args, cwd=ROOT, text=True, check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise Failed(f"`{' '.join(args)}` 실패\n{detail}")
    return (proc.stdout or "").strip()


def step(text: str) -> None:
    print(f"\n\033[36m>> {text}\033[0m")


def ok(text: str) -> None:
    print(f"   \033[32mOK\033[0m {text}")


def warn(text: str) -> None:
    print(f"   \033[33m!!\033[0m {text}")


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_tools() -> None:
    for tool in ("git", "gh"):
        if shutil.which(tool) is None:
            raise Failed(
                f"{tool} 을(를) 찾을 수 없습니다. "
                + ("https://cli.github.com 에서 설치하세요." if tool == "gh" else "")
            )
    ok("git, gh 사용 가능")


def check_repo_clean() -> None:
    if not (ROOT / ".git").exists():
        raise Failed("git 저장소가 아닙니다. 먼저 `git init` 후 원격을 연결하세요.")
    dirty = run("git", "status", "--porcelain", capture=True)
    if dirty:
        raise Failed(
            "커밋하지 않은 변경이 있습니다. 릴리즈되는 exe와 태그가 서로 다른 내용이 "
            "되어버리므로 먼저 정리하세요:\n" + dirty
        )
    ok("작업 트리 깨끗함")


def check_tag_free(tag: str) -> None:
    tags = run("git", "tag", "--list", tag, capture=True)
    if tags:
        raise Failed(f"태그 {tag} 이(가) 이미 있습니다. 버전을 올리거나 태그를 지우세요.")
    ok(f"{tag} 태그 사용 가능")


def check_asset_name() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    match = re.search(r'name\s*=\s*"([^"]+)"', spec)
    if not match:
        raise Failed("build.spec 에서 name= 을 찾지 못했습니다.")
    built = f"{match.group(1)}.exe"
    if built != V.ASSET_NAME:
        raise Failed(
            f"build.spec 이 만드는 파일({built})과 version.ASSET_NAME"
            f"({V.ASSET_NAME})이 다릅니다.\n"
            "이 이름이 어긋나면 자동 업데이트와 다운로드 페이지 링크가 모두 깨집니다."
        )
    ok(f"자산 이름 일치: {V.ASSET_NAME}")


def published_releases() -> list[str]:
    """Tags already published on GitHub, newest first (empty if none)."""
    out = run("gh", "release", "list", "--repo", version_slug(),
              "--limit", "20", capture=True, check=False)
    tags = []
    for line in out.splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip():
            tags.append(parts[0].strip())
    return tags


def version_slug() -> str:
    return V.GITHUB_SLUG


def check_page_links() -> None:
    page = ROOT / "web" / "index.html"
    if not page.exists():
        warn("web/index.html 이 없습니다 (다운로드 페이지 없이 진행)")
        return
    text = page.read_text(encoding="utf-8")
    if V.LATEST_DOWNLOAD_URL not in text:
        raise Failed(
            "web/index.html 의 다운로드 링크가 현재 저장소/자산 이름과 다릅니다.\n"
            f"이 주소가 들어 있어야 합니다: {V.LATEST_DOWNLOAD_URL}"
        )
    ok("다운로드 페이지 링크가 최신 릴리즈를 가리킴")


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------
def set_version(new: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^__version__ = "[^"]*"',
        f'__version__ = "{new}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise Failed("version.py 에서 __version__ 줄을 찾지 못했습니다.")
    VERSION_FILE.write_text(updated, encoding="utf-8")
    ok(f"version.py -> {new}")


def build() -> None:
    if DIST_EXE.exists():
        DIST_EXE.unlink()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "build.spec")
    if not DIST_EXE.exists():
        raise Failed(f"빌드 후 {DIST_EXE} 이(가) 없습니다.")
    size_mb = DIST_EXE.stat().st_size / 1024 / 1024
    if size_mb < 5:
        raise Failed(f"빌드 결과가 너무 작습니다 ({size_mb:.1f} MB). 빌드 로그를 확인하세요.")
    ok(f"빌드 완료: {DIST_EXE.name} ({size_mb:.1f} MB)")


def notes_for(tag: str) -> str:
    """Release notes from the commits since the last tag."""
    try:
        previous = run("git", "describe", "--tags", "--abbrev=0", capture=True)
        span = f"{previous}..HEAD"
    except Failed:
        previous, span = "", "HEAD"
    log = run("git", "log", span, "--pretty=format:- %s", "--no-merges", capture=True)
    body = log or "- 변경 내용 없음"
    if previous:
        body += (
            f"\n\n**전체 변경 내역**: "
            f"https://github.com/{V.GITHUB_SLUG}/compare/{previous}...{tag}"
        )
    return body


def publish(tag: str, dry_run: bool) -> None:
    notes = notes_for(tag)
    print("\n--- 릴리즈 노트 미리보기 ---")
    print(notes)
    print("--- 끝 ---")

    if dry_run:
        warn("--dry-run: 커밋/태그/업로드는 건너뜁니다")
        return

    run("git", "add", "-A")
    run("git", "commit", "-m", f"release {tag}")
    run("git", "tag", "-a", tag, "-m", tag)
    run("git", "push")
    run("git", "push", "origin", tag)
    ok(f"{tag} 태그를 푸시했습니다")

    run("gh", "release", "create", tag, str(DIST_EXE),
        "--title", tag, "--notes", notes)
    ok(f"릴리즈 공개 완료: https://github.com/{V.GITHUB_SLUG}/releases/tag/{tag}")


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Build and publish a release.")
    parser.add_argument("new_version", nargs="?", help="예: 1.1.0")
    parser.add_argument("--dry-run", action="store_true",
                        help="빌드와 검사는 하되 푸시/업로드는 하지 않음")
    parser.add_argument("--check", action="store_true",
                        help="검사만 하고 빌드하지 않음")
    args = parser.parse_args()

    try:
        step("사전 검사")
        check_tools()
        check_asset_name()
        check_page_links()

        if args.check:
            check_repo_clean()
            print(f"\n현재 버전: {V.display()}")
            print("이상 없습니다.")
            return 0

        if not args.new_version:
            parser.error("올릴 버전을 지정하세요 (예: py release.py 1.1.0)")

        new = args.new_version.lstrip("vV")
        tag = f"v{new}"
        # The very first release is the one case where the version does not
        # have to go up: there is nothing published for it to be newer than,
        # and requiring a bump would mean 1.0.0 could never be released as
        # 1.0.0. Every release after that must climb, or the updater will
        # not offer it to anyone.
        existing = published_releases()
        if not existing and new == V.__version__:
            ok(f"첫 릴리즈: 현재 버전 {V.display(new)} 그대로 공개합니다")
        elif not V.is_newer(new):
            raise Failed(
                f"{new} 은(는) 현재 버전 {V.__version__} 보다 높지 않습니다.\n"
                "낮거나 같은 버전은 사용자에게 업데이트로 안내되지 않습니다."
                + ("" if existing else
                   f"\n첫 릴리즈라면 현재 버전 그대로 `py release.py {V.__version__}` 로 실행하세요.")
            )
        check_repo_clean()
        check_tag_free(tag)

        step(f"버전 올리기 {V.__version__} -> {new}")
        set_version(new)

        step("빌드")
        build()

        step("확인")
        print(f"   {DIST_EXE} 를 한 번 실행해 정상 동작을 확인하세요.")
        print("   특히: 창이 뜨는지, 제목이 v" + new + " 인지, 단축키가 먹는지.")
        if input("   계속할까요? [y/N] ").strip().lower() != "y":
            warn("중단했습니다. version.py 는 이미 수정되어 있으니 되돌리려면 "
                 "`git checkout poehelper/version.py`")
            return 1

        step("공개")
        publish(tag, args.dry_run)

        step("완료")
        print(f"   다운로드 페이지: https://github.com/{V.GITHUB_SLUG}/releases/latest")
        print("   실행 중인 사용자에게는 다음 실행 시 업데이트 알림이 뜹니다.")
        return 0

    except Failed as exc:
        print(f"\n\033[31m중단: {exc}\033[0m")
        return 1
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
