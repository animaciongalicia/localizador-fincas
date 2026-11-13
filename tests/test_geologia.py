"""Contexto geológico y minero: parsers puros y servicio con HTTP de mentira (sin red)."""
from fincas.geologia import (ServicioGeologia, distancia_m, estado, interpretar_derechos, interpretar_geoquimica,
                             interpretar_hoja, interpretar_indicios, interpretar_litologia, texto_indicios, veredicto)
from fincas.modelos import DatosGeologia

LON, LAT = -8.5, 43.0
INDICIOS = {"features": [
    {"attributes": {"Nombre_mina": "MINA LEJANA", "Sustancia": " Estaño, Wolframio", "Morfologia": "Filoniana",
                    "Codigo_indicio": "0001", "X": 999, "Y": 999}, "geometry": {"x": -8.5, "y": 43.03}},
    {"attributes": {"Nombre_mina": "MINA CERCANA", "Sustancia": " Oro", "Morfologia": "Aluvionar"},
     "geometry": {"x": -8.5, "y": 43.01}},
    {"attributes": {"Nombre_mina": "SIN GEOMETRIA"}}]}
LEYENDA = {"layers": [{"layerId": 0, "legend": [{"label": "0 - 1"}, {"label": "1 - 5"}, {"label": "5 - 400"}]}]}


def _identify(valor, clase):
    return {"results": [{"layerId": 0, "attributes": {"Classify.Valor de píxel": valor, "Classify.Valor de clase": clase}}]}


def test_distancia():
    assert abs(distancia_m(LON, LAT, LON, LAT + 0.01) - 1112) < 5


def test_hoja_y_litologia():
    assert interpretar_hoja({"features": [{"attributes": {"NUM": 68, "NOMBRE": "VILLAEJEMPLO"}}]}) == "68 Villaejemplo"
    assert interpretar_hoja({"features": []}) == ""
    assert interpretar_litologia({"features": [{"attributes": {"DLO": "Granito de dos micas"}}]}) == "Granito de dos micas"


def test_indicios_ordenados_por_distancia_real():
    ind = interpretar_indicios(INDICIOS, LON, LAT)
    assert [i["nombre"] for i in ind] == ["Mina Cercana", "Mina Lejana"]      # sin geometría: no se inventa distancia
    assert 1100 < ind[0]["distancia_m"] < 1125                                # usa la geometría, no las columnas X/Y


def test_derechos_sin_titular_y_sin_repetir():
    f = {"attributes": {"NUMERO": "XX/A/1", "NOMBRE": "CANTERA", "TIPO_LARGO": "AA / Autorización de aprovechamiento",
                        "RECURSOS": "GRANITO", "TITULAR": "EMPRESA, S.L.", "ESTADO": "Vigente"}}
    d = interpretar_derechos({"features": [f, f]})
    assert len(d) == 1 and d[0]["tipo"] == "Autorización de aprovechamiento" and d[0]["recursos"] == "Granito"
    assert "EMPRESA" not in str(d)


def test_geoquimica_la_clase_empieza_en_cero():
    g = interpretar_geoquimica("W", _identify("25.7", "2"), LEYENDA)
    assert g["tramo"] == 3 and g["tramos"] == 3 and g["rango"] == "5 - 400" and g["nombre"] == "wolframio"
    assert interpretar_geoquimica("W", {"results": []}, LEYENDA) is None
    assert interpretar_geoquimica("W", _identify("x", "9"), LEYENDA) is None


def test_veredictos():
    d = DatosGeologia()
    assert veredicto(d).startswith("Sin indicios")
    d.indicios = interpretar_indicios(INDICIOS, LON, LAT)
    d.veredicto = veredicto(d)
    assert d.veredicto.startswith("Indicios en la comarca: 2 indicio") and "Mina Cercana (oro), a 1,1 km" in d.veredicto
    assert estado(d) == "Indicios en la comarca" and "Mina Cercana (Oro; 1,1 km)" in texto_indicios(d)
    d.geoquimica = [interpretar_geoquimica("W", _identify("25.7", "2"), LEYENDA),
                    interpretar_geoquimica("Au", _identify("0.5", "0"), LEYENDA)]
    d.veredicto = veredicto(d)
    assert d.veredicto.startswith("Anomalía detectada: wolframio") and "oro en" not in d.veredicto
    assert estado(d) == "Anomalía detectada"
    fallo = DatosGeologia(sin_consultar=["indicios mineros (BDMIN, IGME)"])
    assert veredicto(fallo).startswith("Sin verificar")       # una fuente caída nunca da "sin indicios"


class _Resp:
    def __init__(self, datos):
        self._d = datos

    def json(self):
        return self._d


class _Http:
    def __init__(self, falla=""):
        self.urls, self.falla = [], falla

    def get(self, url, params=None):
        self.urls.append(url)
        if self.falla and self.falla in url:
            raise RuntimeError("caído")
        if "MAGNA_50/MapServer/0" in url:
            return _Resp({"features": [{"attributes": {"NUM": 1, "NOMBRE": "HOJA"}}]})
        if "MAGNA_50/MapServer/11" in url:
            return _Resp({"features": [{"attributes": {"DLO": "Granito"}}]})
        if "BDMIN_Indicios" in url:
            return _Resp(INDICIOS)
        if url.endswith("/legend"):
            return _Resp(LEYENDA)
        if url.endswith("/identify"):
            return _Resp(_identify("0.5", "0"))
        return _Resp({"features": []})


def test_servicio_completo_y_galicia():
    http = _Http()
    d = ServicioGeologia(http).consultar(LON, LAT, galicia=True)
    assert d.hoja == "1 Hoja" and d.litologia == "Granito" and len(d.indicios) == 2 and len(d.geoquimica) == 5
    assert d.derechos_consultados and not d.derechos_parcela and not d.sin_consultar
    assert d.veredicto.startswith("Indicios en la comarca")
    fuera = ServicioGeologia(_Http()).consultar(LON, LAT, galicia=False)
    assert not fuera.derechos_consultados                     # fuera de Galicia no hay capa: queda sin verificar


def test_fuente_caida_no_se_da_por_limpia():
    d = ServicioGeologia(_Http(falla="BDMIN_Indicios")).consultar(LON, LAT, galicia=True)
    assert d.sin_consultar == ["indicios mineros (BDMIN, IGME)"] and d.veredicto.startswith("Sin verificar")
    assert d.litologia == "Granito"                           # el resto sigue adelante
