#!/usr/bin/env python3
"""TUI-OS: A macOS-like desktop environment running in the terminal.

Built on the runtui framework, this provides a Finder, Terminal emulator,
Text Editor, and Preferences dialog with per-window menu bar switching
(macOS-style: clicking a window changes the global menu bar).

Usage:
    python examples/tui_os.py
"""

import datetime
import json
import os
import sys
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtui
from runtui import (
    App,
    Window,
    Label,
    Button,
    TextInput,
    TextArea,
    ListBox,
    MenuBar,
    Menu,
    MenuItem,
    MessageBox,
    Container,
    Color,
    Rect,
    WindowAction,
    WindowEvent,
    KeyEvent,
    Keys,
    Modifiers,
)
from runtui.layout.absolute import AbsoluteLayout
from runtui.widgets.terminal import TerminalWidget
from runtui.widgets.image import ImageWidget
from runtui.dialogs.base import Dialog
from runtui.core.unicode import string_width, truncate_to_width
from runtui.widgets.syntax import SyntaxHighlighter


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

EDITABLE_EXTENSIONS = {
    ".txt", ".md", ".py", ".json", ".cfg", ".ini", ".toml",
    ".yaml", ".yml", ".log", ".sh", ".bash", ".zsh", ".conf",
    ".csv", ".xml", ".html", ".css", ".js", ".ts", ".c", ".h",
    ".cpp", ".hpp", ".rs", ".go", ".java", ".rb", ".pl",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp"}


def _is_containerized() -> bool:
    """Best-effort detection for Docker/Kubernetes/container runtime."""
    return (
        os.path.exists("/.dockerenv")
        or os.path.exists("/run/.containerenv")
        or "KUBERNETES_SERVICE_HOST" in os.environ
        or os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount")
    )


def _move_to_trash(path: Path) -> None:
    """Move a file/directory to the platform's trash/recycle bin.

    macOS: uses osascript to tell Finder to trash the item.
    Linux: follows XDG Trash spec (~/.local/share/Trash/).
    Windows: falls back to permanent delete (or send2trash if available).
    """
    import platform
    system = platform.system()

    if system == "Darwin":
        # macOS — use AppleScript via osascript
        import subprocess as _sp
        posix = str(path.resolve())
        _sp.run(
            ["osascript", "-e",
             f'tell application "Finder" to delete POSIX file "{posix}"'],
            capture_output=True,
        )
    elif system == "Linux":
        import time as _time
        trash_dir = Path.home() / ".local" / "share" / "Trash"
        files_dir = trash_dir / "files"
        info_dir = trash_dir / "info"
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)

        dest_name = path.name
        dest = files_dir / dest_name
        counter = 1
        while dest.exists():
            dest_name = f"{path.stem}.{counter}{path.suffix}"
            dest = files_dir / dest_name
            counter += 1

        # Write .trashinfo metadata
        info_file = info_dir / f"{dest_name}.trashinfo"
        deletion_date = _time.strftime("%Y-%m-%dT%H:%M:%S")
        info_file.write_text(
            f"[Trash Info]\nPath={path.resolve()}\nDeletionDate={deletion_date}\n"
        )

        import shutil as _shutil
        _shutil.move(str(path), str(dest))
    else:
        # Windows or other — try send2trash, fall back to permanent delete
        try:
            import send2trash
            send2trash.send2trash(str(path))
        except ImportError:
            import shutil as _shutil
            if path.is_dir():
                _shutil.rmtree(str(path))
            else:
                path.unlink()

VERSION = "1.0.0"

# Config file path (next to the script)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tui_os.json")


def _load_config() -> dict:
    """Load preferences from tui_os.json. Returns defaults if missing."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_config(config: dict) -> None:
    """Save preferences to tui_os.json."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass

# ---------------------------------------------------------------------------
#  Preferences Dialog
# ---------------------------------------------------------------------------

class PreferencesDialog(Dialog):
    """macOS-style Preferences dialog with theme selector and window arrangement."""

    def __init__(self, app: "TuiOS") -> None:
        super().__init__(title="Preferences", width=44, height=14)
        self._app = app

        # Theme list
        self._theme_names = app._theme_engine.theme_names
        current = app._theme_engine.active_theme
        current_name = current.name if current else "light"

        theme_items = []
        for name in self._theme_names:
            prefix = " * " if name == current_name else "   "
            theme_items.append(f"{prefix}{name}")

        self._theme_list = ListBox(
            items=theme_items,
            width=36,
            height=7,
            on_activate=self._on_theme_activate,
            on_select=self._on_theme_select,
        )
        self.add_child(self._theme_list)

        # Window arrangement buttons
        self._cascade_btn = Button(text="Cascade", on_click=self._on_cascade)
        self._tile_h_btn = Button(text="Tile H", on_click=self._on_tile_h)
        self._tile_v_btn = Button(text="Tile V", on_click=self._on_tile_v)
        self.add_child(self._cascade_btn)
        self.add_child(self._tile_h_btn)
        self.add_child(self._tile_v_btn)

        # Close button
        self._close_btn = Button(text="Close", on_click=lambda: self.close(None))
        self.add_child(self._close_btn)

    def _on_theme_select(self, index: int, item: str) -> None:
        pass

    def _on_theme_activate(self, index: int, item: str) -> None:
        name = self._theme_names[index]
        self._app.set_theme(name)
        # Update list markers
        items = []
        for n in self._theme_names:
            prefix = " * " if n == name else "   "
            items.append(f"{prefix}{n}")
        self._theme_list.items = items
        self._theme_list.selected_index = index

    def _on_cascade(self) -> None:
        if self._app._window_manager:
            self._app._window_manager.cascade()
            self._app.invalidate_all()

    def _on_tile_h(self) -> None:
        if self._app._window_manager:
            self._app._window_manager.tile_horizontal()
            self._app.invalidate_all()

    def _on_tile_v(self) -> None:
        if self._app._window_manager:
            self._app._window_manager.tile_vertical()
            self._app.invalidate_all()

    def paint(self, painter: "runtui.rendering.painter.Painter") -> None:
        super().paint(painter)

        sr = self._screen_rect
        lx = sr.x - painter._offset.x
        ly = sr.y - painter._offset.y

        bg = self.theme_color("dialog.bg", Color.BRIGHT_BLACK)
        fg = self.theme_color("dialog.fg", Color.BLACK)
        from runtui.core.types import Attrs

        # Theme section
        painter.put_str(lx + 2, ly + 2, "Theme:", fg=fg, bg=bg, attrs=Attrs.BOLD)
        self._theme_list._screen_rect = Rect(sr.x + 3, sr.y + 3, 36, 7)
        self._theme_list.paint(painter)

        # Window arrangement section
        #painter.put_str(lx + 2, ly + 11, "Window Arrangement:", fg=fg, bg=bg, attrs=Attrs.BOLD)

        # btn_y = sr.y + 13
        # self._cascade_btn._screen_rect = Rect(sr.x + 3, btn_y, 11, 1)
        # self._tile_h_btn._screen_rect = Rect(sr.x + 16, btn_y, 10, 1)
        # self._tile_v_btn._screen_rect = Rect(sr.x + 28, btn_y, 10, 1)
        # self._cascade_btn.paint(painter)
        # self._tile_h_btn.paint(painter)
        # self._tile_v_btn.paint(painter)

        # Close button centered at bottom
        self._close_btn._screen_rect = Rect(sr.x + 16, sr.y + sr.height - 3, 10, 1)
        self._close_btn.paint(painter)


