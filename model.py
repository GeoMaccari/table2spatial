# -*- coding: utf-8 -*-
""" @author: Gabriel Maccari """

import csv
import pandas
import geopandas
import pyproj
import re

from icecream import ic

#geopandas.options.io_engine = "pyogrio" #  pyogrio é melhor que fiona, mas não funciona com o pyinstaller

crs_types = {
    "PJType.GEOGRAPHIC_2D_CRS": "Geographic 2D CRS",
    "PJType.GEOGRAPHIC_3D_CRS": "Geographic 3D CRS",
    "PJType.PROJECTED_CRS": "Projected CRS",
}

crs_db = pyproj.database.query_crs_info(pj_types=("GEOGRAPHIC_2D_CRS", "PROJECTED_CRS", "GEOGRAPHIC_3D_CRS"))
CRS_DICT = {
    f"{crs_info.name} {'(3D) ' if crs_types[str(crs_info.type)] == 'Geographic 3D CRS' else ''}({crs_info.auth_name}:{crs_info.code})":
    {
        "name": crs_info.name,
        "auth_name": crs_info.auth_name,
        "code": crs_info.code,
        "type": crs_types[str(crs_info.type)]
    }
    for crs_info in crs_db if not crs_info.auth_name.startswith("IAU")  # Os SRCs da IAU são para outros planetas
}
del crs_db

DTYPES_DICT = {
    "String": {
        "pandas_dtypes": ("str", "object", "category"),
        "icon": "icons/string.png"
    },
    "Integer": {
        "pandas_dtypes": ('int64', 'uint64', 'int32', 'uint32', 'int16', 'uint16', 'int8', 'uint8'),
        "icon": "icons/integer.png"
    },
    "Float": {
        "pandas_dtypes": ("float64", "float32", "float16"),
        "icon": "icons/float.png"
    },
    "Boolean": {
        "pandas_dtypes": ("bool",),
        "icon": "icons/boolean.png"
    },
    "Datetime": {
        "pandas_dtypes": ("datetime64[ns]", "<M8[ns]", ">M8[ns]", "datetime64[us]"),
        "icon": "icons/datetime.png"
    }
}

DATETIME_FORMATS = {
    "DD/MM/YYYY": "%d/%m/%Y",
    "YYYY/MM/DD": "%Y/%m/%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM-DD-YYYY": "%m-%d-%Y",
    "YYYY/MM/DD HH:MM:SS": "%Y/%m/%d %H:%M:%S",
    "MM/DD/YYYY HH:MM:SS": "%m/%d/%Y %H:%M:%S",
    "DD/MM/YYYY HH:MM:SS": "%d/%m/%Y %H:%M:%S",
    "DD-MM-YYYY HH:MM:SS": "%d-%m-%Y %H:%M:%S",
    "YYYY-MM-DD HH:MM:SS": "%Y-%m-%d %H:%M:%S",
    "MM-DD-YYYY HH:MM:SS": "%m-%d-%Y %H:%M:%S",
}


