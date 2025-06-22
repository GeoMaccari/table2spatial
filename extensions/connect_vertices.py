from PyQt6 import QtCore, QtGui, QtWidgets
import os
import pandas
import geopandas
import shapely
from icecream import ic

from extensions.shared_functions import handle_exception, toggle_wait_cursor
from dialogs import show_file_dialog, show_input_dialog


class ConnectVerticesWindow(QtWidgets.QMainWindow):
    def __init__(self, parent: QtWidgets.QMainWindow, gdf: pandas.DataFrame):
        super(ConnectVerticesWindow, self).__init__(parent)
        self.parent = parent
        self.gdf = gdf

        self.setWindowTitle('Conectar pontos')
        self.setWindowIcon(QtGui.QIcon('icons/connect.png'))
        self.setMinimumWidth(270)

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setSpacing(5)
        self.layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        self.widget = QtWidgets.QWidget()
        self.widget.setLayout(self.layout)

        self.geometry_type_lbl = QtWidgets.QLabel("Tipo de geometria de saída:", self)
        self.geometry_type_cbx = QtWidgets.QComboBox(self)
        self.geometry_type_cbx.addItems(["Polígonos", "Linhas"])
        self.group_features_chk = QtWidgets.QCheckBox("Separar feições por:", self)
        self.group_features_cbx = QtWidgets.QComboBox(self)
        self.group_features_cbx.addItems(self.gdf.columns)
        self.group_features_cbx.setEnabled(False)
        self.order_vertices_chk = QtWidgets.QCheckBox("Ordenar vértices por:", self)
        self.order_vertices_cbx = QtWidgets.QComboBox(self)
        self.order_vertices_cbx.addItems(self.gdf.columns)
        self.order_vertices_cbx.setEnabled(False)
        self.close_lines_chk = QtWidgets.QCheckBox("Fechar linhas", self)
        self.close_lines_chk.setEnabled(False)
        self.ok_btn = QtWidgets.QPushButton("OK", self)

        self.layout.addWidget(self.geometry_type_lbl)
        self.layout.addWidget(self.geometry_type_cbx)
        self.layout.addWidget(self.group_features_chk)
        self.layout.addWidget(self.group_features_cbx)
        self.layout.addWidget(self.order_vertices_chk)
        self.layout.addWidget(self.order_vertices_cbx)
        self.layout.addWidget(self.close_lines_chk)
        self.layout.addWidget(self.ok_btn)

        self.setCentralWidget(self.widget)

        self.geometry_type_cbx.currentTextChanged.connect(self.geometry_type_selected)
        self.order_vertices_chk.checkStateChanged.connect(self.order_vertices_checkbox_toggled)
        self.group_features_chk.checkStateChanged.connect(self.group_features_checkbox_toggled)
        self.ok_btn.clicked.connect(self.ok_button_clicked)

    def geometry_type_selected(self):
        geometry_type = self.geometry_type_cbx.currentText()
        if geometry_type == "Linhas":
            self.close_lines_chk.setEnabled(True)
        else:
            self.close_lines_chk.setChecked(False)
            self.close_lines_chk.setEnabled(False)

    def order_vertices_checkbox_toggled(self):
        order_vertices = self.order_vertices_chk.isChecked()
        self.order_vertices_cbx.setEnabled(order_vertices)

    def group_features_checkbox_toggled(self):
        group_features = self.group_features_chk.isChecked()
        self.group_features_cbx.setEnabled(group_features)

    def ok_button_clicked(self):
        try:
            toggle_wait_cursor()

            geometry_type = self.geometry_type_cbx.currentText()
            order_vertices = self.order_vertices_chk.isChecked()
            group_features = self.group_features_chk.isChecked()
            order_column = None if not order_vertices else self.order_vertices_cbx.currentText()
            group_column = None if not group_features else self.group_features_cbx.currentText()
            close_lines = self.close_lines_chk.isChecked()

            features = self.make_features(geometry_type, order_column, group_column, close_lines)

            toggle_wait_cursor(False)

            self.save_features(features, geometry_type)
            self.close()
        except Exception as error:
            handle_exception(error, "rose_chart - filter_azimuth_columns()", "Ops! Ocorreu um erro!", self)

    def make_features(self, geometry_type: str, order_column: str | None = None, group_column: str | None = None, close_lines: bool = False):
        """
        Transforma um geodataframe de pontos/vértices em linhas ou polígonos.
        :param geometry_type: Tipo de geometria desejada para as feições de saída ("Polígonos" ou "Linhas").
        :param order_column: Coluna com dados de ordenamento dos vértices.
        :param group_column: Coluna com dados de agrupamento dos vértices.
        :param close_lines: Fechar ou não as linhas geradas.
        :return: geodataframe com as feições.
        """
        gdf = self.gdf.copy()
        features = []

        if group_column is None:
            if order_column is not None:
                gdf = gdf.sort_values(by=order_column)

            vertices = list(gdf.geometry)

            if geometry_type == "Polígonos":
                feature = shapely.geometry.Polygon(vertices)
            else:
                if close_lines:
                    vertices.append(vertices[0])
                feature = shapely.geometry.LineString(vertices)

            features.append(feature)
        else:
            for group, group_data in gdf.groupby(group_column):
                if order_column is not None:
                    group_data = group_data.sort_values(by=order_column)

                vertices = list(group_data.geometry)

                if geometry_type == "Polígonos":
                    feature = shapely.geometry.Polygon(vertices)
                else:
                    if close_lines:
                        vertices.append(vertices[0])
                    feature = shapely.geometry.LineString(vertices)

                features.append((group, feature))

        if group_column is None:
            features_gdf = geopandas.GeoDataFrame(geometry=features, crs=gdf.crs)
        else:
            features_gdf = geopandas.GeoDataFrame(features, columns=[group_column, "geometry"], crs=gdf.crs)

        return features_gdf

    def save_features(self, features, geometry_type):
        output_formats = (
            "Formatos suportados (*.gpkg *.geojson *.shp);;"
            "Geopackage (*.gpkg);;"
            "GeoJSON (*.geojson);;"
            "Shapefile (*.shp);;"
        )

        file_name = show_file_dialog(
            caption="Salvar arquivo", mode="save", parent=self, extension_filter=output_formats
        )

        if file_name == "":
            return

        _, file_extension = os.path.splitext(file_name)
        if not file_extension:
            file_name += ".geojson"

        if file_name.endswith(".gpkg"):
            layer_name = "poligonos" if geometry_type == "Polígonos" else "linhas"
            layer_name, ok_clicked = show_input_dialog(
                "Insira um nome para a camada:", "Nome da camada", layer_name, self
            )
            if not ok_clicked:
                return

            features.to_file(filename=file_name, layer=layer_name, driver="GPKG", encoding="utf-8")
        else:  # GeoJSON e Shapefile
            features.to_file(filename=file_name, encoding="utf-8")
