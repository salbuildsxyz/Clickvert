"""A small progress window for one conversion, built with tkinter.

How it stays responsive: the conversion (``ConversionJob.run``) runs on a
background thread. That thread never touches the window. It only drops
messages into a queue, and the window checks the queue every 50 ms. tkinter
must only be used from the thread that created the window, and the queue keeps
it that way.

The window goes through these states:

    converting ──► done        (file saved: "Show in folder" / "Close")
        │      ──► error       (plain-language message / "Close")
        └─ Cancel ──► cancelling ──► window closes, nothing is left behind
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
import traceback
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from .converter import ConversionJob
from .errors import ClickvertError, ConversionCancelledError

POLL_MS = 50
WIDTH = 440  # at 100% display scaling

# Windows 11-style palette.
BACKGROUND = "#F9F9F9"
TEXT = "#1A1A1A"
MUTED = "#5F5F5F"
ACCENT = "#0067C0"
ACCENT_HOVER = "#1975C5"
TRACK = "#E1E1E1"
ERROR = "#C42B1C"
FONT = "Segoe UI"

JobFactory = Callable[..., ConversionJob]


def shorten_middle(text: str, limit: int = 48) -> str:
    """Shorten long names in the middle so the extension stays visible."""
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[: keep - keep // 2] + "…" + text[-(keep // 2):]


class ProgressWindow:
    def __init__(
        self,
        root: tk.Tk,
        input_path: str | os.PathLike[str],
        target: str,
        *,
        job_factory: JobFactory = ConversionJob,
    ) -> None:
        self.root = root
        self.target = target.upper().lstrip(".")
        self.exit_code: int | None = None  # set once the conversion has ended
        self.output_path: Path | None = None
        self._messages: queue.Queue = queue.Queue()
        self._close_requested = False
        self._job = job_factory(input_path, target, on_progress=lambda f: self._messages.put(("progress", f)))
        self._thread = threading.Thread(target=self._work, name="clickvert-conversion")

        scale = root.winfo_fpixels("1i") / 96
        self._px = lambda n: round(n * scale)
        self._build(Path(input_path).name)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda e: self.close())

    # --- layout ---------------------------------------------------------------

    def _build(self, file_name: str) -> None:
        px = self._px
        self.root.title("Clickvert")
        self.root.configure(background=BACKGROUND)
        self.root.resizable(False, False)

        body = ttk.Frame(self.root, padding=(px(24), px(20), px(24), px(20)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self.heading = ttk.Label(body, text=f"Converting to {self.target}", style="Heading.TLabel")
        self.heading.grid(row=0, column=0, columnspan=2, sticky="w")

        self.detail = ttk.Label(body, text=shorten_middle(file_name), style="Muted.TLabel",
                                wraplength=px(WIDTH - 48), justify="left")
        self.detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(2), px(14)))

        self.bar = ttk.Progressbar(body, style="Accent.Horizontal.TProgressbar", maximum=100)
        self.bar.grid(row=2, column=0, sticky="ew")
        self.percent = ttk.Label(body, text="Starting…", style="Muted.TLabel", width=9, anchor="e")
        self.percent.grid(row=2, column=1, sticky="e", padx=(px(12), 0))

        self.buttons = ttk.Frame(body)
        self.buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(px(18), 0))
        self.primary = ttk.Button(self.buttons, text="Cancel", command=self.cancel)
        self.primary.pack(side="right")
        self.secondary = ttk.Button(self.buttons, style="Accent.TButton")  # shown when done

        self.root.geometry(f"{px(WIDTH)}x{self.root.winfo_reqheight()}")

    # --- running ----------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self.root.after(POLL_MS, self._poll)

    def _work(self) -> None:
        """Runs on the background thread."""
        try:
            self._messages.put(("done", self._job.run()))
        except ClickvertError as exc:
            self._messages.put(("error", exc))
        except BaseException:
            self._messages.put(("error", ClickvertError(
                "Something unexpected went wrong. Please try again.", details=traceback.format_exc())))

    def _poll(self) -> None:
        latest_progress, ending = "none", None
        while True:
            try:
                kind, value = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                latest_progress = value  # only the newest value matters
            else:
                ending = (kind, value)

        if ending is None:
            if latest_progress != "none":
                self._show_progress(latest_progress)
            self.root.after(POLL_MS, self._poll)
            return

        self._thread.join()
        kind, value = ending
        if kind == "done":
            self._show_done(value)
        elif isinstance(value, ConversionCancelledError):
            self.exit_code = value.exit_code
            self.root.destroy()
            return
        else:
            self._show_error(value)
        if self._close_requested:
            self.root.destroy()

    @property
    def finished(self) -> bool:
        return self.exit_code is not None

    # --- states ------------------------------------------------------------------

    def _show_progress(self, fraction: float | None) -> None:
        if self._job.cancel_requested:
            return
        if fraction is None:
            if str(self.bar["mode"]) != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(15)
            self.percent.configure(text="Working…")
        else:
            if str(self.bar["mode"]) != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar["value"] = fraction * 100
            self.percent.configure(text=f"{fraction:.0%}")

    def _show_done(self, output: Path) -> None:
        self.exit_code = 0
        self.output_path = output
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100)
        self.percent.configure(text="100%")
        self.heading.configure(text="Done")
        self.detail.configure(text=f"Saved as {shorten_middle(output.name)}\nin {output.parent}")
        self.primary.configure(text="Close", command=self.close)
        self.secondary.configure(text="Show in folder", command=self.show_in_folder)
        self.secondary.pack(side="right", padx=(0, self._px(8)))
        self.secondary.focus_set()
        self._fit_height()

    def _show_error(self, error: ClickvertError) -> None:
        self.exit_code = error.exit_code
        self.bar.stop()
        self.bar.grid_remove()
        self.percent.grid_remove()
        self.heading.configure(text="Couldn't convert", style="Error.TLabel")
        self.detail.configure(text=error.user_message, style="TLabel")
        self.primary.configure(text="Close", command=self.close)
        self.primary.focus_set()
        self._fit_height()

    def _fit_height(self) -> None:
        self.root.update_idletasks()
        self.root.geometry(f"{self._px(WIDTH)}x{self.root.winfo_reqheight()}")

    # --- buttons -----------------------------------------------------------------

    def cancel(self) -> None:
        if self.finished or self._job.cancel_requested:
            return
        self._job.cancel()
        self.bar.stop()
        self.heading.configure(text="Cancelling…")
        self.percent.configure(text="")
        self.primary.state(["disabled"])

    def close(self) -> None:
        """Window X, Escape, or the Close button."""
        if self.finished:
            self.root.destroy()
        else:
            # Never leave a conversion running behind a closed window:
            # cancel, and close once the partial file has been cleaned up.
            self._close_requested = True
            self.cancel()

    def show_in_folder(self) -> None:
        if self.output_path is not None:
            # Windows file names can't contain quotes, so this is safe to quote.
            subprocess.Popen(f'explorer /select,"{self.output_path}"')


def apply_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")  # the most customizable built-in theme
    px = lambda n: round(n * root.winfo_fpixels("1i") / 96)  # noqa: E731

    style.configure(".", background=BACKGROUND, foreground=TEXT, font=(FONT, 10))
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Heading.TLabel", font=(f"{FONT} Semibold", 13))
    style.configure("Error.TLabel", font=(f"{FONT} Semibold", 13), foreground=ERROR)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure(
        "Accent.Horizontal.TProgressbar",
        troughcolor=TRACK, background=ACCENT, bordercolor=TRACK,
        lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(6),
    )
    style.configure(
        "TButton", padding=(px(16), px(5)), relief="solid", borderwidth=1,
        background="#FDFDFD", bordercolor="#D0D0D0", lightcolor="#FDFDFD", darkcolor="#FDFDFD",
        focuscolor="#FDFDFD",
    )
    style.map("TButton", background=[("disabled", "#F3F3F3"), ("active", "#F0F0F0")],
              foreground=[("disabled", "#A0A0A0")])
    style.configure("Accent.TButton", foreground="white", background=ACCENT, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT)
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])


def _app_icon(root: tk.Tk) -> tk.PhotoImage:
    """A simple accent-colored icon, drawn in code (replaces Tk's feather)."""
    icon = tk.PhotoImage(master=root, width=32, height=32)
    icon.put(ACCENT, to=(2, 2, 30, 30))
    icon.put("white", to=(10, 14, 22, 18))  # a little "convert" arrow
    icon.put("white", to=(18, 10, 20, 22))
    return icon


def _enable_sharp_text() -> None:
    """Without this, Windows blurs the window on high-DPI displays."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def run_window(input_path: str | os.PathLike[str], target: str) -> int:
    """Show the window, run the conversion, and return an exit code."""
    _enable_sharp_text()
    root = tk.Tk()
    apply_style(root)
    root.iconphoto(True, _app_icon(root))
    window = ProgressWindow(root, input_path, target)

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    # Come to the front when launched from Explorer, without staying on top.
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.focus_force()

    window.start()
    try:
        root.mainloop()
    finally:
        # Safety net: if the window died unexpectedly, stop FFmpeg and clean up.
        if window._thread.is_alive():
            window._job.cancel()
            window._thread.join()
    return window.exit_code if window.exit_code is not None else 1
