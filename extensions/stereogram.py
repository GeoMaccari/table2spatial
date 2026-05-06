# -*- coding: utf-8 -*-
""" @author: Gabriel Maccari """

import matplotlib
import numpy
import pandas
import mplstereonet
import os
import matplotlib.pyplot as plt
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.lines import Line2D
import matplotlib.colors as mcolors
from icecream import ic

from extensions.shared_functions import handle_exception, toggle_wait_cursor, select_figure_save_location

matplotlib.use("svg")

MEASUREMENT_TYPES = {
    'Planos (dip direction/dip)': ('Dip direction', 'Dip'),
    'Planos (strike/dip)': ('Strike', 'Dip'),
    'Linhas (plunge/trend)': ('Trend', 'Plunge'),
    'Linhas em planos (strike/dip/rake)': ('Strike', 'Dip', 'Rake')
}

MARKERS = {
    'Círculo': {"marker": 'o', "size": 5},
    'Triângulo': {"marker": '^', "size": 5},
    'Quadrado': {"marker": 's', "size": 4},
    'Losango': {"marker": 'D', "size": 4}
}

COLORMAPS = {
    "Exhalitic": mcolors.LinearSegmentedColormap.from_list("Exhalitic", ["white", "#404040"], N=10),
    "Granitic": mcolors.LinearSegmentedColormap.from_list("Granitic", ["white", "#7c202b"], N=10),
    "Gneissic": mcolors.LinearSegmentedColormap.from_list("Gneissic", ["white", "#584468"], N=10),
    "Mafic": mcolors.LinearSegmentedColormap.from_list("Mafic", ["white", "#304531"], N=10),
    "Plutonic": mcolors.LinearSegmentedColormap.from_list("Plutonic", ["white", "#843d66"], N=10),
    "Sedimentary": mcolors.LinearSegmentedColormap.from_list("Sedimentary", ["white", "#5b483f"], N=10),
    "Rhyolitic": mcolors.LinearSegmentedColormap.from_list("Rhyolitic", ["white", "#ae612d"], N=10),
    "Cenozoic": mcolors.LinearSegmentedColormap.from_list("Cenozoic", ["white", "#b19c29"], N=10),
    "Carbonatic": mcolors.LinearSegmentedColormap.from_list("Carbonatic", ["white", "#2b769e"], N=10),
    "Pelitic": mcolors.LinearSegmentedColormap.from_list("Pelitic", ["white", "#5e5956"], N=10),
}

PLOT_WIDTH = 350  # Largura da tela de exibição dos diagramas


