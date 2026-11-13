# -*- coding: utf-8 -*-

import os
import matplotlib
import matplotlib.pyplot as plt
import numpy
import pandas
from pandas.api.types import is_numeric_dtype
from PyQt6 import QtCore, QtGui, QtWidgets

from shared import Icons, ColorButton, handle_exception, wait_cursor, show_file_dialog, show_popup

matplotlib.use("Agg")

PLOT_WIDTH = 400  # px — display width of the diagram on the plot page


class RoseChartWindow(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, df: pandas.DataFrame):
        super().__init__(parent)
        self.setWindowTitle("Diagrama de Roseta")
        self.setWindowIcon(QtGui.QIcon(Icons.GRAPH))
        self.setModal(False)

        self.df = df.drop(columns="geometry") if "geometry" in df.columns else df
        self.fig = None

        # ------------------------------------------------------------------ #
        # Stacked widget — page 0: config  |  page 1: plot
        # ------------------------------------------------------------------ #
        self.stack = QtWidgets.QStackedWidget(self)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

        # ================================================================== #
        # PAGE 0 — Configuration
        # ================================================================== #
        config_page = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_page)
        config_layout.setSpacing(5)
        config_layout.setContentsMargins(5, 5, 5, 5)
        config_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.stack.addWidget(config_page)

        # --- Data ---
        data_grp = QtWidgets.QGroupBox("Dados")
        data_form = QtWidgets.QFormLayout(data_grp)
        data_form.setSpacing(5)

        self.col_cbx = QtWidgets.QComboBox()
        self.col_cbx.addItems(self.azimuth_columns())
        data_form.addRow("Coluna de azimutes:", self.col_cbx)

        self.sectors_spn = QtWidgets.QSpinBox()
        self.sectors_spn.setRange(4, 360)
        self.sectors_spn.setSingleStep(2)
        self.sectors_spn.setValue(16)
        data_form.addRow("Número de setores:", self.sectors_spn)

        self.mirror_chk = QtWidgets.QCheckBox("Espelhar dados direcionais")
        data_form.addRow(self.mirror_chk)

        config_layout.addWidget(data_grp)

        # --- Appearance ---
        appear_grp = QtWidgets.QGroupBox("Aparência")
        appear_form = QtWidgets.QFormLayout(appear_grp)
        appear_form.setSpacing(5)

        self.bar_color_btn = ColorButton("#000000")
        appear_form.addRow("Cor das barras:", self.bar_color_btn)

        self.edge_color_btn = ColorButton("#ffffff")
        appear_form.addRow("Cor da borda:", self.edge_color_btn)

        self.bar_width_spn = QtWidgets.QDoubleSpinBox()
        self.bar_width_spn.setRange(0.1, 1.0)
        self.bar_width_spn.setSingleStep(0.05)
        self.bar_width_spn.setValue(0.9)
        self.bar_width_spn.setDecimals(2)
        appear_form.addRow("Largura das barras:", self.bar_width_spn)

        config_layout.addWidget(appear_grp)

        # --- Labels ---
        labels_grp = QtWidgets.QGroupBox("Rótulos")
        labels_form = QtWidgets.QFormLayout(labels_grp)
        labels_form.setSpacing(5)

        self.rgrid_chk = QtWidgets.QCheckBox("Mostrar escala radial")
        labels_form.addRow(self.rgrid_chk)

        self.n_label_chk = QtWidgets.QCheckBox("Mostrar n (total de dados)")
        self.n_label_chk.setChecked(True)
        labels_form.addRow(self.n_label_chk)

        config_layout.addWidget(labels_grp)

        # --- OK button ---
        self.ok_btn = QtWidgets.QPushButton("Plotar")
        config_layout.addWidget(self.ok_btn)

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
        self.save_btn.setIconSize(QtCore.QSize(26, 26))
        self.save_btn.setFixedSize(32, 32)
        self.save_btn.setToolTip("Salvar figura")

        self.back_btn = QtWidgets.QToolButton()
        self.back_btn.setIcon(QtGui.QIcon(Icons.BACK))
        self.back_btn.setIconSize(QtCore.QSize(26, 26))
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.setToolTip("Voltar às configurações")

        # Flat button that displays the plot image
        self.image_btn = QtWidgets.QPushButton()
        self.image_btn.setFlat(True)
        self.image_btn.setStyleSheet(
            "QPushButton { background-color: transparent; border: none; }"
            "QPushButton:hover { background-color: transparent; }"
            "QPushButton:pressed { background-color: transparent; }"
        )

        plot_layout.addWidget(self.save_btn,  0, 0)
        plot_layout.addWidget(self.back_btn,  0, 1)
        plot_layout.addWidget(self.image_btn, 1, 0, 10, 10)

        # ------------------------------------------------------------------ #
        # Signal wiring
        # ------------------------------------------------------------------ #
        self.ok_btn.clicked.connect(self.on_ok)
        self.save_btn.clicked.connect(self.on_save)
        self.back_btn.clicked.connect(self.on_back)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def azimuth_columns(self) -> list[str]:
        return [
            col for col in self.df.columns
            if is_numeric_dtype(self.df[col])
            and not self.df[col].dropna().empty
            and self.df[col].dropna().between(0, 360).all()
        ]

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def on_ok(self) -> None:
        try:
            wait_cursor(True)
            col = self.col_cbx.currentText()
            if not col:
                raise ValueError("Nenhuma coluna de azimutes disponível.")

            self.plot_diagram(
                azimuths=self.df[col].values.copy(),
                sectors=self.sectors_spn.value(),
                mirror=self.mirror_chk.isChecked(),
                bar_color=self.bar_color_btn.color,
                edge_color=self.edge_color_btn.color,
                bar_width_frac=self.bar_width_spn.value(),
                show_rgrid=self.rgrid_chk.isChecked(),
                show_n=self.n_label_chk.isChecked(),
            )
            self.load_image()
            self.stack.setCurrentIndex(1)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
            wait_cursor(False)
        except Exception as e:
            wait_cursor(False)
            handle_exception(e, "on_ok()", "Erro ao plotar o diagrama.", self)

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
            show_popup("Diagrama salvo com sucesso!", parent=self)
        except Exception as e:
            handle_exception(e, "on_save()", "Erro ao salvar a figura.", self)

    def on_back(self) -> None:
        try:
            self.stack.setCurrentIndex(0)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
        except Exception as e:
            handle_exception(e, "on_back()", parent=self)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def plot_diagram(self, azimuths, sectors, mirror, bar_color, edge_color,
                     bar_width_frac, show_rgrid, show_n) -> None:

        azimuths = numpy.mod(azimuths, 360)
        azimuths = azimuths[~numpy.isnan(azimuths)]

        if len(azimuths) == 0:
            raise ValueError("Nenhum dado válido para plotar.")

        sector_width = 360 / sectors
        shifted = numpy.mod(azimuths + sector_width / 2, 360)
        counts, _ = numpy.histogram(shifted, bins=numpy.linspace(0, 360, sectors + 1))

        if counts.sum() == 0:
            raise ValueError("Nenhum dado válido para plotar.")

        if mirror:
            if sectors % 2 != 0:
                raise ValueError("O número de setores deve ser par ao usar a opção de espelhar.")
            half = numpy.sum(numpy.split(counts, 2), axis=0)
            counts = numpy.concatenate([half, half])

        angles = numpy.deg2rad(numpy.arange(0, 360, sector_width))
        bar_width = max(numpy.deg2rad(sector_width * bar_width_frac), 0.01)

        if self.fig is not None:
            plt.close(self.fig)

        self.fig, ax = plt.subplots(figsize=(5, 5), dpi=300, subplot_kw={"projection": "polar"})

        ax.bar(angles, counts, width=bar_width, bottom=0.0,
               color=bar_color, edgecolor=edge_color, linewidth=0.5)

        max_count = counts.max()
        step = max(1, int(numpy.ceil(max_count / 5)))
        r_grid = numpy.arange(0, max_count + step, step)

        ax.set_facecolor("white")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_thetagrids(numpy.arange(0, 360, 90), labels=[])
        ax.set_rgrids(r_grid, labels=r_grid if show_rgrid else [])
        ax.grid(alpha=0.1, color="black")

        for lbl, angle in zip(["N", "L", "S", "O"], numpy.arange(0, 360, 90)):
            lx = 0.5 - 0.54 * numpy.cos(numpy.radians(angle + 90))
            ly = 0.5 + 0.54 * numpy.sin(numpy.radians(angle + 90))
            ax.text(lx, ly, lbl, transform=ax.transAxes, ha="center", va="center", fontsize=10)

        if show_n:
            ax.text(-0.05, -0.057, f"n = {len(azimuths)}", transform=ax.transAxes,
                    fontsize=8.5, va="bottom", ha="left")

        self.fig.patch.set_alpha(0)

        plots_dir = os.path.join(os.getcwd(), "plots")
        os.makedirs(plots_dir, exist_ok=True)
        self.preview_path = os.path.join(plots_dir, "rose_chart.png")
        self.fig.savefig(self.preview_path, dpi=600, format="png", transparent=True)

    def load_image(self) -> None:
        pixmap = QtGui.QPixmap(self.preview_path)
        height = int(pixmap.height() * PLOT_WIDTH / pixmap.width())
        self.image_btn.setIcon(QtGui.QIcon(self.preview_path))
        self.image_btn.setIconSize(QtCore.QSize(PLOT_WIDTH, height))
        self.image_btn.setFixedSize(PLOT_WIDTH, height)
