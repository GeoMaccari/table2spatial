# -*- coding: utf-8 -*-
"""
window_stereogram.py — Stereonet window.

Config page mirrors the original layout exactly. Each OK click adds one
measurement layer to the existing figure. No limit on the number of layers.
"""

import os
from dataclasses import dataclass, field

import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import mplstereonet
import numpy
import pandas
from matplotlib.lines import Line2D
from PyQt6 import QtCore, QtGui, QtWidgets

from shared import Icons, ColorButton, handle_exception, wait_cursor, show_file_dialog, show_popup

matplotlib.use("Agg")

PLOT_WIDTH = 400

MEASUREMENT_TYPES = {
    "Planos (dip direction/dip)":         ("Dip direction", "Dip",    None),
    "Planos (strike/dip)":                ("Strike",        "Dip",    None),
    "Linhas (plunge/trend)":              ("Trend",         "Plunge", None),
    "Linhas em planos (strike/dip/rake)": ("Strike",        "Dip",    "Rake"),
}

MARKERS = {
    "Círculo":   {"marker": "o", "size": 5},
    "Triângulo": {"marker": "^", "size": 5},
    "Quadrado":  {"marker": "s", "size": 4},
    "Losango":   {"marker": "D", "size": 4},
}

COLORMAPS = {
    "Exhalitic":   mcolors.LinearSegmentedColormap.from_list("Exhalitic",   ["white", "#404040"], N=10),
    "Granitic":    mcolors.LinearSegmentedColormap.from_list("Granitic",    ["white", "#7c202b"], N=10),
    "Gneissic":    mcolors.LinearSegmentedColormap.from_list("Gneissic",    ["white", "#584468"], N=10),
    "Mafic":       mcolors.LinearSegmentedColormap.from_list("Mafic",       ["white", "#304531"], N=10),
    "Plutonic":    mcolors.LinearSegmentedColormap.from_list("Plutonic",    ["white", "#843d66"], N=10),
    "Sedimentary": mcolors.LinearSegmentedColormap.from_list("Sedimentary", ["white", "#5b483f"], N=10),
    "Rhyolitic":   mcolors.LinearSegmentedColormap.from_list("Rhyolitic",   ["white", "#ae612d"], N=10),
    "Cenozoic":    mcolors.LinearSegmentedColormap.from_list("Cenozoic",    ["white", "#b19c29"], N=10),
    "Carbonatic":  mcolors.LinearSegmentedColormap.from_list("Carbonatic",  ["white", "#2b769e"], N=10),
    "Pelitic":     mcolors.LinearSegmentedColormap.from_list("Pelitic",     ["white", "#5e5956"], N=10),
}


@dataclass
class MeasurementLayer:
    label: str
    msr_type: str
    az_col: str
    dip_col: str
    rake_col: str | None
    color: str
    marker: str
    plot_poles: bool
    plot_density: bool
    colormap: str
    filters: dict = field(default_factory=dict)

    def plot_type(self) -> str:
        if self.msr_type.startswith("Planos"):
            return "poles" if self.plot_poles else "planes"
        if "rake" in self.msr_type:
            return "rakes"
        return "lines"

    def az_type(self) -> str:
        return "dip direction" if MEASUREMENT_TYPES[self.msr_type][0] == "Dip direction" else "strike"