# ---------------------------------------------------------------------------
#  Finder Window
# ---------------------------------------------------------------------------

class FinderWindow:
    """Creates and manages a Finder file browser window."""

    def __init__(self, app: "TuiOS", path: str | None = None) -> None:
        self.app = app
        self._current_dir = Path(path or os.getcwd()).resolve()

        self.window = Window(
            title=f"Finder - {self._short_path()}",
            width=55,
            height=20,
            on_close=self._on_close,
        )

        # Custom container that dynamically sizes children on resize
        content = Container()
        finder = self  # capture reference for the arrange override

        def _finder_arrange(rect: Rect) -> None:
            """Dynamically size Finder children to fit the window."""
            content._screen_rect = rect
            content.x = rect.x
            content.y = rect.y
            content.width = rect.width
            content.height = rect.height
            content._needs_layout = False

            cw = rect.width
            ch = rect.height

            # Path label: fixed at top-left
            finder._path_label.arrange(Rect(rect.x, rect.y, 5, 1))
            # Path input: fills width after label
            finder._path_input.arrange(Rect(rect.x + 6, rect.y, max(1, cw - 6), 1))
            # File list: fills remaining space (rows 2..ch-2)
            list_h = max(1, ch - 3)
            finder._file_list.arrange(Rect(rect.x, rect.y + 2, cw, list_h))
            # Status bar: bottom row
            finder._status_label.arrange(Rect(rect.x, rect.y + 2 + list_h, cw, 1))

        content.arrange = _finder_arrange

        # Path bar
        self._path_label = Label(text="Path:", x=0, y=0, width=5)
        content.add_child(self._path_label)
        self._path_input = TextInput(
            text=str(self._current_dir),
            x=6, y=0, width=45,
            on_submit=self._on_path_submit,
        )
        content.add_child(self._path_input)

        # File list (multi-select enabled for copy/cut/paste)
        self._file_list = ListBox(
            x=0, y=2, width=51, height=13,
            on_activate=self._on_file_activate,
            on_select=self._on_file_select,
            multi_select=True,
        )
        content.add_child(self._file_list)

        # Status bar
        self._status_label = Label(text="", x=0, y=16, width=51)
        content.add_child(self._status_label)

        self.window.set_content(content)
        self._refresh()

        # Focus the file list so arrow keys work immediately
        self._file_list.focus()

    def _short_path(self) -> str:
        home = Path.home()
        try:
            rel = self._current_dir.relative_to(home)
            return f"~/{rel}"
        except ValueError:
            return str(self._current_dir)

    def _refresh(self) -> None:
        """Refresh file list from current directory."""
        items: list[str] = []
        try:
            if self._current_dir.parent != self._current_dir:
                items.append("📁 ..")

            entries = sorted(
                self._current_dir.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
            file_count = 0
            for entry in entries:
                try:
                    if entry.name.startswith("."):
                        continue  # Skip hidden files
                    if entry.is_dir():
                        items.append(f"📁 {entry.name}")
                    else:
                        size = self._format_size(entry.stat().st_size)
                        # Right-align size in a fixed column
                        name_part = truncate_to_width(entry.name, 38)
                        padding = max(1, 40 - string_width(name_part) - string_width(size))
                        items.append(f"📄 {name_part}{' ' * padding}{size}")
                    file_count += 1
                except (PermissionError, OSError):
                    continue

        except PermissionError:
            items.append("  (Permission denied)")
        except OSError as e:
            items.append(f"  (Error: {e})")

        self._file_list.items = items
        self._path_input.text = str(self._current_dir)
        self.window.title = f"Finder - {self._short_path()}"

        # Update status
        n = len([i for i in items if not i.endswith("..")])
        self._status_label.text = f" {n} items"
        self.window.invalidate_layout()

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / (1024 * 1024):.1f}MB"

    def _extract_name(self, item: str) -> str:
        """Extract the filename from a display item."""
        if item.startswith("📁 ") or item.startswith("📄 "):
            # Remove icon prefix
            rest = item[2:].strip()
            # For files, strip the size suffix
            if item.startswith("📄 "):
                parts = rest.rsplit(" ", 1)
                if len(parts) > 1 and (parts[-1].endswith("B") or parts[-1].endswith("KB") or parts[-1].endswith("MB")):
                    return parts[0].strip()
            return rest
        return item.strip()

    def _on_file_select(self, index: int, item: str) -> None:
        name = self._extract_name(item)
        selected_path = self._current_dir / name if name != ".." else self._current_dir.parent
        if not item.startswith("📁"):
            self._status_label.text = f" {name}"

    def _on_file_activate(self, index: int, item: str) -> None:
        name = self._extract_name(item)

        if item.startswith("📁"):
            if name == "..":
                self._current_dir = self._current_dir.parent
            else:
                new_dir = self._current_dir / name
                if new_dir.is_dir():
                    self._current_dir = new_dir
            self._refresh()
        elif item.startswith("📄"):
            filepath = str(self._current_dir / name)
            self.app._on_finder_open_file(filepath)

    def _on_path_submit(self, text: str) -> None:
        path = Path(text)
        if path.is_dir():
            self._current_dir = path.resolve()
            self._refresh()

    def _on_close(self) -> None:
        if self in self.app._finder_windows:
            self.app._finder_windows.remove(self)

    def get_menu(self) -> MenuBar:
        """Return the Finder-specific menu bar."""
        return MenuBar(menus=[
            self.app._system_menu(),
            Menu("Application", [
                MenuItem("New Finder Window", shortcut="Ctrl+N", action=lambda: self.app._open_finder()),
                MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: self.app._open_terminal()),
                MenuItem.separator(),
                MenuItem("Close Window", shortcut="Ctrl+W", action=lambda: self.window._do_close()),
            ]),
            Menu("Edit", [
                MenuItem("Copy", shortcut="Ctrl+C", action=self._copy),
                MenuItem("Cut", shortcut="Ctrl+X", action=self._cut),
                MenuItem("Paste", shortcut="Ctrl+V", action=self._paste),
                MenuItem.separator(),
                MenuItem("Rename...", action=self._rename),
                MenuItem("Move to Trash", action=self._delete),
            ]),
            Menu("Go", [
                MenuItem("Home", action=lambda: self._navigate(Path.home())),
                MenuItem("Parent Folder", action=lambda: self._navigate(self._current_dir.parent)),
                MenuItem("Root /", action=lambda: self._navigate(Path("/"))),
                MenuItem.separator(),
                MenuItem("Refresh", shortcut="F5", action=self._refresh),
            ]),
        ])

    def _get_selected_paths(self) -> list[Path]:
        """Get full paths for all selected items."""
        paths = []
        indices = self._file_list.selected_indices
        if not indices:
            # Fall back to single selection
            idx = self._file_list.selected_index
            if 0 <= idx < len(self._file_list.items):
                indices = {idx}
        for idx in sorted(indices):
            if 0 <= idx < len(self._file_list.items):
                item = self._file_list.items[idx]
                name = self._extract_name(item)
                if name == "..":
                    continue
                paths.append(self._current_dir / name)
        return paths

    def _copy(self) -> None:
        """Copy selected files to internal clipboard."""
        paths = self._get_selected_paths()
        if paths:
            self.app._clipboard = [(str(p), "copy") for p in paths]
            n = len(paths)
            self._status_label.text = f" {n} item{'s' if n > 1 else ''} copied"

    def _cut(self) -> None:
        """Cut selected files to internal clipboard."""
        paths = self._get_selected_paths()
        if paths:
            self.app._clipboard = [(str(p), "cut") for p in paths]
            n = len(paths)
            self._status_label.text = f" {n} item{'s' if n > 1 else ''} cut"

    def _paste(self) -> None:
        """Paste files from internal clipboard into current directory."""
        import shutil
        if not self.app._clipboard:
            self._status_label.text = " Clipboard is empty"
            return

        pasted = 0
        errors = []
        for src_path_str, operation in self.app._clipboard:
            src = Path(src_path_str)
            if not src.exists():
                errors.append(f"{src.name}: not found")
                continue
            dst = self._current_dir / src.name
            # Avoid overwriting by adding suffix
            if dst.exists():
                base = dst.stem
                ext = dst.suffix
                counter = 1
                while dst.exists():
                    dst = self._current_dir / f"{base} ({counter}){ext}"
                    counter += 1
            try:
                if src.is_dir():
                    if operation == "cut":
                        shutil.move(str(src), str(dst))
                    else:
                        shutil.copytree(str(src), str(dst))
                else:
                    if operation == "cut":
                        shutil.move(str(src), str(dst))
                    else:
                        shutil.copy2(str(src), str(dst))
                pasted += 1
            except Exception as e:
                errors.append(f"{src.name}: {e}")

        # Clear clipboard after cut
        if any(op == "cut" for _, op in self.app._clipboard):
            self.app._clipboard.clear()

        self._refresh()
        if errors:
            self._status_label.text = f" Pasted {pasted}, errors: {len(errors)}"
        else:
            self._status_label.text = f" {pasted} item{'s' if pasted > 1 else ''} pasted"

    def _delete(self) -> None:
        """Move selected files to trash."""
        paths = self._get_selected_paths()
        if not paths:
            self._status_label.text = " Nothing selected"
            return

        names = ", ".join(p.name for p in paths[:3])
        if len(paths) > 3:
            names += f" ... ({len(paths)} total)"

        # Show confirmation
        mb = MessageBox(
            title="Move to Trash",
            message=f"Move to Trash?\n\n{names}",
            buttons=["Trash", "Cancel"],
        )
        mb.center_on_screen(
            self.app._screen.width if self.app._screen else 80,
            self.app._screen.height if self.app._screen else 24,
        )
        self.app.root.add_child(mb)
        mb.invalidate()

        def _check_result():
            if mb.closed:
                if mb.result == "Trash":
                    errors = []
                    for p in paths:
                        try:
                            _move_to_trash(p)
                        except Exception as e:
                            errors.append(f"{p.name}: {e}")
                    self._refresh()
                    if errors:
                        self._status_label.text = f" Errors: {len(errors)}"
                    else:
                        self._status_label.text = f" {len(paths)} item{'s' if len(paths) > 1 else ''} trashed"
                self.app._needs_repaint = True
            else:
                self.app.call_later(0.1, _check_result)
        self.app.call_later(0.1, _check_result)

    def _rename(self) -> None:
        """Rename the selected file/directory using a form dialog."""
        from runtui.dialogs.form import FormDialog, FormField

        paths = self._get_selected_paths()
        if not paths or len(paths) != 1:
            self._status_label.text = " Select exactly one item to rename"
            return

        target = paths[0]
        dialog = FormDialog(
            title="Rename",
            fields=[FormField(name="name", label="New name", default=target.name)],
            width=46,
        )
        dialog.center_on_screen(
            self.app._screen.width if self.app._screen else 80,
            self.app._screen.height if self.app._screen else 24,
        )
        self.app.root.add_child(dialog)
        dialog.invalidate()

        def _check_result():
            if dialog.closed:
                if dialog.result and isinstance(dialog.result, dict):
                    new_name = dialog.result.get("name", "").strip()
                    if new_name and new_name != target.name:
                        new_path = target.parent / new_name
                        try:
                            target.rename(new_path)
                            self._refresh()
                            self._status_label.text = f" Renamed to {new_name}"
                        except Exception as e:
                            self._status_label.text = f" Rename error: {e}"
                self.app._needs_repaint = True
            else:
                self.app.call_later(0.1, _check_result)
        self.app.call_later(0.1, _check_result)

    def _navigate(self, path: Path) -> None:
        if path.is_dir():
            self._current_dir = path.resolve()
            self._refresh()


