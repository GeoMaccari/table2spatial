# -*- coding: utf-8 -*-
"""
shared.py — Small utility functions used across modules.
"""

import os
from icecream import ic
from PyQt6 import QtCore, QtGui, QtWidgets

ICONS_FOLDER = "appdata/icons/"
class Icons:
    ADD = ICONS_FOLDER + "add.png"
    ALERT = ICONS_FOLDER + "alert.png"
    BACK = ICONS_FOLDER + "back.png"
    BOOLEAN = ICONS_FOLDER + "boolean.png"
    CIRCLE = ICONS_FOLDER + "circle.png"
    CONNECT = ICONS_FOLDER + "connect.png"
    DATETIME = ICONS_FOLDER + "datetime.png"
    DELETE = ICONS_FOLDER + "delete.png"
    ERROR = ICONS_FOLDER + "error.png"
    FILTER = ICONS_FOLDER + "filter.png"
    FLOAT = ICONS_FOLDER + "float.png"
    GEOMETRY = ICONS_FOLDER + "geometry.png"
    GLOBE = ICONS_FOLDER + "globe.png"
    GRAPH = ICONS_FOLDER + "graph.png"
    IMPORT = ICONS_FOLDER + "import.png"
    INFO = ICONS_FOLDER + "info.png"
    INTEGER = ICONS_FOLDER + "integer.png"
    EXPORT = ICONS_FOLDER + "export.png"
    LIST = ICONS_FOLDER + "list.png"
    MERGE = ICONS_FOLDER + "merge.png"
    NOT_OK = ICONS_FOLDER + "not_ok.png"
    OK = ICONS_FOLDER + "ok.png"
    PHOTO = ICONS_FOLDER + "photo.png"
    PROFILE = ICONS_FOLDER + "profile.png"
    REMOVE = ICONS_FOLDER + "remove.png"
    RENAME = ICONS_FOLDER + "rename.png"
    REPROJECT = ICONS_FOLDER + "reproject.png"
    SAVE = ICONS_FOLDER + "save.png"
    SELECT = ICONS_FOLDER + "select.png"
    STRING = ICONS_FOLDER + "string.png"
    TOPO_PROFILE = ICONS_FOLDER + "topo_profile.png"
    UNKNOWN = ICONS_FOLDER + "unknown.png"


class ColorButton(QtWidgets.QPushButton):
    """Button that shows the selected color and opens a QColorDialog on click."""

    color_changed = QtCore.pyqtSignal()

    def __init__(self, initial_color: str = "#000000"):
        super().__init__()
        self.color = initial_color
        self.setFixedSize(64, 24)
        self.update_style()
        self.clicked.connect(self.pick_color)

    def update_style(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self.color}; border: 1px solid #888; border-radius: 3px; }}"
        )

    def pick_color(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), None, "Escolher cor")
        if chosen.isValid():
            self.color = chosen.name()
            self.update_style()
            self.color_changed.emit()


def show_popup(message: str, msg_type: str = "notification", details: str | None = None,
               parent: QtWidgets.QWidget | None = None) -> None:
    icon_path = "appdata/icons/error.png" if msg_type == "error" else "appdata/icons/info.png"
    title = "Erro" if msg_type == "error" else "Notificação"
    box = QtWidgets.QMessageBox(parent)
    box.setWindowIcon(QtGui.QIcon(icon_path))
    box.setWindowTitle(title)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    box.exec()


def show_file_dialog(caption: str, extension_filter: str | None = None, mode: str = "open",
                     parent: QtWidgets.QWidget | None = None) -> tuple[str, str]:
    if mode == "open":
        path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, caption=caption, filter=extension_filter or "")
    elif mode == "save":
        path, _ = QtWidgets.QFileDialog.getSaveFileName(parent, caption=caption, filter=extension_filter or "")
    else:
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, caption=caption)
        return path, ""
    _, ext = os.path.splitext(path)
    return path, ext.lower()


def show_input_dialog(message: str, title: str = "Inserir", default: str = "",
                      parent: QtWidgets.QWidget | None = None) -> tuple[str, bool]:

    return QtWidgets.QInputDialog.getText(parent, title, message, text=default)


def show_selection_dialog(message: str, items: list, selected: int = 0, allow_edit: bool = False,
                          title: str = "Selecionar", parent: QtWidgets.QWidget | None = None) -> tuple[str, bool]:

    return QtWidgets.QInputDialog.getItem(parent, title, message, items, selected, allow_edit)


def show_question_dialog(
    message: str,
    parent: QtWidgets.QWidget | None = None,
) -> int:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowIcon(QtGui.QIcon("appdata/icons/unknown.png"))
    box.setWindowTitle("Confirmação")
    box.setText(message)
    box.setStandardButtons(
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
    )
    box.button(QtWidgets.QMessageBox.StandardButton.Yes).setText("Sim")
    box.button(QtWidgets.QMessageBox.StandardButton.No).setText("Não")
    return box.exec()


def wait_cursor(activate: bool = True) -> None:
    if activate:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
    else:
        QtWidgets.QApplication.restoreOverrideCursor()


def handle_exception(
    error: Exception,
    context: str,
    message: str = "Ocorreu um erro.",
    parent: QtWidgets.QWidget | None = None,
) -> None:
    from icecream import ic
    wait_cursor(False)
    ic(context, error)
    show_popup(message, "error", f"Descrição: {error}\n\nContexto: {context}", parent)


def center_on_parent(window: QtWidgets.QWidget, parent: QtWidgets.QWidget) -> None:
    center = parent.geometry().center()
    geo = window.geometry()
    geo.moveCenter(center)
    window.move(geo.topLeft())


def center_on_screen(window: QtWidgets.QWidget) -> None:
    screen = window.screen()
    geometry = screen.availableGeometry()

    x = geometry.x() + (geometry.width() - window.width()) // 2
    y = geometry.y() + (geometry.height() - window.height()) // 2

    window.move(x, y)


def search_coordinates_column_by_name(axis: str, crs_type: str, columns) -> str:
    """Heuristic: find the most likely column for a given axis by name."""
    geographic = crs_type in ("Geographic 2D CRS", "Geographic 3D CRS")
    hints = {
        "x": ["longitude", "lon", "long", "x", "leste", "east", "e"] if geographic else ["x", "leste", "east", "e", "easting", "utme", "utm_e"],
        "y": ["latitude", "lat", "y", "norte", "north", "n"] if geographic else ["y", "norte", "north", "n", "northing", "utmn", "utm_n"],
        "z": ["altitude", "alt", "z", "cota", "elev", "elevation"],
    }

    for col in columns:
        if col.lower() in hints.get(axis, []):
            return col
    return columns[0] if len(columns) else ""