class StereogramWindow(QtWidgets.QMainWindow):
    def __init__(self, parent: QtWidgets.QMainWindow, df: pandas.DataFrame):
        super(StereogramWindow, self).__init__(parent)
        self.parent = parent
        self.df = df.drop(columns="geometry") if "geometry" in df.columns else df
        self.filter = {}
        self.fig = None
        self.ax = None
        self.legend = {"markers": [], "labels": []}
        self.figure_loop = 1

        self.setWindowTitle('Estereograma')
        self.setWindowIcon(QtGui.QIcon('icons/graph.png'))

        self.frame_stack = QtWidgets.QStackedWidget(self)
        self.setCentralWidget(self.frame_stack)

        # CONFIG PAGE
        self.config_layout = QtWidgets.QGridLayout()
        self.config_layout.setSpacing(5)
        self.config_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignCenter)
        self.config_page = QtWidgets.QWidget(self.frame_stack)
        self.config_page.setLayout(self.config_layout)
        self.frame_stack.addWidget(self.config_page)

        msr_type = "Planos (dip direction/dip)"
        lb1, lb2, lb3 = MEASUREMENT_TYPES[msr_type][0]+"s:", MEASUREMENT_TYPES[msr_type][1]+"s:", "Rakes:"

        self.title_lbl = QtWidgets.QLabel("Título do gráfico:", self.config_page)
        self.title_edt = QtWidgets.QLineEdit(self.config_page)
        self.title_edt.setToolTip("Insira um título para o gráfico ou\ndeixe em branco para omiti-lo.")
        self.measurement_type_lbl = QtWidgets.QLabel("Tipo de medida:", self.config_page)
        self.measurement_type_cbx = QtWidgets.QComboBox(self.config_page)
        self.measurement_type_cbx.addItems(MEASUREMENT_TYPES)
        self.measurement_type_cbx.setMinimumWidth(300)
        self.filter_btn = QtWidgets.QToolButton(self.config_page)
        self.filter_btn.setIcon(QtGui.QIcon("icons/filter.png"))
        self.filter_btn.setMaximumSize(24, 24)
        self.azimuths_column_lbl = QtWidgets.QLabel(lb1, self.config_page)
        self.azimuths_column_cbx = QtWidgets.QComboBox(self.config_page)
        self.azimuths_column_cbx.addItems(self.filter_angle_columns("azimuth"))
        self.dips_column_lbl = QtWidgets.QLabel(lb2, self.config_page)
        self.dips_column_cbx = QtWidgets.QComboBox(self.config_page)
        self.dips_column_cbx.addItems(self.filter_angle_columns("dip"))
        self.rakes_column_lbl = QtWidgets.QLabel(lb3, self.config_page)
        self.rakes_column_lbl.setEnabled(False)
        self.rakes_column_cbx = QtWidgets.QComboBox(self.config_page)
        self.rakes_column_cbx.setEnabled(False)
        self.plot_poles_chk = QtWidgets.QCheckBox("Plotar planos como polos", self.config_page)
        self.density_contour_chk = QtWidgets.QCheckBox("Plotar contornos de densidade", self.config_page)
        self.show_legend_chk = QtWidgets.QCheckBox("Mostrar legenda", self.config_page)
        self.label_lbl = QtWidgets.QLabel("Rótulo das medidas:", self.config_page)
        self.label_lbl.setEnabled(False)
        self.label_edt = QtWidgets.QLineEdit("Planos", self.config_page)
        self.label_edt.setToolTip("Insira um rótulo para a\nmedida (Ex: Xistosidade).")
        self.label_edt.setEnabled(False)
        self.color_lbl = QtWidgets.QLabel("Cor da simbologia:", self.config_page)
        self.color_btn = QtWidgets.QPushButton("#000000", self.config_page)
        self.color_btn.setToolTip("Clique para definir a cor das linhas\ne/ou marcadores da medida no gráfico.")
        self.marker_lbl = QtWidgets.QLabel("Marcador de linhas/polos:", self.config_page)
        self.marker_cbx = QtWidgets.QComboBox(self.config_page)
        self.marker_cbx.addItems(MARKERS.keys())
        self.colormap_lbl = QtWidgets.QLabel("Cores de densidade:", self.config_page)
        self.colormap_lbl.setEnabled(False)
        self.colormap_cbx = QtWidgets.QComboBox(self.config_page)
        self.colormap_cbx.addItems(COLORMAPS.keys())
        self.colormap_cbx.setEnabled(False)
        self.ok_btn = QtWidgets.QPushButton("OK", self.config_page)

        self.config_layout.addWidget(self.title_lbl, 0, 0, 1, 8)
        self.config_layout.addWidget(self.title_edt, 1, 0, 1, 8)
        self.config_layout.addWidget(self.measurement_type_lbl, 2, 0, 1, 8)
        self.config_layout.addWidget(self.measurement_type_cbx, 3, 0, 1, 7)
        self.config_layout.addWidget(self.filter_btn, 3, 7, 1, 1)
        self.config_layout.addWidget(self.azimuths_column_lbl, 4, 0, 1, 2)
        self.config_layout.addWidget(self.azimuths_column_cbx, 4, 2, 1, 6)
        self.config_layout.addWidget(self.dips_column_lbl, 5, 0, 1, 2)
        self.config_layout.addWidget(self.dips_column_cbx, 5, 2, 1, 6)
        self.config_layout.addWidget(self.rakes_column_lbl, 6, 0, 1, 2)
        self.config_layout.addWidget(self.rakes_column_cbx, 6, 2, 1, 6)
        self.config_layout.addWidget(self.plot_poles_chk, 7, 0, 1, 8)
        self.config_layout.addWidget(self.density_contour_chk, 8, 0, 1, 8)
        self.config_layout.addWidget(self.show_legend_chk, 9, 0, 1, 8)
        self.config_layout.addWidget(self.label_lbl, 10, 0, 1, 4)
        self.config_layout.addWidget(self.label_edt, 10, 4, 1, 4)
        self.config_layout.addWidget(self.color_lbl, 11, 0, 1, 4)
        self.config_layout.addWidget(self.color_btn, 11, 4, 1, 4)
        self.config_layout.addWidget(self.marker_lbl, 12, 0, 1, 4)
        self.config_layout.addWidget(self.marker_cbx, 12, 4, 1, 4)
        self.config_layout.addWidget(self.colormap_lbl, 13, 0, 1, 4)
        self.config_layout.addWidget(self.colormap_cbx, 13, 4, 1, 4)
        self.config_layout.addWidget(self.ok_btn, 14, 0, 1, 8)

        self.measurement_type_cbx.currentTextChanged.connect(self.measurement_type_selected)
        self.filter_btn.clicked.connect(self.filter_button_clicked)
        self.show_legend_chk.checkStateChanged.connect(self.show_legend_checkbox_checked)
        self.color_btn.clicked.connect(self.color_button_clicked)
        self.density_contour_chk.checkStateChanged.connect(self.density_contour_checkbox_checked)
        self.ok_btn.clicked.connect(self.ok_button_clicked)

        self.initial_size = self.layout().sizeHint()

        # PLOT PAGE
        self.plot_layout = QtWidgets.QGridLayout()
        self.plot_layout.setSpacing(5)
        self.plot_page = QtWidgets.QWidget(self.frame_stack)
        self.plot_page.setLayout(self.plot_layout)
        self.frame_stack.addWidget(self.plot_page)

        self.save_btn = QtWidgets.QPushButton(self.plot_page)
        self.save_btn.setIcon(QtGui.QIcon("icons/save.png"))
        self.save_btn.setIconSize(QtCore.QSize(28, 28))
        self.save_btn.setFixedSize(30, 30)
        self.save_btn.setToolTip("Salvar estereograma")
        self.add_btn = QtWidgets.QPushButton(self.plot_page)
        self.add_btn.setIcon(QtGui.QIcon("icons/add.png"))
        self.add_btn.setIconSize(QtCore.QSize(28, 28))
        self.add_btn.setFixedSize(30, 30)
        self.add_btn.setToolTip("Adicionar novas medidas a esse estereograma (máx. 3)")
        self.image_btn = QtWidgets.QPushButton(self.plot_page)
        self.image_btn.setFlat(True)

        self.plot_layout.addWidget(self.save_btn, 0, 0, 1, 1)
        self.plot_layout.addWidget(self.add_btn, 0, 1, 1, 1)
        self.plot_layout.addWidget(self.image_btn, 1, 0, 10, 10)

        self.save_btn.clicked.connect(self.save_button_clicked)
        self.add_btn.clicked.connect(self.add_button_clicked)

    def filter_angle_columns(self, angle_type):
        try:
            ranges = {"azimuth": (0, 360), "dip": (0, 90), "rake": (0, 180)}
            min_angle, max_angle = ranges[angle_type]
            valid_columns = []
            for col in self.df:
                if pandas.api.types.is_numeric_dtype(self.df[col]):
                    if self.df[col].dropna().between(min_angle, max_angle).all():
                        valid_columns.append(col)
            return valid_columns
        except Exception as error:
            handle_exception(error, "stereogram - filter_angle_columns()", "Ops! Ocorreu um erro!", self)

    def measurement_type_selected(self):
        try:
            msr_type = self.measurement_type_cbx.currentText()
            comp = MEASUREMENT_TYPES[msr_type]
            self.azimuths_column_lbl.setText(f"{comp[0]}s:")
            self.dips_column_lbl.setText(f"{comp[1]}s:")
            has_rake = len(comp) == 3
            self.rakes_column_lbl.setEnabled(has_rake)
            self.rakes_column_cbx.setEnabled(has_rake)
            self.plot_poles_chk.setEnabled(msr_type.startswith("Planos"))
            self.plot_poles_chk.setChecked(msr_type.startswith("Planos"))
            if has_rake:
                self.rakes_column_cbx.clear()
                self.rakes_column_cbx.addItems(self.filter_angle_columns("rake"))
            if self.label_edt.text() in ("Planos", "Linhas"):
                self.label_edt.setText("Planos" if msr_type.startswith("Planos") else "Linhas")
        except Exception as error:
            handle_exception(error, "stereogram - measurement_type_selected()", "Ops! Ocorreu um erro!", self)

    def filter_button_clicked(self):
        try:
            dialog = FilterWindow(self, self.df, previous_selection=self.filter)
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                self.filter = dialog.get_all_selected_values()
        except Exception as error:
            handle_exception(error, "stereogram - filter_button_clicked()", "Ops! Ocorreu um erro!", self)

    def color_button_clicked(self):
        try:
            color_object = QtWidgets.QColorDialog.getColor()
            color = color_object.name()
            self.color_btn.setStyleSheet(f"color: {color}")
            self.color_btn.setText(color)
        except Exception as error:
            handle_exception(error, "stereogram - color_button_clicked()", "Ops! Ocorreu um erro!", self)

    def show_legend_checkbox_checked(self):
        show_legend = self.show_legend_chk.isChecked()
        self.label_lbl.setEnabled(show_legend)
        self.label_edt.setEnabled(show_legend)

    def density_contour_checkbox_checked(self):
        plot_density = self.density_contour_chk.isChecked()
        self.colormap_lbl.setEnabled(plot_density)
        self.colormap_cbx.setEnabled(plot_density)

    def ok_button_clicked(self):
        try:
            toggle_wait_cursor(True)

            msr_type = self.measurement_type_cbx.currentText()
            plot_poles = self.plot_poles_chk.isChecked()
            plot_density = self.density_contour_chk.isChecked()
            show_legend = self.show_legend_chk.isChecked()
            label = self.label_edt.text()
            color = self.color_btn.text()
            marker = self.marker_cbx.currentText()
            title = None if self.title_edt.text() == "" else self.title_edt.text()
            colormap = self.colormap_cbx.currentText()

            show_colorbar = False  # TODO adicionar widgets para selecionar se mostra ou não a escala de cores

            filtered_df = self.df.copy()
            for column, selected_values in self.filter.items():
                if pandas.api.types.is_datetime64_any_dtype(filtered_df[column]):
                    selected_values = pandas.to_datetime(selected_values)
                filtered_df = filtered_df[filtered_df[column].isin(selected_values)]

            azimuths_column_name = self.azimuths_column_cbx.currentText()
            dips_column_name = self.dips_column_cbx.currentText()

            if filtered_df[azimuths_column_name].empty or filtered_df[dips_column_name].empty:
                toggle_wait_cursor(False)
                QtWidgets.QMessageBox.warning(
                    self, "Dados Insuficientes",
                    "As colunas de azimute e/ou mergulho estão vazias após a aplicação dos filtros. "
                    "Ajuste seus filtros ou selecione outras colunas."
                )
                return

            azimuths = filtered_df[azimuths_column_name].to_numpy()
            dips = filtered_df[dips_column_name].to_numpy()
            rakes = None

            if msr_type.startswith("Planos"):
                plot_type = "poles" if plot_poles else "planes"
            elif msr_type.startswith("Linhas em planos"):
                plot_type = "rakes"
                rakes_column_name = self.rakes_column_cbx.currentText()
                if filtered_df[rakes_column_name].empty:
                    toggle_wait_cursor(False)
                    QtWidgets.QMessageBox.warning(
                        self, "Dados Insuficientes",
                        "A coluna de rake está vazia após a aplicação dos filtros. "
                        "Ajuste seus filtros ou selecione outra coluna.")
                    return
                rakes = filtered_df[rakes_column_name].to_numpy()
            else:
                plot_type = "lines"

            azimuths, dips, rakes = self.check_pairs_and_trios(azimuths, dips, rakes)

            az_type = MEASUREMENT_TYPES[msr_type][0].lower() if MEASUREMENT_TYPES[msr_type][0] != "Trend" else "strike"

            self.plot_stereogram(azimuths, dips, rakes, plot_type, az_type, title, color, marker, plot_density,
                                 colormap, show_colorbar, show_legend, label)

            self.frame_stack.setCurrentIndex(1)
            self.load_image()

            if len(self.legend["markers"]) >= 3:
                self.add_btn.setEnabled(False)

            toggle_wait_cursor(False)
        except IndexError as error:
            handle_exception(
                error, "stereogram - ok_button_clicked()",
                "A quantidade de componentes das medidas não é a mesma. Confira se há dados faltando.", self
            )
        except Exception as error:
            handle_exception(
                error, "stereogram - ok_button_clicked()",
                "Ops! Ocorreu um erro ao plotar o gráfico!", self
            )

    @staticmethod
    def check_pairs_and_trios(azimuths, dips, rakes):
        indices_to_delete = []

        for i in range(min(azimuths.size, dips.size)):
            az, dip = azimuths[i], dips[i]

            if rakes is not None:
                rake = rakes[i]
                if numpy.isnan(az) and numpy.isnan(dip) and numpy.isnan(rake):
                    indices_to_delete.append(i)
                elif (numpy.isnan(az) or numpy.isnan(dip) or numpy.isnan(rake)) and not (
                        numpy.isnan(az) and numpy.isnan(dip) and numpy.isnan(rake)):
                    raise IndexError(f"A medida {az}/{dip}/{rake} (linha {i+1}) é inválida, pois um dos componentes "
                                     f"está faltando (nan).")
            else:
                if numpy.isnan(az) and numpy.isnan(dip):
                    indices_to_delete.append(i)
                elif (numpy.isnan(az) and not numpy.isnan(dip)) or (not numpy.isnan(az) and numpy.isnan(dip)):
                    raise IndexError(f"A medida {az}/{dip} (linha {i+1}) é inválida, pois um dos componentes está "
                                     f"faltando (nan).")

        azimuths = numpy.delete(azimuths, indices_to_delete)
        dips = numpy.delete(dips, indices_to_delete)

        if rakes is not None:
            rakes = numpy.delete(rakes, indices_to_delete)
            if rakes.size == 0:
                raise Exception("Coluna de obliquidades (rake) não deve estar vazia.")

        if azimuths.size == 0:
            raise Exception("Coluna de azimutes (strike, dip direction ou trend) não deve estar vazia.")
        if dips.size == 0:
            raise Exception("Coluna de mergulhos (dip ou plunge) não deve estar vazia.")

        return azimuths, dips, rakes

    def load_image(self):
        self.image_btn.setIcon(QtGui.QIcon(f"{os.getcwd()}\\plots\\stereogram.png"))
        pixmap = QtGui.QPixmap(f"{os.getcwd()}\\plots\\stereogram.png")
        height = int(pixmap.height() * PLOT_WIDTH / pixmap.width())
        self.image_btn.setIconSize(QtCore.QSize(PLOT_WIDTH, height))
        self.image_btn.resize(PLOT_WIDTH, height)

        if self.fig:
            self.setFixedSize(self.plot_page.sizeHint())

    def save_button_clicked(self):
        try:
            file_path, file_extension = select_figure_save_location(self)
            if not file_path:
                return
            self.fig.savefig(file_path, dpi=600, format=file_extension, transparent=True)
        except Exception as error:
            handle_exception(error, "stereogram - save_button_clicked()", "Ops! Ocorreu um erro!", self)

    def add_button_clicked(self):
        self.frame_stack.setCurrentIndex(0)
        self.setFixedSize(self.initial_size)
        self.density_contour_chk.setChecked(False)
        self.density_contour_chk.setEnabled(False)
        self.figure_loop += 1

    def plot_stereogram(self, azimuths: numpy.array, dips: numpy.array, rakes: numpy.array = None,
                        plot_type: str = "poles", plane_azimuth_type: str = "strike", title: str | None = None,
                        color: str = "black", marker: str = 'Círculo', plot_density: bool = False,
                        colormap: str = "Exhalitic", show_colorbar: bool = False, show_legend: bool = True,
                        label: str = "", marker_alpha=1) -> None:
        """
        :param azimuths: Array contendo os azimutes (strikes, dip directions ou trends)
        :param dips: Array contendo os ângulos de mergulho (dips ou plunges)
        :param rakes: Array contendo os rakes/pitches
        :param plot_type: O tipo de plotagem ("planes", "poles", "lines" ou "rakes")
        :param plane_azimuth_type: O tipo de azimute usado na medida dos planos ("strike" ou "dip direction")
        :param title: O título do gráfico.
        :param color: A cor da simbologia da medida.
        :param marker: O marcador a ser usado para representar linhas e polos.
        :param plot_density: Plotar ou não contornos de densidade para as medidas.
        :param colormap: Rampa de cores para os contornos de densidade.
        :param show_colorbar: Mostrar ou não a escala da rampa de cores.
        :param show_legend: Mostrar ou não a legenda.
        :param label: O rótulo das medidas.
        :param marker_alpha: Transparência do preenchimento do marcador (0-1):
        :return: Nada.
        """
        new_figure = self.fig is None

        # Caso seja um novo gráfico, cria e configura a figura base (gráfico, grid e rótulos)
        if new_figure:
            plt.close(self.fig)
            self.fig, self.ax = mplstereonet.subplots(figsize=[5, 5], projection="stereonet")
            self.ax.set_facecolor('white')
            self.ax.set_azimuth_ticks([])
            self.ax.grid(color='black', alpha=0.1)

            labels = ['N', 'E', 'S', 'W']
            lbl_angles = numpy.arange(0, 360, 360 / len(labels))
            label_x = 0.5 - 0.54 * numpy.cos(numpy.radians(lbl_angles + 90))
            label_y = 0.5 + 0.54 * numpy.sin(numpy.radians(lbl_angles + 90))
            for i in range(len(labels)):
                self.ax.text(label_x[i], label_y[i], labels[i], transform=self.ax.transAxes, ha='center', va='center')

        # Escreve o título do gráfico, se ele for definido e já não existir
        if title and self.ax.get_title() != title:
            self.ax.set_title(title, y=1.05, fontsize=14, fontweight='bold')

        # Dip directions precisam ser convertidas pra strikes para plotar
        if plane_azimuth_type == "dip direction":
            azimuths -= 90

        # Plota os contornos de densidade e sua escala quando marcado pelo usuário
        if plot_density:
            colors = COLORMAPS[colormap]
            if plot_type in ("planes", "poles"):
                density = self.ax.density_contourf(azimuths, dips, measurement="poles", cmap=colors)
            elif plot_type == "rakes":
                density = self.ax.density_contourf(azimuths, dips, rakes, measurement="rakes", cmap=colors)
            elif plot_type == "lines":
                density = self.ax.density_contourf(dips, azimuths, measurement="lines", cmap=colors)
            else:
                raise Exception(f"Contornos de densidade com plot_type = \"{plot_type}\" não podem ser plotados.")

            if show_colorbar:
                self.fig.colorbar(density, pad=0.08, shrink=0.5)

        # Os marcadores têm tamanhos levemente diferentes, então precisa especificar o tamanho para ficarem todos iguais
        mk, sz = MARKERS[marker]["marker"], MARKERS[marker]["size"]

        # Plota as medidas
        if plot_type == "planes":
            self.ax.plane(azimuths, dips, color=color)
        elif plot_type == "poles":
            self.ax.pole(azimuths, dips, color=color, marker=mk, markersize=sz, alpha=marker_alpha)
        elif plot_type == "lines":
            self.ax.line(dips, azimuths, color=color, marker=mk, markersize=sz, alpha=marker_alpha)
        elif plot_type == "rakes":
            self.ax.plane(azimuths, dips, color=color)
            self.ax.rake(azimuths, dips, rakes, color=color, marker=mk, markersize=sz)
        else:
            raise ValueError("plot_type deve ser \"poles\", \"lines\" ou \"rakes\".")

        # Cria um novo item na legenda para as medidas plotadas
        if plot_type == "planes":
            symbol = Line2D([0], [0], color=color, linestyle='-', linewidth=1.5)
        else:
            symbol = Line2D([0], [0], color=color, marker=mk, markersize=sz, linestyle='None')
        self.legend["markers"].append(symbol)
        self.legend["labels"].append(f"{label if label != "" else "---"} (n = {len(azimuths)})")

        # Exibe a legenda, caso marcado pelo usuário, ou remove ela, caso desmarcado
        if show_legend:
            self.fig.subplots_adjust(left=0.05, bottom=0.17, right=0.95, top=0.9)
            h = len(self.legend["markers"])
            self.ax.legend(self.legend["markers"], self.legend["labels"], loc='lower center', fontsize=9,
                           bbox_to_anchor=(0.5, -0.144 - (h-1) * 0.053), facecolor='none')
        else:
            if self.ax.get_legend():
                self.ax.get_legend().remove()

        self.save_plot()

    def save_plot(self):
        plots_folder = os.path.join(os.getcwd(), "plots")
        os.makedirs(plots_folder, exist_ok=True)
        image_path = f"{plots_folder}\\stereogram.png"
        self.fig.savefig(image_path, dpi=600, format="png", transparent=True)