class StereogramWindow(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, df: pandas.DataFrame):
        super().__init__(parent)
        self.setWindowTitle("Estereograma")
        self.setWindowIcon(QtGui.QIcon(Icons.GRAPH))
        self.setModal(False)

        self.df = df.drop(columns="geometry") if "geometry" in df.columns else df
        self.fig = None
        self.ax  = None
        self.layers: list[MeasurementLayer] = []
        self.active_filter: dict = {}
        self.preview_path: str = ""
        self.legend_handles: list = []
        self.legend_labels: list = []

        self.stack = QtWidgets.QStackedWidget(self)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

        # ================================================================== #
        # PAGE 0 — Configuration (flat grid, same as original)
        # ================================================================== #
        config_page = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(config_page)
        g.setSpacing(5)
        g.setContentsMargins(10, 10, 10, 10)
        g.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.stack.addWidget(config_page)

        row = 0

        g.addWidget(QtWidgets.QLabel("Título do gráfico:"), row, 0, 1, 8); row += 1
        self.title_edt = QtWidgets.QLineEdit()
        self.title_edt.setToolTip("Insira um título para o gráfico ou deixe em branco para omiti-lo.")
        g.addWidget(self.title_edt, row, 0, 1, 8); row += 1

        g.addWidget(QtWidgets.QLabel("Tipo de medida:"), row, 0, 1, 8); row += 1
        self.msr_type_cbx = QtWidgets.QComboBox()
        self.msr_type_cbx.addItems(MEASUREMENT_TYPES.keys())
        self.msr_type_cbx.setMinimumWidth(300)
        self.filter_btn = QtWidgets.QToolButton()
        self.filter_btn.setIcon(QtGui.QIcon("appdata/icons/filter.png"))
        self.filter_btn.setFixedSize(24, 24)
        self.filter_btn.setToolTip("Definir filtros")
        g.addWidget(self.msr_type_cbx, row, 0, 1, 7)
        g.addWidget(self.filter_btn,   row, 7, 1, 1); row += 1

        self.az_lbl   = QtWidgets.QLabel("Dip directions:")
        self.dip_lbl  = QtWidgets.QLabel("Dips:")
        self.rake_lbl = QtWidgets.QLabel("Rakes:")
        self.az_cbx   = QtWidgets.QComboBox()
        self.dip_cbx  = QtWidgets.QComboBox()
        self.rake_cbx = QtWidgets.QComboBox()
        self.rake_lbl.setEnabled(False)
        self.rake_cbx.setEnabled(False)
        g.addWidget(self.az_lbl,   row, 0, 1, 2); g.addWidget(self.az_cbx,   row, 2, 1, 6); row += 1
        g.addWidget(self.dip_lbl,  row, 0, 1, 2); g.addWidget(self.dip_cbx,  row, 2, 1, 6); row += 1
        g.addWidget(self.rake_lbl, row, 0, 1, 2); g.addWidget(self.rake_cbx, row, 2, 1, 6); row += 1

        self.poles_chk = QtWidgets.QCheckBox("Plotar planos como polos")
        self.poles_chk.setChecked(True)
        g.addWidget(self.poles_chk, row, 0, 1, 8); row += 1

        self.density_chk = QtWidgets.QCheckBox("Plotar contornos de densidade")
        g.addWidget(self.density_chk, row, 0, 1, 8); row += 1

        self.show_legend_chk = QtWidgets.QCheckBox("Mostrar legenda")
        g.addWidget(self.show_legend_chk, row, 0, 1, 8); row += 1

        self.label_lbl = QtWidgets.QLabel("Rótulo das medidas:")
        self.label_edt = QtWidgets.QLineEdit("Planos")
        self.label_lbl.setEnabled(False)
        self.label_edt.setEnabled(False)
        self.label_edt.setToolTip("Insira um rótulo para a medida (Ex: Xistosidade).")
        g.addWidget(self.label_lbl, row, 0, 1, 4); g.addWidget(self.label_edt, row, 4, 1, 4); row += 1

        g.addWidget(QtWidgets.QLabel("Cor da simbologia:"), row, 0, 1, 4)
        self.color_btn = ColorButton("#000000")
        g.addWidget(self.color_btn, row, 4, 1, 4); row += 1

        self.marker_lbl = QtWidgets.QLabel("Marcador de linhas/polos:")
        g.addWidget(self.marker_lbl, row, 0, 1, 4)
        self.marker_cbx = QtWidgets.QComboBox()
        self.marker_cbx.addItems(MARKERS.keys())
        g.addWidget(self.marker_cbx, row, 4, 1, 4); row += 1

        self.colormap_lbl = QtWidgets.QLabel("Cores de densidade:")
        self.colormap_cbx = QtWidgets.QComboBox()
        self.colormap_cbx.addItems(COLORMAPS.keys())
        self.colormap_lbl.setEnabled(False)
        self.colormap_cbx.setEnabled(False)
        g.addWidget(self.colormap_lbl, row, 0, 1, 4); g.addWidget(self.colormap_cbx, row, 4, 1, 4); row += 1

        self.ok_btn = QtWidgets.QPushButton("Plotar")
        g.addWidget(self.ok_btn, row, 0, 1, 8)

        # ================================================================== #
        # PAGE 1 — Plot
        # ================================================================== #
        plot_page = QtWidgets.QWidget()
        plot_layout = QtWidgets.QGridLayout(plot_page)
        plot_layout.setSpacing(5)
        plot_layout.setContentsMargins(5, 5, 5, 5)
        self.stack.addWidget(plot_page)

        self.save_btn = QtWidgets.QToolButton()
        self.save_btn.setIcon(QtGui.QIcon(Icons.SAVE))
        self.save_btn.setIconSize(QtCore.QSize(28, 28))
        self.save_btn.setFixedSize(32, 32)
        self.save_btn.setToolTip("Salvar figura")

        self.add_btn = QtWidgets.QToolButton()
        self.add_btn.setIcon(QtGui.QIcon(Icons.ADD))
        self.add_btn.setIconSize(QtCore.QSize(28, 28))
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setToolTip("Adicionar mais medidas")

        self.image_btn = QtWidgets.QPushButton()
        self.image_btn.setFlat(True)
        self.image_btn.setStyleSheet(
            "QPushButton { background-color: transparent; border: none; }"
            "QPushButton:hover { background-color: transparent; }"
            "QPushButton:pressed { background-color: transparent; }"
        )

        plot_layout.addWidget(self.save_btn,  0, 0)
        plot_layout.addWidget(self.add_btn,   0, 1)
        plot_layout.addWidget(self.image_btn, 1, 0, 10, 10)

        # ------------------------------------------------------------------ #
        # Signals
        # ------------------------------------------------------------------ #
        self.msr_type_cbx.currentTextChanged.connect(self.on_msr_type_changed)
        self.poles_chk.checkStateChanged.connect(self.on_poles_toggled)
        self.density_chk.checkStateChanged.connect(self.on_density_toggled)
        self.show_legend_chk.checkStateChanged.connect(self.on_legend_toggled)
        self.filter_btn.clicked.connect(self.on_filter)
        self.ok_btn.clicked.connect(self.on_ok)
        self.save_btn.clicked.connect(self.on_save)
        self.add_btn.clicked.connect(self.on_add_more)

        self.on_msr_type_changed(self.msr_type_cbx.currentText())

    # ------------------------------------------------------------------ #
    # Column helpers
    # ------------------------------------------------------------------ #

    def angle_columns(self, angle_type: str) -> list[str]:
        ranges = {"azimuth": (0, 360), "dip": (0, 90), "rake": (0, 180)}
        lo, hi = ranges[angle_type]
        return [
            col for col in self.df.columns
            if pandas.api.types.is_numeric_dtype(self.df[col])
            and not self.df[col].dropna().empty
            and self.df[col].dropna().between(lo, hi).all()
        ]

    def populate_col_combos(self) -> None:
        for cbx, angle_type in (
            (self.az_cbx,   "azimuth"),
            (self.dip_cbx,  "dip"),
            (self.rake_cbx, "rake"),
        ):
            cols = self.angle_columns(angle_type)
            cbx.blockSignals(True)
            cbx.clear()
            cbx.addItems(cols)
            cbx.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Config page slots
    # ------------------------------------------------------------------ #

    def on_msr_type_changed(self, msr_type: str) -> None:
        comp = MEASUREMENT_TYPES[msr_type]
        self.az_lbl.setText(f"{comp[0]}s:")
        self.dip_lbl.setText(f"{comp[1]}s:")
        has_rake = comp[2] is not None
        self.rake_lbl.setEnabled(has_rake)
        self.rake_cbx.setEnabled(has_rake)
        is_plane = msr_type.startswith("Planos")
        self.poles_chk.setEnabled(is_plane)
        self.poles_chk.setChecked(is_plane)
        if self.label_edt.text() in ("Planos", "Linhas"):
            self.label_edt.setText("Planos" if is_plane else "Linhas")
        self.populate_col_combos()
        self.on_poles_toggled()

    def on_poles_toggled(self) -> None:
        # Marker only makes sense when plotting poles or lines, not great circles
        is_plane = self.msr_type_cbx.currentText().startswith("Planos")
        show_marker = (not is_plane) or self.poles_chk.isChecked()
        self.marker_lbl.setEnabled(show_marker)
        self.marker_cbx.setEnabled(show_marker)

    def on_density_toggled(self) -> None:
        enabled = self.density_chk.isChecked()
        self.colormap_lbl.setEnabled(enabled)
        self.colormap_cbx.setEnabled(enabled)

    def on_legend_toggled(self) -> None:
        enabled = self.show_legend_chk.isChecked()
        self.label_lbl.setEnabled(enabled)
        self.label_edt.setEnabled(enabled)

    def on_filter(self) -> None:
        dlg = FilterDialog(self, self.df, self.active_filter)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.active_filter = dlg.get_selections()
            n = len(self.active_filter)
            self.filter_btn.setToolTip(
                f"Filtros ({n} ativo{'s' if n != 1 else ''})" if n else "Definir filtros"
            )

    def on_ok(self) -> None:
        try:
            wait_cursor(True)
            msr_type = self.msr_type_cbx.currentText()
            has_rake = MEASUREMENT_TYPES[msr_type][2] is not None
            layer = MeasurementLayer(
                label=self.label_edt.text() or "---",
                msr_type=msr_type,
                az_col=self.az_cbx.currentText(),
                dip_col=self.dip_cbx.currentText(),
                rake_col=self.rake_cbx.currentText() if has_rake else None,
                color=self.color_btn.color,
                marker=self.marker_cbx.currentText(),
                plot_poles=self.poles_chk.isChecked(),
                plot_density=self.density_chk.isChecked(),
                colormap=self.colormap_cbx.currentText(),
                filters=self.active_filter.copy(),
            )
            self.layers.append(layer)
            try:
                self.render()
            except Exception:
                self.layers.pop()
                raise
            self.load_image()
            self.stack.setCurrentIndex(1)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
            wait_cursor(False)
        except Exception as e:
            wait_cursor(False)
            handle_exception(e, "on_ok()", "Erro ao plotar o estereograma.", self)

    # ------------------------------------------------------------------ #
    # Plot page slots
    # ------------------------------------------------------------------ #

    def on_add_more(self) -> None:
        self.density_chk.setChecked(False)
        self.density_chk.setEnabled(len(self.layers) == 0)
        self.active_filter = {}
        self.filter_btn.setToolTip("Definir filtros")
        self.stack.setCurrentIndex(0)
        self.setFixedSize(self.stack.currentWidget().sizeHint())

    def on_save(self) -> None:
        try:
            path, ext = show_file_dialog(
                "Salvar figura",
                "Formatos suportados (*.png *.jpg *.svg);;PNG (*.png);;JPG (*.jpg);;SVG (*.svg)",
                mode="save", parent=self,
            )
            if not path:
                return
            if not ext:
                ext = ".png"
                path += ext
            self.fig.savefig(path, dpi=600, format=ext.lstrip("."), transparent=True)
            show_popup("Estereograma salvo com sucesso!", parent=self)
        except Exception as e:
            handle_exception(e, "on_save()", "Erro ao salvar a figura.", self)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def apply_filters(self, layer: MeasurementLayer) -> pandas.DataFrame:
        df = self.df.copy()
        for col, values in layer.filters.items():
            if pandas.api.types.is_datetime64_any_dtype(df[col]):
                values = pandas.to_datetime(values)
            df = df[df[col].isin(values)]
        return df

    @staticmethod
    def clean_pairs(az, dip, rake=None):
        drop = []
        for i in range(len(az)):
            components = [az[i], dip[i]] + ([rake[i]] if rake is not None else [])
            all_nan = all(numpy.isnan(v) for v in components)
            any_nan = any(numpy.isnan(v) for v in components)
            if all_nan:
                drop.append(i)
            elif any_nan:
                raise ValueError(f"Medida incompleta na linha {i + 1}: um componente está faltando.")
        az  = numpy.delete(az,  drop)
        dip = numpy.delete(dip, drop)
        if rake is not None:
            rake = numpy.delete(rake, drop)
        if len(az) == 0:
            raise ValueError("Nenhuma medida válida após remover dados faltantes.")
        return az, dip, rake

    def render(self) -> None:
        # Build a fresh figure each time, replaying all layers accumulated so far
        if self.fig is not None:
            plt.close(self.fig)

        self.fig, self.ax = mplstereonet.subplots(figsize=(5, 5), projection="stereonet")
        self.ax.set_facecolor("white")
        self.ax.set_azimuth_ticks([])
        self.ax.grid(color="black", alpha=0.1)

        for lbl, angle in zip(["N", "E", "S", "W"], numpy.arange(0, 360, 90)):
            lx = 0.5 - 0.54 * numpy.cos(numpy.radians(angle + 90))
            ly = 0.5 + 0.54 * numpy.sin(numpy.radians(angle + 90))
            self.ax.text(lx, ly, lbl, transform=self.ax.transAxes, ha="center", va="center")

        title = self.title_edt.text().strip()
        if title:
            self.ax.set_title(title, y=1.05, fontsize=14, fontweight="bold")

        legend_handles = []
        legend_labels  = []

        for layer in self.layers:
            df = self.apply_filters(layer)
            if df[layer.az_col].empty or df[layer.dip_col].empty:
                raise ValueError(f"A camada '{layer.label}' ficou sem dados após os filtros.")

            az   = df[layer.az_col].to_numpy(dtype=float)
            dip  = df[layer.dip_col].to_numpy(dtype=float)
            rake = df[layer.rake_col].to_numpy(dtype=float) if layer.rake_col else None
            az, dip, rake = self.clean_pairs(az, dip, rake)

            if layer.az_type() == "dip direction":
                az = az - 90

            plot_type = layer.plot_type()
            color = layer.color
            mk, sz = MARKERS[layer.marker]["marker"], MARKERS[layer.marker]["size"]

            if layer.plot_density:
                cmap = COLORMAPS[layer.colormap]
                if plot_type in ("planes", "poles"):
                    self.ax.density_contourf(az, dip, measurement="poles", cmap=cmap)
                elif plot_type == "rakes":
                    self.ax.density_contourf(az, dip, rake, measurement="rakes", cmap=cmap)
                elif plot_type == "lines":
                    self.ax.density_contourf(dip, az, measurement="lines", cmap=cmap)

            if plot_type == "planes":
                self.ax.plane(az, dip, color=color)
            elif plot_type == "poles":
                self.ax.pole(az, dip, color=color, marker=mk, markersize=sz)
            elif plot_type == "lines":
                self.ax.line(dip, az, color=color, marker=mk, markersize=sz)
            elif plot_type == "rakes":
                self.ax.plane(az, dip, color=color)
                self.ax.rake(az, dip, rake, color=color, marker=mk, markersize=sz)

            if plot_type == "planes":
                handle = Line2D([0], [0], color=color, linestyle="-", linewidth=1.5)
            else:
                handle = Line2D([0], [0], color=color, marker=mk, markersize=sz, linestyle="None")
            legend_handles.append(handle)
            legend_labels.append(f"{layer.label or '---'} (n = {len(az)})")

        show_legend = self.show_legend_chk.isChecked()
        if show_legend and legend_handles:
            h = len(legend_handles)
            self.ax.legend(
                legend_handles, legend_labels,
                loc="lower center", fontsize=9, facecolor="white",
                bbox_to_anchor=(0.5, -0.144 - (h - 1) * 0.053),
            )
            self.fig.subplots_adjust(left=0.05, bottom=0.17, right=0.95, top=0.9)

        self.fig.patch.set_alpha(0)

        plots_dir = os.path.join(os.getcwd(), "plots")
        os.makedirs(plots_dir, exist_ok=True)
        self.preview_path = os.path.join(plots_dir, "stereogram.png")
        self.fig.savefig(self.preview_path, dpi=600, format="png", transparent=True)

    def load_image(self) -> None:
        pixmap = QtGui.QPixmap(self.preview_path)
        height = int(pixmap.height() * PLOT_WIDTH / pixmap.width())
        self.image_btn.setIcon(QtGui.QIcon(self.preview_path))
        self.image_btn.setIconSize(QtCore.QSize(PLOT_WIDTH, height))
        self.image_btn.setFixedSize(PLOT_WIDTH, height)


