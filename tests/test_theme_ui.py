"""Themes, fonts, marquee and transitions (offscreen Qt)."""
import os
import sys
import tempfile
import unittest

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QModelIndex  # noqa: E402
from PyQt6.QtGui import QColor, QFontDatabase, QFontMetrics  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem  # noqa: E402

from ui import marquee, motion, theme  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.marquee import MarqueeItemDelegate, MarqueeLabel  # noqa: E402
from ui.widgets import ModernGlassDropdown  # noqa: E402

# What the pronunciations use: letters, stress and length marks, tie bar and syllabic diacritics.
IPA_CHARS = "ɹɚɝɾʊʌəɪˈˌːɛæɔɑɒθðʃʒŋɡɫʔʰɜɐɨɵʉøœɯɤɘɞʏɶɦʍɥɻɭɳɲʎʟɬɮʋɸβçʝɣχʁħʕʀʙʜɕʑɺɧɴɱ˞‿̩̯̥̃͡"


def luminance(hex_color: str) -> float:
    c = QColor(hex_color)
    chan = [v / 255 for v in (c.red(), c.green(), c.blue())]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in chan]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()

    def test_old_settings_values_still_select_the_themes(self):
        qss_dark = theme.apply_theme({"theme": "Dark Mode", "base_font_px": 14})
        self.assertEqual(theme.THEME_COLORS["bg_main"], theme.DARK_COLORS["bg_main"])
        self.assertIn(theme.DARK_COLORS["bg_card"], qss_dark)
        self.assertEqual(self.app.palette().color(self.app.palette().ColorRole.Window).name().upper(), theme.DARK_COLORS["bg_main"])
        qss_light = theme.apply_theme({"theme": "Light Mode", "base_font_px": 14})
        self.assertEqual(theme.THEME_COLORS["bg_main"], theme.LIGHT_COLORS["bg_main"])
        self.assertNotEqual(qss_dark, qss_light)
        self.assertEqual(self.app.palette().color(self.app.palette().ColorRole.Window).name().upper(), theme.LIGHT_COLORS["bg_main"])
        # Back again, and a missing or unknown value means dark.
        theme.apply_theme({"theme": "Dark Mode"})
        self.assertEqual(theme.THEME_COLORS["bg_main"], theme.DARK_COLORS["bg_main"])
        theme.apply_theme({"theme": "Light Mode"})
        theme.apply_theme({})
        self.assertEqual(theme.THEME_COLORS["bg_main"], theme.DARK_COLORS["bg_main"])
        theme.apply_theme({"theme": "Sepia"})
        self.assertEqual(theme.THEME_COLORS["bg_main"], theme.DARK_COLORS["bg_main"])

    def test_palettes_have_the_same_roles_and_readable_contrast(self):
        self.assertEqual(set(theme.DARK_COLORS), set(theme.LIGHT_COLORS))
        for name, c in (("dark", theme.DARK_COLORS), ("light", theme.LIGHT_COLORS)):
            for surface in ("bg_main", "bg_card", "bg_inner", "bg_popup"):
                self.assertGreaterEqual(contrast(c["text_main"], c[surface]), 7.0, f"{name} text on {surface}")
                self.assertGreaterEqual(contrast(c["text_muted"], c[surface]), 4.5, f"{name} muted text on {surface}")
            self.assertGreaterEqual(contrast(c["accent"], c["bg_card"]), 4.5, f"{name} accent on card")
            self.assertGreaterEqual(contrast(c["accent_fg"], c["accent_bg"]), 4.5, f"{name} hover text")
            self.assertGreaterEqual(contrast(c["on_primary"], c["accent"]), 4.5, f"{name} primary button")

    def test_bundled_fonts_are_registered_with_the_expected_families(self):
        self.assertEqual(theme.EN_FONT_FAMILY, "Google Sans Flex")
        self.assertEqual(theme.FA_FONT_FAMILY, "Vazirmatn")
        self.assertEqual(theme.IPA_FONT_FAMILY, "Charis SIL")
        families = QFontDatabase.families()
        for family in (theme.EN_FONT_FAMILY, theme.FA_FONT_FAMILY, theme.IPA_FONT_FAMILY):
            self.assertIn(family, families)
        styles = QFontDatabase.styles(theme.EN_FONT_FAMILY)
        for style in ("Regular", "Medium", "SemiBold", "Bold"):
            self.assertTrue(any(style in s for s in styles), (style, styles))

    def test_ipa_font_covers_every_pronunciation_symbol(self):
        fm = QFontMetrics(theme.make_ipa_font(16))
        self.assertEqual(theme.make_ipa_font(16).family(), "Charis SIL")
        missing = [c for c in IPA_CHARS if not fm.inFontUcs4(ord(c))]
        self.assertEqual(missing, [])

    def test_the_ipa_font_is_only_used_for_ipa_labels(self):
        qss = theme.apply_theme({"theme": "Dark Mode", "base_font_px": 14})
        ipa_rule = qss[qss.index(f"QLabel#{theme.IPA_OBJECT_NAME}"):].split("}")[0]
        self.assertIn('"Charis SIL"', ipa_rule)
        self.assertEqual(qss.count("Charis SIL"), 1)
        self.assertEqual(self.app.font().family(), theme.EN_FONT_FAMILY)
        self.assertEqual(theme.make_polished_font(theme.EN_FONT_FAMILY, 14).families()[0], "Google Sans Flex")

    def test_the_polished_font_keeps_platform_hinting_and_kerning(self):
        f = theme.make_polished_font(theme.EN_FONT_FAMILY, 14)
        self.assertTrue(f.kerning())
        self.assertEqual(f.hintingPreference(), f.HintingPreference.PreferDefaultHinting)
        self.assertEqual(f.letterSpacing(), 0.0)


class MarqueeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()
        theme.apply_theme({"theme": "Dark Mode", "base_font_px": 14})

    def test_offset_timeline_waits_scrolls_holds_and_returns(self):
        dist = 120
        self.assertEqual(marquee.marquee_offset(0, dist), 0.0)
        self.assertEqual(marquee.marquee_offset(marquee.START_DELAY_MS - 1, dist), 0.0)
        offsets = [marquee.marquee_offset(t, dist) for t in range(0, 6000, 50)]
        self.assertEqual(max(offsets), dist)
        self.assertTrue(all(0.0 <= o <= dist for o in offsets))
        self.assertEqual(marquee.marquee_offset(1e9 + 3, 0), 0.0)
        # It returns to the start and the cycle repeats.
        first_peak = offsets.index(dist)
        self.assertTrue(any(o == 0.0 for o in offsets[first_peak:]))
        self.assertEqual(marquee.overflow_distance(100, 120), 0)
        self.assertGreater(marquee.overflow_distance(200, 120), 80)

    def test_label_only_scrolls_when_the_text_overflows(self):
        label = MarqueeLabel("Short")
        label.resize(220, 30)
        label.show()
        self.assertFalse(label.is_overflowing())
        label.anim.start(label.distance())  # nothing to scroll: stays idle
        self.assertFalse(label.anim.active)

        label.setText("A rather long option that does not fit in this little box at all")
        self.assertTrue(label.is_overflowing())
        label.anim.start(label.distance())
        self.assertTrue(label.anim.active)
        QTest.qWait(1000)
        self.assertGreater(label.anim.offset, 0.0)
        self.assertLessEqual(label.anim.offset, label.distance())
        label.anim.stop()
        QTest.qWait(350)
        self.assertFalse(label.anim.active)
        self.assertEqual(label.anim.offset, 0.0)

        label.resize(900, 30)  # now it fits: no longer overflowing
        self.assertFalse(label.is_overflowing())
        label.close()

    def test_wrapping_labels_never_scroll(self):
        label = MarqueeLabel("word " * 40)
        label.setWordWrap(True)
        label.resize(200, 200)
        self.assertFalse(label.is_overflowing())

    def test_dropdown_value_scrolls_on_hover_only_when_it_is_wider_than_the_capsule(self):
        long_opt = "am_michael (Male - US) with a very long description"
        drop = ModernGlassDropdown(["Short", long_opt], "Short")
        drop.setFixedWidth(180)
        drop.show()
        self.assertFalse(drop.value_label.is_overflowing())
        drop.setCurrentText(long_opt)
        self.assertEqual(drop.currentText(), long_opt)
        self.assertTrue(drop.value_label.is_overflowing())
        drop.value_label.eventFilter(drop, _FakeEvent(marquee.QEvent.Type.Enter))
        self.assertTrue(drop.value_label.anim.active)
        drop.value_label.eventFilter(drop, _FakeEvent(marquee.QEvent.Type.Leave))
        drop.value_label.anim.stop()
        drop.close()

    def test_popup_rows_scroll_only_when_wider_than_the_row(self):
        lst = QListWidget()
        lst.resize(200, 150)
        for text in ("Short", "An option text that is much wider than the list row"):
            lst.addItem(QListWidgetItem(text))
        delegate = marquee.apply_marquee(lst)
        self.assertIsInstance(lst.itemDelegate(), MarqueeItemDelegate)
        lst.show()
        delegate._on_entered(lst.model().index(0, 0))
        self.assertFalse(delegate.anim.active)
        delegate._on_entered(lst.model().index(1, 0))
        self.assertTrue(delegate.anim.active)
        delegate._on_entered(QModelIndex())  # pointer moved off the rows
        delegate.anim.stop()
        lst.close()


