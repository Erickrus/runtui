"""Unix (Linux/macOS) terminal backend using ANSI escape sequences."""

from __future__ import annotations

import os
import re
import select
import signal
import sys
import termios
import tty
from typing import Any, Callable

from ..core.event import (
    Event,
    ResizeEvent,
)
from ..core.types import ColorDepth
from .base import Backend
from .input_decoder import AnsiInputDecoder


class UnixBackend(Backend):
    """Terminal backend for Linux and macOS."""

    def __init__(self) -> None:
        self._original_termios: list[Any] | None = None
        self._resize_pending = False
        self._old_sigwinch: Any = None
        self._decoder = AnsiInputDecoder()
        self._pty_callbacks: dict[int, Callable[[bytes], None]] = {}

    def init(self) -> None:
        fd = sys.stdin.fileno()
        self._original_termios = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSANOW)
        self.enter_alternate_screen()
        self.set_cursor_visible(False)
        self.enable_mouse()
        self.clear_screen()
        self.flush()
        # Handle SIGWINCH for terminal resize
        self._old_sigwinch = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, self._handle_sigwinch)

    def shutdown(self) -> None:
        self.disable_mouse()
        self.set_cursor_visible(True)
        self.reset_attributes()
        self.leave_alternate_screen()
        self.flush()
        if self._original_termios is not None:
            fd = sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, self._original_termios)
        if self._old_sigwinch is not None:
            signal.signal(signal.SIGWINCH, self._old_sigwinch)

    def get_size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            return (size.columns, size.lines)
        except OSError:
            return (80, 24)

    def read_input(self) -> bytes:
        fd = sys.stdin.fileno()
        # Build the list of fds to poll: stdin + any registered PTY masters
        watch_fds = [fd] + list(self._pty_callbacks.keys())
        rlist, _, _ = select.select(watch_fds, [], [], 0.01)

        # Service any PTY fds that have data ready
        for ready_fd in rlist:
            if ready_fd != fd and ready_fd in self._pty_callbacks:
                try:
                    data = os.read(ready_fd, 65536)
                    if data:
                        self._pty_callbacks[ready_fd](data)
                except OSError:
                    # Child probably died — invoke callback with empty
                    # to let the widget detect the EOF on its next read
                    cb = self._pty_callbacks.get(ready_fd)
                    if cb:
                        try:
                            cb(b"")
                        except Exception:
                            pass

        if fd not in rlist:
            # No stdin data — check for pending resize
            if self._resize_pending:
                self._resize_pending = False
                return b"\x1b[RESIZE]"  # Synthetic marker
            return b""
        # Drain all currently-available stdin in one frame. With any-event
        # mouse tracking (?1003h), fast motion produces far more than a single
        # 4096-byte read per frame; if we only read one chunk, the OS buffer
        # backs up and interleaved scroll events are delivered in delayed
        # bursts, causing the list to lurch by pages while the mouse moves.
        chunks = [os.read(fd, 4096)]
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def register_pty_fd(self, fd: int, callback: Callable[[bytes], None]) -> None:
        """Register a PTY master fd to be polled in the select() loop."""
        self._pty_callbacks[fd] = callback

    def unregister_pty_fd(self, fd: int) -> None:
        """Remove a PTY fd from the select() loop."""
        self._pty_callbacks.pop(fd, None)

    def decode_input(self, raw: bytes) -> list[Event]:
        if not raw:
            return []

        # Check for synthetic resize marker
        if raw == b"\x1b[RESIZE]":
            cols, rows = self.get_size()
            return [ResizeEvent(width=cols, height=rows)]

        return self._decoder.feed(raw)


    def write(self, data: str) -> None:
        sys.stdout.buffer.write(data.encode("utf-8", errors="replace"))

    def flush(self) -> None:
        sys.stdout.buffer.flush()

    def set_cursor_position(self, x: int, y: int) -> None:
        # ANSI is 1-based
        self.write(f"\x1b[{y + 1};{x + 1}H")

    def set_cursor_visible(self, visible: bool) -> None:
        if visible:
            self.write("\x1b[?25h")
        else:
            self.write("\x1b[?25l")

    def color_support(self) -> ColorDepth:
        term = os.environ.get("TERM", "")
        colorterm = os.environ.get("COLORTERM", "")
        if colorterm in ("truecolor", "24bit"):
            return ColorDepth.TRUE_COLOR
        if "256color" in term:
            return ColorDepth.COLORS_256
        if term in ("xterm", "screen", "tmux", "rxvt"):
            return ColorDepth.COLORS_256
        return ColorDepth.COLORS_16

    def _handle_sigwinch(self, signum: int, frame: Any) -> None:
        self._resize_pending = True
