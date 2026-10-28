import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read().splitlines()


def _lock_pins():
    pins = {}
    for line in _read("requirements.lock"):
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line)
        if m:
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def _direct_names():
    names = []
    for name in ("requirements.txt", "requirements-build.txt"):
        for line in _read(name):
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line.strip())
            if m:
                names.append(_norm(m.group(1)))
    return names


class RequirementsLockTests(unittest.TestCase):
    def test_every_direct_dependency_is_pinned(self):
        pins = _lock_pins()
        for name in _direct_names():
            self.assertIn(name, pins, f"{name} is in requirements*.txt but not pinned in requirements.lock")

    def test_lock_covers_linux_and_windows(self):
        text = "\n".join(_read("requirements.lock"))
        self.assertRegex(text, r"(?m)^pywin32==\S+ ; sys_platform == 'win32'")
        self.assertRegex(text, r"(?m)^pyqt6==")

    def test_setup_scripts_install_from_the_lock(self):
        for script in ("setup_linux.sh", "setup_windows.ps1", "build_linux.sh"):
            with open(os.path.join(ROOT, script), encoding="utf-8") as f:
                self.assertIn("requirements.lock", f.read(), f"{script} must install from requirements.lock")


if __name__ == "__main__":
    unittest.main()
