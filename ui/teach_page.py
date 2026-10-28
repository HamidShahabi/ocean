"""Drawer page for teaching Ocean new words, idioms and Persian meanings (its permanent offline memory)."""
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget

import lexical_engine
from ui.widgets import ModernGlassDropdown, SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class TeachPage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win

        t_lay = QVBoxLayout(self)
        t_lay.setContentsMargins(0, 4, 0, 0)
        t_lay.setSpacing(10)

        t_desc = QLabel("Add or customize any word, idiom, or Persian meaning in Ocean's permanent offline memory:")
        t_desc.setWordWrap(True)
        t_desc.setObjectName("MutedText")
        t_lay.addWidget(t_desc)

        self.teach_term = QLineEdit()
        self.teach_term.setObjectName("DrawerInput")
        self.teach_term.setPlaceholderText("English Word or Idiom (e.g. get lousy)")
        self.teach_term.setFixedHeight(36)
        t_lay.addWidget(self.teach_term)

        self.teach_fa = QLineEdit()
        self.teach_fa.setObjectName("DrawerInput")
        self.teach_fa.setPlaceholderText("Persian Meaning (\u0645\u0639\u0627\u062f\u0644 \u062f\u0642\u06cc\u0642 \u0641\u0627\u0631\u0633\u06cc)")
        self.teach_fa.setFixedHeight(36)
        t_lay.addWidget(self.teach_fa)

        self.teach_pos = ModernGlassDropdown(["Noun", "Verb", "Adjective", "Adverb", "Idiom / Expression"], "Idiom / Expression", self)
        t_lay.addWidget(self.teach_pos)

        self.teach_def = QLineEdit()
        self.teach_def.setObjectName("DrawerInput")
        self.teach_def.setPlaceholderText("English Definition (Optional)")
        self.teach_def.setFixedHeight(36)
        t_lay.addWidget(self.teach_def)

        self.teach_ex_en = QLineEdit()
        self.teach_ex_en.setObjectName("DrawerInput")
        self.teach_ex_en.setPlaceholderText("Example Sentence in English (Optional)")
        self.teach_ex_en.setFixedHeight(36)
        t_lay.addWidget(self.teach_ex_en)

        self.teach_ex_fa = QLineEdit()
        self.teach_ex_fa.setObjectName("DrawerInput")
        self.teach_ex_fa.setPlaceholderText("Example Persian Translation (Optional)")
        self.teach_ex_fa.setFixedHeight(36)
        t_lay.addWidget(self.teach_ex_fa)

        save_teach_btn = QPushButton("Save to Ocean Memory")
        save_teach_btn.setObjectName("PrimaryPillButton")
        save_teach_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_teach_btn.setFixedHeight(38)
        save_teach_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_teach_btn.clicked.connect(self._save_taught_entry)
        t_lay.addWidget(save_teach_btn)

        mem_box = SmoothGlassFrame(radius=16, variant="inner")
        mb_lay = QVBoxLayout(mem_box)
        mb_lay.setContentsMargins(8, 8, 8, 8)
        self.teach_list = QListWidget()
        self.teach_list.setObjectName("PopupList")
        self.teach_list.setFrameShape(QFrame.Shape.NoFrame)
        self.teach_list.setWordWrap(True)
        self.teach_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.teach_list.itemClicked.connect(self._on_taught_item_clicked)
        mb_lay.addWidget(self.teach_list)
        t_lay.addWidget(mem_box, stretch=1)

        del_teach_btn = QPushButton("Remove Selected from Memory")
        del_teach_btn.setObjectName("GlassPillButton")
        del_teach_btn.setFixedHeight(34)
        del_teach_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_teach_btn.clicked.connect(self._delete_taught_entry)
        t_lay.addWidget(del_teach_btn)


    def on_show(self) -> None:
        if self.main_win.current_term and not self.teach_term.text().strip():
            self.teach_term.setText(self.main_win.current_term)
        self.populate_taught_list()

    def populate_taught_list(self) -> None:
        self.teach_list.clear()
        for k, val in lexical_engine.USER_MEMORY.get("entries", {}).items():
            hw = val.get("headword", k)
            fa = val.get("fa", "")
            it = QListWidgetItem(f"{hw}\n\u2192 {fa}")
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.teach_list.addItem(it)
        for k, fa_val in lexical_engine.USER_MEMORY.get("overrides", {}).items():
            if k not in lexical_engine.USER_MEMORY.get("entries", {}):
                it = QListWidgetItem(f"{k}\n\u2192 {fa_val}")
                it.setData(Qt.ItemDataRole.UserRole, k)
                self.teach_list.addItem(it)

    def _save_taught_entry(self) -> None:
        term = self.teach_term.text().strip()
        fa = self.teach_fa.text().strip()
        if not term or not fa:
            QMessageBox.information(self, "Teach Ocean", "Please enter both the English term and its Persian meaning.")
            return
        lexical_engine.record_taught_entry(
            term=term,
            fa_equiv=fa,
            pos=self.teach_pos.currentText(),
            eng_def=self.teach_def.text().strip(),
            ex_en=self.teach_ex_en.text().strip(),
            ex_fa=self.teach_ex_fa.text().strip(),
        )
        self.populate_taught_list()
        self.teach_term.clear()
        self.teach_fa.clear()
        self.teach_def.clear()
        self.teach_ex_en.clear()
        self.teach_ex_fa.clear()
        self.main_win._analyze_input(force_refresh=True, override_term=term)

    def _on_taught_item_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.teach_term.setText(key)
            ent = lexical_engine.USER_MEMORY.get("entries", {}).get(key, {})
            ov = lexical_engine.USER_MEMORY.get("overrides", {}).get(key, "")
            self.teach_fa.setText(ent.get("fa", ov))
            self.teach_def.setText(ent.get("def", ""))
            self.teach_ex_en.setText(ent.get("example_en", ""))
            self.teach_ex_fa.setText(ent.get("example_fa", ""))

    def _delete_taught_entry(self) -> None:
        item = self.teach_list.currentItem()
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                lexical_engine.delete_taught_entry(key)
                if key in lexical_engine.USER_MEMORY.get("overrides", {}):
                    del lexical_engine.USER_MEMORY["overrides"][key]
                    lexical_engine.save_user_memory()
                self.populate_taught_list()