# ---------------------------------------------------------------------------
#  Terminal Window
# ---------------------------------------------------------------------------

class TerminalWindow:
    """Creates and manages a Terminal emulator window."""

    def __init__(self, app: "TuiOS", command: str = "") -> None:
        self.app = app
        self._command = command

        title = "Terminal"
        if command:
            short_cmd = command[:30] + "..." if len(command) > 30 else command
            title = f"Terminal - {short_cmd}"

        self.window = Window(
            title=title,
            width=82,
            height=26,
            on_close=self._on_close,
        )

        self._terminal = TerminalWidget(
            width=80,
            height=24,
        )
        self.window.set_content(self._terminal)

        # Give the terminal widget access to the backend so it can
        # register its PTY master fd in the select() loop.
        if app._backend is not None:
            self._terminal.set_backend(app._backend)

        # Start the terminal process
        self._terminal.start(command=command)

        # Focus the terminal widget so it receives keystrokes
        self._terminal.focus()

    def _on_close(self) -> None:
        self._terminal.stop()
        if self in self.app._terminal_windows:
            self.app._terminal_windows.remove(self)

    def get_menu(self) -> MenuBar:
        """Return the Terminal-specific menu bar."""
        return MenuBar(menus=[
            self.app._system_menu(),
            Menu("Application", [
                MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: self.app._open_terminal()),
                MenuItem("New Finder", shortcut="Ctrl+N", action=lambda: self.app._open_finder()),
                MenuItem.separator(),
                MenuItem("Close Window", shortcut="Ctrl+W", action=lambda: self.window._do_close()),
            ]),
            Menu("Edit", [
                MenuItem("Copy", shortcut="Right-Click", action=self._copy),
                MenuItem("Paste", action=self._paste),
                MenuItem.separator(),
                MenuItem("Clear Screen", action=self._clear),
            ]),
        ])

    def _copy(self) -> None:
        if self.app._prefer_internal_terminal_clipboard:
            text = self._terminal._get_selected_text()
        else:
            text = self._terminal.copy_selection()

        if text:
            TextArea._shared_text_clipboard = text

    def _paste(self) -> None:
        if TextArea._shared_text_clipboard:
            self._terminal.write_input(TextArea._shared_text_clipboard)

    def _clear(self) -> None:
        # Send "clear" to the PTY — the shell handles it natively
        if self._terminal._running and not self._terminal._child_exited:
            if sys.platform.startswith("win"):
                self._terminal.write_input("cls\r\n")
            else:
                self._terminal.write_input("clear\n")
        self._terminal.invalidate()


