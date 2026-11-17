# -*- coding: utf-8 -*-

import io
import math
import os
from collections import OrderedDict
from typing import NamedTuple
import geopandas
import matplotlib
import numpy
import rasterio
from pyproj import CRS
from PyQt6 import QtCore, QtGui, QtWidgets
from icecream import ic
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge
import matplotlib.pyplot as plt
from matplotlib.transforms import Bbox

from shared import Icons, ColorButton, handle_exception, wait_cursor, show_file_dialog, show_popup, center_on_screen

matplotlib.use("Agg")

VECTOR_FILTER = (
    "Formatos suportados (*.shp *.gpkg *.geojson *.json);;"
    "Shapefile (*.shp);;"
    "Geopackage (*.gpkg);;"
    "GeoJSON (*.geojson *.json)"
)
RASTER_FILTER = "GeoTIFF (*.tif *.tiff);;"

MAX_FIG_PX = 32000  # matplotlib internal pixel ceiling (hard limit is 32767); only matters for raster exports
SCREEN_DPI_OVERRIDE = None  # logical pixels per inch of your monitor; set it if the profile isn't drawn at true scale
MAX_EXPORT_PIXELS = 150_000_000  # keeps a raster export under ~600 MB of RGBA memory

SAVE_FILTER = (
    "Formatos suportados (*.png *.jpg *.svg);;"
    "PNG (*.png);;"
    "JPG (*.jpg);;"
    "SVG (*.svg)"
)


class LineCandidate(NamedTuple):
    """A feature of the selected layer. `line` is None (and `reason` explains why) when it can't be a profile line."""
    number: int                 # position of the feature in the layer, starting at 1
    line: LineString | None
    reason: str | None


def check_feature_geometry(geometry) -> tuple[LineString | None, str | None]:
    """Returns (line, None) if the geometry can be used as a profile line, or (None, reason) if it can't.
    A MultiLineString is accepted when its parts can be merged into a single continuous line."""
    if geometry is None or geometry.is_empty:
        return None, "A feição não possui geometria."
    if isinstance(geometry, MultiLineString):
        merged = linemerge(geometry)
        if not isinstance(merged, LineString):
            return None, (
                "A feição é uma MultiLineString com partes desconectadas. "
                "Una as partes em uma única linha antes de gerar o perfil."
            )
        geometry = merged
    if not isinstance(geometry, LineString):
        return None, f"A feição é do tipo {geometry.geom_type}. É necessária uma linha."
    if geometry.length == 0:
        return None, "A linha da feição tem comprimento zero."
    return geometry, None


def smooth_profile(distances, elevations, window_m: float) -> numpy.ndarray:
    """
    Moving average of the elevations over a window of `window_m` meters.
    The window is symmetric and shrinks next to both ends of the profile and next to nodata stretches, so those
    points keep their real elevation, straight slopes aren't bent, and nodata (NaN) never leaks into its neighbors.
    """
    count = len(elevations)
    step = float(numpy.median(numpy.diff(distances))) if count > 1 else 0.0
    if step <= 0:
        return elevations

    half = int(round(window_m / step)) // 2
    index = numpy.arange(count)
    nodata = numpy.isnan(elevations)

    # Distance (in samples) from each sample to the nearest nodata sample, so that windows never include one
    previous = numpy.maximum.accumulate(numpy.where(nodata, index, -2 * count))
    following = numpy.minimum.accumulate(numpy.where(nodata, index, 3 * count)[::-1])[::-1]
    to_nodata = numpy.minimum(index - previous, following - index)

    # Half-width of the window at each sample
    reach = numpy.maximum(0, numpy.minimum.reduce([
        numpy.full(count, half), index, count - 1 - index, to_nodata - 1
    ]))
    low, high = index - reach, index + reach + 1

    sums = numpy.concatenate(([0.0], numpy.cumsum(numpy.where(nodata, 0.0, elevations))))
    smoothed = (sums[high] - sums[low]) / (high - low)
    smoothed[nodata] = numpy.nan
    return smoothed


