"""Precio de referencia comarcal y parcelas colindantes. Sin red."""
from fincas import precios
from fincas.colindantes import ServicioColindantes, interpretar, texto
from fincas.modelos import Codigos, DatosCatastro, DatosInventario, Finca, Resultado


def _res(uso="MATORRAL", coberturas=None, clase="", ine="061", prov="15"):
    r = Resultado(finca=Finca("Ortigueira", "A Coruña", "34", "175"),
                  codigos=Codigos(prov, "A CORUÑA", ine, "62", "ORTIGUEIRA"),
                  catastro=DatosCatastro(refcat="15062A03400175", superficie_m2=1000.0, uso=uso, clase=clase))
    if coberturas:
        r.inventario = DatosInventario(anio=2024, coberturas=coberturas, dominante=max(coberturas, key=coberturas.get))
    return r


def test_comarca_y_precio_por_tipo_de_terreno():
    assert precios.comarca_de("15", "61") == "Ortegal"          # admite el código sin ceros a la izquierda
    monte = precios.referencia(_res(coberturas={"Eucaliptos": 800.0, "Prado o cultivo": 200.0}))
    assert monte["comarca"] == "Ortegal" and monte["tipo"] == "monte" and monte["precio"] == monte["monte"] == 0.56
    prado = precios.referencia(_res(uso="PINAR MADERABLE", coberturas={"Prado o cultivo": 900.0, "Matorral": 100.0}))
    assert prado["tipo"] == "tierra agraria" and prado["precio"] == 0.75     # manda lo que hay HOY, no el Catastro
    assert precios.referencia(_res(uso="PRADOS O PRADERAS"))["tipo"] == "tierra agraria"   # sin inventario: Catastro
    assert precios.euros_m2(0.56) == "0,56 €/m²" and precios.euros_ha(0.56) == "5.600 €/ha"


def test_precio_no_aplica_a_urbanos_ni_fuera_de_galicia():
    urbano = precios.referencia(_res(uso="Residencial", clase="Urbano"))
    assert urbano["tipo"] == "" and urbano["precio"] is None                 # se cita la comarca, sin precio
    assert precios.referencia(_res(prov="28", ine="079")) is None            # fuera de Galicia: sin tabla
    assert precios.euros_m2(None) == "sin dato publicado"


def test_comarcas_con_nombre_en_otra_grafia_tienen_precio():
    # la capa de la Xunta escribe 'Terra De Soneira' y el informe 'Terra de Soneira'
    assert precios._tablas()["precios"]["por_clave"][precios._clave("Terra De Soneira")]["monte"] == 0.61


RESP = {"features": [
    {"attributes": {"POLIGONO": 23, "PARCELA": 294, "REFERENCIA_CAT": "99999A023002940000AA", "DN_SURFACE": 5535.4}},
    {"attributes": {"POLIGONO": 23, "PARCELA": 314, "REFERENCIA_CAT": "99999A023003140000AA", "DN_SURFACE": 13857.6}},
    {"attributes": {"POLIGONO": 23, "PARCELA": 297, "REFERENCIA_CAT": "99999A023002970000AA", "DN_SURFACE": 15808.9}},
    {"attributes": {"POLIGONO": 23, "PARCELA": 9000, "DN_SURFACE": 52.2}},
    {"attributes": {"POLIGONO": 23, "PARCELA": 9000, "DN_SURFACE": 24.7}},
    {"attributes": {"POLIGONO": 24, "PARCELA": 5, "REFERENCIA_CAT": "99999A024000050000AA", "DN_SURFACE": 100}}]}


def test_colindantes_sin_la_propia_finca_y_con_dominio_publico():
    d = interpretar(RESP, "23", "294")
    assert [(p["poligono"], p["parcela"]) for p in d.parcelas] == [("23", "297"), ("23", "314"), ("24", "5")]
    assert d.parcelas[0]["refcat"] == "99999A02300297" and d.dominio_publico == 2
    assert texto(d) == "Polígono 23: parcelas 297, 314 · Polígono 24: parcelas 5"
    assert interpretar({"features": []}, "1", "1").parcelas == []


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class _Http:
    def __init__(self, con_geometria=True):
        self.urls, self.con_geometria = [], con_geometria

    def get(self, url, params=None):
        self.urls.append(url)
        if url.endswith("/ParcelasCatastrais"):
            return _Resp({"services": [{"name": "ParcelasCatastrais/SIXPAC_2024"}, {"name": "ParcelasCatastrais/SIXPAC_2026"},
                                       {"name": "ParcelasCatastrais/Otra"}]})
        rings = [[[0, 0], [0, 10], [10, 10], [0, 0]]] if self.con_geometria else []
        return _Resp({"features": [{"geometry": {"rings": rings}}] if rings else []})

    def post(self, url, data=None):
        self.urls.append(url)
        assert data["spatialRel"] == "esriSpatialRelIntersects" and data["distance"] == "1"
        return _Resp(RESP)


def test_servicio_usa_la_edicion_mas_reciente():
    http = _Http()
    srv = ServicioColindantes(http, base="http://x")
    d = srv.consultar("15", "78", "23", "294")
    assert d.anio == 2026 and len(d.parcelas) == 3 and "SIXPAC_2026/MapServer/1/query" in http.urls[-1]
    srv.consultar("15", "78", "23", "294")
    assert sum(u.endswith("/ParcelasCatastrais") for u in http.urls) == 1     # el año se averigua una vez por lote
    assert ServicioColindantes(_Http(con_geometria=False), base="http://x").consultar("15", "78", "1", "1") is None


def test_movilidad_y_tabla_de_madera():
    m = precios.movilidad(_res())
    assert m == {"comarca": "Ortegal", "tasa": 2.54}
    assert precios.movilidad(_res(prov="28", ine="079")) is None
    md = precios.madera()
    assert all(x["min"] < x["max"] for x in md["precios"]) and md["revisado"] and len(md["fuentes"]) >= 3
    g = precios.gastos()
    assert len(g["filas"]) >= 5 and all(len(f) == 3 for f in g["filas"]) and g["revisado"]


def test_planeamiento_del_municipio_y_caducidad_de_precios():
    from datetime import date
    pl = precios.planeamiento("15", "61")
    assert pl["concello"] == "Ortigueira" and pl["figura"] and pl["estado"]
    assert precios.planeamiento("28", "079") is None
    assert precios.meses_sin_revisar_madera(date(2026, 11, 30)) == 0
    assert precios.meses_sin_revisar_madera(date(2027, 6, 1)) == 7           # a partir de 6 meses la CLI avisa
