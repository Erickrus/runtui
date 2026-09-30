#!/usr/bin/env python3
"""t-monitor - a terminal (TUI) training monitor in the style of htop / nvtop."""

import argparse
import glob
import math
import os
import re
import sys
import time

from runtui import App
from runtui.core.event import KeyEvent
from runtui.core.keys import Keys, Modifiers
from runtui.core.types import Color, Rect, Attrs
from runtui.widgets.base import Widget

METRIC_RE = re.compile(r"^\s*([^:]+?)\s*:\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)(?=[A-Za-z]*(?:$|[\s,)\]]))")
PIPE_RE = re.compile(r"^\s*(.+?)\s*\|\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)(?=$|[\s,)\]])")
EQ_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_./-]*)\s*=\s*(-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)(?=$|[\s,)\]])")
_NUM_RE = re.compile(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
_METRIC_BLOCK = {
    "epoch", "epochs", "step", "steps", "iteration", "iterations", "iter",
    "global_step", "globalstep", "v_num", "it",
}

ITER_RE = re.compile(r"Learning iteration\s+(\d+)\s*/\s*(\d+)")
GLOBAL_STEP_RE = re.compile(r"global_step\s*[:=]\s*(\d+)")
EPOCH_RE = re.compile(r"(?:^|[\s\[(])[Ee]poch\s*[:=]?\s*(\d+)(?:\s*/\s*(\d+))?")
STEP_RE = re.compile(r"\b[sS]tep\b\s*[:= ]\s*(\d+)(?:\s*/\s*(\d+))?")
TQDM_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")

RUN_RE = re.compile(r"(?:Run name|Experiment|Job)\s*[:=]\s*(.+)")
ELAPSED_RE = re.compile(r"(?:^|[\s:])(?:Time )?[Ee]lapsed\s*[:=]\s*([0-9:]+)")
ETA_RE = re.compile(r"(?:^|[\s:])ETA\s*[:=]\s*([0-9:]+)")

PARTIAL = " ▁▂▃▄▅▆▇█"

_BRAILLE_BITS = {
    (0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (1, 0): 0x08,
    (1, 1): 0x10, (1, 2): 0x20, (0, 3): 0x40, (1, 3): 0x80,
}


class LogParser:
    def __init__(self, path):
        self.path = path
        self.metrics = {}
        self.run_name = ""
        self.max_iter = 0
        self.cur_iter = -1
        self.elapsed = ""
        self.eta = ""
        self.samples = 0
        self._fh = None
        self._ready = False

    def open(self):
        try:
            self._fh = open(self.path, "r", errors="replace")
            self._ready = True
        except OSError:
            self._fh = None
            self._ready = False
        return self._ready

    def refresh(self):
        if not self._ready:
            if not self.open():
                return False
        try:
            lines = self._fh.readlines()
        except OSError:
            return False
        if not lines:
            return False
        self._parse(lines)
        return True

    def _parse(self, lines):
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            self._parse_header(line)
            self._parse_iter(line)
            self._parse_metrics(line)

    def _parse_header(self, line):
        m = RUN_RE.search(line)
        if m:
            self.run_name = m.group(1).strip()
        m = ELAPSED_RE.search(line)
        if m:
            self.elapsed = m.group(1)
        m = ETA_RE.search(line)
        if m:
            self.eta = m.group(1)

    def _parse_iter(self, line):
        m = ITER_RE.search(line)
        if m:
            self._set_iter(int(m.group(1)), int(m.group(2)))
            return
        m = GLOBAL_STEP_RE.search(line)
        if m:
            self._set_iter(int(m.group(1)))
            return
        m = EPOCH_RE.search(line)
        if m:
            total = int(m.group(2)) if m.group(2) else 0
            self._set_iter(int(m.group(1)), total)
            return
        m = STEP_RE.search(line)
        if m:
            total = int(m.group(2)) if m.group(2) else 0
            self._set_iter(int(m.group(1)), total)
            return
        m = TQDM_RE.search(line)
        if m:
            self._set_iter(int(m.group(1)), int(m.group(2)))
            return

    def _set_iter(self, cur, total=0):
        if cur != self.cur_iter:
            self.cur_iter = cur
            self.samples += 1
        if total:
            self.max_iter = max(self.max_iter, total)

    def _parse_metrics(self, line):
        if self.cur_iter < 0:
            return
        colon = METRIC_RE.match(line)
        if colon:
            name = colon.group(1).strip().rstrip(":")
            try:
                value = float(colon.group(2))
            except ValueError:
                value = None
            self._add(name, value)
        eq_found = False
        for m in EQ_RE.finditer(line):
            try:
                value = float(m.group(2))
            except ValueError:
                continue
            self._add(m.group(1), value)
            eq_found = True
        if colon or eq_found:
            return
        pipe = PIPE_RE.match(line)
        if pipe:
            name = pipe.group(1).strip().rstrip("|").strip()
            try:
                value = float(pipe.group(2))
            except ValueError:
                value = None
            self._add(name, value)
            return
        self._parse_space(line)

    def _add(self, name, value):
        if not name or value is None:
            return
        ln = name.lower().strip()
        if ln in _METRIC_BLOCK:
            return
        if re.match(r"^(?:epoch|step|iter|iteration)s?\b(?:\s+\d+)?$", ln):
            return
        self.metrics.setdefault(name, []).append(value)

    def _parse_space(self, line):
        if ":" in line or "=" in line or "|" in line or "%" in line or "[" in line:
            return
        tokens = line.replace(",", " ").split()
        i = 0
        while i + 1 < len(tokens):
            name = tokens[i]
            val = tokens[i + 1]
            if not _NAME_RE.fullmatch(name):
                i += 1
                continue
            if not _NUM_RE.fullmatch(val):
                i += 1
                continue
            self._add(name, float(val))
            i += 2

    def metric_names(self):
        return sorted(self.metrics.keys())

    def series(self, name):
        return self.metrics.get(name, [])


class DemoParser:
    def __init__(self):
        self.metrics = {}
        self.run_name = "demo-sine"
        self.max_iter = 1000
        self.cur_iter = 0
        self.elapsed = "00:00:00"
        self.eta = "00:00:00"
        self.samples = 0
        self._t = 0

    def refresh(self):
        t = self._t
        for i in range(8):
            k = self._t
            self.metrics.setdefault("loss", []).append(
                1.0 / (1.0 + k * 0.05) + 0.05 * math.sin(k * 0.3))
            self.metrics.setdefault("reward", []).append(
                2.0 + 1.5 * math.sin(k * 0.2) + 0.3 * math.sin(k * 0.9))
            self.metrics.setdefault("ep_len", []).append(
                20 + 10 * math.sin(k * 0.1) + 2 * math.sin(k * 0.5))
            self.metrics.setdefault("track", []).append(
                0.5 + 0.4 * math.sin(k * 0.15) + 0.1 * math.cos(k * 0.4))
            self.metrics.setdefault("sps", []).append(
                22000 + 1500 * math.sin(k * 0.05))
            self._t += 1
        self.cur_iter = self._t
        self.samples = self._t
        self.elapsed = _sec_to_hms(self._t * 2.2)
        self.eta = _sec_to_hms((1000 - self._t) * 2.2)
        time.sleep(0.05)
        return True

    def metric_names(self):
        return sorted(self.metrics.keys())

    def series(self, name):
        return self.metrics.get(name, [])


def _sec_to_hms(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _clip(s, w):
    s = str(s)
    if len(s) <= w:
        return s
    if w <= 3:
        return s[:w]
    return s[: w - 3] + "..."


def _fmt(v):
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 10000:
        return f"{v:.0f}"
    if a >= 100:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}"
    if a >= 0.01:
        return f"{v:.3f}"
    return f"{v:.4f}"


def heat_color(f):
    f = max(0.0, min(1.0, f))
    if f < 0.5:
        t = f / 0.5
        return Color.from_rgb(int(255 * t), 255, 0)
    t = (f - 0.5) / 0.5
    return Color.from_rgb(255, int(255 * (1.0 - t)), 0)


def _bresenham(grid, p0, p1):
    x0, y0 = p0
    x1, y1 = p1
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    H = len(grid)
    W = len(grid[0])
    while True:
        if 0 <= y0 < H and 0 <= x0 < W:
            grid[y0][x0] = True
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def render_braille(series, w, h):
    if not series or w < 1 or h < 1:
        return [" " * w for _ in range(h)]
    vmin, vmax = min(series), max(series)
    if vmax - vmin < 1e-12:
        vmax = vmin + 1.0
    n = len(series)
    DW = w * 2
    DH = h * 4
    grid = [[False] * DW for _ in range(DH)]

    def pos(i):
        dx = int((i / (n - 1)) * (DW - 1)) if n > 1 else 0
        dy = DH - 1 - int(((series[i] - vmin) / (vmax - vmin)) * (DH - 1))
        return dx, dy

    prev = pos(0)
    grid[prev[1]][prev[0]] = True
    for i in range(1, n):
        cur = pos(i)
        _bresenham(grid, prev, cur)
        prev = cur
    rows = []
    for cy in range(h):
        line = []
        for cx in range(w):
            bits = 0
            for dy in range(4):
                for dx in range(2):
                    if grid[cy * 4 + dy][cx * 2 + dx]:
                        bits |= _BRAILLE_BITS[(dx, dy)]
            line.append(chr(0x2800 + bits))
        rows.append("".join(line))
    return rows


class MonitorView(Widget):
    def __init__(self, app, plot_h=5):
        super().__init__()
        self.app = app
        self.filter = ""
        self.typing = False
        self.cursor = 0
        self.list_scroll = 0
        self.selected = set()
        self.mode = "bar"
        self.plot_h = plot_h
        self.chart_scroll = 0
        self.can_focus = False

    def filtered(self):
        f = self.filter.lower()
        names = self.app.parser.metric_names()
        if not f:
            return names
        return [n for n in names if f in n.lower()]

    def selected_in_order(self):
        names = self.app.parser.metric_names()
        return [n for n in names if n in self.selected]

    def toggle_cursor(self):
        fl = self.filtered()
        if 0 <= self.cursor < len(fl):
            name = fl[self.cursor]
            if name in self.selected:
                self.selected.discard(name)
            else:
                self.selected.add(name)

    def on_key(self, event):
        k = event.key
        ch = event.char
        mods = event.modifiers
        ctrl = Modifiers.CTRL in mods
        if self.typing:
            if k == Keys.ESCAPE or k == Keys.ENTER:
                self.typing = False
            elif k == Keys.BACKSPACE:
                self.filter = self.filter[:-1]
                self.cursor = 0
            elif k == Keys.CHAR and ch:
                self.filter += ch
                self.cursor = 0
            return
        if k == Keys.CHAR:
            if ctrl and ch == "c":
                self.app.quit()
                return
            if ch == "q":
                self.app.quit()
                return
            if ch == "/":
                self.typing = True
                return
            if ch == " ":
                self.toggle_cursor()
                return
            if ch in ("m", "M"):
                self.mode = "braille" if self.mode == "bar" else "bar"
                return
            if ch in ("-", "_"):
                self.plot_h = max(3, self.plot_h - 1)
                return
            if ch in ("=", "+"):
                self.plot_h = min(12, self.plot_h + 1)
                return
            if ch in ("a", "A"):
                self.selected.update(self.filtered())
                return
            if ch in ("n", "N"):
                self.selected.clear()
                return
            if ch == "g":
                self.cursor = 0
                self.list_scroll = 0
                return
            if ch == "G":
                self.cursor = max(0, len(self.filtered()) - 1)
                return
            if ch == "[":
                self.chart_scroll = max(0, self.chart_scroll - 1)
                return
            if ch == "]":
                self.chart_scroll += 1
                return
            return
        if k == Keys.UP:
            self.cursor = max(0, self.cursor - 1)
            return
        if k == Keys.DOWN:
            self.cursor = min(len(self.filtered()) - 1, self.cursor + 1)
            return
        if k == Keys.PAGE_UP:
            self.cursor = max(0, self.cursor - 10)
            return
        if k == Keys.PAGE_DOWN:
            self.cursor = min(len(self.filtered()) - 1, self.cursor + 10)
            return
        if k == Keys.ENTER:
            self.toggle_cursor()
            return
        if k == Keys.HOME:
            self.cursor = 0
            return
        if k == Keys.END:
            self.cursor = max(0, len(self.filtered()) - 1)
            return

    def paint(self, painter):
        sr = self._screen_rect
        W, H = sr.width, sr.height
        painter.fill_rect(0, 0, W, H, bg=Color.DEFAULT)
        header_h = 2
        self._paint_header(painter, W)
        charts_w = self._paint_sidebar(painter, W, H, header_h)
        self._paint_charts(painter, charts_w, H, header_h)
        self._paint_help(painter, W, H)

    def _paint_header(self, painter, W):
        p = self.app.parser
        title = " t-monitor "
        painter.put_str(0, 0, title, fg=Color.BLACK, bg=Color.BRIGHT_CYAN, attrs=Attrs.BOLD)
        x = len(title)
        path = getattr(p, "path", "")
        run = p.run_name or (os.path.basename(path) if path else "log")
        info = f" {_clip(run, 30)} "
        painter.put_str(x, 0, info, fg=Color.BRIGHT_WHITE, attrs=Attrs.BOLD)
        x += len(info)
        if p.max_iter > 0:
            frac = max(0.0, min(1.0, p.cur_iter / p.max_iter))
            pct = int(round(frac * 100))
            bar_w = min(30, max(10, W - x - 30))
            bar_w = max(10, bar_w)
            filled = int(round(frac * bar_w))
            label = f" {p.cur_iter}/{p.max_iter} "
            painter.put_str(x, 0, " ", fg=Color.DEFAULT)
            x += 1
            painter.put_str(x, 0, "█" * filled, fg=Color.GREEN)
            x += filled
            painter.put_str(x, 0, "░" * (bar_w - filled), fg=Color.BRIGHT_BLACK)
            x += bar_w - filled
            painter.put_str(x, 0, label, fg=Color.BRIGHT_WHITE)
            x += len(label)
            painter.put_str(x, 0, f"{pct:3d}%", fg=Color.BRIGHT_GREEN, attrs=Attrs.BOLD)
            x += 4
        elif p.cur_iter >= 0:
            label = f" iter {p.cur_iter} "
            painter.put_str(x, 0, label, fg=Color.BRIGHT_WHITE, attrs=Attrs.BOLD)
            x += len(label)
        if p.eta:
            eta = f" ETA {p.eta} "
            painter.put_str(min(x, W - len(eta) - 1), 0, eta, fg=Color.YELLOW)

        line2 = []
        sps = p.series("Steps per second")
        if sps:
            line2.append(f"SPS {_fmt(sps[-1])}")
        if p.elapsed:
            line2.append(f"elapsed {p.elapsed}")
        if getattr(p, "samples", 0):
            line2.append(f"samples {p.samples}")
        n_metrics = len(p.metric_names())
        line2.append(f"metrics {n_metrics}")
        line2.append(f"shown {len(self.selected)}")
        line2.append(f"mode {self.mode}")
        line2.append(f"plot {self.plot_h}")
        painter.put_str(0, 1, "  " + "  |  ".join(line2), fg=Color.BRIGHT_BLACK)

    def _paint_help(self, painter, W, H):
        help_text = ("[/] filter  [↑↓] nav  [space] toggle  [a] all  [n] none  "
                     "[m] bar/line  [-+] plot  [[]] chart-scroll  [q] quit")
        painter.fill_rect(0, H - 1, W, 1, bg=Color.BRIGHT_BLACK)
        painter.put_str(0, H - 1, " " + help_text[: W - 2], fg=Color.BLACK, bg=Color.BRIGHT_BLACK, attrs=Attrs.BOLD)

    def _paint_sidebar(self, painter, W, H, header_h):
        sidebar_w = min(44, max(24, W // 4))
        if W - sidebar_w < 40:
            sidebar_w = max(18, W - 40)
        x0 = W - sidebar_w
        painter.put_str(x0, header_h, "─" * sidebar_w, fg=Color.BRIGHT_BLACK)
        painter.put_str(x0, header_h + 1, " FILTER ", fg=Color.BRIGHT_CYAN, attrs=Attrs.BOLD)
        box = self.filter
        if self.typing:
            box = (self.filter + "_") if len(self.filter) < sidebar_w - 2 else self.filter
        painter.put_str(x0 + 2, header_h + 2, "[" + _clip(box, sidebar_w - 4) + "]", fg=Color.WHITE, attrs=Attrs.BOLD if self.typing else Attrs.NONE)
        fl = self.filtered()
        list_top = header_h + 3
        list_h = max(1, H - list_top - 2)
        painter.put_str(x0, list_top, f" {len(fl)}/{len(self.app.parser.metric_names())} ", fg=Color.BRIGHT_BLACK)
        if self.cursor >= len(fl) and fl:
            self.cursor = len(fl) - 1
        if self.cursor < 0:
            self.cursor = 0
        if self.cursor < self.list_scroll:
            self.list_scroll = self.cursor
        if self.cursor >= self.list_scroll + list_h:
            self.list_scroll = self.cursor - list_h + 1
        self.list_scroll = max(0, self.list_scroll)
        for r in range(list_h):
            idx = self.list_scroll + r
            if idx >= len(fl):
                break
            name = fl[idx]
            is_cursor = idx == self.cursor
            is_sel = name in self.selected
            marker = "[x]" if is_sel else "[ ]"
            cur = self.app.parser.series(name)
            val = _fmt(cur[-1]) if cur else "-"
            y = list_top + 1 + r
            if is_cursor:
                painter.fill_rect(x0, y, sidebar_w, 1, bg=Color.BRIGHT_CYAN)
                painter.put_str(x0 + 1, y, marker + " ", fg=Color.BLACK, bg=Color.BRIGHT_CYAN, attrs=Attrs.BOLD)
                avail = sidebar_w - len(marker) - 1 - len(val) - 2
                painter.put_str(x0 + 1 + len(marker) + 1, y, _clip(name, avail), fg=Color.BLACK, bg=Color.BRIGHT_CYAN, attrs=Attrs.BOLD)
                painter.put_str(x0 + sidebar_w - len(val) - 1, y, val, fg=Color.BLACK, bg=Color.BRIGHT_CYAN, attrs=Attrs.BOLD)
            else:
                painter.put_str(x0 + 1, y, marker + " ", fg=Color.GREEN if is_sel else Color.BRIGHT_BLACK)
                avail = sidebar_w - len(marker) - 1 - len(val) - 2
                painter.put_str(x0 + 1 + len(marker) + 1, y, _clip(name, avail), fg=Color.WHITE)
                painter.put_str(x0 + sidebar_w - len(val) - 1, y, val, fg=Color.BRIGHT_WHITE)
        return x0

    def _paint_charts(self, painter, charts_w, H, header_h):
        p = self.app.parser
        sel = self.selected_in_order()
        if not sel:
            painter.put_str(2, header_h + 1, "No metrics selected. Press / to filter and SPACE to toggle.", fg=Color.BRIGHT_BLACK)
            return
        panel_h = self.plot_h + 1
        fit = max(1, (H - header_h - 2) // panel_h)
        if self.chart_scroll > max(0, len(sel) - fit):
            self.chart_scroll = max(0, len(sel) - fit)
        start = self.chart_scroll
        end = min(len(sel), start + fit)
        for i, name in enumerate(sel[start:end]):
            y = header_h + i * panel_h
            self._paint_chart(painter, name, charts_w, y, self.plot_h)
        if end < len(sel):
            painter.put_str(2, header_h + fit * panel_h, f" +{len(sel) - end} more (press ] to scroll)", fg=Color.BRIGHT_BLACK)

    def _paint_chart(self, painter, name, w, y, h):
        p = self.app.parser
        series = p.series(name)
        painter.put_str(1, y, _clip(name, w - 2), fg=Color.WHITE, bg=Color.DEFAULT, attrs=Attrs.BOLD)
        if series:
            cur = series[-1]
            vmin = min(series)
            vmax = max(series)
            stats = f"cur {_fmt(cur)}  min {_fmt(vmin)}  max {_fmt(vmax)}"
            painter.put_str(max(1, w - len(stats) - 1), y, stats, fg=Color.BRIGHT_BLACK, bg=Color.DEFAULT)
            window = series[-self.app.history:]
            cw = max(4, w - 2)
            if self.mode == "braille":
                rows = render_braille(window, cw, h)
                for r, row in enumerate(rows):
                    painter.put_str(1, y + 1 + r, row, fg=Color.GREEN, bg=Color.DEFAULT)
            else:
                self._draw_bars(painter, 1, y + 1, window, cw, h)
        else:
            painter.put_str(1, y + 1, "no data yet", fg=Color.BRIGHT_BLACK, bg=Color.DEFAULT)

    def _draw_bars(self, painter, x, y, series, w, h):
        if not series or w < 1 or h < 1:
            return
        vmin, vmax = min(series), max(series)
        if vmax - vmin < 1e-12:
            vmax = vmin + 1.0
        n = len(series)
        if n <= w:
            cols = series
        else:
            step = (n - 1) / (w - 1) if w > 1 else 0.0
            cols = [series[int(round(i * step))] for i in range(w)]
        for c, v in enumerate(cols):
            f = (v - vmin) / (vmax - vmin)
            f = max(0.0, min(1.0, f))
            color = heat_color(f)
            total = f * h
            full = int(total)
            frac = total - full
            part = int(round(frac * 8))
            for r in range(h):
                from_bottom = h - r
                if from_bottom <= full:
                    painter.put_char(x + c, y + r, "█", fg=color, bg=Color.DEFAULT)
                elif from_bottom == full + 1 and part > 0:
                    painter.put_char(x + c, y + r, PARTIAL[part], fg=color, bg=Color.DEFAULT)


class MonitorApp(App):
    def __init__(self, parser, interval, history, plot_height=5):
        super().__init__(theme="dark")
        self.parser = parser
        self.interval = interval
        self.history = history
        self.plot_height = plot_height
        self.view = None

    def on_ready(self):
        cols, rows = self._screen.width, self._screen.height
        self.view = MonitorView(self, plot_h=self.plot_height)
        self.view._screen_rect = Rect(0, 0, cols, rows)
        self.view.parent = self.root
        self._desktop = self.view
        self._taskbar = None
        self._tick()
        self.set_interval(self.interval, self._tick)

    def _tick(self):
        try:
            self.parser.refresh()
        except Exception:
            pass
        self.invalidate_all()

    def _handle_resize(self, event):
        if self._screen:
            self._screen.resize(event.width, event.height)
        if self.root:
            self.root.width = event.width
            self.root.height = event.height
            self.root._screen_rect = Rect(0, 0, event.width, event.height)
        if self.view:
            self.view._screen_rect = Rect(0, 0, event.width, event.height)
        self.invalidate_all()

    def _handle_key(self, event):
        super()._handle_key(event)
        if event.handled:
            return
        if self.view:
            self.view.on_key(event)
            self._needs_repaint = True


def detect_log():
    candidates = sorted(glob.glob("train*.log") + glob.glob("*train*.log"), key=os.path.getmtime)
    if candidates:
        return candidates[-1]
    return None


def main():
    ap = argparse.ArgumentParser(description="t-monitor: htop/nvtop-style training monitor")
    ap.add_argument("logfile", nargs="?", default=None, help="training log to follow (auto-detected if omitted)")
    ap.add_argument("--interval", type=float, default=1.0, help="refresh interval in seconds")
    ap.add_argument("--history", type=int, default=160, help="number of points shown per chart")
    ap.add_argument("--plot-height", type=int, default=5, help="default chart height in rows")
    ap.add_argument("--demo", action="store_true", help="use synthetic demo data")
    args = ap.parse_args()

    if args.demo:
        parser = DemoParser()
    else:
        path = args.logfile or detect_log()
        if not path:
            print("No log file found. Pass a path or use --demo.", file=sys.stderr)
            sys.exit(1)
        parser = LogParser(path)
        parser.open()
        parser.refresh()

    app = MonitorApp(parser, args.interval, args.history, plot_height=args.plot_height)
    app.run()


if __name__ == "__main__":
    main()