def fill_nodata_with_zero(elevations):
    """Replaces NaN (nodata) samples with zero. Returns (filled elevations, whether any nodata was found)."""
    nodata_mask = numpy.isnan(elevations)
    return numpy.where(nodata_mask, 0.0, elevations), bool(nodata_mask.any())


def automatic_altitude_limits(elevations) -> tuple[float, float]:
    """Altitude range of the profile plus a padding that grows with the relief."""
    low, high = float(numpy.nanmin(elevations)), float(numpy.nanmax(elevations))
    relief = high - low
    if relief < 10:
        pad = 1
    elif relief < 100:
        pad = 10
    elif relief < 1000:
        pad = 100
    else:
        pad = 1000
    return low - pad, high + pad


def round_up_scale(value: float) -> int:
    """Rounds a map scale denominator up to the next "round" one (e.g. 4589 -> 5000, 10313 -> 12500)."""
    value = max(1.0, value)
    magnitude = 10 ** math.floor(math.log10(value))
    for factor in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= factor * magnitude:
            return int(factor * magnitude)
    return int(10 * magnitude)


class ProfileCanvas(QtWidgets.QWidget):
    """
    Shows a matplotlib figure at its real physical size (`display_dpi` logical pixels per inch), however large it is.
    The figure is rendered in square tiles that are only drawn when they scroll into view and are kept in a
    small cache, so there is never one huge image in memory.
    """

    TARGET_TILE_PX = 1024
    CACHE_SIZE = 24
    MAX_WIDGET_PX = 16_000_000  # Qt widgets can't be larger than 16,777,215 px

    def __init__(self, fig, display_dpi: float, device_pixel_ratio: float = 1.0):
        super().__init__()
        self.fig = fig
        fig_w, fig_h = fig.get_size_inches()

        # Rendering at (logical dpi x device ratio) and drawing the pixmaps with the screen's own ratio puts every
        # rendered pixel on exactly one screen pixel (no resampling) and keeps the physical size exact.
        self.render_dpi = display_dpi * device_pixel_ratio
        self.pixmap_ratio = device_pixel_ratio
        self.tile_px = self.TARGET_TILE_PX                          # tile size in rendered pixels (a whole number)
        self.tile_span = self.tile_px / device_pixel_ratio          # tile size in logical pixels

        # Same size matplotlib gives to a plain savefig() at this dpi
        self.full_w = int(fig_w * self.render_dpi)
        self.full_h = int(fig_h * self.render_dpi)
        self.cols = math.ceil(self.full_w / self.tile_px)
        self.rows = math.ceil(self.full_h / self.tile_px)
        self.tiles = OrderedDict()

        width = math.ceil(self.full_w / self.pixmap_ratio)
        height = math.ceil(self.full_h / self.pixmap_ratio)
        if max(width, height) > self.MAX_WIDGET_PX:
            raise ValueError(
                "A escala selecionada gera um perfil grande demais para ser exibido. Use uma escala menor."
            )
        self.setFixedSize(width, height)

    def render_tile(self, col: int, row: int) -> QtGui.QPixmap:
        dpi = self.render_dpi
        left, top = col * self.tile_px, row * self.tile_px  # whole pixels, so tiles line up even with a fractional dpi
        right, bottom = min(left + self.tile_px, self.full_w), min(top + self.tile_px, self.full_h)
        eps = 1e-3 / dpi  # keeps matplotlib's int() truncation from dropping a pixel to floating point error
        # Figure coordinates are in inches with the origin at the bottom left; rows are counted from the top
        bbox = Bbox.from_extents(left / dpi, (self.full_h - bottom) / dpi,
                                 right / dpi + eps, (self.full_h - top) / dpi + eps)
        buffer = io.BytesIO()
        self.fig.savefig(buffer, format="png", dpi=dpi, transparent=True, bbox_inches=bbox)
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        pixmap.setDevicePixelRatio(self.pixmap_ratio)
        return pixmap

    def tile(self, col: int, row: int) -> QtGui.QPixmap | None:
        key = (col, row)
        if key in self.tiles:
            self.tiles.move_to_end(key)
            return self.tiles[key]
        try:
            pixmap = self.render_tile(col, row)
        except Exception as error:  # never let an exception escape from paintEvent
            ic("ProfileCanvas.tile()", error)
            return None
        self.tiles[key] = pixmap
        while len(self.tiles) > self.CACHE_SIZE:
            self.tiles.popitem(last=False)
        return pixmap

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(event.rect(), QtGui.QColor("white"))  # the figure is transparent, so show it on paper
        span = self.tile_span
        rect = event.rect()
        first_row, last_row = math.floor(rect.top() / span), math.floor(rect.bottom() / span)
        first_col, last_col = math.floor(rect.left() / span), math.floor(rect.right() / span)
        for row in range(max(0, first_row), min(self.rows - 1, last_row) + 1):
            for col in range(max(0, first_col), min(self.cols - 1, last_col) + 1):
                pixmap = self.tile(col, row)
                if pixmap is not None:
                    painter.drawPixmap(QtCore.QPointF(col * span, row * span), pixmap)
                else:
                    painter.setPen(QtGui.QColor("gray"))
                    painter.drawText(QtCore.QRectF(col * span, row * span, span, span),
                                     QtCore.Qt.AlignmentFlag.AlignCenter, "Não foi possível\nexibir esta região")
        painter.end()


