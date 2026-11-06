"""Reports: the "!" popover on a result, and the drawer page that lists, opens, deletes and exports reports."""
import logging
import os
from typing import Callable, TYPE_CHECKING

from PyQt6.QtCore import QPoint, QSize, QStandardPaths, Qt
from PyQt6.QtGui import QCursor, QFontMetrics
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import report_export
from ui.confirm_bar import ConfirmBar
from ui.widgets import SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow

log = logging.getLogger("ocean.app")

KIND_LABELS = {"word": "Word", "idiom": "Idiom", "sentence": "Sentence", "persian": "Persian"}


def format_when(created_at: str) -> str:
    """"2026-11-05T14:03:09" -> "2026-11-05 14:03"."""
    return created_at.replace("T", " ")[:16]


def _pill(text: str, object_name: str, height: int = 34) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName(object_name)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setFixedHeight(height)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    return btn


class ReportPopover(QWidget):
    """Small popup under the "!" button: the report is already saved; the one-line note is optional."""

    def __init__(self, main_win: "MainWindow", report_id: int, note: str, already_reported: bool,
                 on_removed: Callable[[], None]) -> None:
        super().__init__(main_win, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ReportPopover")
        self.main_win = main_win
        self.report_id = report_id
        self._on_removed = on_removed
        self.setStyleSheet(main_win.styleSheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = SmoothGlassFrame(radius=16, variant="popup")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        outer.addWidget(card)

        self.title = QLabel("Already reported" if already_reported else "Reported. Thank you!")
        self.title.setObjectName("CardTitle")
        lay.addWidget(self.title)

        self.note_edit = QLineEdit(note)
        self.note_edit.setObjectName("DrawerInput")
        self.note_edit.setPlaceholderText("What looks wrong? (optional)")
        self.note_edit.setMaxLength(300)
        self.note_edit.setFixedHeight(36)
        self.note_edit.returnPressed.connect(self._save_note)
        lay.addWidget(self.note_edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        remove_btn = _pill("Remove report", "GlassPillButton")
        remove_btn.clicked.connect(self._remove)
        self.save_btn = _pill("Save note", "PrimaryPillButton")
        self.save_btn.clicked.connect(self._save_note)
        row.addWidget(remove_btn, 1)
        row.addWidget(self.save_btn, 1)
        lay.addLayout(row)
        self.setFixedWidth(340)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        # Right-aligned under the button, but kept inside the window.
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        self.move(pos)
        self.show()
        self.note_edit.setFocus()

    def _save_note(self) -> None:
        self.main_win.reports.set_note(self.report_id, self.note_edit.text())
        self.main_win.reports_changed()
        self.close()

    def _remove(self) -> None:
        self.main_win.reports.delete(self.report_id)
        self.main_win.reports_changed()
        self._on_removed()
        self.close()


class ReportRowWidget(QWidget):
    def __init__(self, report: dict, on_delete: Callable[[int], None]) -> None:
        super().__init__()
        self.report_id = report["id"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.term_lbl = QLabel(report["term"])
        self.term_lbl.setObjectName("HistoryTermLabel")
        self.term_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        col.addWidget(self.term_lbl)
        meta = f"{format_when(report['created_at'])}  ·  {KIND_LABELS.get(report['kind'], report['kind'])}"
        self.meta_lbl = QLabel(meta)
        self.meta_lbl.setObjectName("MutedText")
        self.meta_lbl.setToolTip(meta + (f"\n{report['note']}" if report["note"] else ""))
        col.addWidget(self.meta_lbl)
        if report["note"]:
            self.note_lbl = QLabel(QFontMetrics(self.font()).elidedText(report["note"], Qt.TextElideMode.ElideRight, 280))
            self.note_lbl.setObjectName("MutedText")
            col.addWidget(self.note_lbl)
        lay.addLayout(col, 1)
        self.del_btn = QPushButton("✕")
        self.del_btn.setObjectName("RowDeleteButton")
        self.del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.del_btn.setFixedSize(28, 28)
        self.del_btn.setToolTip("Delete this report")
        self.del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.del_btn.clicked.connect(lambda: on_delete(self.report_id))
        lay.addWidget(self.del_btn, 0, Qt.AlignmentFlag.AlignVCenter)


class ReportsPage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        self.intro = QLabel("Results you flagged with the ! button. Click one to open it; export them to hand "
                            "to Claude with your next update.")
        self.intro.setObjectName("MutedText")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)

        list_box = SmoothGlassFrame(radius=16, variant="inner")
        lb_lay = QVBoxLayout(list_box)
        lb_lay.setContentsMargins(8, 8, 8, 8)
        self.report_list = QListWidget()
        self.report_list.setObjectName("PopupList")
        self.report_list.setFrameShape(QFrame.Shape.NoFrame)
        self.report_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.report_list.itemClicked.connect(self._on_item_clicked)
        lb_lay.addWidget(self.report_list)
        self.empty_lbl = QLabel("No reports yet.\nPress ! on a result to report a problem with it.")
        self.empty_lbl.setObjectName("MutedText")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        lb_lay.addWidget(self.empty_lbl)
        lay.addWidget(list_box, stretch=1)

        self.confirm = ConfirmBar()
        lay.addWidget(self.confirm)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("MutedText")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        self.export_btn = _pill("Export for Claude", "PrimaryPillButton", 38)
        self.export_btn.setToolTip("Save all reports as one .zip to attach to a chat with Claude")
        self.export_btn.clicked.connect(self.export_reports)
        lay.addWidget(self.export_btn)

    def on_show(self) -> None:
        self.status_lbl.setText("")
        self.confirm.hide()
        self.populate()

    def populate(self) -> None:
        self.report_list.clear()
        reports = self.main_win.reports.list()
        for rep in reports:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rep["id"])
            item.setSizeHint(QSize(0, 78 if rep["note"] else 58))
            row = ReportRowWidget(rep, self._ask_delete)
            self.report_list.addItem(item)
            self.report_list.setItemWidget(item, row)
        self.report_list.setVisible(bool(reports))
        self.empty_lbl.setVisible(not reports)
        self.export_btn.setEnabled(bool(reports))

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.main_win.open_report(item.data(Qt.ItemDataRole.UserRole))

    def _ask_delete(self, report_id: int) -> None:
        rep = next((r for r in self.main_win.reports.list() if r["id"] == report_id), None)
        if rep is None:
            return
        self.confirm.ask(f"Delete the report of “{rep['term']}”?", lambda: self._delete(report_id))

    def _delete(self, report_id: int) -> None:
        self.main_win.reports.delete(report_id)
        self.main_win.reports_changed()
        self.populate()

    def _choose_export_path(self) -> str:
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export reports for Claude", os.path.join(docs, report_export.default_filename()), "Zip archive (*.zip)")
        return path

    def export_reports(self) -> None:
        reports = self.main_win.reports.all_full()
        if not reports:
            return
        path = self._choose_export_path()
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            count = report_export.write_bundle(path, reports, self.main_win.settings.data)
        except OSError as exc:
            log.exception("Could not export reports to %s", path)
            self.status_lbl.setText(f"Could not save the file: {exc.strerror or exc}")
            return
        self.status_lbl.setText(f"Exported {count} report{'s' if count != 1 else ''} to {path}")
