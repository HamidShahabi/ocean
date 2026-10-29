# Ocean entry point (run.sh / run.bat / the PyInstaller builds all start here): it only starts the app.
# The user interface lives in the ui/ package (main_window.py wires the pages together), the lookup
# engine in lexical_engine.py and dictionary_sources/.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lexical_engine
import desktop_lookup
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import register_custom_fonts, write_app_icon_ico
from ui.workers import exit_app


def main() -> None:
    lexical_engine.log_uncaught_exceptions()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    if "--make-icon" in sys.argv:  # build step (setup_windows.ps1 / update_ocean.ps1)
        register_custom_fonts()
        write_app_icon_ico("ocean.ico")
        return
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
