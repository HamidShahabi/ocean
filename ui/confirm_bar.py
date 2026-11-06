"""A slim in-page confirmation strip ("Delete 3 entries? [Delete] [Cancel]") for destructive actions."""
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.widgets import SmoothGlassFrame


class ConfirmBar(SmoothGlassFrame):
    """Hidden until ask() is called; confirming runs the callback, either button hides the bar again."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(radius=14, variant="inner", parent=parent)
        self._on_confirm: Optional[Callable[[], None]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        self.message = QLabel("")
        self.message.setObjectName("BodyText")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.confirm_btn = self._button("Delete", "PrimaryPillButton", self._confirm)
        self.cancel_btn = self._button("Cancel", "GlassPillButton", self.hide)
        row.addWidget(self.cancel_btn, 1)
        row.addWidget(self.confirm_btn, 1)
        lay.addLayout(row)
        self.hide()

    @staticmethod
    def _button(text: str, object_name: str, slot: Callable[[], None]) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(34)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(lambda _=False: slot())
        return btn

    def ask(self, message: str, on_confirm: Callable[[], None], confirm_text: str = "Delete") -> None:
        self.message.setText(message)
        self.confirm_btn.setText(confirm_text)
        self._on_confirm = on_confirm
        self.show()

    def _confirm(self) -> None:
        callback, self._on_confirm = self._on_confirm, None
        self.hide()
        if callback is not None:
            callback()
