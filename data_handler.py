# -*- coding: utf-8 -*-

import io
import csv
import json
import os
import re
from pathlib import Path

import geopandas
import pandas
import pyproj
from icecream import ic
from pandas.api.types import (
    is_bool_dtype,
    is_categorical_dtype,
    is_datetime64_any_dtype,
    is_float_dtype,
    is_integer_dtype,
    is_string_dtype,
    is_timedelta64_dtype,
)


geopandas.options.io_engine = "fiona"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DTYPE_ICONS = {
    "String":   "appdata/icons/string.png",
    "Integer":  "appdata/icons/integer.png",
    "Float":    "appdata/icons/float.png",
    "Boolean":  "appdata/icons/boolean.png",
    "Datetime": "appdata/icons/datetime.png",
}

DATETIME_FORMATS = {
    "DD/MM/YYYY":           "%d/%m/%Y",
    "YYYY/MM/DD":           "%Y/%m/%d",
    "MM/DD/YYYY":           "%m/%d/%Y",
    "DD-MM-YYYY":           "%d-%m-%Y",
    "YYYY-MM-DD":           "%Y-%m-%d",
    "MM-DD-YYYY":           "%m-%d-%Y",
    "YYYY/MM/DD HH:MM:SS":  "%Y/%m/%d %H:%M:%S",
    "MM/DD/YYYY HH:MM:SS":  "%m/%d/%Y %H:%M:%S",
    "DD/MM/YYYY HH:MM:SS":  "%d/%m/%Y %H:%M:%S",
    "DD-MM-YYYY HH:MM:SS":  "%d-%m-%Y %H:%M:%S",
    "YYYY-MM-DD HH:MM:SS":  "%Y-%m-%d %H:%M:%S",
    "MM-DD-YYYY HH:MM:SS":  "%m-%d-%Y %H:%M:%S",
}


def get_dtype_key(dtype) -> str:
    """Maps a pandas dtype to one of the canonical keys used in DTYPE_ICONS."""
    if is_string_dtype(dtype) or is_categorical_dtype(dtype) or is_timedelta64_dtype(dtype):
        return "String"
    if is_integer_dtype(dtype):
        return "Integer"
    if is_float_dtype(dtype):
        return "Float"
    if is_bool_dtype(dtype):
        return "Boolean"
    if is_datetime64_any_dtype(dtype):
        return "Datetime"
    return "Unknown"