# ---------------------------------------------------------------------------
# Filter dialog
# ---------------------------------------------------------------------------

class FilterDialog(QtWidgets.QDialog):
    def __init__(self, parent, df: pandas.DataFrame, previous: dict):
        super().__init__(parent)
        self.df = df
        self.selections: dict = previous.copy()

        self.setWindowTitle("Filtros")
        self.setWindowIcon(QtGui.QIcon(Icons.FILTER))
        self.setMinimumSize(300, 380)

        layout = QtWidgets.QVBoxLayout(self)

        self.col_cbx = QtWidgets.QComboBox()
        self.col_cbx.addItems(df.columns)
        layout.addWidget(self.col_cbx)

        hdr = QtWidgets.QHBoxLayout()
        hdr.addWidget(QtWidgets.QLabel("Selecione valores:"))
        hdr.addStretch()
        self.toggle_btn = QtWidgets.QToolButton()
        self.toggle_btn.setIcon(QtGui.QIcon(Icons.SELECT))
        self.toggle_btn.setStyleSheet("QToolButton { background-color: transparent; }")
        self.toggle_btn.setToolTip("Selecionar/desselecionar todos")
        self.toggle_btn.setFixedSize(20, 20)
        hdr.addWidget(self.toggle_btn)
        layout.addLayout(hdr)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.chk_container = QtWidgets.QWidget()
        self.chk_layout = QtWidgets.QVBoxLayout(self.chk_container)
        self.chk_layout.addStretch()
        self.scroll.setWidget(self.chk_container)
        layout.addWidget(self.scroll)

        active_hdr = QtWidgets.QHBoxLayout()
        active_hdr.addWidget(QtWidgets.QLabel("Filtros ativos:"))
        active_hdr.addStretch()
        self.remove_btn = QtWidgets.QToolButton()
        self.remove_btn.setIcon(QtGui.QIcon(Icons.REMOVE))
        self.remove_btn.setStyleSheet("QToolButton { background-color: transparent; }")
        self.remove_btn.setToolTip("Remover filtro selecionado")
        self.remove_btn.setFixedSize(20, 20)
        active_hdr.addWidget(self.remove_btn)
        layout.addLayout(active_hdr)

        self.active_list = QtWidgets.QListWidget()
        self.active_list.setFixedHeight(65)
        layout.addWidget(self.active_list)

        btn_row = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btn_row.accepted.connect(self.accept)
        btn_row.rejected.connect(self.reject)
        layout.addWidget(btn_row)

        self.col_cbx.currentTextChanged.connect(self.refresh_checkboxes)
        self.toggle_btn.clicked.connect(self.toggle_all)
        self.remove_btn.clicked.connect(self.remove_rule)

        self.refresh_checkboxes(self.col_cbx.currentText())
        self.refresh_active_list()

    def refresh_checkboxes(self, col: str) -> None:
        while self.chk_layout.count() > 1:
            item = self.chk_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        all_values = self.df[col].unique()
        selected = set(map(str, self.selections.get(col, all_values)))
        for val in all_values:
            chk = QtWidgets.QCheckBox(str(val))
            chk.setChecked(str(val) in selected)
            chk.stateChanged.connect(self.update_selection)
            self.chk_layout.insertWidget(self.chk_layout.count() - 1, chk)
        self.update_selection()

    def update_selection(self) -> None:
        col = self.col_cbx.currentText()
        dtype = self.df[col].dtype
        selected = []
        for i in range(self.chk_layout.count() - 1):
            chk = self.chk_layout.itemAt(i).widget()
            if not chk.isChecked():
                continue
            text = chk.text()
            try:
                if pandas.api.types.is_numeric_dtype(dtype):
                    value = float(text) if "." in text else int(text)
                elif pandas.api.types.is_bool_dtype(dtype):
                    value = text.lower() == "true"
                else:
                    value = text
            except ValueError:
                value = text
            selected.append(value)

        if set(selected) == set(self.df[col].unique()):
            self.selections.pop(col, None)
        else:
            self.selections[col] = selected
        self.refresh_active_list()

    def refresh_active_list(self) -> None:
        self.active_list.clear()
        for col, vals in self.selections.items():
            self.active_list.addItem(f"{col}: {vals or 'nenhum'}")

    def remove_rule(self) -> None:
        item = self.active_list.currentItem()
        if not item:
            return
        col = item.text().split(":")[0]
        self.selections.pop(col, None)
        if col == self.col_cbx.currentText():
            # Block signals so unchecking all boxes doesn't re-trigger update_selection
            for i in range(self.chk_layout.count() - 1):
                self.chk_layout.itemAt(i).widget().blockSignals(True)
            self.refresh_checkboxes(col)
            for i in range(self.chk_layout.count() - 1):
                self.chk_layout.itemAt(i).widget().blockSignals(False)
        else:
            self.refresh_active_list()

    def toggle_all(self) -> None:
        checks = [self.chk_layout.itemAt(i).widget() for i in range(self.chk_layout.count() - 1)]
        state = not all(c.isChecked() for c in checks)
        for c in checks:
            c.setChecked(state)

    def get_selections(self) -> dict:
        return self.selections