# ---------------------------------------------------------------------------
#  Text Editor Window
# ---------------------------------------------------------------------------

class EditorWindow:
    """Creates and manages a Text Editor window."""

    def __init__(self, app: "TuiOS", filepath: str = "") -> None:
        self.app = app
        self._filepath = filepath
        self._dirty = False

        name = os.path.basename(filepath) if filepath else "Untitled"
        self.window = Window(
            title=f"Editor - {name}",
            width=65,
            height=22,
            on_close=self._on_close,
        )

        # Custom container with dynamic layout for resize support
        content = Container()
        editor = self

        def _editor_arrange(rect: Rect) -> None:
            """Dynamically size Editor children to fit the window."""
            content._screen_rect = rect
            content.x = rect.x
            content.y = rect.y
            content.width = rect.width
            content.height = rect.height
            content._needs_layout = False

            cw = rect.width
            ch = rect.height

            # Path label: top row, full width
            editor._path_label.arrange(Rect(rect.x, rect.y, cw, 1))
            # Text area: fills middle (rows 1..ch-2)
            ta_h = max(1, ch - 2)
            editor._textarea.arrange(Rect(rect.x, rect.y + 1, cw, ta_h))
            # Status bar: bottom row
            editor._status.arrange(Rect(rect.x, rect.y + 1 + ta_h, cw, 1))

        content.arrange = _editor_arrange

        # Status bar at top showing filepath
        path_display = filepath if filepath else "New File"
        self._path_label = Label(
            text=f" {path_display}",
            x=0, y=0, width=61,
        )
        content.add_child(self._path_label)

        # Text area
        self._textarea = TextArea(
            text="",
            x=0, y=1, width=61, height=17,
            on_change=self._on_text_change,
        )
        content.add_child(self._textarea)

        # Status bar at bottom
        self._status = Label(text=" Ready", x=0, y=19, width=61)
        content.add_child(self._status)

        self.window.set_content(content)

        # Set up syntax highlighting based on file extension
        if filepath:
            ext = os.path.splitext(filepath)[1].lower()
            highlighter = SyntaxHighlighter.for_extension(ext)
            if highlighter:
                self._textarea.set_syntax(highlighter)

        # Load file if provided
        if filepath and os.path.isfile(filepath):
            self._load_file(filepath)

        # Focus the textarea so it receives keystrokes immediately
        self._textarea.focus()

    def _load_file(self, filepath: str) -> None:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            self._textarea.text = text
            self._dirty = False
            self._update_title()
            lines = text.count("\n") + 1
            self._status.text = f" Loaded: {lines} lines"
        except Exception as e:
            self._status.text = f" Error: {e}"

    def _on_text_change(self, text: str) -> None:
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = os.path.basename(self._filepath) if self._filepath else "Untitled"
        marker = " *" if self._dirty else ""
        self.window.title = f"Editor - {name}{marker}"

    def save(self) -> None:
        if not self._filepath:
            self._status.text = " No file path set (use Save As)"
            return
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                f.write(self._textarea.text)
            self._dirty = False
            self._update_title()
            self._status.text = f" Saved: {self._filepath}"
        except Exception as e:
            self._status.text = f" Save error: {e}"

    def _undo(self) -> None:
        self._textarea._undo()

    def _redo(self) -> None:
        self._textarea._redo()

    def _copy(self) -> None:
        txt = self._textarea._get_selected_text()
        if txt:
            TextArea._shared_text_clipboard = txt

    def _cut(self) -> None:
        txt = self._textarea._get_selected_text()
        if txt:
            TextArea._shared_text_clipboard = txt
            self._textarea._delete_selection()

    def _paste(self) -> None:
        ta = self._textarea
        if not TextArea._shared_text_clipboard:
            return
        ta._delete_selection()
        ta._save_undo()
        clip_lines = TextArea._shared_text_clipboard.split("\n")
        if len(clip_lines) == 1:
            ta._insert_text(clip_lines[0])
        else:
            line = ta._lines[ta._cursor_row]
            before = line[:ta._cursor_col]
            after = line[ta._cursor_col:]
            ta._lines[ta._cursor_row] = before + clip_lines[0]
            for i, cl in enumerate(clip_lines[1:], 1):
                if i == len(clip_lines) - 1:
                    ta._lines.insert(ta._cursor_row + i, cl + after)
                else:
                    ta._lines.insert(ta._cursor_row + i, cl)
            ta._cursor_row += len(clip_lines) - 1
            ta._cursor_col = len(clip_lines[-1])
            ta._desired_col = ta._cursor_col
            ta._ensure_cursor_visible()
            ta.invalidate()
            ta._notify_change()

    def _select_all(self) -> None:
        ta = self._textarea
        ta._sel_anchor = (0, 0)
        ta._cursor_row = len(ta._lines) - 1
        ta._cursor_col = len(ta._lines[-1])
        ta._desired_col = ta._cursor_col
        ta.invalidate()

    def _find(self) -> None:
        ta = self._textarea
        ta._search_active = True
        ta._search_cursor = 0
        ta._find_all_matches()
        ta.invalidate()

    def _replace(self) -> None:
        ta = self._textarea
        ta._search_active = True
        ta._search_cursor = 1
        ta._find_all_matches()
        ta.invalidate()

    def _on_close(self) -> None:
        if self in self.app._editor_windows:
            self.app._editor_windows.remove(self)

    def get_menu(self) -> MenuBar:
        """Return the Editor-specific menu bar."""
        return MenuBar(menus=[
            self.app._system_menu(),
            Menu("Application", [
                MenuItem("Save", shortcut="Ctrl+S", action=self.save),
                MenuItem.separator(),
                MenuItem("New Finder", shortcut="Ctrl+N", action=lambda: self.app._open_finder()),
                MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: self.app._open_terminal()),
                MenuItem.separator(),
                MenuItem("Close Window", shortcut="Ctrl+W", action=lambda: self.window._do_close()),
            ]),
            Menu("Edit", [
                MenuItem("Undo", shortcut="Ctrl+Z", action=self._undo),
                MenuItem("Redo", shortcut="Ctrl+Y", action=self._redo),
                MenuItem.separator(),
                MenuItem("Cut", shortcut="Ctrl+X", action=self._cut),
                MenuItem("Copy", shortcut="Ctrl+C", action=self._copy),
                MenuItem("Paste", shortcut="Ctrl+V", action=self._paste),
                MenuItem.separator(),
                MenuItem("Select All", shortcut="Ctrl+A", action=self._select_all),
                MenuItem.separator(),
                MenuItem("Find...", shortcut="Ctrl+F", action=self._find),
                MenuItem("Replace...", shortcut="Ctrl+H", action=self._replace),
            ]),
        ])