class TopoProfileWindow(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle("Perfil Topográfico")
        self.setWindowIcon(QtGui.QIcon(Icons.PROFILE))
        self.setModal(False)

        self.DEM = None
        self.line = None
        self.line_path = None        # vector file the profile line comes from
        self.current_layer = None    # selected layer (GeoPackage only)
        self.line_candidates = []    # LineCandidate for every feature of the selected layer, in combobox order
        self.fig = None
        self.line_status = None      # validation of the selected feature: None, "ok", "alert" or "error"

        screen = QtGui.QGuiApplication.primaryScreen()
        self.plot_width = int(screen.availableGeometry().width() * 0.9)    # px — max window width on the plot page
        self.plot_height = int(screen.availableGeometry().height() * 0.9)  # px — max window height on the plot page

        self.stack = QtWidgets.QStackedWidget(self)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

        # ================================================================== #
        # PAGE 0 — Configuration
        # ================================================================== #
        config_page = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(config_page)
        g.setSpacing(5)
        g.setContentsMargins(10, 10, 10, 10)
        g.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.stack.addWidget(config_page)

        row = 0

        # DEM file
        g.addWidget(QtWidgets.QLabel("MDT:"), row, 0, 1, 10); row += 1
        self.dem_edt = QtWidgets.QLineEdit()
        self.dem_edt.setReadOnly(True)
        self.dem_edt.setPlaceholderText("Nenhum arquivo selecionado")
        self.dem_browse_btn = QtWidgets.QPushButton("Selecionar")
        g.addWidget(self.dem_edt,        row, 0, 1, 8)
        g.addWidget(self.dem_browse_btn, row, 8, 1, 2); row += 1

        # Line file
        g.addWidget(QtWidgets.QLabel("Linha de perfil:"), row, 0, 1, 10); row += 1
        self.line_edt = QtWidgets.QLineEdit()
        self.line_edt.setReadOnly(True)
        self.line_edt.setPlaceholderText("Nenhum arquivo selecionado")
        self.line_browse_btn = QtWidgets.QPushButton("Selecionar")
        self.line_browse_btn.setEnabled(False)  # unlocked once a valid DEM is selected
        self.line_browse_btn.setToolTip("Selecione um MDT válido primeiro.")
        self.line_edt.setPlaceholderText("Selecione o MDT primeiro")
        g.addWidget(self.line_edt,        row, 0, 1, 8)
        g.addWidget(self.line_browse_btn, row, 8, 1, 2); row += 1

        # Layer selector (only visible for GPKG)
        self.layer_lbl = QtWidgets.QLabel("Camada:")
        self.layer_lbl.setVisible(False)
        g.addWidget(self.layer_lbl, row, 0, 1, 2)
        self.layer_cbx = QtWidgets.QComboBox()
        self.layer_cbx.setVisible(False)
        g.addWidget(self.layer_cbx, row, 2, 1, 8); row += 1

        # Feature selector
        g.addWidget(QtWidgets.QLabel("Feição:"), row, 0, 1, 10)
        self.feature_cbx = QtWidgets.QComboBox()
        self.feature_cbx.setToolTip("Número da feição na camada (a partir de 1) e comprimento da linha.")
        g.addWidget(self.feature_cbx, row, 2, 1, 7)

        self.feature_icn = QtWidgets.QPushButton()
        self.feature_icn.setIcon(QtGui.QIcon(Icons.CIRCLE))
        self.feature_icn.setFlat(True)
        self.feature_icn.setFixedSize(24, 24)
        g.addWidget(self.feature_icn, row, 9, 1, 1); row += 1

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        g.addWidget(sep, row, 0, 1, 10); row += 1

        # Scale
        g.addWidget(QtWidgets.QLabel("Escala horizontal (1:X):"), row, 0, 1, 4)
        self.scale_spn = QtWidgets.QSpinBox()
        self.scale_spn.setRange(1, 50000000)
        self.scale_spn.setSingleStep(1)
        self.scale_spn.setValue(10000)
        self.scale_spn.setToolTip("Escala horizontal do perfil.")
        g.addWidget(self.scale_spn, row, 4, 1, 6); row += 1

        # Vertical exaggeration
        g.addWidget(QtWidgets.QLabel("Exagero vertical:"), row, 0, 1, 4)
        self.vex_spn = QtWidgets.QDoubleSpinBox()
        self.vex_spn.setRange(0.1, 100.0)
        self.vex_spn.setSingleStep(0.5)
        self.vex_spn.setValue(1.0)
        self.vex_spn.setDecimals(1)
        self.vex_spn.setToolTip(
            "Multiplica a escala vertical para realçar o relevo. "
            "1.0 = sem exagero."
        )
        g.addWidget(self.vex_spn, row, 4, 1, 6); row += 1

        # Altitude limits (filled automatically when a profile line is loaded)
        g.addWidget(QtWidgets.QLabel("Altitude mín./máx. (m):"), row, 0, 1, 4)
        self.ymin_spn = QtWidgets.QDoubleSpinBox()
        self.ymin_spn.setRange(-12000.0, 9000.0)
        self.ymin_spn.setDecimals(0)
        self.ymin_spn.setSingleStep(1)
        self.ymin_spn.setValue(0)
        self.ymin_spn.setToolTip(f"Elevação mínima do gráfico.")
        g.addWidget(self.ymin_spn, row, 4, 1, 3)
        self.ymax_spn = QtWidgets.QDoubleSpinBox()
        self.ymax_spn.setRange(-12000.0, 9000.0)
        self.ymax_spn.setDecimals(0)
        self.ymax_spn.setSingleStep(1)
        self.ymax_spn.setValue(1000)
        self.ymax_spn.setToolTip(f"Elevação máxima do gráfico.")
        g.addWidget(self.ymax_spn, row, 7, 1, 3); row += 1

        # Sampling resolution
        g.addWidget(QtWidgets.QLabel("Resolução de amostragem (m):"), row, 0, 1, 4)
        self.resolution_spn = QtWidgets.QDoubleSpinBox()
        self.resolution_spn.setRange(0.1, 1000000.0)
        self.resolution_spn.setSingleStep(10.0)
        self.resolution_spn.setValue(1.0)
        self.resolution_spn.setDecimals(1)
        self.resolution_spn.setToolTip("Distância entre pontos amostrados ao longo da linha. ")
        g.addWidget(self.resolution_spn, row, 4, 1, 6);
        row += 1

        # Smoothing
        self.smooth_chk = QtWidgets.QCheckBox("Suavizar perfil (m):")
        self.smooth_chk.setToolTip("Aplica uma média móvel às altitudes amostradas ao longo da linha.")
        g.addWidget(self.smooth_chk, row, 0, 1, 4)
        self.smooth_spn = QtWidgets.QDoubleSpinBox()
        self.smooth_spn.setRange(1.0, 1000000.0)
        self.smooth_spn.setSingleStep(10.0)
        self.smooth_spn.setDecimals(1)
        self.smooth_spn.setValue(100.0)
        self.smooth_spn.setEnabled(False)
        self.smooth_spn.setToolTip(
            "Largura da janela da média móvel. Deve ser de pelo menos 3 vezes a resolução de amostragem."
        )
        g.addWidget(self.smooth_spn, row, 4, 1, 6); row += 1

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        g.addWidget(sep2, row, 0, 1, 10); row += 1

        # Start / end labels
        g.addWidget(QtWidgets.QLabel("Rótulos:"), row, 0, 1, 4)
        self.start_label_edt = QtWidgets.QLineEdit("A")
        self.start_label_edt.setToolTip(f"Rótulo de início do perfil. Deixe em branco para não exibir.")
        g.addWidget(self.start_label_edt, row, 4, 1, 3)
        self.end_label_edt = QtWidgets.QLineEdit("A′")
        self.end_label_edt.setToolTip(f"Rótulo de fim do perfil. Deixe em branco para não exibir.")
        g.addWidget(self.end_label_edt, row, 7, 1, 3); row += 1

        # Appearance
        g.addWidget(QtWidgets.QLabel("Cor da linha do perfil:"), row, 0, 1, 4)
        self.color_btn = ColorButton("#000000")
        g.addWidget(self.color_btn, row, 4, 1, 6); row += 1

        self.grid_chk = QtWidgets.QCheckBox("Mostrar grade")
        self.grid_chk.setChecked(True)
        g.addWidget(self.grid_chk, row, 0, 1, 10); row += 1

        # OK
        self.ok_btn = QtWidgets.QPushButton("Gerar perfil")
        self.ok_btn.setEnabled(False)
        g.addWidget(self.ok_btn, row, 0, 1, 10)

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

        # The profile is shown at its real size; wider/taller profiles scroll
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(False)

        plot_layout.addWidget(self.save_btn, 0, 0)
        plot_layout.addWidget(self.back_btn, 0, 1)
        plot_layout.setColumnStretch(2, 1)
        plot_layout.addWidget(self.scroll, 1, 0, 1, 3)

        # ------------------------------------------------------------------ #
        # Signals
        # ------------------------------------------------------------------ #
        self.dem_browse_btn.clicked.connect(self.browse_dem)
        self.line_browse_btn.clicked.connect(self.browse_line)
        self.layer_cbx.currentTextChanged.connect(self.on_layer_changed)
        self.feature_cbx.currentIndexChanged.connect(self.on_feature_changed)
        self.smooth_chk.toggled.connect(self.smooth_spn.setEnabled)
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
            self.line_browse_btn.setEnabled(True)
            self.line_browse_btn.setToolTip("")
            self.line_edt.setPlaceholderText("Nenhum arquivo selecionado")
            if self.line_path is not None:
                self.reload_line()  # reproject the line to the new DEM CRS and validate it again
            self.check_ok_enabled()
        except Exception as e:
            handle_exception(e, "browse_dem()", parent=self)

    def browse_line(self) -> None:
        try:
            path, ext = show_file_dialog("Selecionar linha de perfil", VECTOR_FILTER, mode="open", parent=self)
            if not path:
                return
            if self.DEM is None:  # the button is disabled until a valid DEM is selected
                return

            # Read everything first, so that a failure leaves the previous selection untouched
            is_gpkg = ext == ".gpkg"
            layers = self.list_line_layers(path) if is_gpkg else []
            if is_gpkg and not layers:
                raise ValueError("O GeoPackage selecionado não possui camadas de linhas.")
            layer = layers[0] if is_gpkg else None
            candidates = self.read_line_candidates(path, layer)

            self.line_path = path
            self.current_layer = layer
            self.line_edt.setText(path)

            self.layer_cbx.blockSignals(True)
            self.layer_cbx.clear()
            self.layer_cbx.addItems(layers)
            self.layer_cbx.blockSignals(False)
            self.layer_lbl.setVisible(is_gpkg)
            self.layer_cbx.setVisible(is_gpkg)

            self.set_candidates(candidates)
        except Exception as e:
            handle_exception(e, "browse_line()", parent=self)

    @staticmethod
    def list_line_layers(path: str) -> list[str]:
        """Names of the layers of a GeoPackage that can hold lines (points, polygons and tables are left out)."""
        try:
            info = geopandas.list_layers(path)
            names, types = info["name"].tolist(), info["geometry_type"].tolist()
        except AttributeError:  # older geopandas: names only, geometry types unknown
            import fiona
            names = fiona.listlayers(path)
            types = ["Unknown"] * len(names)

        return [
            name for name, gtype in zip(names, types)
            if gtype is not None and not any(k in str(gtype) for k in ("Point", "Polygon"))
        ]

    def read_line_candidates(self, path: str, layer: str | None = None) -> list[LineCandidate]:
        """Reads a vector layer and returns a LineCandidate for every feature (valid or not)."""
        gdf = geopandas.read_file(path, layer=layer)

        if gdf.crs is None:
            raise ValueError("Line layer has no CRS defined.")
        if not gdf.crs.is_projected:
            raise ValueError("Line layer CRS must be projected.")
        if gdf.empty:
            raise ValueError("A camada não possui feições.")
        if gdf.crs != self.DEM.crs:
            gdf = gdf.to_crs(self.DEM.crs)

        candidates = []
        for number, geometry in enumerate(gdf.geometry, start=1):
            line, reason = check_feature_geometry(geometry)
            candidates.append(LineCandidate(number, line, reason))
        return candidates

    def set_candidates(self, candidates: list[LineCandidate], select_number: int | None = None) -> None:
        """Fills the feature combobox and selects a feature: `select_number` if given, else the first usable one."""
        self.line_candidates = candidates

        index = next((i for i, c in enumerate(candidates) if c.number == select_number), None)
        if index is None:
            index = next((i for i, c in enumerate(candidates) if c.line is not None), 0)

        self.feature_cbx.blockSignals(True)
        self.feature_cbx.clear()
        self.feature_cbx.addItems([
            f"Feição {c.number} — {c.line.length:.1f} m" if c.line is not None else f"Feição {c.number} — inválida"
            for c in candidates
        ])
        self.feature_cbx.setCurrentIndex(index)
        self.feature_cbx.blockSignals(False)

        self.refit_window()  # the layer row may have appeared/disappeared
        self.apply_selected_feature()

    def apply_selected_feature(self) -> None:
        """Makes the selected feature the profile line and refreshes what depends on it."""
        index = self.feature_cbx.currentIndex()
        if index < 0 or index >= len(self.line_candidates):
            return
        candidate = self.line_candidates[index]

        if candidate.line is None:
            self.line = None
            self.set_line_status("error", candidate.reason)
            return

        self.line = candidate.line
        self.auto_set_parameters(self.line_path)
        self.evaluate_line()

    def reload_line(self) -> None:
        """Reads the current layer again (e.g. after a new DEM was selected), keeping the selected feature."""
        try:
            index = self.feature_cbx.currentIndex()
            selected = self.line_candidates[index].number if 0 <= index < len(self.line_candidates) else None
            candidates = self.read_line_candidates(self.line_path, self.current_layer)
            self.set_candidates(candidates, select_number=selected)
        except Exception as e:
            handle_exception(e, "reload_line()", parent=self)

    def on_feature_changed(self, index: int) -> None:
        self.apply_selected_feature()

    def on_layer_changed(self, layer: str) -> None:
        if not layer or layer == self.current_layer or self.line_path is None:
            return
        try:
            candidates = self.read_line_candidates(self.line_path, layer)
            self.current_layer = layer
            self.set_candidates(candidates)
        except Exception as e:
            # Keep showing the layer that is actually loaded
            self.layer_cbx.blockSignals(True)
            self.layer_cbx.setCurrentText(self.current_layer)
            self.layer_cbx.blockSignals(False)
            handle_exception(e, "on_layer_changed()", parent=self)

    def refit_window(self) -> None:
        """The config page is fixed-size after the first page switch, so resize it when rows are shown/hidden."""
        if self.stack.currentIndex() == 0:
            self.setFixedSize(self.stack.currentWidget().sizeHint())

    def set_line_status(self, status: str | None, message: str = "") -> None:
        """Shows the validation result of the selected feature: None, "ok", "alert" or "error"."""
        icons = {None: Icons.CIRCLE, "ok": Icons.OK, "alert": Icons.ALERT, "error": Icons.NOT_OK}
        self.line_status = status
        self.feature_icn.setIcon(QtGui.QIcon(icons[status]))
        self.feature_icn.setToolTip(message)
        self.check_ok_enabled()

    def check_ok_enabled(self) -> None:
        self.ok_btn.setEnabled(
            bool(self.dem_edt.text()) and self.line is not None and self.line_status in ("ok", "alert")
        )

    # ------------------------------------------------------------------ #
    # Core slots
    # ------------------------------------------------------------------ #

    def on_ok(self) -> None:
        try:
            wait_cursor(True)

            scale     = self.scale_spn.value()
            vex       = self.vex_spn.value()
            res       = self.resolution_spn.value()
            color     = self.color_btn.color
            show_grid = self.grid_chk.isChecked()

            y_limits = [self.ymin_spn.value(), self.ymax_spn.value()]
            if y_limits[0] >= y_limits[1]:
                raise ValueError("A altitude mínima deve ser menor que a altitude máxima.")

            # Typing ' is easier than ′ — show the proper prime symbol
            start_label = self.start_label_edt.text().strip().replace("'", "′")
            end_label = self.end_label_edt.text().strip().replace("'", "′")

            smoothing = self.smooth_spn.value() if self.smooth_chk.isChecked() else None
            if smoothing is not None and smoothing < 3 * res:
                raise ValueError(
                    f"A janela de suavização deve ser de pelo menos 3 vezes a resolução de amostragem "
                    f"({3 * res:g} m)."
                )

            self.plot_profile(
                map_scale=scale, vertical_exaggeration=vex, sampling_resolution=res,
                y_limits=y_limits, color=color, show_grid=show_grid,
                start_label=start_label, end_label=end_label, smoothing_window=smoothing,
            )

            self.show_profile()
            self.stack.setCurrentIndex(1)
            self.fit_plot_page()
            center_on_screen(self)
            wait_cursor(False)

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
            # Raster images can't exceed matplotlib's pixel ceiling, so very wide profiles get a lower dpi (SVG has no limit)
            dpi = 300
            if ext != ".svg":
                fig_w, fig_h = self.fig.get_size_inches()
                dpi = min(300, int(MAX_FIG_PX / max(fig_w, fig_h)), int(math.sqrt(MAX_EXPORT_PIXELS / (fig_w * fig_h))))
                if dpi < 20:
                    raise ValueError(
                        "O perfil é grande demais para ser salvo como imagem. Salve em SVG ou use uma escala menor."
                    )

            wait_cursor(True)
            self.fig.savefig(path, dpi=dpi, format=ext.lstrip("."), bbox_inches="tight",
                             transparent=(ext == ".svg"))
            wait_cursor(False)

            message = "Perfil salvo com sucesso!"
            if dpi < 300:
                message += f"\nComo o perfil é muito largo, a resolução da imagem foi reduzida para {dpi} dpi. Salve em SVG para manter a qualidade."
            show_popup(message, parent=self)
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
            self.smooth_spn.setValue(5 * self.resolution_spn.value())

            # Default scale: the largest "round" scale at which the whole profile fits the window width
            INCHES_PER_METER = 39.3701
            usable_in = (self.plot_width - 20) / self.screen_dpi() - (0.6 + 0.2)  # minus the figure margins
            self.scale_spn.setValue(round_up_scale(length * INCHES_PER_METER / max(usable_in, 1.0)))
        except Exception as e:
            handle_exception(e, "auto_set_parameters()", parent=self)

    def evaluate_line(self) -> None:
        """Samples the DEM along the selected line, validates it (status icon) and, if it is usable,
        fills the altitude fields with the automatic limits."""
        if self.DEM is None or self.line is None:
            self.set_line_status(None)
            return
        try:
            wait_cursor(True)
            _, elevations, _ = self.sample_elevations(self.DEM, self.line, self.resolution_spn.value())
            wait_cursor(False)

            if numpy.isnan(elevations).all():
                self.set_line_status(
                    "error", "A linha do perfil não intersecta o MDT (nenhuma altitude válida foi amostrada)."
                )
                return

            elevations, has_nodata = fill_nodata_with_zero(elevations)
            low, high = automatic_altitude_limits(elevations)
            self.ymin_spn.setValue(math.floor(low))
            self.ymax_spn.setValue(math.ceil(high))

            if has_nodata:
                self.set_line_status(
                    "alert",
                    "A linha do perfil passa por seções sem dados do raster. \n"
                    "A elevação foi definida como zero nesses trechos.",
                )
            else:
                self.set_line_status("ok")
        except Exception as e:
            wait_cursor(False)
            self.set_line_status("error", f"Não foi possível amostrar o MDT ao longo da linha: {e}")

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
        elevations[~numpy.isfinite(elevations)] = numpy.nan  # NaN nodata values, infinities

        return distances, elevations, total_length

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def plot_profile(self, sampling_resolution: float, map_scale: int, vertical_exaggeration=1.0,
                     y_limits=None, color="black", show_grid=False,
                     start_label="A", end_label="A′", smoothing_window=None) -> None:

        # ---- Sample elevations along the line -------------------------------
        distances, elevations, total_length = self.sample_elevations(self.DEM, self.line, sampling_resolution)

        if numpy.isnan(elevations).all():
            raise ValueError("A linha de perfil não intersecta o MDT (nenhuma altitude válida foi amostrada).")

        if smoothing_window:
            elevations = smooth_profile(distances, elevations, smoothing_window)

        # Stretches without DEM data are plotted at elevation zero (the validation icon warns about it)
        elevations, _ = fill_nodata_with_zero(elevations)

        if not y_limits:
            y_limits = list(automatic_altitude_limits(elevations))

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

        if start_label:
            ax.text(0, 1 + y_offset, start_label, transform=ax.transAxes, fontsize=12, ha="center", va="bottom")
        if end_label:
            ax.text(1, 1 + y_offset, end_label, transform=ax.transAxes, fontsize=12, ha="center", va="bottom")

    def show_profile(self) -> None:
        """Displays the figure at its real size on the plot page."""
        canvas = ProfileCanvas(self.fig, self.screen_dpi(), self.screen().devicePixelRatio())
        self.scroll.setWidget(canvas)  # replaces (and deletes) the previous one

    def screen_dpi(self) -> float:
        """Logical pixels per inch of the screen the window is on, from the monitor's reported physical size."""
        if SCREEN_DPI_OVERRIDE:
            return float(SCREEN_DPI_OVERRIDE)
        screen = self.screen()
        width_mm = screen.physicalSize().width()
        if width_mm > 0:
            dpi = screen.geometry().width() / (width_mm / 25.4)
            if 40 <= dpi <= 400:  # ignore nonsense values reported by some drivers/projectors
                return dpi
        return 96.0

    def fit_plot_page(self) -> None:
        """Sizes the window to the profile, up to 90% of the screen; beyond that the profile scrolls."""
        canvas = self.scroll.widget()
        margin, toolbar_h, spacing = 5, 32, 5  # the plot page layout
        chrome_w = 2 * margin
        chrome_h = 2 * margin + toolbar_h + spacing
        scrollbar = self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)

        avail_w = self.plot_width - chrome_w
        avail_h = self.plot_height - chrome_h
        need_h = canvas.width() > avail_w
        need_v = canvas.height() + (scrollbar if need_h else 0) > avail_h
        if need_v and not need_h:
            need_h = canvas.width() + scrollbar > avail_w

        view_w = min(canvas.width() + (scrollbar if need_v else 0), avail_w)
        view_h = min(canvas.height() + (scrollbar if need_h else 0), avail_h)

        self.scroll.horizontalScrollBar().setValue(0)
        self.scroll.verticalScrollBar().setValue(0)
        self.setFixedSize(max(view_w, 300) + chrome_w, view_h + chrome_h)
