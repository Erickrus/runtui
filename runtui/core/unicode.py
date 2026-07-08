"""Unicode width detection and grapheme helpers."""

from __future__ import annotations

import functools
import unicodedata


@functools.lru_cache(maxsize=4096)
def char_width(ch: str) -> int:
    """Return display width of a character or grapheme cluster: 0, 1, or 2."""
    if not ch:
        return 0
    # pyte may produce multi-character strings (e.g. base + combining mark);
    # measure the base character and treat combining marks as zero-width.
    if len(ch) > 1:
        return char_width(ch[0])
    # Control characters
    cp = ord(ch)
    if cp < 32 or cp == 0x7F:
        return 0
    # Zero-width characters
    if cp in (0x200B, 0x200C, 0x200D, 0xFEFF):
        return 0
    # Combining marks
    cat = unicodedata.category(ch)
    if cat.startswith("M"):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("F", "W"):
        return 2
    return 1


def string_width(s: str) -> int:
    """Return total display width of a string."""
    return sum(char_width(ch) for ch in s)


def truncate_to_width(s: str, max_width: int, ellipsis: str = "…") -> str:
    """Truncate string to fit within max_width display columns."""
    if string_width(s) <= max_width:
        return s
    ellipsis_w = string_width(ellipsis)
    if max_width <= ellipsis_w:
        return ellipsis[:max_width] if max_width > 0 else ""
    result = []
    width = 0
    for ch in s:
        cw = char_width(ch)
        if width + cw > max_width - ellipsis_w:
            break
        result.append(ch)
        width += cw
    return "".join(result) + ellipsis


def pad_to_width(s: str, target_width: int, fill: str = " ", align: str = "left") -> str:
    """Pad string to exact display width."""
    current = string_width(s)
    if current >= target_width:
        return truncate_to_width(s, target_width, ellipsis="")
    pad_count = target_width - current
    if align == "left":
        return s + fill * pad_count
    elif align == "right":
        return fill * pad_count + s
    else:  # center
        left_pad = pad_count // 2
        right_pad = pad_count - left_pad
        return fill * left_pad + s + fill * right_pad


def split_by_width(s: str, width: int) -> list[str]:
    """Split string into lines that fit within the given width."""
    lines: list[str] = []
    current: list[str] = []
    current_width = 0

    for ch in s:
        if ch == "\n":
            lines.append("".join(current))
            current = []
            current_width = 0
            continue
        cw = char_width(ch)
        if current_width + cw > width:
            lines.append("".join(current))
            current = [ch]
            current_width = cw
        else:
            current.append(ch)
            current_width += cw

    lines.append("".join(current))
    return lines