class _FakeEvent:
    def __init__(self, kind):
        self._kind = kind

    def type(self):
        return self._kind


class MotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()

    def setUp(self):
        self.win = MainWindow()
        self.win.resize(1300, 760)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        motion.set_motion_override(None)
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def test_motion_is_off_on_the_headless_platform_and_the_state_is_still_correct(self):
        self.assertFalse(motion.motion_enabled())
        self.win._toggle_right_drawer()
        self.assertTrue(self.win.right_drawer.isVisible())
        self.assertFalse(self.win.right_drawer._workspace().running)
        self.win._toggle_right_drawer()
        self.assertFalse(self.win.right_drawer.isVisible())

    def test_drawer_toggle_cross_fades_and_ends_in_the_right_state(self):
        motion.set_motion_override(True)
        drawer = self.win.right_drawer
        fade = drawer._workspace()
        self.win._toggle_right_drawer()
        self.assertTrue(drawer.isVisible())  # the change itself is immediate
        self.assertTrue(fade.running)        # the overlay covers it with the old snapshot
        QTest.qWait(60)
        self.assertTrue(fade.running)
        self.assertFalse(fade.overlay.new.isNull())
        self.assertIsNotNone(fade.overlay.slide)  # the drawer slides in
        QTest.qWait(motion.DRAWER_FADE_MS + 200)
        self.assertFalse(fade.running)
        self.win._toggle_right_drawer()
        self.assertFalse(drawer.isVisible())
        QTest.qWait(60)
        self.assertFalse(fade.overlay.opening)  # sliding out
        QTest.qWait(motion.DRAWER_FADE_MS + 200)
        self.assertFalse(fade.running)

    def test_a_second_toggle_during_the_animation_wins(self):
        motion.set_motion_override(True)
        drawer = self.win.right_drawer
        self.win._toggle_right_drawer()
        self.win._toggle_right_drawer()
        self.assertFalse(drawer.isVisible())
        QTest.qWait(motion.DRAWER_FADE_MS + 250)
        self.assertFalse(drawer._workspace().running)
        self.assertFalse(drawer.isVisible())

    def test_page_switch_fades_between_home_and_results(self):
        motion.set_motion_override(True)
        stack = self.win.stack
        self.win._switch_to_results_view()
        self.assertIs(stack.currentWidget(), self.win.results_page)
        self.assertTrue(stack._fade.running)
        QTest.qWait(motion.PAGE_FADE_MS + 250)
        self.assertFalse(stack._fade.running)
        self.win.go_home()
        self.assertIs(stack.currentWidget(), self.win.home_page)
        QTest.qWait(motion.PAGE_FADE_MS + 250)
        self.assertFalse(stack._fade.running)

    def test_reduced_motion_environment_variable(self):
        os.environ["OCEAN_REDUCE_MOTION"] = "1"
        try:
            self.assertFalse(motion.motion_enabled())
        finally:
            del os.environ["OCEAN_REDUCE_MOTION"]


if __name__ == "__main__":
    unittest.main()
