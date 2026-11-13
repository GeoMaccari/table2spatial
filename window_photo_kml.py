# -*- coding: utf-8 -*-
"""
window_photo_kml.py — Reads GPS metadata from photographs in a folder and
exports a KMZ file with each geotagged photo plotted on the map.

Dependencies:
    Pillow        >= 10.0
    pillow-heif   >= 0.13
    simplekml     >= 1.3
"""

import io
import os
import tempfile
import simplekml
from pathlib import Path
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from PyQt6 import QtCore, QtGui, QtWidgets

from shared import (
    Icons,
    handle_exception,
    show_file_dialog,
    show_popup,
    wait_cursor,
)

register_heif_opener()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heif", ".heic"}

# EXIF/TIFF spec tag IDs — fixed by the standard, will never change.
_TAG_GPSINFO = 34853   # GPSInfo IFD pointer inside the main EXIF IFD
_GPS_LAT_REF = 1       # "N" or "S"
_GPS_LAT     = 2       # (degrees, minutes, seconds)
_GPS_LON_REF = 3       # "E" or "W"
_GPS_LON     = 4       # (degrees, minutes, seconds)
_GPS_ALT_REF = 5       # 0 = above sea level, 1 = below  (may arrive as b'\x00'/b'\x01')
_GPS_ALT     = 6       # metres as float or IFDRational

# ---------------------------------------------------------------------------
# GPS extraction helpers
# ---------------------------------------------------------------------------

def _dms_to_decimal(dms, ref: str) -> float:
    """Convert a GPS DMS tuple from EXIF to a signed decimal degree.

    Handles IFDRational objects (Pillow ≥ 10), plain int/float tuples,
    and the case where seconds are already a float.
    """
    degrees, minutes, seconds = dms
    value = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    if str(ref).upper() in ("S", "W"):
        value = -value
    return value


def _gps_from_exif_dict(raw: dict) -> tuple[float, float, float | None] | None:
    """Parse a raw {tag_id: value} EXIF dict and return (lat, lon, alt) or None."""
    gps_info = raw.get(_TAG_GPSINFO)
    if not gps_info:
        return None

    lat_dms = gps_info.get(_GPS_LAT)
    lat_ref = gps_info.get(_GPS_LAT_REF, "N")
    lon_dms = gps_info.get(_GPS_LON)
    lon_ref = gps_info.get(_GPS_LON_REF, "E")

    if lat_dms is None or lon_dms is None:
        return None

    lat = _dms_to_decimal(lat_dms, lat_ref)
    lon = _dms_to_decimal(lon_dms, lon_ref)

    alt = None
    alt_raw = gps_info.get(_GPS_ALT)
    if alt_raw is not None:
        alt_ref = gps_info.get(_GPS_ALT_REF, 0)
        # AltitudeRef may arrive as int, b'\x00'/b'\x01', or str "0"/"1"
        if isinstance(alt_ref, (bytes, bytearray)):
            alt_ref = alt_ref[0] if alt_ref else 0
        alt = -float(alt_raw) if int(alt_ref) == 1 else float(alt_raw)

    return lat, lon, alt


def _extract_gps(path: str) -> tuple[float, float, float | None] | None:
    """Return (latitude, longitude, altitude_m) from a photo's EXIF, or None.

    Tries three strategies in order:
      1. img.getexif()       — public API, works for JPEG and TIFF (Pillow ≥ 6)
      2. img._getexif()      — legacy private JPEG-only method, fallback
      3. img.tag_v2/img.tag  — raw TIFF IFD tables (some TIFFs bypass getexif)
    """
    try:
        img = Image.open(path)

        try:
            exif_obj = img.getexif()
            if exif_obj:
                result = _gps_from_exif_dict(dict(exif_obj))
                if result is not None:
                    return result
        except Exception:
            pass

        try:
            raw = img._getexif()
            if raw:
                result = _gps_from_exif_dict(raw)
                if result is not None:
                    return result
        except Exception:
            pass

        for tag_attr in ("tag_v2", "tag"):
            try:
                tag_dict = getattr(img, tag_attr, None)
                if tag_dict:
                    result = _gps_from_exif_dict(dict(tag_dict))
                    if result is not None:
                        return result
            except Exception:
                pass

        return None

    except Exception:
        return None


