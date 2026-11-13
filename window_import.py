# -*- coding: utf-8 -*-

from PyQt6 import QtCore, QtGui, QtWidgets

from data_handler import DataHandler, CRS_DICT
from shared import Icons, wait_cursor, handle_exception, show_file_dialog, search_coordinates_column_by_name


class ImportWindow(QtWidgets.QDialog):
    """
    Lets the user pick an Excel/CSV file, configure coordinate columns and CRS,
    and emits `import_done` when finished.  The controller connects to this signal
    to receive the ready DataHandler.
    """

    import_done = QtCore.pyqtSignal(object)  # emits the DataHandler

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle("Importar tabela")
        self.setWindowIcon(QtGui.QIcon(Icons.IMPORT))
        self.setModal(True)
        self.setFixedSize(450, 350)

        self.model = DataHandler()
        self.is_csv = False
        self.building = False  # guard against cascade signals during setup

        # ------------------------------------------------------------------ #
        # Layout
        # ------------------------------------------------------------------ #
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        form = QtWidgets.QFormLayout()
        form.setSpacing(6)
        layout.addLayout(form)

        # File row
        file_row = QtWidgets.QHBoxLayout()
        self.file_edt = QtWidgets.QLineEdit()
        self.file_edt.setReadOnly(True)
        self.file_edt.setPlaceholderText("Nenhum arquivo selecionado")
        self.browse_btn = QtWidgets.QPushButton("Procurar…")
        self.browse_btn.clicked.connect(self.browse)
        file_row.addWidget(self.file_edt)
        file_row.addWidget(self.browse_btn)
        form.addRow("Arquivo:", file_row)

        # Sheet combo (hidden for CSVs)
        self.sheet_lbl = QtWidgets.QLabel("Planilha:")
        self.sheet_cbx = QtWidgets.QComboBox()
        form.addRow(self.sheet_lbl, self.sheet_cbx)

        # Separator
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        layout.addWidget(sep)

        # No-coordinates checkbox
        self.no_coords_chk = QtWidgets.QCheckBox("O arquivo não possui coordenadas")
        layout.addWidget(self.no_coords_chk)

        # Coordinate group
        self.coords_grp = QtWidgets.QGroupBox("Coordenadas")
        coords_layout = QtWidgets.QFormLayout(self.coords_grp)
        coords_layout.setSpacing(6)

        self.crs_cbx = QtWidgets.QComboBox()
        self.crs_cbx.addItems(sorted(CRS_DICT.keys()))
        self.crs_cbx.setCurrentText("SIRGAS 2000 (EPSG:4674)")
        self.crs_cbx.setEditable(True)
        self.crs_cbx.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        coords_layout.addRow("SRC:", self.crs_cbx)

        self.dms_chk = QtWidgets.QCheckBox("Formato GMS (GG°MM'SS.sss\")")
        coords_layout.addRow(self.dms_chk)

        # X / Y / Z rows with validation icon
        self.x_lbl, self.x_cbx, self.x_icn = self.make_coord_row(coords_layout, "X (longitude):")
        self.y_lbl, self.y_cbx, self.y_icn = self.make_coord_row(coords_layout, "Y (latitude):")
        self.z_lbl, self.z_cbx, self.z_icn = self.make_coord_row(coords_layout, "Z (altitude):")
        self.set_z_enabled(False)

        layout.addWidget(self.coords_grp)

        # Buttons
        btn_row = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_btn = btn_row.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setEnabled(False)
        btn_row.accepted.connect(self.accept_import)
        btn_row.rejected.connect(self.reject)
        layout.addWidget(btn_row)

        # ------------------------------------------------------------------ #
        # Signal wiring
        # ------------------------------------------------------------------ #
        self.sheet_cbx.currentTextChanged.connect(self.on_sheet_changed)
        self.no_coords_chk.checkStateChanged.connect(self.on_no_coords_toggled)
        self.crs_cbx.currentTextChanged.connect(self.on_crs_changed)
        self.dms_chk.clicked.connect(self.validate_coords)
        self.x_cbx.currentTextChanged.connect(self.validate_coords)
        self.y_cbx.currentTextChanged.connect(self.validate_coords)
        self.z_cbx.currentTextChanged.connect(self.validate_coords)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_coord_row(parent_layout, label_text):
        label = QtWidgets.QLabel(label_text)

        row_widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)

        cbx = QtWidgets.QComboBox()
        icn = QtWidgets.QPushButton()

        icn.setIcon(QtGui.QIcon(Icons.CIRCLE))
        icn.setFlat(True)
        icn.setFixedSize(24, 24)

        row.addWidget(cbx)
        row.addWidget(icn)

        parent_layout.addRow(label, row_widget)

        return label, cbx, icn

    def set_z_enabled(self, enabled: bool) -> None:
        self.z_cbx.setEnabled(enabled)
        self.z_icn.setEnabled(enabled)

    def fill_coord_combos(self) -> None:
        cols = list(self.model.gdf.columns)
        for cbx in (self.x_cbx, self.y_cbx, self.z_cbx):
            cbx.blockSignals(True)
            cbx.clear()
            cbx.addItems(cols)
            cbx.blockSignals(False)
        self.auto_select_xy()

    def auto_select_xy(self) -> None:
        crs_key = self.crs_cbx.currentText()
        crs_type = CRS_DICT[crs_key]["type"]
        for axis, cbx in (("x", self.x_cbx), ("y", self.y_cbx), ("z", self.z_cbx)):
            col = search_coordinates_column_by_name(axis, crs_type, self.model.gdf.columns)
            cbx.setCurrentText(col)

    def validate_coords(self) -> None:
        if self.building or not self.model.gdf is not None:
            return
        if self.no_coords_chk.isChecked():
            self.ok_btn.setEnabled(True)
            return

        crs_key = self.crs_cbx.currentText()
        dms = self.dms_chk.isChecked()

        try:
            valid_x, valid_y, valid_z = self.model.filter_coordinates_columns(crs_key, dms)
        except Exception:
            self.ok_btn.setEnabled(False)
            return

        crs_type = CRS_DICT[crs_key]["type"]
        is_3d = crs_type == "Geographic 3D CRS"

        x_ok = self.x_cbx.currentText() in valid_x
        y_ok = self.y_cbx.currentText() in valid_y
        z_ok = (self.z_cbx.currentText() in valid_z) if is_3d else True

        self.x_icn.setIcon(QtGui.QIcon(Icons.OK if x_ok else Icons.NOT_OK))
        self.y_icn.setIcon(QtGui.QIcon(Icons.OK if y_ok else Icons.NOT_OK))
        if is_3d:
            self.z_icn.setIcon(QtGui.QIcon(Icons.OK if z_ok else Icons.NOT_OK))

        self.ok_btn.setEnabled(x_ok and y_ok and z_ok)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def browse(self) -> None:
        path, ext = show_file_dialog(
            caption="Selecione uma tabela de entrada",
            extension_filter=(
                "Formatos suportados (*.xlsx *.xlsm *.csv *.ods);;"
                "Pasta de Trabalho do Excel (*.xlsx);;"
                "Pasta de Trabalho com Macro (*.xlsm);;"
                "CSV (*.csv);;"
                "OpenDocument Spreadsheet (*.ods)"
            ),
            mode="open",
            parent=self,
        )
        if not path:
            return

        try:
            wait_cursor(True)
            self.building = True
            self.is_csv = ext == ".csv"

            if self.is_csv:
                self.model.read_csv_file(path)
            else:
                self.model.read_excel_file(path)
                self.model.read_excel_sheet(0)

            self.file_edt.setText(path)

            # Sheet selector
            self.sheet_cbx.blockSignals(True)
            self.sheet_cbx.clear()
            if not self.is_csv:
                self.sheet_cbx.addItems(self.model.excel_file.sheet_names)
            self.sheet_lbl.setVisible(not self.is_csv)
            self.sheet_cbx.setVisible(not self.is_csv)
            self.sheet_cbx.blockSignals(False)

            # Reset no-coords
            self.no_coords_chk.setChecked(False)
            self.coords_grp.setEnabled(True)

            self.fill_coord_combos()
            self.on_crs_changed()

            self.building = False
            self.validate_coords()
            wait_cursor(False)

        except Exception as e:
            self.building = False
            handle_exception(e, "browse()", "Erro ao abrir o arquivo.", self)

    def on_sheet_changed(self) -> None:
        if self.building:
            return
        try:
            wait_cursor(True)
            self.model.read_excel_sheet(self.sheet_cbx.currentText())
            self.fill_coord_combos()
            self.validate_coords()
            wait_cursor(False)
        except Exception as e:
            handle_exception(e, "on_sheet_changed()", parent=self)

    def on_no_coords_toggled(self) -> None:
        no_coords = self.no_coords_chk.isChecked()
        self.coords_grp.setEnabled(not no_coords)
        self.ok_btn.setEnabled(no_coords if self.model.gdf is not None else False)

    def on_crs_changed(self) -> None:
        if self.building or self.model.gdf is None:
            return
        crs_key = self.crs_cbx.currentText()
        
        if crs_key not in CRS_DICT:
            self.ok_btn.setEnabled(False)
            return

        crs_type = CRS_DICT[crs_key]["type"]
        is_3d = crs_type == "Geographic 3D CRS"
        is_geographic = crs_type in ("Geographic 2D CRS", "Geographic 3D CRS")
        self.set_z_enabled(is_3d)
        if not is_3d:
            self.z_icn.setIcon(QtGui.QIcon("appdata/icons/circle.png"))
        if not is_geographic:
            self.dms_chk.setChecked(False)
        self.dms_chk.setEnabled(is_geographic)

        if crs_type == "Projected CRS":
            self.x_lbl.setText("X (easting):")
            self.y_lbl.setText("Y (northing):")
        else:
            self.x_lbl.setText("X (longitude):")
            self.y_lbl.setText("Y (latitude):")

        self.auto_select_xy()
        self.validate_coords()

    def accept_import(self) -> None:
        try:
            wait_cursor(True)

            if not self.no_coords_chk.isChecked():
                crs_key = self.crs_cbx.currentText()
                crs_type = CRS_DICT[crs_key]["type"]
                self.model.set_geometry(
                    crs_key=crs_key,
                    x_col=self.x_cbx.currentText(),
                    y_col=self.y_cbx.currentText(),
                    z_col=self.z_cbx.currentText() if crs_type == "Geographic 3D CRS" else None,
                    dms=self.dms_chk.isChecked(),
                )

            wait_cursor(False)
            self.import_done.emit(self.model)
            self.accept()

        except Exception as e:
            handle_exception(e, "accept_import()", "Erro ao importar a tabela.", self)
