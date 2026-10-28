# Ocean entry point (run.sh / run.bat / the PyInstaller builds all start here). The user interface
# lives in the ui/ package: main_window.py wires the pages together, results_page.py, review_page.py,
# drawer.py (history, settings, Teach Ocean), widgets.py and theme.py hold the rest.
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lexical_engine
import desktop_lookup
from PyQt6.QtCore import Qt, QBuffer, QIODevice, QTimer
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import VectorIcons, register_custom_fonts
from ui.workers import exit_app


def generate_ico_file(ico_path: str = "ocean.ico") -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    register_custom_fonts()
    icon = VectorIcons.ocean_app_icon()
    pixmap = icon.pixmap(256, 256)
    ba = QBuffer()
    ba.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(ba, "PNG")
    png_bytes = bytes(ba.data())
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    with open(ico_path, "wb") as f:
        f.write(header + entry + png_bytes)


def main() -> None:
    if "--make-icon" in sys.argv:
        generate_ico_file("ocean.ico")
        return
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    message = {"action": "show", "token": os.environ.get("XDG_ACTIVATION_TOKEN", "")}
    if "--lookup" in sys.argv:
        words = sys.argv[sys.argv.index("--lookup") + 1:]
        message.update(action="lookup", text=" ".join(words) if words else desktop_lookup.read_selected_text())
    if desktop_lookup.send_to_running_instance(message):
        return  # the running Ocean takes over; never open a second window on the same data

    app.setStyle("Fusion")
    register_custom_fonts()
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, lexical_engine.preload_offline_translator)  # once the event loop is running

    instance_server = desktop_lookup.InstanceServer(app)
    instance_server.message_received.connect(window.handle_external_lookup)
    instance_server.listen()
    if sys.platform == "win32":
        hotkey = desktop_lookup.WindowsHotkey(lambda: window.handle_external_lookup({"action": "lookup"}))
        if hotkey.register():
            app.installNativeEventFilter(hotkey)
    if message["action"] == "lookup":
        window.handle_external_lookup(message)
    exit_app(app.exec())


if __name__ == "__main__":
    main()