# ---------------------------------------------------------------------------
#  Image Viewer Window
# ---------------------------------------------------------------------------

class ImageViewerWindow:
    """Creates and manages an Image Viewer window using half-block rendering."""

    def __init__(self, app: "TuiOS", filepath: str) -> None:
        self.app = app
        self._filepath = filepath
        name = os.path.basename(filepath)

        self.window = Window(
            title=f"Image - {name}",
            width=64,
            height=30,
            on_close=self._on_close,
        )

        self._image_widget = ImageWidget(
            width=60,
            height=26,
        )
        self.window.set_content(self._image_widget)

        # Load image
        if not self._image_widget.load(filepath):
            self._image_widget = None

        if self._image_widget:
            self._image_widget.focus()

    def _on_close(self) -> None:
        if self in self.app._image_windows:
            self.app._image_windows.remove(self)

    def get_menu(self) -> MenuBar:
        """Return the Image Viewer menu bar."""
        return MenuBar(menus=[
            self.app._system_menu(),
            Menu("Application", [
                MenuItem("New Finder", shortcut="Ctrl+N", action=lambda: self.app._open_finder()),
                MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: self.app._open_terminal()),
                MenuItem.separator(),
                MenuItem("Close Window", shortcut="Ctrl+W", action=lambda: self.window._do_close()),
            ]),
            Menu("View", [
                MenuItem("Zoom In", shortcut="+", action=self._zoom_in),
                MenuItem("Zoom Out", shortcut="-", action=self._zoom_out),
                MenuItem("Fit to Window", shortcut="0", action=self._fit),
                MenuItem("Actual Size", shortcut="1", action=self._actual),
            ]),
        ])

    def _zoom_in(self) -> None:
        if self._image_widget:
            self._image_widget._zoom_in()

    def _zoom_out(self) -> None:
        if self._image_widget:
            self._image_widget._zoom_out()

    def _fit(self) -> None:
        if self._image_widget:
            self._image_widget._fit_to_view()
            self._image_widget.invalidate()

    def _actual(self) -> None:
        if self._image_widget:
            self._image_widget._zoom = 1.0
            self._image_widget._offset_x = 0
            self._image_widget._offset_y = 0
            self._image_widget._scaled_cache = None
            self._image_widget.invalidate()


# ---------------------------------------------------------------------------
#  Window ↔ Menu mapping
# ---------------------------------------------------------------------------

