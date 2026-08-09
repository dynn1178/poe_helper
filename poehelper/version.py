"""The app's own version, and the rules for comparing it to a release tag.

Kept in its own module with no imports from the rest of the package so that
anything -- the updater, the GUI, a build script -- can read the version
without dragging in Tk or the hotkey stack.

Bump ``__version__`` and tag the GitHub release to match (``v1.2.0``); the
updater compares this string against the tag of the newest release, so a
release tagged lower than (or equal to) this never prompts anyone.
"""
from __future__ import annotations

import re

__version__ = "1.0.7"

GITHUB_OWNER = "dynn1178"
GITHUB_REPO = "poe_helper"
GITHUB_SLUG = f"{GITHUB_OWNER}/{GITHUB_REPO}"

# Human-facing pages, and the permanent "newest asset" URL GitHub keeps
# pointing at whatever the current release is -- which is what lets the
# download page keep working across releases without being edited.
RELEASES_URL = f"https://github.com/{GITHUB_SLUG}/releases"
LATEST_RELEASE_URL = f"{RELEASES_URL}/latest"
LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_SLUG}/releases/latest"

# The asset the updater downloads. Must match ``name=`` in build.spec, and
# must stay the same from release to release or the "latest/download/<name>"
# link on the download page breaks.
ASSET_NAME = "KuanPoeHelper.exe"
LATEST_DOWNLOAD_URL = f"{RELEASES_URL}/latest/download/{ASSET_NAME}"

_NUM_RE = re.compile(r"\d+")


def parse(version: str) -> tuple[tuple[int, int, int], str]:
    """``"v1.2.3-beta.1"`` -> ``((1, 2, 3), "beta.1")``.

    Deliberately forgiving: release tags get typed by hand, and refusing to
    parse "V1.2" or "1.2.3.4" would mean silently never offering an update.
    Missing components read as 0, extra ones are ignored.
    """
    text = (version or "").strip().lstrip("vV").split("+")[0]
    core, _, pre = text.partition("-")
    nums = [int(n) for n in _NUM_RE.findall(core)[:3]]
    nums += [0] * (3 - len(nums))
    return (nums[0], nums[1], nums[2]), pre


def is_newer(candidate: str, current: str = __version__) -> bool:
    """Is *candidate* a version worth updating to from *current*?

    A pre-release loses to the same numbers without one (1.2.0 beats
    1.2.0-rc1), so tagging a release candidate cannot pull stable users onto
    it by accident.
    """
    cand_nums, cand_pre = parse(candidate)
    cur_nums, cur_pre = parse(current)
    if cand_nums != cur_nums:
        return cand_nums > cur_nums
    if bool(cand_pre) != bool(cur_pre):
        return not cand_pre  # release > pre-release of the same numbers
    return cand_pre > cur_pre


def display(version: str = __version__) -> str:
    return f"v{version.lstrip('vV')}"
