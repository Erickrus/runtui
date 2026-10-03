"""Mouse state machine -- click, double-click, drag detection."""

from __future__ import annotations

import time

from ..core.event import MouseEvent
from ..core.keys import MouseAction, MouseButton


class MouseTracker:
    """Tracks mouse state to detect double-clicks and drag operations."""

    DOUBLE_CLICK_INTERVAL = 0.4  # seconds
    DRAG_THRESHOLD = 2  # pixels

    def __init__(self) -> None:
        self._last_click_time = 0.0
        self._last_click_x = -1
        self._last_click_y = -1
        self._last_click_button = MouseButton.NONE
        self._button_down = False
        self._press_x = 0
        self._press_y = 0
        self._is_dragging = False
        self.x = 0
        self.y = 0

    def process(self, event: MouseEvent) -> MouseEvent:
        """Process a mouse event and potentially modify it (e.g., detect double-click)."""
        self.x = event.x
        self.y = event.y

        # The scroll wheel is reported as a PRESS with no matching RELEASE
        # (SGR has no wheel-release event). Treating it as a button-down would
        # leave _button_down stuck True, so every later move would be rewritten
        # into a bogus DRAG carrying the SCROLL button — making lists/editors
        # keep scrolling while the mouse merely moves. The wheel is not a drag
        # gesture, so leave button/drag state untouched and pass it through.
        if event.button in (MouseButton.SCROLL_UP, MouseButton.SCROLL_DOWN):
            return event

        if event.action == MouseAction.PRESS:
            now = time.monotonic()
            # Check for double-click
            if (
                event.button == self._last_click_button
                and now - self._last_click_time < self.DOUBLE_CLICK_INTERVAL
                and abs(event.x - self._last_click_x) <= 1
                and abs(event.y - self._last_click_y) <= 1
            ):
                self._last_click_time = 0  # Reset to prevent triple-click
            else:
                self._last_click_time = now
                self._last_click_x = event.x
                self._last_click_y = event.y
                self._last_click_button = event.button

            self._button_down = True
            self._press_x = event.x
            self._press_y = event.y
            self._is_dragging = False

        elif event.action == MouseAction.MOVE and self._button_down:
            # Detect drag
            dx = abs(event.x - self._press_x)
            dy = abs(event.y - self._press_y)
            if dx >= self.DRAG_THRESHOLD or dy >= self.DRAG_THRESHOLD:
                self._is_dragging = True
                return MouseEvent(
                    x=event.x,
                    y=event.y,
                    button=self._last_click_button,
                    action=MouseAction.DRAG,
                    modifiers=event.modifiers,
                    timestamp=event.timestamp,
                )

        elif event.action == MouseAction.RELEASE:
            self._button_down = False
            self._is_dragging = False

        return event

    @property
    def is_dragging(self) -> bool:
        return self._is_dragging