class LoadedAppWindowWrapper:
    """Wrapper for windows created by loaded .py files, providing a safe menu."""

    def __init__(self, app: "TuiOS", window: Window, foreign_app=None) -> None:
        self.app = app
        self.window = window
        self._foreign_app = foreign_app

    def _close_all_windows(self) -> None:
        """Close all windows belonging to this foreign app."""
        windows = [w for w, fa in self.app._loaded_app_windows.items()
                    if fa is self._foreign_app]
        for w in windows:
            w._do_close()

    @staticmethod
    def _is_quit_label(label: str) -> bool:
        return label.strip().lower() in ("exit", "quit")

    def _patch_menu_items(self, menu: "Menu") -> None:
        """Replace Exit/Quit items with Close that closes all foreign windows."""
        for item in menu.items:
            if item.is_separator:
                continue
            if self._is_quit_label(item.label):
                item.label = "Close Window" # "Close"
                item.shortcut = ""
                item.action = self._close_all_windows
            if item.submenu:
                self._patch_menu_items(item.submenu)

    def get_menu(self) -> MenuBar:
        menus = [self.app._system_menu()]

        # Include the foreign app's own menus if available
        foreign_menu = None
        if self._foreign_app and hasattr(self._foreign_app, 'get_menu'):
            try:
                foreign_menu = self._foreign_app.get_menu()
            except Exception:
                pass
        if not foreign_menu and self._foreign_app:
            foreign_menu = getattr(self._foreign_app, '_captured_menu', None)

        if foreign_menu and hasattr(foreign_menu, 'menus'):
            for m in foreign_menu.menus:
                self._patch_menu_items(m)
            menus.extend(foreign_menu.menus)
        else:
            menus.append(Menu("Application", [
                MenuItem("New Finder", shortcut="Ctrl+N", action=lambda: self.app._open_finder()),
                MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: self.app._open_terminal()),
                MenuItem.separator(),
                MenuItem("Close Window", shortcut="Ctrl+W", action=lambda: self.window._do_close()),
            ]))

        return MenuBar(menus=menus)


def _find_wrapper(app: "TuiOS", window: Window):
    """Find the wrapper object for a Window."""
    for fw in app._finder_windows:
        if fw.window is window:
            return fw
    for tw in app._terminal_windows:
        if tw.window is window:
            return tw
    for ew in app._editor_windows:
        if ew.window is window:
            return ew
    for iw in app._image_windows:
        if iw.window is window:
            return iw
    # Check loaded app windows — return a wrapper with the foreign app ref
    if window in app._loaded_app_windows:
        foreign_app = app._loaded_app_windows[window]
        return LoadedAppWindowWrapper(app, window, foreign_app)
    return None


# ---------------------------------------------------------------------------
#  Default Desktop Menu (when no windows are active)
# ---------------------------------------------------------------------------

def _desktop_menu(app: "TuiOS") -> MenuBar:
    """Menu bar shown when no window is active."""
    return MenuBar(menus=[
        app._system_menu(),
        Menu("Application", [
            MenuItem("New Finder", shortcut="Ctrl+N", action=lambda: app._open_finder()),
            MenuItem("New Terminal", shortcut="Ctrl+T", action=lambda: app._open_terminal()),
            MenuItem.separator(),
            MenuItem("Quit", shortcut="Ctrl+Q", action=app.quit),
        ]),
    ])


# ---------------------------------------------------------------------------
#  TuiOS Main Application
# ---------------------------------------------------------------------------

