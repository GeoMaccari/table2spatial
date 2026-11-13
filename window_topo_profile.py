# -*- coding: utf-8 -*-

import os
import geopandas
import matplotlib
import numpy
import rasterio
from pyproj import CRS
from PyQt6 import QtCore, QtGui, QtWidgets
from icecream import ic
from shapely.geometry import LineString, MultiLineString
import matplotlib.pyplot as plt

from shared import Icons, handle_exception, wait_cursor, show_file_dialog, show_popup, center_on_screen

matplotlib.use("Agg")

VECTOR_FILTER = (
    "Formatos suportados (*.shp *.gpkg *.geojson *.json);;"
    "Shapefile (*.shp);;"
    "Geopackage (*.gpkg);;"
    "GeoJSON (*.geojson *.json)"
)
RASTER_FILTER = "GeoTIFF (*.tif *.tiff);;"

MAX_FIG_PX = 32000  # matplotlib internal pixel ceiling (hard limit is 32767)

SAVE_FILTER = (
    "Formatos suportados (*.png *.jpg *.svg);;"
    "PNG (*.png);;"
    "JPG (*.jpg);;"
    "SVG (*.svg)"
)


class TopoProfileWindow(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle("Perfil Topográfico")
        self.setWindowIcon(QtGui.QIcon(Icons.PROFILE))
        self.setModal(False)

        self.DEM = None
        self.line = None
        self.fig = None

        screen_width = QtGui.QGuiApplication.primaryScreen().availableGeometry().width()
        self.plot_width = int(screen_width * 0.9)  # px — display width of the diagram on the plot page

        self.stack = QtWidgets.QStackedWidget(self)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

        # ================================================================== #
        # PAGE 0 — Configuration
        # ================================================================== #
        config_page = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(config_page)
        g.setSpacing(6)
        g.setContentsMargins(10, 10, 10, 10)
        g.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.stack.addWidget(config_page)

        row = 0

        # DEM file
        g.addWidget(QtWidgets.QLabel("MDT:"), row, 0, 1, 8); row += 1
        self.dem_edt = QtWidgets.QLineEdit()
        self.dem_edt.setReadOnly(True)
        self.dem_edt.setPlaceholderText("Nenhum arquivo selecionado")
        self.dem_browse_btn = QtWidgets.QPushButton("Selecionar")
        g.addWidget(self.dem_edt,        row, 0, 1, 6)
        g.addWidget(self.dem_browse_btn, row, 6, 1, 2); row += 1

        # Line file
        g.addWidget(QtWidgets.QLabel("Linha de perfil:"), row, 0, 1, 8); row += 1
        self.line_edt = QtWidgets.QLineEdit()
        self.line_edt.setReadOnly(True)
        self.line_edt.setPlaceholderText("Nenhum arquivo selecionado")
        self.line_browse_btn = QtWidgets.QPushButton("Selecionar")
        g.addWidget(self.line_edt,        row, 0, 1, 6)
        g.addWidget(self.line_browse_btn, row, 6, 1, 2); row += 1

        # Layer selector (only visible for GPKG)
        self.layer_lbl = QtWidgets.QLabel("Camada:")
        self.layer_cbx = QtWidgets.QComboBox()
        self.layer_lbl.setVisible(False)
        self.layer_cbx.setVisible(False)
        g.addWidget(self.layer_lbl, row, 0, 1, 2)
        g.addWidget(self.layer_cbx, row, 2, 1, 6); row += 1

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        g.addWidget(sep, row, 0, 1, 8); row += 1

        # Scale
        g.addWidget(QtWidgets.QLabel("Escala horizontal (1:X):"), row, 0, 1, 5)
        self.scale_spn = QtWidgets.QSpinBox()
        self.scale_spn.setRange(1, 50000000)
        self.scale_spn.setSingleStep(1)
        self.scale_spn.setValue(10000)
        self.scale_spn.setToolTip("Escala horizontal do perfil.")
        g.addWidget(self.scale_spn, row, 5, 1, 3); row += 1

        # Vertical exaggeration
        g.addWidget(QtWidgets.QLabel("Exagero vertical:"), row, 0, 1, 5)
        self.vex_spn = QtWidgets.QDoubleSpinBox()
        self.vex_spn.setRange(0.1, 100.0)
        self.vex_spn.setSingleStep(0.5)
        self.vex_spn.setValue(1.0)
        self.vex_spn.setDecimals(1)
        self.vex_spn.setToolTip(
            "Multiplica a escala vertical para realçar o relevo. "
            "1.0 = sem exagero."
        )
        g.addWidget(self.vex_spn, row, 5, 1, 3);
        row += 1

        # Sampling resolution
        g.addWidget(QtWidgets.QLabel("Resolução de amostragem (m):"), row, 0, 1, 5)
        self.resolution_spn = QtWidgets.QDoubleSpinBox()
        self.resolution_spn.setRange(0.1, 1000000.0)
        self.resolution_spn.setSingleStep(10.0)
        self.resolution_spn.setValue(1.0)
        self.resolution_spn.setDecimals(1)
        self.resolution_spn.setToolTip(
            "Distância entre pontos amostrados ao longo da linha. "
            "Valores menores geram perfis mais suaves, mas mais lentos."
        )
        g.addWidget(self.resolution_spn, row, 5, 1, 3); row += 1

        # Tick interval
        g.addWidget(QtWidgets.QLabel("Intervalo de marcações no eixo X (m):"), row, 0, 1, 5)
        self.tick_spn = QtWidgets.QDoubleSpinBox()
        self.tick_spn.setRange(1.0, 1000000.0)
        self.tick_spn.setSingleStep(100.0)
        self.tick_spn.setValue(500.0)
        self.tick_spn.setDecimals(1)
        g.addWidget(self.tick_spn, row, 5, 1, 3); row += 1

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        g.addWidget(sep2, row, 0, 1, 8); row += 1

        # Appearance
        g.addWidget(QtWidgets.QLabel("Cor do perfil:"), row, 0, 1, 5)
        self.color_btn = _ColorButton("black")
        g.addWidget(self.color_btn, row, 5, 1, 3); row += 1

        self.grid_chk = QtWidgets.QCheckBox("Mostrar grade")
        self.grid_chk.setChecked(True)
        g.addWidget(self.grid_chk, row, 0, 1, 8); row += 1

        # OK
        self.ok_btn = QtWidgets.QPushButton("Gerar perfil")
        self.ok_btn.setEnabled(False)
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

        plot_layout.addWidget(self.save_btn, 0, 0)
        plot_layout.addWidget(self.back_btn, 0, 1)
        plot_layout.addWidget(self.image_btn, 1, 0, 10, 10)

        # ------------------------------------------------------------------ #
        # Signals
        # ------------------------------------------------------------------ #
        self.dem_browse_btn.clicked.connect(self.browse_dem)
        self.line_browse_btn.clicked.connect(self.browse_line)
        self.layer_cbx.currentTextChanged.connect(self.check_ok_enabled)
        self.ok_btn.clicked.connect(self.on_ok)
        self.save_btn.clicked.connect(self.on_save)
        self.back_btn.clicked.connect(self.on_back)

    # ------------------------------------------------------------------ #
    # File browsing
    # ------------------------------------------------------------------ #

    def browse_dem(self) -> None:
        try:
            path, _ = show_file_dialog("Selecionar raster DEM", RASTER_FILTER, mode="open", parent=self)
            if not path:
                return

            # ---- Read and validate DEM
            dem = rasterio.open(path)
            if dem.crs is None:
                raise ValueError("O raster selecionado não é georreferenciado.")
            if not dem.crs.is_projected:
                show_popup(f"O MDT deve estar em um SRC projetado (SRC atual: {CRS(dem.crs).name}).", parent=self)
                return

            self.DEM = dem

            self.dem_edt.setText(path)
            self.check_ok_enabled()
        except Exception as e:
            handle_exception(e, "browse_dem()", parent=self)

    def browse_line(self) -> None:
        try:
            path, ext = show_file_dialog("Selecionar linha de perfil", VECTOR_FILTER, mode="open", parent=self)

            if not path:
                return

            gdf = geopandas.read_file(path)

            if gdf.crs is None:
                raise ValueError("Line layer has no CRS defined.")
            if not gdf.crs.is_projected:
                raise ValueError("Line layer CRS must be projected.")
            if gdf.crs != self.DEM.crs:
                gdf = gdf.to_crs(self.DEM.crs)

            line = gdf.geometry.iloc[0]  # TODO feature selection

            if isinstance(line, MultiLineString):
                raise ValueError(
                    "Selected feature is a MultiLineString. Convert it to a single LineString before generating a profile."
                )
            if not isinstance(line, LineString):
                raise ValueError(f"Selected feature is {line.geom_type}. Expected a LineString.")

            self.line = line

            self.line_edt.setText(path)
            self.auto_set_parameters(path)
            self.check_ok_enabled()
        except Exception as e:
            handle_exception(e, "browse_line()", parent=self)

    def check_ok_enabled(self) -> None:
        self.ok_btn.setEnabled(
            bool(self.dem_edt.text()) and bool(self.line_edt.text())
        )

    # ------------------------------------------------------------------ #
    # Core slots
    # ------------------------------------------------------------------ #

    def on_ok(self) -> None:
        try:
            wait_cursor(True)

            # TODO Finish layer selection for GPKG; Feature selection

            scale     = self.scale_spn.value()
            vex       = self.vex_spn.value()
            res       = self.resolution_spn.value()
            color     = self.color_btn.color
            show_grid = self.grid_chk.isChecked()

            self.plot_profile(
                map_scale=scale, vertical_exaggeration=vex, sampling_resolution=res,
                color=color, show_grid=show_grid,
            )
            wait_cursor(False)

            if self._too_large:
                # Figure is too large to display — ask user where to save it
                path, ext = show_file_dialog("Salvar perfil", SAVE_FILTER, mode="save", parent=self)
                if not path:
                    return
                if not ext:
                    ext = ".png"
                    path += ext
                wait_cursor(True)
                self.fig.savefig(path, dpi=300, format=ext.lstrip("."),
                                 bbox_inches="tight", transparent=(ext == ".svg"))
                wait_cursor(False)
                show_popup(
                    "A escala selecionada gera uma figura muito grande para ser exibida. "
                    "O perfil foi salvo com sucesso.",
                    parent=self,
                )
                self.accept()
                return

            self.load_image()
            self.stack.setCurrentIndex(1)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
            center_on_screen(self)

        except Exception as e:
            wait_cursor(False)
            handle_exception(e, "on_ok()", "Erro ao gerar o perfil.", self)

    def on_back(self) -> None:
        try:
            self.stack.setCurrentIndex(0)
            self.setFixedSize(self.stack.currentWidget().sizeHint())
            center_on_screen(self)
        except Exception as e:
            handle_exception(e, "on_back()", parent=self)

    def on_save(self) -> None:
        try:
            path, ext = show_file_dialog("Salvar figura", SAVE_FILTER, mode="save", parent=self)
            if not path:
                return
            if not ext:
                ext = ".png"
                path += ext
            self.fig.savefig(path, dpi=300, format=ext.lstrip("."), bbox_inches="tight",
                             transparent=(ext == ".svg"))
            show_popup("Perfil salvo com sucesso!", parent=self)
        except Exception as e:
            handle_exception(e, "on_save()", "Erro ao salvar.", self)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def auto_set_parameters(self, path) -> None:
        try:
            length = self.line.length
            self.resolution_spn.setValue(max(1, int(length / 200)))

            dem_res, _ = self.DEM.res
            self.resolution_spn.setMinimum(dem_res)

            # Auto-set scale so the figure fits within matplotlib's pixel limit at 300 dpi
            INCHES_PER_METER = 39.3701
            min_scale = int(numpy.ceil((length * INCHES_PER_METER * 300) / MAX_FIG_PX))
            self.scale_spn.setValue(min_scale)
        except Exception as e:
            handle_exception(e, "auto_set_parameters()", parent=self)

    def sample_elevations(self, src, line, sampling_resolution):
        """Interpolate points along the line and sample the DEM."""
        total_length = line.length

        if sampling_resolution <= 0:
            raise ValueError("A resolução de amostragem deve ser maior que zero.")
        distances = numpy.arange(0, total_length + sampling_resolution, sampling_resolution)
        if distances[-1] > total_length:
            distances[-1] = total_length

        points = [line.interpolate(d) for d in distances]
        coords = [(p.x, p.y) for p in points]

        elevations = numpy.array([v[0] for v in src.sample(coords)], dtype=float)

        if src.nodata is not None:
            elevations[elevations == src.nodata] = numpy.nan

        return distances, elevations, total_length

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def plot_profile(self, sampling_resolution: float, map_scale: int, vertical_exaggeration=1.0,
                     y_limits=None, color="black", show_grid=False) -> None:

        # ---- Sample elevations along the line -------------------------------
        distances, elevations, total_length = self.sample_elevations(self.DEM, self.line, sampling_resolution)

        if not y_limits:
            y_limits = [numpy.nanmin(elevations), numpy.nanmax(elevations)]
            y_range = y_limits[1] - y_limits[0]
            if y_range < 10:
                y_limits[0] -= 1
                y_limits[1] += 1
            elif y_range < 100:
                y_limits[0] -= 10
                y_limits[1] += 10
            elif y_range < 1000:
                y_limits[0] -= 100
                y_limits[1] += 100
            else:
                y_limits[0] -= 1000
                y_limits[1] += 1000

        # ---- Figure geometry ------------------------------------------------
        x_range = total_length
        y_range = y_limits[1] - y_limits[0]

        if x_range == 0:
            raise ValueError(
                "A linha de perfil tem comprimento zero. "
                "Verifique se o arquivo vetorial contém uma linha válida."
            )
        if y_range == 0:
            raise ValueError(
                "Todas as elevações amostradas são iguais. "
                "Verifique se o MDT cobre a linha de perfil corretamente."
            )

        INCHES_PER_METER = 39.3701

        base_width  = (total_length / map_scale) * INCHES_PER_METER
        plot_height = base_width * (y_range / x_range) * vertical_exaggeration
        plot_height = max(plot_height, 0.1)

        left_margin   = 0.6
        right_margin  = 0.2
        bottom_margin = 0.4
        top_margin    = 0.4

        fig_width  = base_width + left_margin + right_margin
        fig_height = plot_height + bottom_margin + top_margin

        self._too_large = (fig_width * 300) > MAX_FIG_PX

        # ---- Plot ---------------------------------------------------------------

        if self.fig is not None:
            plt.close(self.fig)

        self.fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=300)

        ax.set_position([
            left_margin / fig_width,
            bottom_margin / fig_height,
            base_width / fig_width,
            plot_height / fig_height,
        ])

        ax.plot(distances, elevations, linewidth=1, color=color)

        #ax.set_title(title, fontsize=12)
        ax.set_xlabel("Distância (m)", fontsize=8)
        ax.set_ylabel("Altitude (m)", fontsize=8)

        ax.set_xlim(0, total_length)
        if y_limits:
            ax.set_ylim(y_limits[0], y_limits[1])
        ax.margins(x=0)

        #y_ticks = np.arange(y_min, y_max + y_tick_interval, y_tick_interval)
        #ax.set_yticks(y_ticks)
        #ax.set_yticklabels([f"{v:.0f}" for v in y_ticks])

        ax.spines["top"].set_visible(False)
        for spine in ax.spines.values():
            spine.set_linewidth(0.7)
        ax.tick_params(axis="both", labelsize=8, width=0.7)

        ax.grid(color="black", linewidth=0.1)
        ax.yaxis.grid(show_grid)
        ax.xaxis.grid(False)

        y_offset = 4 / 72 / (ax.get_position().height * self.fig.get_size_inches()[1])

        start_label, end_label = "A", "A′"

        ax.text(0, 1 + y_offset, start_label, transform=ax.transAxes, fontsize=12, ha="center", va="bottom")
        ax.text(1, 1 + y_offset, end_label, transform=ax.transAxes, fontsize=12, ha="center", va="bottom")

        plots_dir = os.path.join(os.getcwd(), "plots")
        os.makedirs(plots_dir, exist_ok=True)
        self.preview_path = os.path.join(plots_dir, "topo_profile.png")
        self.fig.savefig(self.preview_path, dpi=600, format="png", transparent=True)

    def load_image(self) -> None:
        pixmap = QtGui.QPixmap(self.preview_path)
        height = int(pixmap.height() * self.plot_width / pixmap.width())
        self.image_btn.setIcon(QtGui.QIcon(self.preview_path))
        self.image_btn.setIconSize(QtCore.QSize(self.plot_width, height))
        self.image_btn.setFixedSize(self.plot_width, height)


# ---------------------------------------------------------------------------
# Color button (same pattern as in other windows)
# ---------------------------------------------------------------------------

class _ColorButton(QtWidgets.QPushButton):
    color_changed = QtCore.pyqtSignal()

    def __init__(self, initial_color: str = "#000000"):
        super().__init__()
        self.color = initial_color
        self.setFixedSize(64, 24)
        self.update_style()
        self.clicked.connect(self.pick_color)

    def update_style(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self.color}; "
            f"border: 1px solid #888; border-radius: 3px; }}"
        )

    def pick_color(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), None, "Escolher cor")
        if chosen.isValid():
            self.color = chosen.name()
            self.update_style()
            self.color_changed.emit()