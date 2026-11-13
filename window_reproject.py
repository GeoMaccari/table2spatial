# -*- coding: utf-8 -*-

from PyQt6 import QtCore, QtGui, QtWidgets

from data_handler import DataHandler, CRS_DICT
from shared import Icons, wait_cursor, handle_exception, show_popup


class ReprojectWindow(QtWidgets.QDialog):
    """
    Allows the user to reproject the loaded points to a different CRS,
    with an optional step to save the reprojected coordinates as table columns.
    Emits `reproject_done` with the updated DataHandler on success.
    """

    reproject_done = QtCore.pyqtSignal(object)

    def __init__(self, parent: QtWidgets.QWidget, model: DataHandler):
        super().__init__(parent)
        self.setWindowTitle("Reprojetar pontos")
        self.setWindowIcon(QtGui.QIcon(Icons.REPROJECT))
        self.setModal(True)

        self._model = model

        # ------------------------------------------------------------------ #
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        form = QtWidgets.QFormLayout()
        form.setSpacing(6)
        layout.addLayout(form)

        current_crs = model.crs_key or "Desconhecido"
        form.addRow("SRC atual:", QtWidgets.QLabel(current_crs))

        self.target_cbx = QtWidgets.QComboBox()
        self.target_cbx.addItems(sorted(CRS_DICT.keys()))
        self.target_cbx.setCurrentText("SIRGAS 2000 (EPSG:4674)")
        self.target_cbx.setEditable(True)
        self.target_cbx.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.target_cbx.setMaximumWidth(300)
        form.addRow("SRC de destino:", self.target_cbx)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        layout.addWidget(sep)

        self.save_chk = QtWidgets.QCheckBox("Salvar coordenadas reprojetadas como colunas na tabela")
        layout.addWidget(self.save_chk)

        self.coords_grp = QtWidgets.QGroupBox("Nomes das colunas")
        self.coords_grp.setEnabled(False)
        coords_form = QtWidgets.QFormLayout(self.coords_grp)
        coords_form.setSpacing(6)

        self.x_edt, self.x_icn = self.make_col_row(coords_form, "X:")
        self.y_edt, self.y_icn = self.make_col_row(coords_form, "Y:")
        self.z_edt, self.z_icn = self.make_col_row(coords_form, "Z:")
        self.x_edt.setText("X")
        self.y_edt.setText("Y")
        self.z_edt.setText("Z")
        self.set_z_enabled(False)

        layout.addWidget(self.coords_grp)

        btn_row = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_btn = btn_row.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        btn_row.accepted.connect(self.accept_reprojection)
        btn_row.rejected.connect(self.reject)
        layout.addWidget(btn_row)

        # ------------------------------------------------------------------ #
        self.save_chk.checkStateChanged.connect(self.update_state)
        self.target_cbx.currentTextChanged.connect(self.update_state)
        self.target_cbx.editTextChanged.connect(self.update_state)
        self.target_cbx.activated.connect(self.update_state)
        self.x_edt.textChanged.connect(self.update_state)
        self.y_edt.textChanged.connect(self.update_state)
        self.z_edt.textChanged.connect(self.update_state)

        self.update_state()
        
        self.adjustSize()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_col_row(parent_layout: QtWidgets.QFormLayout, label: str):
        row_widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        edt = QtWidgets.QLineEdit()
        icn = QtWidgets.QPushButton()
        icn.setIcon(QtGui.QIcon(Icons.CIRCLE))
        icn.setFlat(True)
        icn.setFixedSize(24, 24)
        row.addWidget(edt)
        row.addWidget(icn)
        parent_layout.addRow(label, row_widget)
        return edt, icn

    def set_z_enabled(self, enabled: bool) -> None:
        self.z_edt.setEnabled(enabled)
        self.z_icn.setEnabled(enabled)

    def validate_col_names(self) -> None:
        crs_key = self.target_cbx.currentText()
        if crs_key not in CRS_DICT:
            return
        is_3d = CRS_DICT[crs_key]["type"] == "Geographic 3D CRS"

        cols_to_check = [
            (self.x_edt, self.x_icn),
            (self.y_edt, self.y_icn),
        ]
        if is_3d:
            cols_to_check.append((self.z_edt, self.z_icn))

        all_ok = True
        for edt, icn in cols_to_check:
            name = edt.text().strip()
            if name == "":
                icn.setIcon(QtGui.QIcon(Icons.NOT_OK))
                icn.setToolTip("O nome não pode estar vazio.")
                all_ok = False
            elif name in self._model.gdf.columns:
                icn.setIcon(QtGui.QIcon(Icons.ALERT))
                icn.setToolTip("Esta coluna já existe — seus dados serão substituídos.")
            else:
                icn.setIcon(QtGui.QIcon(Icons.OK))
                icn.setToolTip("")

        return all_ok

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def update_state(self, *_) -> None:
        crs_key = self.target_cbx.currentText()
        crs_valid = crs_key in CRS_DICT
        save = self.save_chk.isChecked()
        is_3d = crs_valid and CRS_DICT[crs_key]["type"] == "Geographic 3D CRS"

        self.coords_grp.setEnabled(save)
        self.set_z_enabled(is_3d and save)

        if not save:
            for icn in (self.x_icn, self.y_icn, self.z_icn):
                icn.setIcon(QtGui.QIcon(Icons.CIRCLE))
                icn.setToolTip("")
            self.ok_btn.setEnabled(crs_valid)
            return

        if not is_3d:
            self.z_icn.setIcon(QtGui.QIcon(Icons.CIRCLE))
            self.z_icn.setToolTip("")

        cols_ok = self.validate_col_names()
        self.ok_btn.setEnabled(crs_valid and cols_ok)

    def accept_reprojection(self) -> None:
        try:
            wait_cursor(True)

            crs_key = self.target_cbx.currentText()
            self._model.reproject(crs_key)

            if self.save_chk.isChecked():
                x_col = self.x_edt.text().strip()
                y_col = self.y_edt.text().strip()
                self._model.gdf[x_col] = self._model.gdf.geometry.x
                self._model.gdf[y_col] = self._model.gdf.geometry.y

                if CRS_DICT[crs_key]["type"] == "Geographic 3D CRS":
                    z_col = self.z_edt.text().strip()
                    self._model.gdf[z_col] = self._model.gdf.geometry.z

                # Keep geometry at the end
                cols = [c for c in self._model.gdf.columns if c != "geometry"] + ["geometry"]
                self._model.gdf = self._model.gdf[cols]

            wait_cursor(False)
            show_popup("Pontos reprojetados com sucesso!", parent=self)
            self.reproject_done.emit(self._model)
            self.accept()

        except KeyError as e:
            handle_exception(e, "accept_reprojection()",
                             "SRC não reconhecido. Pressione Tab ao pesquisar pelo SRC para autocompletar ou "
                             "clique em uma das opções na caixa de seleção.",
                             self)
        except Exception as e:
            handle_exception(e, "accept_reprojection()", "Erro ao reprojetar.", self)
