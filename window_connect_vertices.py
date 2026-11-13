# -*- coding: utf-8 -*-

import os

import geopandas
import pandas
import shapely.geometry as sg
from pandas.api.types import is_categorical_dtype, is_datetime64_any_dtype, is_timedelta64_dtype
from PyQt6 import QtGui, QtWidgets

from shared import Icons, wait_cursor, handle_exception, show_file_dialog, show_input_dialog, show_popup

POLYGONS = "Polígonos"
LINES = "Linhas"

MIN_VERTICES = {POLYGONS: 3, LINES: 2}


# ---------------------------------------------------------------------------
# Geometry building
# ---------------------------------------------------------------------------

def build_features(
    gdf: geopandas.GeoDataFrame,
    geometry_type: str,
    order_column: str | None = None,
    group_column: str | None = None,
    close_lines: bool = False,
) -> tuple[geopandas.GeoDataFrame, list[str]]:
    """
    Connects the points of a GeoDataFrame into lines or polygons.

    :param gdf: GeoDataFrame of points.
    :param geometry_type: POLYGONS or LINES.
    :param order_column: Column that defines the vertex order. None keeps the table order.
    :param group_column: Column used to split the points into separate features. None builds a single feature.
    :param close_lines: Closes the line by repeating the first vertex (ignored for polygons).
    :return: (features GeoDataFrame, labels of the groups skipped for having too few vertices)
    """
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    min_vertices = MIN_VERTICES[geometry_type]

    def make_geometry(points: geopandas.GeoSeries):
        coords = [(p.x, p.y, p.z) if p.has_z else (p.x, p.y) for p in points]
        if len(coords) < min_vertices:
            return None
        if geometry_type == POLYGONS:
            return sg.Polygon(coords)
        if close_lines:
            coords.append(coords[0])
        return sg.LineString(coords)

    rows, skipped = [], []

    if group_column is None:
        if order_column is not None:
            gdf = gdf.sort_values(by=order_column, kind="stable")
        geom = make_geometry(gdf.geometry)
        if geom is None:
            skipped.append("(todos os pontos)")
        else:
            rows.append((1, geom))
        out = geopandas.GeoDataFrame(rows, columns=["id", "geometry"], geometry="geometry", crs=gdf.crs)
    else:
        for group, group_data in gdf.groupby(group_column, sort=False):
            if order_column is not None:
                group_data = group_data.sort_values(by=order_column, kind="stable")
            geom = make_geometry(group_data.geometry)
            if geom is None:
                skipped.append(str(group))
            else:
                rows.append((group, geom))
        out = geopandas.GeoDataFrame(rows, columns=[group_column, "geometry"], geometry="geometry", crs=gdf.crs)

    return out, skipped


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ConnectVerticesWindow(QtWidgets.QDialog):
    """
    Connects the imported points into lines or polygons and saves the result as a vector layer.
    Does not modify the model.
    """

    def __init__(self, parent: QtWidgets.QWidget, gdf: geopandas.GeoDataFrame):
        super().__init__(parent)
        self.setWindowTitle("Conectar pontos")
        self.setWindowIcon(QtGui.QIcon(Icons.CONNECT))
        self.setModal(True)
        self.setMinimumWidth(300)

        self.gdf = gdf
        columns = [c for c in gdf.columns if c != gdf.geometry.name]

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Output ---
        out_grp = QtWidgets.QGroupBox("Saída")
        out_form = QtWidgets.QFormLayout(out_grp)
        out_form.setSpacing(5)

        self.type_cbx = QtWidgets.QComboBox()
        self.type_cbx.addItems([POLYGONS, LINES])
        out_form.addRow("Tipo de geometria:", self.type_cbx)

        self.close_chk = QtWidgets.QCheckBox("Fechar linhas")
        self.close_chk.setEnabled(False)
        out_form.addRow(self.close_chk)

        layout.addWidget(out_grp)

        # --- Vertices ---
        vert_grp = QtWidgets.QGroupBox("Vértices")
        vert_form = QtWidgets.QFormLayout(vert_grp)
        vert_form.setSpacing(5)

        self.group_chk = QtWidgets.QCheckBox("Separar feições por:")
        self.group_cbx = QtWidgets.QComboBox()
        self.group_cbx.addItems(columns)
        self.group_cbx.setEnabled(False)
        vert_form.addRow(self.group_chk)
        vert_form.addRow(self.group_cbx)

        self.order_chk = QtWidgets.QCheckBox("Ordenar vértices por:")
        self.order_cbx = QtWidgets.QComboBox()
        self.order_cbx.addItems(columns)
        self.order_cbx.setEnabled(False)
        vert_form.addRow(self.order_chk)
        vert_form.addRow(self.order_cbx)

        layout.addWidget(vert_grp)

        # --- Buttons ---
        btn_row = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btn_row.accepted.connect(self.on_ok)
        btn_row.rejected.connect(self.reject)
        layout.addWidget(btn_row)

        # --- Signals ---
        self.type_cbx.currentTextChanged.connect(self.on_type_changed)
        self.group_chk.toggled.connect(self.group_cbx.setEnabled)
        self.order_chk.toggled.connect(self.order_cbx.setEnabled)

        # Grouping/ordering need at least one attribute column
        if not columns:
            self.group_chk.setEnabled(False)
            self.order_chk.setEnabled(False)

        self.adjustSize()

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def on_type_changed(self) -> None:
        is_line = self.type_cbx.currentText() == LINES
        self.close_chk.setEnabled(is_line)
        if not is_line:
            self.close_chk.setChecked(False)

    def on_ok(self) -> None:
        try:
            wait_cursor(True)
            geometry_type = self.type_cbx.currentText()
            features, skipped = build_features(
                self.gdf,
                geometry_type=geometry_type,
                order_column=self.order_cbx.currentText() if self.order_chk.isChecked() else None,
                group_column=self.group_cbx.currentText() if self.group_chk.isChecked() else None,
                close_lines=self.close_chk.isChecked(),
            )
            wait_cursor(False)

            if features.empty:
                raise ValueError(
                    f"Nenhuma feição pôde ser criada: são necessários ao menos "
                    f"{MIN_VERTICES[geometry_type]} pontos por feição."
                )

            if not self.save_features(features, geometry_type):
                return

            msg = f"{len(features)} feição(ões) salva(s) com sucesso!"
            if skipped:
                msg += (
                    f"\n\nGrupos ignorados por terem poucos pontos "
                    f"(mín. {MIN_VERTICES[geometry_type]}): {', '.join(skipped)}."
                )
            show_popup(msg, parent=self)
            self.accept()

        except Exception as e:
            handle_exception(e, "on_ok()", "Erro ao conectar os pontos.", self)

    # ------------------------------------------------------------------ #
    # Saving
    # ------------------------------------------------------------------ #

    def save_features(self, features: geopandas.GeoDataFrame, geometry_type: str) -> bool:
        """Asks for a path and writes the layer. Returns False if the user cancelled."""
        path, ext = show_file_dialog(
            "Salvar camada",
            "Formatos suportados (*.gpkg *.geojson *.shp);;Geopackage (*.gpkg);;GeoJSON (*.geojson);;Shapefile (*.shp)",
            mode="save",
            parent=self,
        )
        if not path:
            return False
        if not ext:
            ext = ".gpkg"
            path += ext

        layer_name = "poligonos" if geometry_type == POLYGONS else "linhas"
        if ext == ".gpkg":
            layer_name, ok = show_input_dialog("Nome da camada:", "Nome da camada", layer_name, self)
            if not ok:
                return False

        # Convert the dtypes the vector formats can't store (the group column is the only attribute)
        for col in features.columns.drop(features.geometry.name):
            dtype = features[col].dtype
            if is_categorical_dtype(dtype) or is_timedelta64_dtype(dtype) or (
                    ext == ".shp" and is_datetime64_any_dtype(dtype)):
                features[col] = features[col].astype("string")

        wait_cursor(True)
        if ext == ".gpkg":
            features.to_file(path, layer=layer_name, driver="GPKG", encoding="utf-8")
        else:  # GeoJSON and Shapefile
            features.to_file(path, encoding="utf-8")
        wait_cursor(False)
        return True