class DataHandler:
    def __init__(self):
        self.excel_file = None
        self.gdf = None
        self.x_column = None
        self.y_column = None
        self.z_column = None
        self.crs_key = None

    def read_excel_file(self, path: str) -> None:
        """
        Lê uma pasta de trabalho do Excel/OpenDocument e a armazena como um objeto pandas.ExcelFile no
        atributo "excel_file" da classe.
        :param path: Caminho do arquivo a ser lido.
        :return: Nada.
        """
        self.excel_file = pandas.ExcelFile(path)

    def read_excel_sheet(self, sheet: str | int) -> None:
        """
        Lê uma planilha contida no arquivo do atributo "excel_file" e armazena os dados como um
        geopandas.GeoDataFrame no atributo "gdf" da classe. Automaticamente chama a função process_data para tratar os
        dados.
        :param sheet: O nome (str) ou índice (int) da planilha a ser lida.
        :return: Nada.
        """
        df = self.process_data(self.excel_file.parse(sheet_name=sheet))
        self.gdf = geopandas.GeoDataFrame(df)

    def read_csv_file(self, path: str, decimal: str = ',') -> None:
        """
        Lê um arquivo CSV, identifica o delimitador de células e armazena os dados como um geopandas.GeoDataFrame
        no atributo "gdf" da classe. Automaticamente chama a função process_data para tratar os dados.
        :param path:  Caminho do arquivo a ser lido.
        :param decimal: O separador decimal usado no arquivo. O padrão é ',' (vírgula).
        :return: Nada.
        """
        sniffer = csv.Sniffer()
        with open(path, 'r') as csv_file:
            data = csv_file.read(4096)
        sep = str(sniffer.sniff(data).delimiter)

        # Retirar isso caso seja implementada alguma seleção manual de separador decimal
        if sep == ',':
            decimal = '.'

        df = self.process_data(pandas.read_csv(path, delimiter=sep, decimal=decimal))
        self.gdf = geopandas.GeoDataFrame(df)

    @staticmethod
    def process_data(df: pandas.DataFrame) -> pandas.DataFrame:
        """
        Função que recebe um pandas.DataFrame e 1) Converte todos os rótulos de colunas para strings, 2) Descarta
        colunas sem rótulo/cabeçalho e 3) Descarta linhas completamente vazias. Também levanta um erro caso não haja
        nenhuma linha preenchida na planilha (com exceção dos cabeçalhos).
        :param df: O DataFrame a ser tratado.
        :return: O DataFrame após o tratamento.
        """
        # Converte os nomes das colunas para string
        df.columns = df.columns.astype(str)
        # Descarta colunas sem nome
        df = df.drop([col for col in df.columns if 'Unnamed' in col], axis='columns')
        # Descarta linhas vazias
        df = df.dropna(how='all', axis='index')
        # Verifica se existem linhas preenchidas no arquivo
        if len(df.index) <= 0:
            raise IndexError('A tabela selecionada está vazia ou contém apenas cabeçalhos.')
        return df

    def filter_coordinates_columns(self, crs_key: str, dms_format: bool = False) -> (list[str], list[str], list[str]):
        """
        Encontra as colunas válidas para coordenadas no GeoDataFrame e retorna uma lista de colunas válidas para x
        (longitude/easting), y (latitude/northing) e z (altitude). São consideradas colunas válidas aquelas que podem
        ser convertidas para float e cujos valores estão dentro dos limites esperados para as coordenadas do SRC.
        :param crs_key: A chave para o dicionário de SRCs (CRS_DICT), no formato "name (auth:code)". Ex: "SIRGAS 2000 (EPSG:4674)".
        :param dms_format: Booleano indicando se as coordenadas estão em formato GMS (GG°MM'SS.ssss"H) ou não.
        :return: Listas contendo os rótulos das colunas válidas para x, y e z, respectivamente.
        """
        x_columns, y_columns = [], []

        z_columns = self.gdf.select_dtypes(include='number').columns.tolist()

        if dms_format:
            x_columns, y_columns = self.filter_dms_coordinates_columns()
            return x_columns, y_columns, z_columns

        crs = pyproj.CRS.from_authority(CRS_DICT[crs_key]["auth_name"], CRS_DICT[crs_key]["code"])

        if CRS_DICT[crs_key]["type"] in ["Geographic 2D CRS", "Geographic 3D CRS"]:
            x_min, y_min, x_max, y_max = crs.area_of_use.bounds
        else:
            transformer = pyproj.Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
            x_min, y_min, x_max, y_max = transformer.transform_bounds(*crs.area_of_use.bounds)

        for col in self.gdf.columns:
            try:
                converted = self.gdf[col].replace(",", ".", regex=True).astype(float)
                if converted.dropna().between(y_min, y_max).all():
                    y_columns.append(col)
                if converted.dropna().between(x_min, x_max).all():
                    x_columns.append(col)
            except (ValueError, TypeError):
                continue

        return x_columns, y_columns, z_columns

    def filter_dms_coordinates_columns(self):
        """
        Encontra as colunas válidas para coordenadas em formato GMS (GG°MM'SS,sss"D) no GeoDataFrame e retorna uma lista
        de colunas válidas para x (longitude) e y (latitude).
        :return: Listas contendo os rótulos das colunas válidas para x e y, respectivamente.
        """
        y_pattern = re.compile(r"^(\d{1,2})([°º])(\d{1,2})(['’′])(\d{1,2}([.,]\d+)?)([\"”″])([NSns])$")
        x_pattern = re.compile(r"^(\d{1,3})([°º])(\d{1,2})(['’′])(\d{1,2}([.,]\d+)?)([\"”″])([EWOLewol])$")
        x_columns, y_columns = [], []

        for c in self.gdf.columns:
            rows = self.gdf[c].values
            x_ok, y_ok = True, True

            for value in rows:
                value = str(value).replace(" ", "")
                if x_pattern.match(value) is None:
                    x_ok = False
                if y_pattern.match(value) is None:
                    y_ok = False

                if not x_ok and not y_ok:
                    continue

                parts = re.split(r'[^\d\w]+', value)
                degrees = int(parts[0])
                minutes = int(parts[1])
                seconds = float(f"{parts[2]}.{parts[3]}" if parts[3].isdigit() else parts[2])

                if degrees > 180:
                    x_ok = False
                    y_ok = False
                elif degrees > 90:
                    y_ok = False

                if minutes > 60 or seconds > 60:
                    x_ok, y_ok = False, False

            if x_ok:
                x_columns.append(c)
            if y_ok:
                y_columns.append(c)

        return x_columns, y_columns

    @staticmethod
    def search_coordinates_column_by_name(axis: str, crs_type: str, column_names: list[str]) -> str | None:
        """
        Função que seleciona uma coluna que provavelmente contém coordenadas com base no nome.
        :param axis: O eixo das coordenadas ("y": latitude/northing, "x": longitude/easting, "z": altitude).
        :param crs_type: O tipo de SRC ("Geographic 2D CRS", "Geographic 3D CRS" ou "Projected CRS").
        :param column_names: Uma lista contendo os rótulos das colunas a serem consideradas.
        :return: O rótulo da coluna que provavelmente contém as coordenadas (str) ou None, se nenhuma coluna provável for encontrada.
        """
        options = {
            ("y", "Geographic 2D CRS"): ("latitude", "lat", "y"),
            ("x", "Geographic 2D CRS"): ("longitude", "lon", "x"),
            ("y", "Geographic 3D CRS"): ("latitude", "lat", "y"),
            ("x", "Geographic 3D CRS"): ("longitude", "lon", "x"),
            ("z", "Geographic 3D CRS"): ("altitude", "alt", "z", "cota"),
            ("x", "Projected CRS"): ("easting", "utm_e", "utme", "e", "utme (m)", "utm_e (m)", "x"),
            ("y", "Projected CRS"): ("northing", "utm_n", "utmn", "n", "utmn (m)", "utm_n (m)", "y")
        }

        common_names = options[(axis, crs_type)]

        return next((col for col in column_names if str(col).lower() in common_names), None)

    def set_geodataframe_geometry(self, crs_key: str, x_column: str, y_column: str, z_column: str = None, dms: bool = False) -> None:
        """
        Define a geometria e o crs do GeoDataFrame contido no adributo "gdf" da classe. Também define os atributos
        "crs_key", "x_column", "y_column" e "z_column" da classe com base nos parâmetros dados.
        :param crs_key: A chave para o dicionário de SRCs (CRS_DICT), no formato "name (auth:code)". Ex: "SIRGAS 2000 (EPSG:4674)".
        :param x_column: O rótulo da coluna que contém as coordenadas do eixo X.
        :param y_column: O rótulo da coluna que contém as coordenadas do eixo Y.
        :param z_column: O rótulo da coluna que contém as coordenadas do eixo Z.
        :param dms: True caso as coordenadas estejam em formato Graus, Minutos e Segundos. Do contrário, False.
        """
        crs = pyproj.CRS.from_authority(CRS_DICT[crs_key]["auth_name"], CRS_DICT[crs_key]["code"])

        if dms:
            x, y = self.convert_dms_to_decimal(x_column, y_column)
        else:
            x, y = self.gdf[x_column], self.gdf[y_column]

        z = self.gdf[z_column] if z_column is not None else None

        geometry = geopandas.points_from_xy(x, y, z, crs=crs)

        self.gdf = geopandas.GeoDataFrame(self.gdf, geometry=geometry, crs=crs)

        self.x_column, self.y_column, self.z_column = x_column, y_column, z_column
        self.crs_key = crs_key

    def convert_dms_to_decimal(self, x_column: str, y_column: str):
        """
        Converte coordenadas em formato GMS contidas em duas colunas distintas do GeoDataFrame para formato decimal.
        :param x_column: A coluna contendo as longitudes em GMS.
        :param y_column: A coluna contendo as latitudes em GMS.
        :return: Duas listas contendo longitudes e latitudes, respectivamente, em formato decimal.
        """
        x, y = [], []
        for i, c in enumerate((x_column, y_column)):
            rows = self.gdf[c].values
            for value in rows:
                value = str(value).strip()
                parts = re.split(r"°|º|'|’|′|\"|”|″|''", value)
                d, m, s = int(parts[0]), int(parts[1]), float(parts[2].replace(",", "."))
                direction = parts[3]

                dd = d + (m / 60) + (s / 3600)

                if direction in "SWOswo":
                    dd *= -1

                if i == 0:
                    x.append(dd)
                else:
                    y.append(dd)
        return x, y

    def merge_sheets(self, merge_column: str) -> (list[str], list[str]):
        """
        Mescla múltiplas abas de uma pasta de trabalho do Excel/OpenDocument armazenado no atributo "excel_file" da
        classe, com base em uma coluna de ID. Armazena os novos dados no atributo "gdf", usando os atributos
        "x_column", "y_column" e "crs" para construir a geometria e definir o SRC. Planilhas que não contenham a coluna
        merge_column são ignoradas.
        :param merge_column: A coluna identificadora.
        :return: Listas contendo os rótulos das colunas que foram e não foram incluídas na mesclagem, respectivamente.
        """
        sheets_to_merge, sheets_to_skip = [], []
        sheet_dfs, merge_column_dtypes = [], []

        no_coordinates_mode = True

        # Itera pelo ExcelFile, convertendo as planilhas para DFs, e verifica se cada uma contém a coluna de mescla
        for s in self.excel_file.sheet_names:
            if s == self.excel_file.sheet_names[0]:
                sheet_df = pandas.DataFrame(self.gdf)
                if "geometry" in sheet_df.columns:
                    sheet_df = sheet_df.drop(columns=["geometry"])
                    no_coordinates_mode = False
            else:
                sheet_df = self.excel_file.parse(sheet_name=s)

            if merge_column in sheet_df.columns:
                if sheet_df[merge_column].duplicated().any():
                    raise Exception(f"A coluna {merge_column} possui valores duplicados na planilha {s}.")

                try:
                    sheet_dfs.append(self.process_data(sheet_df))
                    sheets_to_merge.append(s)
                except IndexError:
                    sheets_to_skip.append(s)

                merge_column_dtypes.append(sheet_df[merge_column].dtype)
            else:
                sheets_to_skip.append(s)

        # Verifica se a coluna de mescla tem o mesmo dtype em todas as abas. Se não tiver, converte todas para string
        if len(set(merge_column_dtypes)) > 1:
            for s in sheet_dfs:
                s[merge_column] = s[merge_column].astype(str)

        # Mescla as abas com base na coluna de mescla selecionada pelo usuário
        df = sheet_dfs[0]
        for i in range(1, len(sheet_dfs)):
            df = pandas.merge(df, sheet_dfs[i], how='outer', on=merge_column)

        # Restaura a ordem correta das colunas
        cols = df.columns.to_list()
        cols.remove(merge_column)
        columns = [merge_column] + cols
        df = df[columns]

        self.gdf = geopandas.GeoDataFrame(df, geometry=None if no_coordinates_mode else self.gdf.geometry,
                                          crs=None if no_coordinates_mode else self.gdf.crs)

        return sheets_to_merge, sheets_to_skip

    def change_column_dtype(self, column: str, target_dtype_key: str, **kwargs) -> None:
        """
        Muda o tipo de dado de uma coluna.
        :param column: O nome da coluna.
        :param target_dtype_key: O tipo de dado de destino (String, Integer, Float, Boolean ou Datetime).
        :kwarg true_key: O valor encontrado na coluna a ser considerado como True (necessário apenas ao converter para Boolean). Ex: "Verdadeiro".
        :kwarg false_key: O valor encontrado na coluna a ser considerado como False (necessário apenas ao converter para Boolean). Ex: "Falso".
        :kwarg datetime_format: O formato de data e hora (necessário apenas ao converter para Datetime)
        :return: Nada
        """
        def switch_to_boolean(c, t, f):
            t = pandas.NA if t == "<Células vazias>" else t
            f = pandas.NA if f == "<Células vazias>" else f
            if not self.gdf[c].astype("string").isin((t, f)).all():
                raise ValueError(f"A coluna deve conter apenas os valores indicados para verdadeiro e falso ({t} e {f}).")
            if str(t) == "<Nenhum>":
                self.gdf[c] = self.gdf[c].astype("string").map({t: False, f: False}).astype(bool)
            elif str(f) == "<Nenhum>":
                self.gdf[c] = self.gdf[c].astype("string").map({t: True, f: True}).astype(bool)
            else:
                self.gdf[c] = self.gdf[c].astype("string").map({t: True, f: False}).astype(bool)

        if target_dtype_key == "Boolean":
            true, false = kwargs.get("true_key", "Sim"), kwargs.get("false_key", "Não")
            switch_to_boolean(column, true, false)
        elif target_dtype_key == "Datetime":
            datetime_format = kwargs.get("datetime_format", "DD-MM-YYYY")
            self.gdf[column] = pandas.to_datetime(self.gdf[column], format=DATETIME_FORMATS[datetime_format], errors="raise")
        else:
            target_dtype = DTYPES_DICT[target_dtype_key]["pandas_dtypes"][0]
            self.gdf[column] = self.gdf[column].astype(target_dtype, errors="raise")

    def reproject_geodataframe(self, target_crs_key: str) -> None:
        """
        Reprojeta o GeoDataFrame para um SRC de destino.
        :param target_crs_key: A chave para o dicionário de SRCs (CRS_DICT) do SRC de destino, no formato "name (auth:code)". Ex: "SIRGAS 2000 (EPSG:4674)".
        :return: Nada
        """
        target_crs = pyproj.CRS.from_authority(CRS_DICT[target_crs_key]["auth_name"], CRS_DICT[target_crs_key]["code"])
        self.gdf = self.gdf.to_crs(crs=target_crs)
        self.crs_key = target_crs_key

    def export_geodataframe(self, path: str, layer_name: str = "pontos"):
        """
        Exporta o GeoDataFrame armazenado no atributo "gdf" da classe para um arquivo vetorial ou tabela.
        :param path: Caminho do arquivo de saída.
        :param layer_name: Nome da camada (para arquivos geopackage).
        :return: Nada
        """
        if path.endswith(".shp"):
            unsupported_dtypes = ("category", "timedelta64[ns]", "datetime64[ns]", "<M8[ns]", ">M8[ns]")
        else:
            unsupported_dtypes = ("category", "timedelta64[ns]")

        for c in self.gdf.columns:
            if self.gdf[c].dtype in unsupported_dtypes:
                self.gdf[c] = self.gdf[c].astype(str)

        if path.endswith(".gpkg"):
            self.gdf.to_file(filename=path, layer=layer_name, driver="GPKG", encoding="utf-8")
        elif path.endswith(".csv"):
            df = pandas.DataFrame(self.gdf)
            df.to_csv(path, sep=";", decimal=".", index=False, encoding="utf-8")
        elif path.endswith(".xlsx"):
            df = pandas.DataFrame(self.gdf)
            df.to_excel(path, index=False)
        else:  # GeoJSON e Shapefile
            self.gdf.to_file(filename=path, encoding="utf-8")


def get_dtype_key(value: str) -> str | None:
    """
    Função que retorna a chave de um tipo de dado presente no DTYPES_DICT com base em seu pandas dtype.
    Ex: uint8 --> Integer; float64 --> Float; object --> String.
    :param value: pandas dtype string alias.
    :return: Chave do tipo de dado ou None.
    """
    for dt_key, inner_dict in DTYPES_DICT.items():
        if value in inner_dict["pandas_dtypes"]:
            return dt_key
    return None

