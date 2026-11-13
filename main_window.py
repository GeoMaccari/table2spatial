# -*- coding: utf-8 -*-

import pandas
from PyQt6 import QtCore, QtGui, QtWidgets
from platform import platform

from data_handler import DataHandler, DTYPE_ICONS, DATETIME_FORMATS, get_dtype_key
from shared import (
    Icons, wait_cursor, handle_exception, show_popup,
    show_file_dialog, show_input_dialog, show_selection_dialog, show_question_dialog,
    center_on_parent,
)
from window_import import ImportWindow
from window_reproject import ReprojectWindow
from window_histogram import HistogramWindow
from window_rose_chart import RoseChartWindow
from window_stereogram import StereogramWindow
from window_photo_kml import PhotoKmlWindow
from window_connect_vertices import ConnectVerticesWindow
from window_topo_profile import TopoProfileWindow

OS = platform()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("table2spatial")
        self.setWindowIcon(QtGui.QIcon(Icons.GLOBE))
        self.setMinimumSize(425, 550)

        self.model: DataHandler | None = None

        # ------------------------------------------------------------------ #
        # Central widget + layout
        # ------------------------------------------------------------------ #
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 4)

        # ------------------------------------------------------------------ #
        # Toolbar
        # ------------------------------------------------------------------ #
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(2)

        self.btn_import    = self.make_toolbar_btn("Importar tabela de pontos", Icons.IMPORT, enabled=True)
        self.btn_merge     = self.make_toolbar_btn("Mesclar abas pelo campo em comum", Icons.MERGE)
        self.btn_reproject = self.make_toolbar_btn("Reprojetar para outro SRC", Icons.REPROJECT)
        self.btn_export    = self.make_toolbar_btn("Exportar como camada vetorial/tabela", Icons.EXPORT)
        self.btn_graph     = self.make_toolbar_btn("Criar gráfico", Icons.GRAPH, has_menu=True)
        self.btn_connect   = self.make_toolbar_btn("Conectar pontos", Icons.CONNECT)
        self.btn_photo_kml = self.make_toolbar_btn("Plotar fotos como KMZ", Icons.PHOTO, enabled=True)
        #self.btn_topo      = self.make_toolbar_btn("Gerar perfil topográfico", Icons.PROFILE, enabled=True)

        self.graph_menu = QtWidgets.QMenu(self)
        self.btn_graph.setMenu(self.graph_menu)
        self.btn_graph.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.action_stereogram  = self.graph_menu.addAction("Estereograma")
        self.action_rosediagram = self.graph_menu.addAction("Diagrama de roseta")
        self.action_histogram   = self.graph_menu.addAction("Histograma")

        for btn in (self.btn_import, self.btn_merge, self.btn_reproject, self.btn_export, self.btn_graph,
                    self.btn_connect, self.btn_photo_kml):   # self.btn_topo,
            toolbar.addWidget(btn)
        toolbar.addStretch()
        root.addLayout(toolbar)

        # ------------------------------------------------------------------ #
        # Column list
        # ------------------------------------------------------------------ #
        self.col_list = QtWidgets.QListWidget()
        self.col_list.setIconSize(QtCore.QSize(22, 22))
        self.col_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.col_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.col_list.customContextMenuRequested.connect(self.show_context_menu)
        root.addWidget(self.col_list)

        # ------------------------------------------------------------------ #
        # Status bar
        # ------------------------------------------------------------------ #
        self.status_lbl = QtWidgets.QLabel("Importe uma tabela de pontos.")
        self.status_lbl.setStyleSheet("font-size: 8pt;")
        root.addWidget(self.status_lbl)

        # ------------------------------------------------------------------ #
        # Context menu actions
        # ------------------------------------------------------------------ #
        self.ctx_menu = QtWidgets.QMenu(self)
        self.ctx_rename    = self.ctx_menu.addAction(QtGui.QIcon(Icons.RENAME), "Renomear")
        self.ctx_delete    = self.ctx_menu.addAction(QtGui.QIcon(Icons.DELETE), "Excluir")
        self.ctx_uniques   = self.ctx_menu.addAction(QtGui.QIcon(Icons.LIST), "Listar valores únicos")
        self.ctx_stats     = self.ctx_menu.addAction(QtGui.QIcon(Icons.INFO), "Estatísticas descritivas")
        self.ctx_histogram = self.ctx_menu.addAction(QtGui.QIcon(Icons.GRAPH), "Histograma")
        self.ctx_dtype     = self.ctx_menu.addAction(QtGui.QIcon(Icons.STRING), "Alterar tipo de dado")

        # ------------------------------------------------------------------ #
        # Row widgets tracked here (parallel to list items)
        # ------------------------------------------------------------------ #
        self.row_widgets: list[_ColumnRow] = []

        # ------------------------------------------------------------------ #
        # Signal wiring
        # ------------------------------------------------------------------ #
        self.btn_import.clicked.connect(self.on_import)
        self.btn_merge.clicked.connect(self.on_merge)
        self.btn_reproject.clicked.connect(self.on_reproject)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_connect.clicked.connect(self.on_connect_vertices)
        self.btn_photo_kml.clicked.connect(self.on_photo_kml)
        # self.btn_topo.clicked.connect(self.on_topo_profile)

        self.action_stereogram.triggered.connect(self.on_stereogram)
        self.action_rosediagram.triggered.connect(self.on_rose_chart)
        self.action_histogram.triggered.connect(self.on_histogram)
        self.ctx_rename.triggered.connect(self.on_rename)
        self.ctx_delete.triggered.connect(self.on_delete)
        self.ctx_uniques.triggered.connect(self.on_uniques)
        self.ctx_stats.triggered.connect(self.on_stats)
        self.ctx_histogram.triggered.connect(self.on_ctx_histogram)
        self.ctx_dtype.triggered.connect(self.on_dtype_changed)

    # ------------------------------------------------------------------ #
    # Factory helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_toolbar_btn(tooltip: str, icon: str, enabled: bool = False, has_menu: bool = False):
        btn = QtWidgets.QToolButton()
        btn.setIcon(QtGui.QIcon(icon))
        btn.setIconSize(QtCore.QSize(30, 30))
        btn.setAutoRaise(True)
        btn.setFixedSize(40, 40)
        btn.setToolTip(tooltip)
        btn.setEnabled(enabled)
        return btn

    # ------------------------------------------------------------------ #
    # Column list management
    # ------------------------------------------------------------------ #

    def refresh_column_list(self, select_row: int = -1) -> None:
        """Rebuild the column list from the current model."""
        if self.model is None:
            return

        self.col_list.clear()
        self.row_widgets.clear()

        for col_name, col_dtype in zip(self.model.gdf.columns, self.model.gdf.dtypes):
            row = _ColumnRow(col_name, str(col_dtype))
            self.row_widgets.append(row)

            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(400, 32))
            item.setIcon(row.icon())
            if str(col_dtype) == "geometry":
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)

            self.col_list.addItem(item)
            self.col_list.setItemWidget(item, row)

        self.col_list.setCurrentRow(select_row)

    def update_status(self) -> None:
        if self.model is None or self.model.gdf is None:
            self.status_lbl.setText("")
            return
        n = len(self.model.gdf.index)
        if self.model.has_geometry and self.model.gdf.crs is not None:
            crs_name = f"{self.model.gdf.crs.name} ({self.model.gdf.crs.type_name})"
            text = f"Pontos: {n}    SRC: {crs_name}"
        else:
            text = f"Pontos: {n}    (sem coordenadas)"
        self.status_lbl.setText(text)

    # ------------------------------------------------------------------ #
    # Toolbar slots
    # ------------------------------------------------------------------ #

    def on_import(self) -> None:
        dlg = ImportWindow(self)
        dlg.import_done.connect(self.apply_import)
        center_on_parent(dlg, self)
        dlg.exec()

    def apply_import(self, model: DataHandler) -> None:
        self.model = model
        has_excel = model.excel_file is not None
        multi_sheet = has_excel and len(model.excel_file.sheet_names) > 1

        self.btn_merge.setEnabled(multi_sheet)
        self.btn_reproject.setEnabled(model.has_geometry)
        self.btn_export.setEnabled(True)
        self.btn_graph.setEnabled(True)
        self.btn_connect.setEnabled(model.has_geometry)

        self.refresh_column_list()
        self.update_status()

    def on_merge(self) -> None:
        if self.model is None:
            return
        try:
            merge_col, ok = show_selection_dialog(
                message="Selecione a coluna identificadora para a mescla:",
                items=list(self.model.gdf.columns),
                title="Mesclar planilhas",
                parent=self,
            )
            if not ok:
                return

            wait_cursor(True)
            merged, skipped = self.model.merge_sheets(merge_col)
            wait_cursor(False)

            self.refresh_column_list()
            self.update_status()
            show_popup(
                f"Planilhas mescladas com sucesso usando a coluna '{merge_col}':\n"
                f"  Incluídas: {', '.join(merged)}\n"
                f"  Ignoradas: {', '.join(skipped) or 'nenhuma'}",
                parent=self,
            )
        except Exception as e:
            handle_exception(e, "on_merge()", "Erro ao mesclar planilhas.", self)

    def on_reproject(self) -> None:
        if self.model is None:
            return
        dlg = ReprojectWindow(self, self.model)
        dlg.reproject_done.connect(self.apply_reproject)
        center_on_parent(dlg, self)
        dlg.exec()

    def apply_reproject(self, model: DataHandler) -> None:
        self.model = model
        self.refresh_column_list()
        self.update_status()

    def on_export(self) -> None:
        if self.model is None:
            return
        try:
            if self.model.has_geometry:
                ext_filter = (
                    "Formatos suportados (*.gpkg *.geojson *.shp *.csv *.xlsx);;"
                    "Geopackage (*.gpkg);;GeoJSON (*.geojson);;Shapefile (*.shp);;"
                    "CSV (*.csv);;Excel (*.xlsx)"
                )
            else:
                ext_filter = (
                    "Formatos suportados (*.csv *.xlsx);;"
                    "CSV (*.csv);;Excel (*.xlsx)"
                )

            path, ext = show_file_dialog("Salvar arquivo", ext_filter, mode="save", parent=self)
            if not path:
                return
            if not ext:
                ext = ".gpkg" if self.model.has_geometry else ".csv"
                path += ext

            layer_name = "pontos"
            if ext == ".gpkg":
                layer_name, ok = show_input_dialog("Nome da camada:", "Nome da camada", layer_name, self)
                if not ok:
                    return

            wait_cursor(True)
            self.model.export(path, layer_name)
            wait_cursor(False)
            show_popup("Arquivo exportado com sucesso!", parent=self)

        except Exception as e:
            handle_exception(e, "on_export()", "Erro ao exportar.", self)

    def on_topo_profile(self) -> None:
        try:
            dlg = TopoProfileWindow(self)
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_topo_profile()", "Erro ao abrir o perfil topográfico.", self)

    def on_connect_vertices(self) -> None:
        if self.model is None or not self.model.has_geometry:
            return
        try:
            dlg = ConnectVerticesWindow(self, self.model.gdf)
            center_on_parent(dlg, self)
            dlg.exec()
        except Exception as e:
            handle_exception(e, "on_connect_vertices()", "Erro ao abrir a janela de conexão de pontos.", self)

    def on_stereogram(self) -> None:
        try:
            dlg = StereogramWindow(self, pandas.DataFrame(self.model.gdf))
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_stereogram()", "Erro ao abrir o estereograma.", self)

    def on_rose_chart(self) -> None:
        try:
            dlg = RoseChartWindow(self, pandas.DataFrame(self.model.gdf))
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_rose_chart()", "Erro ao abrir o diagrama de roseta.", self)

    def on_histogram(self) -> None:
        try:
            dlg = HistogramWindow(self, pandas.DataFrame(self.model.gdf))
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_histogram()", "Erro ao abrir o histograma.", self)

    def on_ctx_histogram(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return
        try:
            col = self.row_widgets[row].col_name
            dlg = HistogramWindow(self, pandas.DataFrame(self.model.gdf), col=col)
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_ctx_histogram()", "Erro ao abrir o histograma.", self)

    def on_photo_kml(self) -> None:
        try:
            dlg = PhotoKmlWindow(self)
            dlg.show()
            center_on_parent(dlg, self)
        except Exception as e:
            handle_exception(e, "on_photo_kml()", "Erro ao abrir a janela de fotos.", self)

    # ------------------------------------------------------------------ #
    # Context-menu slots
    # ------------------------------------------------------------------ #

    def show_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.col_list.itemAt(pos)
        if item is None or not item.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled:
            return
        self.col_list.setCurrentItem(item)
        row = self.col_list.row(item)
        dtype_key = get_dtype_key(self.row_widgets[row].col_dtype)
        self.ctx_stats.setEnabled(dtype_key in ("Integer", "Float"))
        self.ctx_histogram.setEnabled(dtype_key in ("Integer", "Float"))
        self.ctx_menu.exec(self.col_list.viewport().mapToGlobal(pos))

    def current_row_index(self) -> int:
        return self.col_list.currentRow()

    def on_rename(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return
        try:
            widget = self.row_widgets[row]
            old_name = widget.col_name
            new_name, ok = show_input_dialog("Novo nome para a coluna:", "Renomear coluna", old_name, self)
            if not ok or new_name == old_name:
                return
            if new_name in self.model.gdf.columns:
                raise ValueError("Este nome já está em uso por outra coluna.")
            wait_cursor(True)
            self.model.gdf.rename(columns={old_name: new_name}, inplace=True)
            wait_cursor(False)
            self.refresh_column_list(row)
        except Exception as e:
            handle_exception(e, "on_rename()", "Não foi possível renomear a coluna.", self)

    def on_delete(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return
        try:
            col = self.row_widgets[row].col_name
            answer = show_question_dialog(f"Excluir a coluna \"{col}\"?", self)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes.value:
                return
            wait_cursor(True)
            self.model.gdf.drop(columns=[col], inplace=True)
            wait_cursor(False)
            self.refresh_column_list(max(0, row - 1))
        except Exception as e:
            handle_exception(e, "on_delete()", "Não foi possível excluir a coluna.", self)

    def on_uniques(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return
        try:
            wait_cursor(True)
            col = self.row_widgets[row].col_name
            raw = self.model.gdf[col].astype("string").unique()
            has_na = any(pandas.isna(v) for v in raw)
            uniques = sorted(v for v in raw if not pandas.isna(v))
            wait_cursor(False)

            dlg = _UniquesDialog(col, uniques, has_na, self)
            center_on_parent(dlg, self)
            dlg.exec()
        except Exception as e:
            handle_exception(e, "on_uniques()", "Erro ao listar valores únicos.", self)

    def on_stats(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return
        try:
            wait_cursor(True)
            col = self.row_widgets[row].col_name
            s = self.model.gdf[col].dropna().astype(float)
            wait_cursor(False)
            dlg = _StatsDialog(col, s, self)
            center_on_parent(dlg, self)
            dlg.exec()
        except Exception as e:
            handle_exception(e, "on_stats()", "Erro ao calcular estatísticas.", self)

    def on_dtype_changed(self) -> None:
        row = self.current_row_index()
        if row < 0:
            return

        widget = self.row_widgets[row]
        col = widget.col_name

        try:
            dtype_options = list(DTYPE_ICONS.keys())
            current = get_dtype_key(widget.col_dtype)
            target, ok = show_selection_dialog(
                "Selecione o novo tipo de dado:", dtype_options,
                selected=dtype_options.index(current) if current in dtype_options else 0,
                title="Alterar tipo de dado", parent=self,
            )
            if not ok:
                return

            wait_cursor(True)
            kwargs = {}

            if target == "Boolean":
                wait_cursor(False)
                uniques = sorted(str(v) for v in self.model.gdf[col].unique() if not pandas.isna(v))
                uniques += ["<Células vazias>", "<Nenhum>"]
                true_key, ok = show_selection_dialog("Valor para Verdadeiro:", uniques, title="Conversão booleana")
                if not ok:
                    return
                false_key, ok = show_selection_dialog("Valor para Falso:", uniques, title="Conversão booleana")
                if not ok:
                    return
                kwargs = {"true_key": true_key, "false_key": false_key}
                wait_cursor(True)

            elif target == "Datetime":
                wait_cursor(False)
                fmt, ok = show_selection_dialog(
                    "Formato de data/hora:", list(DATETIME_FORMATS.keys()),
                    title="Conversão para Datetime", parent=self,
                )
                if not ok:
                    return
                kwargs = {"datetime_format": fmt}
                wait_cursor(True)

            self.model.change_column_dtype(col, target, **kwargs)
            wait_cursor(False)
            self.refresh_column_list(row)

        except Exception as e:
            self.refresh_column_list(row)
            handle_exception(e, "on_dtype_changed()", "Não foi possível converter o tipo de dado.", self)


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------

class _ColumnRow(QtWidgets.QWidget):
    """A single row in the column list: icon + label showing column name and current dtype."""

    def __init__(self, col_name: str, col_dtype: str):
        super().__init__()
        self.col_name = col_name
        self.col_dtype = col_dtype

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 12, 0)

        lbl_name = QtWidgets.QLabel(col_name)
        lbl_name.setMinimumWidth(160)
        layout.addWidget(lbl_name, stretch=1)

        dtype_key = "POINT" if col_dtype == "geometry" else get_dtype_key(col_dtype)
        lbl_dtype = QtWidgets.QLabel(dtype_key)
        lbl_dtype.setStyleSheet("color: gray; font-size: 8pt;")
        layout.addWidget(lbl_dtype)

    def icon(self) -> QtGui.QIcon:
        if self.col_dtype == "geometry":
            return QtGui.QIcon(Icons.GEOMETRY)
        key = get_dtype_key(self.col_dtype)
        return QtGui.QIcon(DTYPE_ICONS.get(key, Icons.UNKNOWN))


class _UniquesDialog(QtWidgets.QDialog):
    """Simple dialog listing unique values of a column."""

    def __init__(self, col_name: str, values: list, has_na: bool, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"Valores únicos — {col_name}")
        self.setWindowIcon(QtGui.QIcon(Icons.LIST))
        self.setMinimumSize(280, 300)

        layout = QtWidgets.QVBoxLayout(self)
        lst = QtWidgets.QListWidget()
        lst.addItems(values)
        layout.addWidget(lst)

        if has_na:
            layout.addWidget(QtWidgets.QLabel("Obs.: a coluna contém células vazias/nulas."))

        close_btn = QtWidgets.QPushButton("Fechar")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

class _StatsDialog(QtWidgets.QDialog):
    """Displays descriptive statistics for a numeric column."""

    def __init__(self, col_name: str, series: pandas.Series, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"Estatísticas — {col_name}")
        self.setWindowIcon(QtGui.QIcon(Icons.INFO))

        n        = len(series)
        n_total  = len(parent.model.gdf[col_name])
        n_null   = n_total - n
        mean     = series.mean()
        median   = series.median()
        std      = series.std()
        vmin     = series.min()
        vmax     = series.max()
        q1       = series.quantile(0.25)
        q3       = series.quantile(0.75)

        def fmt(v) -> str:
            return f"{v:.6g}"

        lines = [
            f"n válidos:   {n}",
            f"n nulos:     {n_null}",
            f"",
            f"Mínimo:      {fmt(vmin)}",
            f"Máximo:      {fmt(vmax)}",
            f"Amplitude:   {fmt(vmax - vmin)}",
            f"",
            f"Média:       {fmt(mean)}",
            f"Mediana:     {fmt(median)}",
            f"Desvio pad.: {fmt(std)}",
            f"",
            f"Q1 (25%):    {fmt(q1)}",
            f"Q3 (75%):    {fmt(q3)}",
            f"IQR:         {fmt(q3 - q1)}",
        ]

        layout = QtWidgets.QVBoxLayout(self)

        text = QtWidgets.QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        text.setFont(QtGui.QFont("Courier New", 9))
        text.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        text.setFixedSize(220, 280)
        layout.addWidget(text)

        ok_btn = QtWidgets.QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        layout.addWidget(ok_btn)

        self.setFixedSize(self.sizeHint())