class TuiOS(App):
    """TUI-OS: macOS-like terminal desktop environment."""

    def __init__(self) -> None:
        self._config = _load_config()
        initial_theme = self._config.get("theme", "light")
        super().__init__(theme=initial_theme)
        self._finder_windows: list[FinderWindow] = []
        self._terminal_windows: list[TerminalWindow] = []
        self._editor_windows: list[EditorWindow] = []
        self._loaded_app_windows: dict[Window, object] = {}  # window -> foreign app
        self._image_windows: list[ImageViewerWindow] = []
        self._active_wrapper = None
        self._clipboard: list[tuple[str, str]] = []  # [(path, "copy"|"cut"), ...]
        self._prefer_internal_terminal_clipboard = _is_containerized()

    def set_theme(self, name: str) -> None:
        """Switch theme and persist to config."""
        super().set_theme(name)
        self._config["theme"] = name
        _save_config(self._config)

    def on_ready(self) -> None:
        # Set initial desktop menu
        self.set_menu(_desktop_menu(self))

        # Open initial Finder window
        self._open_finder()

        # Periodic repaint to pick up subprocess output from reader threads
        self.set_interval(0.1, self._tick)
        # Mandatory full refresh every 5 seconds to keep UI in sync
        self.set_interval(5.0, self._force_refresh)

    def _tick(self) -> None:
        """Periodic check for dead child processes and repaint."""
        for tw in self._terminal_windows:
            if tw._terminal._running and not tw._terminal._child_exited:
                tw._terminal._check_child_exit()
            if tw._terminal._running:
                self._needs_repaint = True
                return

    def _force_refresh(self) -> None:
        """Force a full screen refresh periodically."""
        self.invalidate_all()

    # --- System Menu (always leftmost, macOS "apple" menu equivalent) ---

    def _system_menu(self) -> Menu:
        """The persistent system menu (leftmost), like macOS apple menu."""
        return Menu("≡", [
            MenuItem("About TUI-OS", action=self._show_about),
            MenuItem.separator(),
            MenuItem("Preferences...", action=self._show_preferences),
            MenuItem.separator(),
            MenuItem("Cascade Windows", action=self._cascade_windows),
            MenuItem("Tile Horizontal", action=self._tile_h_windows),
            MenuItem("Tile Vertical", action=self._tile_v_windows),
            MenuItem.separator(),
            MenuItem("Quit", shortcut="Ctrl+Q", action=self.quit),
        ])

    def _cascade_windows(self) -> None:
        if self._window_manager:
            self._window_manager.cascade()
            self.invalidate_all()

    def _tile_h_windows(self) -> None:
        if self._window_manager:
            self._window_manager.tile_horizontal()
            self.invalidate_all()

    def _tile_v_windows(self) -> None:
        if self._window_manager:
            self._window_manager.tile_vertical()
            self.invalidate_all()

    # --- Menu Management (per-window menu switching) ---

    def _update_menu_for_window(self, window: Window | None) -> None:
        """Swap the global menu bar to match the active window."""
        if window is None:
            self.set_menu(_desktop_menu(self))
            self._active_wrapper = None
            return

        wrapper = _find_wrapper(self, window)
        if wrapper and hasattr(wrapper, "get_menu"):
            self.set_menu(wrapper.get_menu())
            self._active_wrapper = wrapper
        else:
            self.set_menu(_desktop_menu(self))
            self._active_wrapper = None

    # --- Finder ---

    def _open_finder(self, path: str | None = None) -> None:
        fw = FinderWindow(self, path)
        self._finder_windows.append(fw)
        self.add_window(fw.window)
        self._listen_window_events(fw.window)
        self._update_menu_for_window(fw.window)

    # --- Terminal ---

    def _open_terminal(self, command: str = "") -> None:
        tw = TerminalWindow(self, command)
        self._terminal_windows.append(tw)
        self.add_window(tw.window)
        self._listen_window_events(tw.window)
        self._update_menu_for_window(tw.window)

    def _open_terminal_with_command(self, command: str) -> None:
        self._open_terminal(command)

    # --- Text Editor ---

    def _open_editor(self, filepath: str = "") -> None:
        # Check if already open
        for ew in self._editor_windows:
            if ew._filepath == filepath and filepath:
                self._window_manager.activate(ew.window)
                return

        ew = EditorWindow(self, filepath)
        self._editor_windows.append(ew)
        self.add_window(ew.window)
        self._listen_window_events(ew.window)
        self._update_menu_for_window(ew.window)

    # --- Image Viewer ---

    def _open_image_viewer(self, filepath: str) -> None:
        # Check if already open
        for iw in self._image_windows:
            if iw._filepath == filepath:
                self._window_manager.activate(iw.window)
                return

        iw = ImageViewerWindow(self, filepath)
        self._image_windows.append(iw)
        self.add_window(iw.window)
        self._listen_window_events(iw.window)
        self._update_menu_for_window(iw.window)

    # --- File Type Dispatch (from Finder) ---

    def _on_finder_open_file(self, filepath: str) -> None:
        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".py":
            # Ask user whether to Run or Edit the .py file
            mb = MessageBox(
                title="Open Python File",
                message=f"How would you like to open\n{os.path.basename(filepath)}?",
                buttons=["Run", "Edit"],
            )
            mb.center_on_screen(
                self._screen.width if self._screen else 80,
                self._screen.height if self._screen else 24,
            )
            self.root.add_child(mb)
            mb.invalidate()

            def _check_py_choice():
                if mb.closed:
                    if mb.result == "Run":
                        self._run_py_file(filepath)
                    elif mb.result == "Edit":
                        self._open_editor(filepath)
                    self._needs_repaint = True
                else:
                    self.call_later(0.1, _check_py_choice)
            self.call_later(0.1, _check_py_choice)
            return
        elif ext in IMAGE_EXTENSIONS:
            self._open_image_viewer(filepath)
        elif ext == ".json":
            # Check if it looks like a RAD design file
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict) and ("windows" in data or "widgets" in data):
                    self._run_rad_json(filepath, data)
                    return
            except (json.JSONDecodeError, OSError):
                pass
            # Otherwise open as text
            self._open_editor(filepath)
        elif ext in EDITABLE_EXTENSIONS:
            self._open_editor(filepath)
        else:
            # Default: try to open as text
            self._open_editor(filepath)

    # --- In-process Python file runner ---

    def _run_py_file(self, filepath: str) -> None:
        """Execute a .py file in-process, capturing any runtui windows it creates.

        The file is exec'd. If it defines an App subclass, we instantiate it,
        redirect its add_window calls into our TUI-OS, call its on_ready(),
        and track the created windows. When those windows are closed, they're
        simply removed — no subprocess involved.
        """
        filename = os.path.basename(filepath)

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as e:
            self._show_error(f"Cannot read {filename}: {e}")
            return

        # Prepare a namespace for exec
        file_dir = os.path.dirname(os.path.abspath(filepath))
        namespace: dict = {
            "__name__": "__loaded__",  # NOT "__main__" — prevents auto-run
            "__file__": filepath,
        }

        # Add the file's directory to sys.path temporarily
        original_path = sys.path[:]
        if file_dir not in sys.path:
            sys.path.insert(0, file_dir)

        try:
            exec(compile(source, filepath, "exec"), namespace)
        except Exception as e:
            sys.path[:] = original_path
            self._show_error(f"Error loading {filename}:\n{e}")
            return

        # Find App subclasses defined in the file
        app_class = None
        for obj in namespace.values():
            if (isinstance(obj, type)
                    and issubclass(obj, App)
                    and obj is not App
                    and obj is not TuiOS):
                app_class = obj
                break

        if app_class is None:
            sys.path[:] = original_path
            self._show_error(
                f"{filename} has no App subclass.\n"
                "Opening as text instead."
            )
            self._open_editor(filepath)
            return

        # Instantiate the foreign app — but don't call run().
        # Instead, we'll redirect its window creation into our TUI-OS.
        captured_windows: list[Window] = []

        try:
            # Call __init__ so the foreign app initializes its own attributes
            # (e.g. _current_file, _design_theme, etc.)
            foreign_app = app_class()

            # Override framework internals to redirect into our TUI-OS
            foreign_app._window_manager = self._window_manager
            foreign_app._theme_engine = self._theme_engine
            foreign_app._screen = self._screen
            foreign_app._event_loop = self._event_loop
            foreign_app._menu_bar = None
            foreign_app._needs_repaint = False
            foreign_app.root = self.root

            # Monkey-patch add_window to capture into our TUI-OS
            def _capture_add_window(window, activate=True):
                captured_windows.append(window)
                window.title = f"[{filename}] {window.title}"
                self.add_window(window)
                self._listen_window_events(window)
                self._loaded_app_windows[window] = foreign_app
                self._update_menu_for_window(window)

            foreign_app.add_window = _capture_add_window

            # Capture set_menu calls from the foreign app
            def _capture_set_menu(menu_bar):
                foreign_app._captured_menu = menu_bar
            foreign_app.set_menu = _capture_set_menu

            # Redirect quit — ignore (don't let loaded app quit TUI-OS)
            foreign_app.quit = lambda *a, **kw: None

            # Provide set_theme, call_later, set_interval, invalidate_all
            foreign_app.set_theme = self.set_theme
            foreign_app.call_later = self.call_later
            foreign_app.set_interval = self.set_interval
            foreign_app.invalidate_all = self.invalidate_all

            # Call on_ready to create its windows
            if hasattr(foreign_app, 'on_ready'):
                foreign_app.on_ready()

        except Exception as e:
            sys.path[:] = original_path
            self._show_error(f"Error running {filename}:\n{e}")
            return
        finally:
            sys.path[:] = original_path

        if not captured_windows:
            self._show_error(f"{filename} created no windows.")

        self._needs_repaint = True

    # --- RAD JSON runner (in-process) ---

    def _run_rad_json(self, filepath: str, data: dict) -> None:
        """Load a RAD design JSON file and inject its windows into TUI-OS."""
        from runtui.rad.runner import preview_in_app
        try:
            windows = preview_in_app(data, self)
            for win in windows:
                self._listen_window_events(win)
                self._loaded_app_windows[win] = None
        except Exception as e:
            self._show_error(f"Error loading RAD design:\n{e}")

    def _show_error(self, message: str) -> None:
        """Show an error message box."""
        mb = MessageBox(
            title="Error",
            message=message,
            buttons=["OK"],
        )
        mb.center_on_screen(
            self._screen.width if self._screen else 80,
            self._screen.height if self._screen else 24,
        )
        self.root.add_child(mb)
        mb.invalidate()

    # --- Window Event Listener ---

    def _listen_window_events(self, window: Window) -> None:
        """Register window event handlers for menu bar switching."""
        window.on(WindowEvent, lambda e: self._on_window_event(e))

    def _on_window_event(self, event: WindowEvent) -> None:
        if event.action == WindowAction.ACTIVATE:
            self._update_menu_for_window(event.window)
        elif event.action == WindowAction.CLOSE:
            # Clean up loaded app windows
            if event.window in self._loaded_app_windows:
                del self._loaded_app_windows[event.window]
            # After close, update menu for whatever is now active
            self.call_later(0.05, self._refresh_menu_after_close)

    def _refresh_menu_after_close(self) -> None:
        if self._window_manager and self._window_manager.active_window:
            self._update_menu_for_window(self._window_manager.active_window)
        else:
            self.set_menu(_desktop_menu(self))
            self._active_wrapper = None

    # --- Preferences ---

    def _show_preferences(self) -> None:
        dialog = PreferencesDialog(self)
        dialog.center_on_screen(
            self._screen.width if self._screen else 80,
            self._screen.height if self._screen else 24,
        )
        self.root.add_child(dialog)
        dialog.invalidate()

    # --- About ---

    def _show_about(self) -> None:
        mb = MessageBox(
            title="About TUI-OS",
            message=(

                "▀▛▘▌ ▌▜▘   ▞▀▖▞▀▖\n"
                " ▌ ▌ ▌▐ ▄▄▖▌ ▌▚▄ \n"
                " ▌ ▌ ▌▐    ▌ ▌▖ ▌\n"
                " ▘ ▝▀ ▀▘   ▝▀ ▝▀   "+
                f"v{VERSION}\n"
                "\n"
                "A macOS-like desktop environment\n"
                "running entirely in the terminal.\n"
                "Built on the runtui framework.\n"
                "\n"
                "Author: Hu, Ying-Hao (Eric)\n"
                "\n"
                "Features:\n"
                "  Finder (file browser), Terminal, \n"
                "  Text Editor and Image Viewer\n"
                "  Theme Support\n"
                "\n"
                "Keyboard shortcuts:\n"
                "  Ctrl+N  New Finder\n"
                "  Ctrl+T  New Terminal\n"
                "  Ctrl+Q  Quit"
            ),
            buttons=["OK"],
        )
        mb.center_on_screen(
            self._screen.width if self._screen else 80,
            self._screen.height if self._screen else 24,
        )
        self.root.add_child(mb)
        mb.invalidate()

    # --- Global keyboard shortcuts ---

    def _is_terminal_focused(self) -> bool:
        """Check if the currently focused widget is a TerminalWidget."""
        focused = self._find_focused()
        return isinstance(focused, TerminalWidget)

    def _handle_key(self, event: KeyEvent) -> None:
        # If a TerminalWidget is focused, forward almost everything
        # directly to it. Only intercept our global shortcuts.
        # This is critical: the base App._handle_key intercepts Tab,
        # Escape, etc. for focus cycling / menu activation, but those
        # keys must reach the terminal shell.
        terminal_focused = self._is_terminal_focused()

        # Ctrl+Q: always quit
        if (event.key == Keys.CHAR and event.char == "q"
                and Modifiers.CTRL in event.modifiers):
            self.quit()
            event.mark_handled()
            return

        # Ctrl+N: New Finder
        if (event.key == Keys.CHAR and event.char == "n"
                and Modifiers.CTRL in event.modifiers):
            self._open_finder()
            event.mark_handled()
            self._needs_repaint = True
            return

        # Ctrl+T: New Terminal
        if (event.key == Keys.CHAR and event.char == "t"
                and Modifiers.CTRL in event.modifiers):
            self._open_terminal()
            event.mark_handled()
            self._needs_repaint = True
            return

        # Ctrl+W: Close active window
        if (event.key == Keys.CHAR and event.char == "w"
                and Modifiers.CTRL in event.modifiers):
            if self._window_manager and self._window_manager.active_window:
                win = self._window_manager.active_window
                win._do_close()
                event.mark_handled()
                self._needs_repaint = True
                return

        # Ctrl+S: Save (for editor windows)
        if (event.key == Keys.CHAR and event.char == "s"
                and Modifiers.CTRL in event.modifiers):
            if isinstance(self._active_wrapper, EditorWindow):
                self._active_wrapper.save()
                event.mark_handled()
                self._needs_repaint = True
                return

        # F5: Refresh Finder
        if event.key == Keys.F5:
            if isinstance(self._active_wrapper, FinderWindow):
                self._active_wrapper._refresh()
                event.mark_handled()
                self._needs_repaint = True
                return

        # When terminal is focused, send ALL remaining keys directly
        # to it — bypass the base App's Tab/Escape/focus-cycling logic
        # that would otherwise swallow keystrokes the shell needs.
        if terminal_focused:
            focused = self._find_focused()
            if focused:
                self._dispatch_to(event, focused)
                self._needs_repaint = True
                return

        # Let parent handle the rest (Tab focus cycling, menu, etc.)
        super()._handle_key(event)

    # --- Cleanup ---

    def _shutdown(self) -> None:
        """Clean up all terminal processes on exit."""
        for tw in self._terminal_windows:
            try:
                tw._terminal.stop()
            except Exception:
                pass
        super()._shutdown()


# ---------------------------------------------------------------------------
#  Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = TuiOS()
    app.run()