def build_crs_database(json_path: str = "appdata/crs_database.json") -> dict:
    """
    Loads the CRS dictionary from a cached JSON file, or builds it from pyproj
    and saves it to disk on first run.
    """
    path = Path(json_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    crs_type_map = {
        "PJType.GEOGRAPHIC_2D_CRS": "Geographic 2D CRS",
        "PJType.GEOGRAPHIC_3D_CRS": "Geographic 3D CRS",
        "PJType.PROJECTED_CRS":     "Projected CRS",
    }

    crs_db = pyproj.database.query_crs_info(
        pj_types=("GEOGRAPHIC_2D_CRS", "PROJECTED_CRS", "GEOGRAPHIC_3D_CRS")
    )

    crs_dict = {
        f"{info.name} "
        f"{'(3D) ' if crs_type_map[str(info.type)] == 'Geographic 3D CRS' else ''}"
        f"({info.auth_name}:{info.code})": {
            "name":      info.name,
            "auth_name": info.auth_name,
            "code":      info.code,
            "type":      crs_type_map[str(info.type)],
        }
        for info in crs_db
        if not info.auth_name.startswith("IAU")
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(crs_dict, f, ensure_ascii=False)

    return crs_dict


CRS_DICT: dict = build_crs_database()


# ---------------------------------------------------------------------------
# DataHandler
# ---------------------------------------------------------------------------

class DataHandler:
    """Holds the active GeoDataFrame and exposes all read/write/transform operations."""

    def __init__(self):
        self.excel_file: pandas.ExcelFile | None = None
        self.gdf: geopandas.GeoDataFrame | None = None
        self.crs_key: str | None = None
        self.has_geometry: bool = False

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_excel_file(self, path: str) -> None:
        with open(path, "rb") as f:
            buffer = io.BytesIO(f.read())
        self.excel_file = pandas.ExcelFile(buffer)

    def read_excel_sheet(self, sheet: str | int) -> None:
        df = self.process_data(self.excel_file.parse(sheet_name=sheet))
        self.gdf = geopandas.GeoDataFrame(df)

    def read_csv_file(self, path: str) -> None:
        with open(path, "r") as f:
            sep = str(csv.Sniffer().sniff(f.read(4096)).delimiter)
        decimal = "." if sep == "," else ","
        df = self.process_data(pandas.read_csv(path, delimiter=sep, decimal=decimal))
        self.gdf = geopandas.GeoDataFrame(df)
        self.excel_file = None

    @staticmethod
    def process_data(df: pandas.DataFrame) -> pandas.DataFrame:
        """Strip unnamed columns, drop empty rows, enforce string column names."""
        df.columns = df.columns.astype(str)
        df = df.drop(columns=[c for c in df.columns if "Unnamed" in c])
        df = df.dropna(how="all", axis="index")
        if len(df.index) == 0:
            raise IndexError("A tabela selecionada está vazia ou contém apenas cabeçalhos.")
        return df

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def filter_coordinates_columns(
        self, crs_key: str, dms_format: bool = False
    ) -> tuple[list, list, list]:
        """Returns (valid_x_cols, valid_y_cols, valid_z_cols) for the given CRS."""
        z_columns = self.gdf.select_dtypes(include="number").columns.tolist()

        if dms_format:
            x_cols, y_cols = self.filter_dms_columns()
            return x_cols, y_cols, z_columns

        crs = pyproj.CRS.from_authority(CRS_DICT[crs_key]["auth_name"], CRS_DICT[crs_key]["code"])
        if CRS_DICT[crs_key]["type"] in ("Geographic 2D CRS", "Geographic 3D CRS"):
            x_min, y_min, x_max, y_max = crs.area_of_use.bounds
        else:
            t = pyproj.Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
            x_min, y_min, x_max, y_max = t.transform_bounds(*crs.area_of_use.bounds)

        x_cols, y_cols = [], []
        for col in self.gdf.columns:
            try:
                vals = self.gdf[col].replace(",", ".", regex=True).astype(float).dropna()
                if vals.between(y_min, y_max).all():
                    y_cols.append(col)
                if vals.between(x_min, x_max).all():
                    x_cols.append(col)
            except (ValueError, TypeError):
                continue

        return x_cols, y_cols, z_columns

    def filter_dms_columns(self) -> tuple[list, list]:
        y_pat = re.compile(r"^(\d{1,2})([°º])(\d{1,2})([''′])(\d{1,2}([.,]\d+)?)([\"”″])([NSns])$")
        x_pat = re.compile(r"^(\d{1,3})([°º])(\d{1,2})([''′])(\d{1,2}([.,]\d+)?)([\"”″])([EWOLewol])$")
        x_cols, y_cols = [], []
        for col in self.gdf.columns:
            x_ok = y_ok = True
            for val in self.gdf[col].values:
                v = str(val).replace(" ", "")
                if x_pat.match(v) is None:
                    x_ok = False
                if y_pat.match(v) is None:
                    y_ok = False
                if not x_ok and not y_ok:
                    break
            if x_ok:
                x_cols.append(col)
            if y_ok:
                y_cols.append(col)
        return x_cols, y_cols

    def set_geometry(self, crs_key: str, x_col: str, y_col: str, z_col: str | None = None, dms: bool = False) -> None:
        """Build point geometry and assign CRS to the GeoDataFrame."""
        import shapely.geometry as sg

        crs = pyproj.CRS.from_authority(CRS_DICT[crs_key]["auth_name"], CRS_DICT[crs_key]["code"])

        def parse_dms(value: str) -> float:
            v = str(value).replace(" ", "")
            parts = re.split(r"[^\d\w]+", v)
            deg = int(parts[0])
            mins = int(parts[1])
            secs = float(parts[2] + ("." + parts[3] if len(parts) > 3 and parts[3].isdigit() else ""))
            direction = re.search(r"[NSEWOLnsewol]", v)
            sign = -1 if direction and direction.group().upper() in "SWO" else 1
            return sign * (deg + mins / 60 + secs / 3600)

        if dms:
            xs = self.gdf[x_col].apply(parse_dms)
            ys = self.gdf[y_col].apply(parse_dms)
        else:
            xs = self.gdf[x_col].replace(",", ".", regex=True).astype(float)
            ys = self.gdf[y_col].replace(",", ".", regex=True).astype(float)

        if z_col:
            zs = self.gdf[z_col].replace(",", ".", regex=True).astype(float)
            geoms = [sg.Point(x, y, z) for x, y, z in zip(xs, ys, zs)]
        else:
            geoms = [sg.Point(x, y) for x, y in zip(xs, ys)]

        self.gdf = geopandas.GeoDataFrame(self.gdf, geometry=geoms, crs=crs)
        self.crs_key = crs_key
        self.has_geometry = True

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def merge_sheets(self, merge_column: str) -> tuple[list, list]:
        """Merges all sheets of the open ExcelFile on the given column."""
        dfs, merged, skipped, dtypes = [], [], [], []

        for sheet in self.excel_file.sheet_names:
            if sheet == self.excel_file.sheet_names[0]:
                df = pandas.DataFrame(self.gdf).drop(columns=["geometry"], errors="ignore")
            else:
                df = self.excel_file.parse(sheet_name=sheet)

            if merge_column not in df.columns:
                skipped.append(sheet)
                continue

            if df[merge_column].duplicated().any():
                raise ValueError(f"A coluna '{merge_column}' possui valores duplicados na planilha '{sheet}'.")

            try:
                dfs.append(self.process_data(df))
                merged.append(sheet)
                dtypes.append(df[merge_column].dtype)
            except IndexError:
                skipped.append(sheet)

        if len(set(str(d) for d in dtypes)) > 1:
            for df in dfs:
                df[merge_column] = df[merge_column].astype(str)

        result = dfs[0]
        for df in dfs[1:]:
            result = pandas.merge(result, df, how="outer", on=merge_column)

        # Keep merge_column first
        cols = [merge_column] + [c for c in result.columns if c != merge_column]
        result = result[cols]

        self.gdf = geopandas.GeoDataFrame(
            result,
            geometry=None if not self.has_geometry else self.gdf.geometry,
            crs=None if not self.has_geometry else self.gdf.crs,
        )
        return merged, skipped

    # ------------------------------------------------------------------
    # Reprojection
    # ------------------------------------------------------------------

    def reproject(self, target_crs_key: str) -> None:
        crs = pyproj.CRS.from_authority(CRS_DICT[target_crs_key]["auth_name"], CRS_DICT[target_crs_key]["code"])
        self.gdf = self.gdf.to_crs(crs=crs)
        self.crs_key = target_crs_key

    # ------------------------------------------------------------------
    # dtype conversion
    # ------------------------------------------------------------------

    def change_column_dtype(self, column: str, target_dtype_key: str, **kwargs) -> None:

        def to_boolean(col: str, true_k: str, false_k: str) -> None:
            na_placeholder = "<Células vazias>"
            t = pandas.NA if true_k == na_placeholder else true_k
            f = pandas.NA if false_k == na_placeholder else false_k
            series = self.gdf[col].astype("string")
            if not series.isin((t, f)).all():
                raise ValueError(
                    f"A coluna deve conter apenas os valores '{t}' e '{f}'."
                )
            self.gdf[col] = series.map({t: True, f: False}).astype(bool)

        if target_dtype_key == "Boolean":
            true_key = kwargs.get("true_key", "Sim")
            false_key = kwargs.get("false_key", "Não")
            to_boolean(column, true_key, false_key)
        elif target_dtype_key == "Datetime":
            fmt = DATETIME_FORMATS[kwargs.get("datetime_format", "DD-MM-YYYY")]
            self.gdf[column] = pandas.to_datetime(self.gdf[column], format=fmt, errors="raise")
        elif target_dtype_key == "Integer":
            self.gdf[column] = self.gdf[column].astype("int64")
        elif target_dtype_key == "Float":
            self.gdf[column] = self.gdf[column].astype("float64")
        elif target_dtype_key == "String":
            self.gdf[column] = self.gdf[column].astype("string")
        else:
            raise KeyError(f"Unknown dtype key: '{target_dtype_key}'")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self, path: str, layer_name: str = "pontos") -> None:
        ext = os.path.splitext(path)[1].lower()
        gdf = self.gdf.copy()

        # Coerce unsupported dtypes
        for col in gdf.columns.drop(gdf.geometry.name if self.has_geometry else []):
            dtype = gdf[col].dtype
            if is_categorical_dtype(dtype) or is_timedelta64_dtype(dtype):
                gdf[col] = gdf[col].astype("string")
            elif ext == ".shp" and is_datetime64_any_dtype(dtype):
                gdf[col] = gdf[col].astype("string")

        if ext == ".gpkg":
            gdf.to_file(path, layer=layer_name, driver="GPKG", encoding="utf-8")
        elif ext == ".geojson":
            gdf.to_file(path, encoding="utf-8")
        elif ext == ".shp":
            gdf.to_file(path, encoding="utf-8")
        elif ext == ".csv":
            pandas.DataFrame(gdf).to_csv(path, sep=";", decimal=".", index=False, encoding="utf-8")
        elif ext == ".xlsx":
            pandas.DataFrame(gdf).to_excel(path, index=False)
        else:
            gdf.to_file(path, encoding="utf-8")
