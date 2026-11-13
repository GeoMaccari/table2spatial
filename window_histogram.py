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


class HistogramWindow(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, df: pandas.DataFrame, col: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Histograma")
        self.setWindowIcon(QtGui.QIcon(Icons.GRAPH))
        self.setModal(False)

        self.df  = df.drop(columns="geometry") if "geometry" in df.columns else df
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
        config_page   = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_page)
        config_layout.setSpacing(5)
        config_layout.setContentsMargins(5, 5, 5, 5)
        config_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.stack.addWidget(config_page)

        # --- Data ---
        data_grp  = QtWidgets.QGroupBox("Dados")
        data_form = QtWidgets.QFormLayout(data_grp)
        data_form.setSpacing(5)

        self.col_cbx = QtWidgets.QComboBox()
        self.col_cbx.addItems(self.numeric_columns())
        if col and col in self.numeric_columns():
            self.col_cbx.setCurrentText(col)
        data_form.addRow("Coluna:", self.col_cbx)

        self.bins_spn = QtWidgets.QSpinBox()
        self.bins_spn.setRange(2, 500)
        self.bins_spn.setValue(20)
        data_form.addRow("Número de classes:", self.bins_spn)

        config_layout.addWidget(data_grp)

        # --- Appearance ---
        appear_grp  = QtWidgets.QGroupBox("Aparência")
        appear_form = QtWidgets.QFormLayout(appear_grp)
        appear_form.setSpacing(5)

        self.bar_color_btn = ColorButton("#2b7bba")
        appear_form.addRow("Cor das barras:", self.bar_color_btn)

        self.edge_color_btn = ColorButton("#ffffff")
        appear_form.addRow("Cor da borda:", self.edge_color_btn)

        config_layout.addWidget(appear_grp)

        # --- Labels ---
        labels_grp  = QtWidgets.QGroupBox("Sobreposições")
        labels_form = QtWidgets.QFormLayout(labels_grp)
        labels_form.setSpacing(5)

        self.n_label_chk = QtWidgets.QCheckBox("Mostrar n (total de dados)")
        self.n_label_chk.setChecked(True)
        labels_form.addRow(self.n_label_chk)

        self.mean_chk = QtWidgets.QCheckBox("Mostrar linha da média")
        labels_form.addRow(self.mean_chk)

        self.median_chk = QtWidgets.QCheckBox("Mostrar linha da mediana")
        labels_form.addRow(self.median_chk)

        config_layout.addWidget(labels_grp)

        # --- Plot button ---
        self.ok_btn = QtWidgets.QPushButton("Plotar")
        config_layout.addWidget(self.ok_btn)

        # ================================================================== #
        # PAGE 1 — Plot
        # ================================================================== #
        plot_page   = QtWidgets.QWidget()
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

    def numeric_columns(self) -> list[str]:
        return [
            col for col in self.df.columns
            if is_numeric_dtype(self.df[col]) and not self.df[col].dropna().empty
        ]

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def on_ok(self) -> None:
        try:
            wait_cursor(True)
            col = self.col_cbx.currentText()
            if not col:
                raise ValueError("Nenhuma coluna numérica disponível.")

            self.plot_histogram(
                values=self.df[col].dropna().values.astype(float),
                col_name=col,
                bins=self.bins_spn.value(),
                bar_color=self.bar_color_btn.color,
                edge_color=self.edge_color_btn.color,
                show_n=self.n_label_chk.isChecked(),
                show_mean=self.mean_chk.isChecked(),
                show_median=self.median_chk.isChecked(),
            )
            self.load_image()
            self.stack.setCurrentIndex(1)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
            wait_cursor(False)
        except Exception as e:
            wait_cursor(False)
            handle_exception(e, "on_ok()", "Erro ao plotar o histograma.", self)

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
            show_popup("Histograma salvo com sucesso!", parent=self)
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

    def plot_histogram(self, values, col_name, bins,
                       bar_color, edge_color,
                       show_n, show_mean, show_median) -> None:

        if len(values) == 0:
            raise ValueError("Nenhum dado válido para plotar.")

        if self.fig is not None:
            plt.close(self.fig)

        self.fig, ax = plt.subplots(figsize=(6, 4), dpi=300)

        ax.hist(values, bins=bins,
                color=bar_color, edgecolor=edge_color, linewidth=0.5)

        if show_mean:
            mean = values.mean()
            ax.axvline(mean, color="black", linewidth=1.2, linestyle="-",
                       label=f"Média: {mean:.4g}")

        if show_median:
            median = numpy.median(values)
            ax.axvline(median, color="black", linewidth=1.2, linestyle="--",
                       label=f"Mediana: {median:.4g}")

        if show_mean or show_median:
            ax.legend(fontsize=8)

        ax.set_xlabel(col_name, fontsize=9)
        ax.set_ylabel("Frequência", fontsize=9)
        ax.tick_params(labelsize=8)
        ax.set_facecolor("white")
        self.fig.patch.set_facecolor("white")
        self.fig.patch.set_alpha(0)
        ax.spines[["top", "right"]].set_visible(False)

        if show_n:
            ax.text(0, -0.08, f"n = {len(values)}", transform=ax.transAxes,
                    fontsize=8, va="top", ha="left")

        self.fig.tight_layout()

        plots_dir = os.path.join(os.getcwd(), "plots")
        os.makedirs(plots_dir, exist_ok=True)
        self.preview_path = os.path.join(plots_dir, "histogram.png")
        self.fig.savefig(self.preview_path, dpi=300, format="png", transparent=True)

    def load_image(self) -> None:
        pixmap = QtGui.QPixmap(self.preview_path)
        height = int(pixmap.height() * PLOT_WIDTH / pixmap.width())
        self.image_btn.setIcon(QtGui.QIcon(self.preview_path))
        self.image_btn.setIconSize(QtCore.QSize(PLOT_WIDTH, height))
        self.image_btn.setFixedSize(PLOT_WIDTH, height)