class FilterWindow(QtWidgets.QDialog):
    def __init__(self, parent, df, previous_selection=None):
        super().__init__(parent)
        self.df = df
        self.selections_per_column = previous_selection.copy() if previous_selection else {}

        self.setWindowTitle("Filtros")
        self.setWindowIcon(QtGui.QIcon("icons/filter.png"))
        self.setMinimumSize(300, 350)

        layout = QtWidgets.QGridLayout(self)

        self.column_cbx = QtWidgets.QComboBox(self)
        self.column_cbx.addItems(self.df.columns)
        self.column_cbx.currentTextChanged.connect(self.update_checkboxes)

        self.select_lbl = QtWidgets.QLabel("Selecione valores:", self)
        self.toggle_btn = QtWidgets.QToolButton(self)
        self.toggle_btn.setIcon(QtGui.QIcon("icons/select.png"))
        self.toggle_btn.setToolTip("Selecionar/Desselecionar todos")
        self.toggle_btn.setFixedSize(20, 20)
        self.toggle_btn.clicked.connect(self.toggle_all)

        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.viewport().setStyleSheet("background-color: #FBFBFB")
        self.checkbox_container = QtWidgets.QWidget()
        self.checkbox_layout = QtWidgets.QVBoxLayout(self.checkbox_container)
        self.checkbox_layout.addStretch()
        self.scroll_area.setWidget(self.checkbox_container)

        self.filters_lbl = QtWidgets.QLabel("Filtros atuais:", self)
        self.remove_btn = QtWidgets.QToolButton(self)
        self.remove_btn.setIcon(QtGui.QIcon("icons/remove.png"))
        self.remove_btn.setToolTip("Remover regra de filtro selecionada")
        self.remove_btn.setFixedSize(20, 20)
        self.remove_btn.clicked.connect(self.remove_filter_rule)
        self.filters_list = QtWidgets.QListWidget(self)
        self.filters_list.setFixedHeight(65)

        self.ok_btn = QtWidgets.QPushButton("Aplicar", self)
        self.ok_btn.clicked.connect(self.accept)

        layout.addWidget(self.column_cbx, 0, 0, 1, 2)
        layout.addWidget(self.select_lbl, 1, 0)
        layout.addWidget(self.toggle_btn, 1, 1, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.scroll_area, 2, 0, 1, 2)
        layout.addWidget(self.filters_lbl, 3, 0)
        layout.addWidget(self.remove_btn, 3, 1, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.filters_list, 4, 0, 1, 2)
        layout.addWidget(self.ok_btn, 5, 0, 1, 2)

        self.update_checkboxes(self.column_cbx.currentText())
        self.refresh_filters_list()
        self.resize(300, 350)

    # Os métodos dessa classe foram feitos via ChatGPT, não tenho nem ideia de como funciona hehe

    def update_checkboxes(self, column_name):
        while self.checkbox_layout.count() > 1:
            item = self.checkbox_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        values = self.df[column_name].unique()
        selected_values = set(map(str, self.selections_per_column.get(column_name, values)))

        for val in values:
            checkbox = QtWidgets.QCheckBox(str(val))
            checkbox.setChecked(str(val) in selected_values)
            checkbox.stateChanged.connect(self.update_selection)
            self.checkbox_layout.insertWidget(self.checkbox_layout.count() - 1, checkbox)

        self.update_selection()

    def update_selection(self):
        column_name = self.column_cbx.currentText()
        selected = []
        column_dtype = self.df[column_name].dtype

        for i in range(self.checkbox_layout.count() - 1):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                text = checkbox.text()
                try:
                    if pandas.api.types.is_numeric_dtype(column_dtype):
                        value = float(text) if '.' in text else int(text)
                    elif pandas.api.types.is_bool_dtype(column_dtype):
                        value = text.lower() == 'true'
                    else:
                        value = text
                except ValueError:
                    value = text
                selected.append(value)

        all_values = set(self.df[column_name].unique())
        selected_values = set(selected)
        normalized_all_values = {self._normalize_dtype(v, column_name) for v in all_values}
        if selected_values == normalized_all_values:
            self.selections_per_column.pop(column_name, None)
        else:
            self.selections_per_column[column_name] = selected
        self.refresh_filters_list()

    def _normalize_dtype(self, value, column_name):
        dtype = self.df[column_name].dtype
        try:
            if pandas.api.types.is_numeric_dtype(dtype):
                return float(value)
            elif pandas.api.types.is_bool_dtype(dtype):
                return bool(value)
            else:
                return str(value)
        except ValueError:
            return str(value)

    def refresh_filters_list(self):
        self.filters_list.clear()
        for column, values in self.selections_per_column.items():
            if not values:
                self.filters_list.addItem(f"{column}: nenhum")
            else:
                self.filters_list.addItem(f"{column}: {values}")

    def remove_filter_rule(self):
        selected_item = self.filters_list.currentItem()
        if not selected_item:
            return
        column_name = selected_item.text().split(":")[0]
        if column_name in self.selections_per_column:
            del self.selections_per_column[column_name]
            if column_name == self.column_cbx.currentText():
                self.update_checkboxes(column_name)
            else:
                self.refresh_filters_list()

    def toggle_all(self):
        checkboxes = [self.checkbox_layout.itemAt(i).widget() for i in range(self.checkbox_layout.count() - 1)]
        all_checked = all(cb.isChecked() for cb in checkboxes)
        for cb in checkboxes:
            cb.setChecked(not all_checked)

    def get_all_selected_values(self):
        return self.selections_per_column
