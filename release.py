"""Build and publish a release.

    python release.py                  # bump the patch, build, verify, tag, publish
    python release.py 1.1.0            # ...or say which version
    python release.py 1.1.0 --dry-run  # do everything except push and publish
    python release.py --check          # verify the current state, build nothing
    python release.py --ask            # ask for the version and the release
                                       # notes first (what release.bat runs)

Runnable as ``py release.py`` too -- the build step resolves the venv
interpreter itself (see build_python), because the Windows launcher does not.

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
        # UTF-8 explicitly. text=True otherwise decodes with the locale
        # codepage -- cp949 on a Korean Windows -- and git speaks UTF-8, so a
        # Korean commit message raised UnicodeDecodeError inside subprocess's
        # reader thread. That does not surface as a failed command: the
        # thread dies, the output comes back empty, and the caller carries on
        # with nothing. It is why the generated release notes said
        # "변경 내용 없음" over a repository full of commits.
        encoding="utf-8", errors="replace",
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


def check_map_fragments() -> None:
    """Every map-regex fragment still matches options, not explanations.

    Here rather than only in ``check_fragments.py`` because this is exactly
    what got shipped in 1.2.2: 카오스 as the fragment for "monsters gain extra
    chaos damage" also matched an affix's damage-type tag and the 중독 rules
    blurb, so maps were filtered for a reason that was not on them. Nothing
    about that fails loudly at runtime -- the filter simply lies -- so the
    check belongs where a release cannot get past it.
    """
    script = ROOT / "check_fragments.py"
    if not script.exists():
        warn("check_fragments.py 가 없습니다 (지도 정규식 검사 건너뜀)")
        return
    proc = subprocess.run(
        [build_python(), str(script)],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise Failed(
            "지도 정규식 조각이 실제 옵션이 아닌 줄(설명·구분)에 걸립니다.\n"
            + (proc.stdout or proc.stderr or "").strip()
        )
    ok("지도 정규식 조각이 실제 옵션 줄에만 걸림")


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


def build_python() -> str:
    """The interpreter that actually has PyInstaller.

    Not simply ``sys.executable``: ``py release.py`` goes through the Windows
    launcher, which picks the system Python rather than the project venv --
    and the system Python has no PyInstaller, so the build would fail several
    minutes into the run for a reason that looks nothing like the cause.
    """
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def check_build_python() -> None:
    interpreter = build_python()
    probe = subprocess.run(
        [interpreter, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if probe.returncode != 0:
        raise Failed(
            f"{interpreter} 에 PyInstaller 가 없습니다.\n"
            "가상환경을 만들고 의존성을 설치하세요:\n"
            "  py -3.11 -m venv .venv\n"
            "  .venv\\Scripts\\activate\n"
            "  pip install -r requirements.txt"
        )
    ok(f"빌드용 파이썬: PyInstaller {probe.stdout.strip()}")


def build() -> None:
    if DIST_EXE.exists():
        try:
            DIST_EXE.unlink()
        except PermissionError:
            # Windows will not let a running image be deleted, and the
            # obvious way to test a build is to leave it running. Said
            # plainly here rather than as a traceback several frames deep.
            raise Failed(
                f"{DIST_EXE} 을(를) 지울 수 없습니다 - 실행 중인 것 같습니다.\n"
                "프로그램을 종료(창의 X 또는 트레이 아이콘 -> 종료)한 뒤 다시 실행하세요."
            ) from None
    run(build_python(), "-m", "PyInstaller", "--noconfirm", "build.spec")
    if not DIST_EXE.exists():
        raise Failed(f"빌드 후 {DIST_EXE} 이(가) 없습니다.")
    size_mb = DIST_EXE.stat().st_size / 1024 / 1024
    if size_mb < 5:
        raise Failed(f"빌드 결과가 너무 작습니다 ({size_mb:.1f} MB). 빌드 로그를 확인하세요.")
    ok(f"빌드 완료: {DIST_EXE.name} ({size_mb:.1f} MB)")


def notes_for(tag: str, written: str = "") -> str:
    """The release notes to publish.

    *written* is what the user typed (``--ask``/``--notes-file``) and wins
    when there is any: commit subjects are a log of how the work happened,
    which is a different thing from what changed for someone using the
    program, and only the second belongs in an update dialog they are shown
    mid-game. Commit subjects remain the fallback so the non-interactive path
    is unchanged.

    The compare link is appended either way -- it is the one part nobody
    should have to write out, and it is what anyone wanting the full detail
    goes looking for.
    """
    try:
        previous = run("git", "describe", "--tags", "--abbrev=0", capture=True)
        span = f"{previous}..HEAD"
    except Failed:
        previous, span = "", "HEAD"
    if written.strip():
        body = written.strip()
    else:
        log = run("git", "log", span, "--pretty=format:- %s", "--no-merges", capture=True)
        body = log or "- 변경 내용 없음"
    if previous:
        body += (
            f"\n\n**전체 변경 내역**: "
            f"https://github.com/{V.GITHUB_SLUG}/compare/{previous}...{tag}"
        )
    return body


def publish(tag: str, dry_run: bool, written_notes: str = "") -> None:
    notes = notes_for(tag, written_notes)
    print("\n--- 릴리즈 노트 미리보기 ---")
    print(notes)
    print("--- 끝 ---")

    if dry_run:
        warn("--dry-run: 커밋/태그/업로드는 건너뜁니다")
        return

    run("git", "add", "-A")
    # Only commit if there is something to commit. Everything already
    # committed, and a version that did not actually change (releasing the
    # version already in version.py, which is exactly the first-release
    # case), leaves the tree clean -- and `git commit` treats that as an
    # error and took the whole release down with it.
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False
    ).returncode != 0
    if staged:
        run("git", "commit", "-m", f"release {tag}")
        ok("변경사항을 커밋했습니다")
    else:
        ok("커밋할 변경 없음 - 태그만 붙입니다")
    run("git", "tag", "-a", tag, "-m", tag)
    run("git", "push")
    run("git", "push", "origin", tag)
    ok(f"{tag} 태그를 푸시했습니다")

    run("gh", "release", "create", tag, str(DIST_EXE),
        "--title", tag, "--notes", notes)
    ok(f"릴리즈 공개 완료: https://github.com/{V.GITHUB_SLUG}/releases/tag/{tag}")


# ---------------------------------------------------------------------------
# interactive mode (--ask), which is what release.bat runs
# ---------------------------------------------------------------------------
def ask(prompt: str, default: str = "") -> str:
    """One line of input, with *default* used for a bare Enter."""
    suffix = f" [{default}]" if default else ""
    try:
        return input(f"   {prompt}{suffix}: ").strip() or default
    except EOFError:
        # Double-clicked with no console attached, or input redirected from
        # nothing. Carrying on with defaults would publish a release nobody
        # confirmed, so stop instead.
        raise Failed("입력을 받을 수 없습니다. 콘솔에서 실행해주세요.") from None


def ask_version() -> str:
    """Which version to publish, offered as the three bumps plus free entry."""
    (major, minor, patch), _pre = V.parse(V.__version__)
    choices = {
        "1": (f"{major}.{minor}.{patch + 1}", "패치 - 버그 수정만"),
        "2": (f"{major}.{minor + 1}.0", "마이너 - 기능 추가"),
        "3": (f"{major + 1}.0.0", "메이저 - 설정 형식이 바뀌어 되돌릴 수 없을 때"),
    }
    print(f"   현재 버전: {V.display()}\n")
    for key, (number, why) in choices.items():
        print(f"     {key}) {number:<10} {why}")
    print("     4) 직접 입력\n")

    picked = ask("어느 버전으로 올릴까요?", "1")
    if picked in choices:
        return choices[picked][0]
    if picked == "4":
        typed = ask("버전 (예: 1.3.0)").lstrip("vV")
    else:
        # A version typed straight in at the menu rather than the digit next
        # to it -- an obvious thing to do, and refusing it would be pedantry.
        typed = picked.lstrip("vV")
    if not re.match(r"^\d+\.\d+\.\d+", typed):
        raise Failed(f"버전 형식이 아닙니다: {typed!r} (예: 1.3.0)")
    return typed


def ask_notes() -> str:
    """The 'what changed' list, as the user wants it shown to users.

    Line at a time, ending on a blank one. A text editor would be the other
    way to do this and it is worse: it means a temp file, an $EDITOR that may
    not exist, and a window that opens somewhere behind the console.
    """
    print("   이 버전에서 바뀐 내용을 한 줄에 하나씩 적어주세요.")
    print("   업데이트 알림 창에서 사용자가 그대로 보게 됩니다.")
    print("   다 적었으면 빈 줄에서 Enter를 누르세요.")
    print("   아무것도 적지 않으면 커밋 메시지로 자동 작성합니다.\n")

    lines: list[str] = []
    while True:
        try:
            line = input("   - ").rstrip()
        except EOFError:
            break
        if not line:
            break
        # Typed with its own bullet, or a markdown heading: left alone.
        # Anything else gets one, so the notes render as a list on GitHub
        # and in the app's update dialog regardless of how they were typed.
        lines.append(line if line.startswith(("-", "*", "#")) else f"- {line}")

    if not lines:
        warn("입력 없음 - 커밋 메시지로 릴리즈 노트를 작성합니다")
    return "\n".join(lines)


def commit_pending_changes(default_message: str) -> None:
    """Offer to commit a dirty tree rather than just refusing to proceed.

    ``check_repo_clean`` stops the release when there is uncommitted work,
    for a good reason -- the exe would not match the tag it is published
    under. But "go and commit, then start again" is a strange thing for a
    release script to say when committing is a step of releasing, and the
    long build has not started yet.
    """
    dirty = run("git", "status", "--porcelain", capture=True)
    if not dirty:
        return
    print("   커밋하지 않은 변경이 있습니다:\n")
    for line in dirty.splitlines():
        print(f"     {line}")
    print()
    if ask("이 변경을 모두 커밋하고 계속할까요? (y/n)", "y").lower() != "y":
        raise Failed(
            "커밋하지 않은 변경이 있어 중단합니다. "
            "릴리즈되는 exe와 태그가 서로 다른 내용이 되어버립니다."
        )
    message = ask("커밋 메시지", default_message)
    run("git", "add", "-A")
    run("git", "commit", "-m", message)
    ok(f"커밋했습니다: {message}")


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Build and publish a release.")
    parser.add_argument("new_version", nargs="?",
                        help="예: 1.1.0 (생략하면 패치 자동 증가)")
    parser.add_argument("--dry-run", action="store_true",
                        help="빌드와 검사는 하되 푸시/업로드는 하지 않음")
    parser.add_argument("--check", action="store_true",
                        help="검사만 하고 빌드하지 않음")
    parser.add_argument("--ask", action="store_true",
                        help="버전과 업데이트 내용을 물어본 뒤 진행 (release.bat)")
    parser.add_argument("--notes-file", metavar="PATH",
                        help="릴리즈 노트를 이 파일에서 읽음 (UTF-8)")
    args = parser.parse_args()

    try:
        step("사전 검사")
        check_tools()
        check_build_python()
        check_asset_name()
        check_page_links()
        check_map_fragments()

        if args.check:
            check_repo_clean()
            print(f"\n현재 버전: {V.display()}")
            print("이상 없습니다.")
            return 0

        written_notes = ""
        if args.notes_file:
            written_notes = Path(args.notes_file).read_text(encoding="utf-8")

        if args.ask and not args.new_version:
            step("버전 정하기")
            new = ask_version()
        elif args.new_version:
            new = args.new_version.lstrip("vV")
        else:
            # No version given: bump the patch, the same shorthand the
            # 6PM Assistant build script uses.
            (major, minor, patch), _pre = V.parse(V.__version__)
            new = f"{major}.{minor}.{patch + 1}"
            ok(f"버전 미지정 -> 패치 올림 {V.display()} -> {V.display(new)}")
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
        check_tag_free(tag)

        # Asked before the build, not after: the build is the slow part, and
        # having to sit through it before being asked anything is how a
        # release script stops getting used.
        if args.ask and not written_notes:
            step(f"{tag} 업데이트 내용")
            written_notes = ask_notes()

        if args.ask:
            step("커밋")
            commit_pending_changes(f"release prep {tag}")
        check_repo_clean()

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
        publish(tag, args.dry_run, written_notes)

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
