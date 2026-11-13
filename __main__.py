# -*- coding: utf-8 -*-

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6 import sip  # needed for PyInstaller
from icecream import ic

from main_window import MainWindow

ic.configureOutput(prefix="LOG| ", includeContext=True)


class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setStyle("windows11")  # consistent look on all platforms
        self.window = MainWindow()
        self.window.show()


if __name__ == "__main__":
    app = App(sys.argv)
    sys.exit(app.exec())