def _make_thumbnail(path: str, max_px: int = 800) -> bytes:
    """Return JPEG bytes of a resized thumbnail, with EXIF orientation applied.

    Applies the EXIF Orientation tag before resizing so that portrait photos
    shot with the camera rotated are not saved sideways.
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=75)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# KMZ builder
# ---------------------------------------------------------------------------

def build_kmz(
    photo_paths: list[str],
    doc_name: str = "Fotografias",
    thumbnail_px: int = 800,
) -> tuple[simplekml.Kml, list[str], list[str], list[str]]:
    """Build a KMZ-ready Kml object from a list of photo paths.

    Each geotagged photo becomes a placemark with an embedded thumbnail.
    Photos without GPS metadata are collected in the skipped list.

    Returns:
        (kml, placed, skipped, tmp_files)
        placed    — basenames of photos successfully plotted.
        skipped   — basenames of photos with no GPS data.
        tmp_files — temporary thumbnail paths; delete these after savekmz().
    """
    kml = simplekml.Kml(name=doc_name)
    placed:    list[str] = []
    skipped:   list[str] = []
    tmp_files: list[str] = []

    for path in photo_paths:
        name   = os.path.basename(path)
        coords = _extract_gps(path)
        if coords is None:
            skipped.append(name)
            continue

        lat, lon, alt = coords

        pt = kml.newpoint(name=Path(path).stem)
        pt.coords      = [(lon, lat, alt if alt is not None else 0)]
        pt.altitudemode = "clampToGround"

        thumb_bytes = _make_thumbnail(path, max_px=thumbnail_px)
        # simplekml.addfile() requires a real path on disk, not bytes
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(thumb_bytes)
            tmp_path = tmp.name
        tmp_files.append(tmp_path)
        kml_href = kml.addfile(tmp_path)
        pt.description = f'<![CDATA[<img src="{kml_href}" width="400"/><br/>{name}]]>'

        placed.append(name)

    return kml, placed, skipped, tmp_files


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class PhotoKmlWindow(QtWidgets.QDialog):
    """Two-page dialog for exporting geotagged photos to a KMZ file.

    Page 0 — configuration: folder, options, save path.
    Page 1 — result summary: how many photos were placed and which were skipped.
    """

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowTitle("Plotar fotos em KMZ")
        self.setWindowIcon(QtGui.QIcon(Icons.PHOTO))
        self.setModal(False)

        self._tmp_files: list[str] = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Both pages are created up front; only one is in the layout at a time.
        self._config_page  = self._build_config_page()
        self._result_page  = self._build_result_page()

        root.addWidget(self._config_page)

    # ------------------------------------------------------------------ #
    # Page builders
    # ------------------------------------------------------------------ #

    def _build_config_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        cfg  = QtWidgets.QVBoxLayout(page)
        cfg.setSpacing(5)
        cfg.setContentsMargins(8, 8, 8, 8)

        # --- Source folder ---
        folder_grp  = QtWidgets.QGroupBox("Pasta de fotografias")
        folder_form = QtWidgets.QFormLayout(folder_grp)
        folder_form.setSpacing(5)

        folder_row = QtWidgets.QHBoxLayout()
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setPlaceholderText("Selecione a pasta...")
        self.folder_edit.setReadOnly(True)
        self.browse_btn = QtWidgets.QPushButton("Procurar…")
        folder_row.addWidget(self.folder_edit, stretch=1)
        folder_row.addWidget(self.browse_btn)
        folder_form.addRow("Pasta:", folder_row)

        self.recursive_chk = QtWidgets.QCheckBox("Incluir subpastas")
        self.recursive_chk.setChecked(True)
        folder_form.addRow(self.recursive_chk)

        cfg.addWidget(folder_grp)

        # --- Output options ---
        out_grp  = QtWidgets.QGroupBox("Opções de saída")
        out_form = QtWidgets.QFormLayout(out_grp)
        out_form.setSpacing(5)

        self.doc_name_edit = QtWidgets.QLineEdit("Fotografias")
        out_form.addRow("Nome da camada:", self.doc_name_edit)

        self.thumb_spn = QtWidgets.QSpinBox()
        self.thumb_spn.setRange(200, 2000)
        self.thumb_spn.setSingleStep(100)
        self.thumb_spn.setValue(800)
        self.thumb_spn.setSuffix(" px")
        out_form.addRow("Tamanho das miniaturas:", self.thumb_spn)

        cfg.addWidget(out_grp)

        # --- Run button ---
        self.run_btn = QtWidgets.QPushButton("Plotar")
        cfg.addWidget(self.run_btn)

        self.browse_btn.clicked.connect(self.on_browse)
        self.run_btn.clicked.connect(self.on_run)

        return page

    def _build_result_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        res  = QtWidgets.QVBoxLayout(page)
        res.setSpacing(6)
        res.setContentsMargins(8, 8, 8, 8)

        btn_row = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QToolButton()
        self.back_btn.setIcon(QtGui.QIcon(Icons.BACK))
        self.back_btn.setIconSize(QtCore.QSize(26, 26))
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.setToolTip("Voltar às configurações")
        btn_row.addWidget(self.back_btn)
        btn_row.addStretch()
        res.addLayout(btn_row)

        self.summary_lbl = QtWidgets.QLabel()
        self.summary_lbl.setWordWrap(True)
        res.addWidget(self.summary_lbl)

        self.skip_grp  = QtWidgets.QGroupBox("Fotos sem coordenadas GPS")
        skip_layout    = QtWidgets.QVBoxLayout(self.skip_grp)
        self.skip_list = QtWidgets.QListWidget()
        skip_layout.addWidget(self.skip_list)
        res.addWidget(self.skip_grp, stretch=1)

        self.back_btn.clicked.connect(self.on_back)

        return page

    # ------------------------------------------------------------------ #
    # Page switching
    # ------------------------------------------------------------------ #

    def _show_page(self, page: QtWidgets.QWidget) -> None:
        layout = self.layout()
        # Remove whatever is currently in the layout
        while layout.count():
            layout.takeAt(0).widget().hide()
        layout.addWidget(page)
        page.show()
        self.setFixedSize(page.sizeHint())

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _collect_photos(self, folder: str) -> list[str]:
        folder_path = Path(folder)
        if self.recursive_chk.isChecked():
            files = [
                str(p) for p in folder_path.rglob("*")
                if p.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        else:
            files = [
                str(p) for p in folder_path.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        return sorted(files)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def on_browse(self) -> None:
        path, _ = show_file_dialog("Selecionar pasta de fotografias", mode="dir", parent=self)
        if path:
            self.folder_edit.setText(path)

    def on_run(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            show_popup("Selecione uma pasta válida.", parent=self)
            return

        try:
            wait_cursor(True)
            photos = self._collect_photos(folder)
            if not photos:
                wait_cursor(False)
                show_popup(
                    "Nenhuma fotografia encontrada.\n"
                    "Verifique a pasta e a opção de subpastas.",
                    parent=self,
                )
                return
            wait_cursor(False)

            # Ask for save path before the heavy work
            path, ext = show_file_dialog(
                "Salvar arquivo KMZ",
                "KMZ (*.kmz)",
                mode="save",
                parent=self,
            )
            if not path:
                return
            if not ext:
                path += ".kmz"

            wait_cursor(True)
            kml, placed, skipped, tmp_files = build_kmz(
                photo_paths=photos,
                doc_name=self.doc_name_edit.text() or "Fotografias",
                thumbnail_px=self.thumb_spn.value(),
            )

            if not placed:
                wait_cursor(False)
                show_popup(
                    "Nenhuma fotografia continha dados GPS válidos.\n"
                    "Verifique se as imagens possuem metadados de localização.",
                    parent=self,
                )
                return

            kml.savekmz(path)
            for tmp in tmp_files:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            wait_cursor(False)

            show_popup("Fotos plotadas com sucesso!")

            # Populate result page and switch to it
            total = len(photos)
            self.summary_lbl.setText(
                f"<b>{len(placed)}</b> de <b>{total}</b> foto(s) "
                f"posicionada(s) no mapa."
            )
            self.skip_list.clear()
            self.skip_list.addItems(skipped)
            self.skip_grp.setVisible(bool(skipped))

            self._show_page(self._result_page)

        except Exception as e:
            wait_cursor(False)
            handle_exception(e, "on_run()", "Erro ao processar as fotografias.", self)

    def on_back(self) -> None:
        self._show_page(self._config_page)
