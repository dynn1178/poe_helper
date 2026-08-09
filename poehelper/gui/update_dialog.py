"""The "a new version is available" window.

Everything slow happens on a worker thread and comes back through
``after(0, ...)``: the download is tens of megabytes over someone's home
connection, and a frozen window with no progress is indistinguishable from a
crashed one.

The dialog owns the whole flow after the check -- show notes, download with
progress, hand over to the swap script, ask the app to quit -- so the caller
is one line whether the check came from startup or from the 기타 tab.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk

import customtkinter as ctk

from .. import updater, version
from ..config import Config
from . import theme

logger = logging.getLogger(__name__)

_NOTES_FONT = ("맑은 고딕", 9)


class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, master, config: Config, release: updater.Release):
        super().__init__(master)
        self.app_config = config
        self.release = release
        self._cancelled = False
        self._downloaded = None

        self.title("업데이트")
        self.geometry("470x420")
        self.resizable(False, False)
        self.transient(master)
        # Grabbed only after the window is actually mapped -- grab_set() on an
        # unmapped toplevel raises TclError on Windows.
        self.after(120, self._grab)

        self._build()

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    # ---- layout -----------------------------------------------------------
    def _build(self) -> None:
        ctk.CTkLabel(
            self, text=f"새 버전 {self.release.label} 이(가) 있습니다",
            font=theme.FONT_SECTION, anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 2))

        detail = f"현재 설치된 버전: {version.display()}"
        if self.release.asset_size:
            detail += f"   ·   내려받을 용량: {updater.human_size(self.release.asset_size)}"
        ctk.CTkLabel(
            self, text=detail, anchor="w", font=theme.FONT_CAPTION,
            text_color=theme.PRIMARY,
        ).pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(self, text="변경 내용", anchor="w", font=theme.FONT_BODY_STRONG).pack(
            fill="x", padx=16
        )
        notes = tk.Text(
            self, wrap="word", height=9, font=_NOTES_FONT, borderwidth=1, relief="solid",
            highlightthickness=0,
        )
        notes.insert("1.0", self.release.notes or "(릴리즈 노트가 없습니다)")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True, padx=16, pady=(4, 10))

        self.status = ctk.CTkLabel(
            self, text="", anchor="w", font=theme.FONT_CAPTION, wraplength=430,
            justify="left",
        )
        self.status.pack(fill="x", padx=16)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        # Packed only once a download starts, so the dialog is not showing an
        # empty bar for something the user has not agreed to yet.

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(8, 14))

        self.update_button = ctk.CTkButton(
            buttons, text="지금 업데이트", width=120, command=self.start_update
        )
        self.update_button.pack(side="right")
        self.later_button = ctk.CTkButton(
            buttons, text="나중에", width=80, fg_color=theme.PRIMARY,
            hover_color=theme.PRIMARY_FOCUS, command=self._close,
        )
        self.later_button.pack(side="right", padx=(0, 8))
        self.skip_button = ctk.CTkButton(
            buttons, text="이 버전 건너뛰기", width=120, fg_color="transparent",
            text_color=theme.PRIMARY, hover=False, command=self._skip,
        )
        self.skip_button.pack(side="left")

        if not updater.is_frozen():
            # Running from source: there is no exe to swap, so the honest
            # offer is the download page rather than a button that fails.
            self.update_button.configure(text="릴리즈 페이지 열기", command=self._open_page)
            self.status.configure(
                text="소스에서 실행 중이라 자동 설치는 되지 않습니다.",
                text_color=theme.PRIMARY,
            )

        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- actions ----------------------------------------------------------
    def _open_page(self) -> None:
        updater.open_release_page()
        self._close()

    def _skip(self) -> None:
        self.app_config.data["update"]["skipped_version"] = self.release.version
        self.app_config.save()
        self._close()

    def _close(self) -> None:
        self._cancelled = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def start_update(self) -> None:
        self.update_button.configure(state="disabled")
        self.skip_button.configure(state="disabled")
        self.later_button.configure(text="취소", command=self._close)
        self.progress.pack(fill="x", padx=16, pady=(6, 0))
        self.progress.set(0)
        self.status.configure(text="내려받는 중…", text_color=theme.PRIMARY)
        threading.Thread(target=self._run_download, name="update-download", daemon=True).start()

    def _run_download(self) -> None:
        """Worker thread. Touches Tk only through after()."""
        try:
            path = updater.download(
                self.release,
                on_progress=lambda done, total: self._post(self._on_progress, done, total),
                cancelled=lambda: self._cancelled,
            )
        except updater.UpdateError as exc:
            self._post(self._on_failed, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a crashed thread would just hang the dialog
            logger.exception("update download failed")
            self._post(self._on_failed, f"예상치 못한 오류: {exc}")
            return
        self._post(self._on_downloaded, path)

    def _post(self, fn, *args) -> None:
        try:
            self.after(0, fn, *args)
        except (tk.TclError, RuntimeError):
            pass  # dialog already closed

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.set(done / total)
            self.status.configure(
                text=f"내려받는 중… {updater.human_size(done)} / {updater.human_size(total)}"
            )
        else:
            self.status.configure(text=f"내려받는 중… {updater.human_size(done)}")

    def _on_failed(self, message: str) -> None:
        self.progress.pack_forget()
        self.status.configure(text=message, text_color=theme.DANGER)
        self.update_button.configure(state="normal", text="다시 시도")
        self.skip_button.configure(state="normal")
        self.later_button.configure(text="닫기", command=self._close)

    def _on_downloaded(self, path) -> None:
        if self._cancelled:
            return
        self._downloaded = path
        self.progress.set(1.0)
        self.status.configure(
            text="내려받기 완료. 프로그램을 종료하고 새 버전으로 다시 시작합니다…",
            text_color=theme.PRIMARY,
        )
        self.later_button.configure(state="disabled")
        # A beat on the event loop so the message above actually paints before
        # the app starts tearing itself down.
        self.after(700, self._install)

    def _install(self) -> None:
        try:
            updater.backup_config_before_update()
            updater.install(self._downloaded)
        except updater.UpdateError as exc:
            self._on_failed(str(exc))
            return
        # From here the batch script is waiting for this process to exit.
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.master.finish_update_and_restart()
