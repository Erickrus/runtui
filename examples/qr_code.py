#!/usr/bin/env python3
"""QR Code generator example for runtui.

Type some text and it renders a QR code in the terminal using Unicode
half-block glyphs (▀ ▄ █) on an always-white background.

Requires the ``qrcode`` package (``pip install "qrcode[pil]"``) -- the QR
encoding is delegated to it, this example only draws the resulting matrix
into the terminal.
"""

from runtui import App, Button, Label, TextInput, Window
from runtui.widgets.container import Container
from runtui.widgets.base import Widget
from runtui.layout.absolute import AbsoluteLayout
from runtui.core.types import Color, Size
from runtui.rendering.painter import Painter

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ----------------------------------------------------------------------
# Widget that renders a QR matrix using ▀ ▄ █ on a white background
# ----------------------------------------------------------------------

class QRCodeWidget(Widget):
    """Renders a QR matrix (list of lists of bool, True = dark) with
    half-block glyphs.

    Two module rows are packed into one terminal cell:
        █  top & bottom dark
        ▀  top dark, bottom light
        ▄  top light, bottom dark
        (space)  both light

    The light half is always drawn as white, so the QR background is
    guaranteed to be white.
    """

    def __init__(self, id: str | None = None, x: int = 0, y: int = 0,
                 width: int = 40, height: int = 20) -> None:
        super().__init__(id=id, x=x, y=y, width=width, height=height)
        self._matrix: list | None = None
        self.can_focus = False

    def set_matrix(self, matrix: list | None) -> None:
        self._matrix = matrix
        self.invalidate()

    def measure(self, available: Size) -> Size:
        return Size(self.width or available.width, self.height or available.height)

    def paint(self, painter: Painter) -> None:
        sr = self._screen_rect
        lx = sr.x - painter._offset.x
        ly = sr.y - painter._offset.y
        w = sr.width
        h = sr.height

        white = Color.from_rgb(255, 255, 255)
        black = Color.from_rgb(0, 0, 0)

        painter.fill_rect(lx, ly, w, h, bg=white)

        if not self._matrix:
            hint = "Type text to generate a QR code"
            painter.put_str(lx + max(0, (w - len(hint)) // 2), ly + h // 2,
                            hint, fg=Color.from_rgb(120, 120, 120), bg=white)
            return

        size = len(self._matrix)
        rows = (size + 1) // 2
        ox = lx + (w - size) // 2
        oy = ly + (h - rows) // 2

        for r in range(rows):
            for c in range(size):
                top = self._matrix[2 * r][c]
                bottom = self._matrix[2 * r + 1][c] if 2 * r + 1 < size else False
                if top and bottom:
                    ch = "█"
                elif top:
                    ch = "▀"
                elif bottom:
                    ch = "▄"
                else:
                    ch = " "
                painter.put_char(ox + c, oy + r, ch, fg=black, bg=white)


# ----------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------

class QRCodeApp(App):
    """Application: QR Code Generator."""

    def __init__(self) -> None:
        super().__init__(theme="light")

    def on_ready(self) -> None:
        self.main_window = Window(
            title="QR Code Generator",
            x=3, y=2, width=50, height=28,
        )

        content = Container()
        content._layout_manager = AbsoluteLayout()

        self.inp = TextInput(
            x=2, y=1, width=30, height=1,
            placeholder="Type text to encode...",
            on_change=self._on_change,
            on_submit=self._on_submit,
        )
        content.add_child(self.inp)

        self.btn_generate = Button(
            x=34, y=1, width=10, height=1, text="Generate",
            on_click=self._generate_click,
        )
        content.add_child(self.btn_generate)

        self.lbl_info = Label(x=2, y=3, width=44, height=1, text="", align="left")
        content.add_child(self.lbl_info)

        self.qr = QRCodeWidget(x=2, y=4, width=45, height=21)
        content.add_child(self.qr)

        self.main_window.set_content(content)
        self.add_window(self.main_window)

        self._generate("https://github.com/erickrus/runtui")

    def _generate(self, text: str) -> None:
        text = (text or "").strip()
        if not HAS_QRCODE:
            self.qr.set_matrix(None)
            self.lbl_info.text = "Missing dependency: pip install 'qrcode[pil]'"
            return
        if not text:
            self.qr.set_matrix(None)
            self.lbl_info.text = "Enter some text to generate a QR code"
            return

        try:
            qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=4)
            qr.add_data(text)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            self.qr.set_matrix(matrix)
            modules = qr.version * 4 + 17
            self.lbl_info.text = (
                f"Version {qr.version}  |  {modules}x{modules} modules  |  EC M"
            )
        except Exception as exc:  # noqa: BLE001
            self.qr.set_matrix(None)
            self.lbl_info.text = f"Error: {exc}"

    def _on_change(self, text: str) -> None:
        self._generate(text)

    def _on_submit(self, text: str) -> None:
        self._generate(text)

    def _generate_click(self) -> None:
        self._generate(self.inp.text)


if __name__ == "__main__":
    app = QRCodeApp()
    app.run()
