"""Condicionantes de corta: parsers puros y servicio con HTTP simulado (cero red)."""
from pathlib import Path

from openpyxl import load_workbook

from fincas.aprovechamiento import (INTEGRADA, TECNICAS, ServicioAprovechamiento, geometria_ultima_edicion,
                                    interpretar_histograma, interpretar_identify, resumen_afecciones, texto_clases)
from fincas.cache import Cache
from fincas.entrada import leer_fincas
from fincas.modelos import DatosAprovechamiento
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro
from test_pipeline import Catastro, Resolutor, Sigpac

EJEMPLO = Path(__file__).parent / "ejemplo.csv"
# respuesta real (Ortigueira 34/176, 2026-11-12)
AGUAS = {"results": [
    {"layerId": 5, "layerName": "Afeccion hidrografia gener", "attributes": {"OBJECTID": "478", "confederac": "Galicia Costa"}},
    {"layerId": 6, "layerName": "Afeccion hidrografia", "attributes": {"OBJECTID": "483", "confederac": "Galicia Costa"}}]}
HIST_TEC = {"histograms": [{"size": 5, "min": -0.5, "max": 4.5, "counts": [0, 0, 0, 0, 17]}]}
HIST_INT = {"histograms": [{"size": 4, "min": -0.5, "max": 3.5, "counts": [0, 0, 5, 15]}]}
RECINTOS = {"features": [
    {"attributes": {"ADM_FECINI": 1672527600000}, "geometry": {"rings": [[[0, 0], [9, 0], [9, 9], [0, 0]]]}},
    {"attributes": {"ADM_FECINI": 1704063600000}, "geometry": {"rings": [[[0, 0], [10, 0], [10, 10], [0, 0]]]}}]}


class _R:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class Http:
    def __init__(self, falla=""):
        self.urls, self.falla = [], falla

    def get(self, url, params=None):
        self.urls.append(url)
        return _R(RECINTOS)

    def post(self, url, data=None):
        self.urls.append(url)
        if self.falla and self.falla in url:
            raise RuntimeError("caído")
        if "Augas" in url:
            return _R(AGUAS)
        if "Tecnicas" in url:
            return _R(HIST_TEC)
        if "TecLeg" in url:
            return _R(HIST_INT)
        return _R({"results": []})


def test_identify_real_aguas_y_organismo():
    a = interpretar_identify("Augas", AGUAS)
    assert len(a) == 1 and a[0]["tipo"].startswith("Aguas") and a[0]["detalle"] == "Zona de afección de cauces"
    assert a[0]["organismo"] == "Augas de Galicia (demarcación Galicia Costa)"
    otra = interpretar_identify("Augas", {"results": [{"layerName": "Afeccion hidrografia",
                                                        "attributes": {"confederac": "Miño-Sil"}}]})
    assert otra[0]["organismo"] == "Confederación Hidrográfica (Miño-Sil)"


def test_identify_carreteras_nombre_y_repetidos():
    r = {"results": [{"layerName": "Afección Autonómicas Convencionais", "attributes": {}},
                     {"layerName": "Afección Autonómicas Convencionais", "attributes": {}},
                     {"layerName": "Afección Deputacion", "attributes": {}}]}
    a = interpretar_identify("Estradas", r)
    assert [x["organismo"] for x in a] == ["Axencia Galega de Infraestruturas (Xunta de Galicia)", "Deputación provincial"]
    assert a[0]["detalle"] == "Zona de afección de carretera autonómica convencional"
    n = interpretar_identify("ZonasDemarcadas", {"results": [{"layerName": "ZONA NEMATODO", "attributes": {"NAME": "As Neves"}}]})
    assert n[0]["detalle"] == "ZONA NEMATODO: As Neves"
    assert interpretar_identify("Desconocido", r) == [] and interpretar_identify("Augas", {}) == []


def test_histograma_real_y_vacio():
    assert interpretar_histograma(HIST_TEC, TECNICAS) == {"Muy desfavorable": 1.0}
    h = interpretar_histograma(HIST_INT, INTEGRADA)
    assert h == {"Intermedia": 0.25, "Desfavorable": 0.75}
    assert texto_clases(h) == "Desfavorable 75 % · Intermedia 25 %"
    assert interpretar_histograma({"histograms": []}, TECNICAS) == {}
    assert interpretar_histograma({"histograms": [{"size": 5, "min": -0.5, "max": 4.5, "counts": [0] * 5}]}, TECNICAS) == {}


def test_geometria_solo_ultima_edicion():
    g = geometria_ultima_edicion(RECINTOS["features"])
    assert g["rings"] == [[[0, 0], [10, 0], [10, 10], [0, 0]]] and g["spatialReference"] == {"wkid": 25829}
    assert geometria_ultima_edicion([]) is None


def test_servicio_completo_y_capa_que_falla():
    http = Http()
    d = ServicioAprovechamiento(http, base="http://x").consultar("15062A03400176000AB")
    assert len(http.urls) == 16                       # geometría + 13 capas de afecciones + 2 restricciones
    assert any("/PBA/Afeccions_Forestal/" in u for u in http.urls) and any("/SIRFOG/" in u for u in http.urls)
    assert resumen_afecciones(d) == "Aguas" and d.saca == {"Muy desfavorable": 1.0} and d.sin_consultar == []
    d2 = ServicioAprovechamiento(Http(falla="Estradas"), base="http://x").consultar("15062A03400176")
    assert d2.sin_consultar == ["Carreteras"] and len(d2.afecciones) == 1   # no se da por limpia, y se sigue
    assert resumen_afecciones(DatosAprovechamiento()) == "Ninguna detectada"


def test_pipeline_anota_en_excel_y_no_cachea_consulta_incompleta(tmp_path):
    class Srv:
        def __init__(self, d):
            self.d, self.n = d, 0

        def consultar(self, refcat):
            self.n += 1
            return self.d

    fincas, _ = leer_fincas(str(EJEMPLO))
    completo = Srv(DatosAprovechamiento(afecciones=interpretar_identify("Augas", AGUAS), saca={"Favorable": 1.0}))
    cache = Cache(tmp_path / ".cache")
    srv = Servicios(Resolutor(), Catastro(), Sigpac(), cache, aprovechamiento=completo)
    op = Opciones("lote", salidas=tmp_path, fecha="2026-01-01", mapa=False, informe=False)
    raiz = ejecutar(fincas, op, srv, Registro(eco=None))
    ws = load_workbook(raiz / "resultados.xlsx")["Fincas"]
    cab = [c.value for c in ws[1]]
    assert ws.cell(row=2, column=cab.index("afecciones para una corta") + 1).value == "Aguas"
    assert ws.cell(row=2, column=cab.index("dificultad de saca") + 1).value == "Favorable 100 %"
    n = completo.n
    ejecutar(fincas, Opciones("lote2", salidas=tmp_path, fecha="2026-01-01", mapa=False, informe=False), srv, Registro(eco=None))
    assert completo.n == n                            # segunda vez: de la caché

    parcial = Srv(DatosAprovechamiento(sin_consultar=["Carreteras"]))
    srv2 = Servicios(Resolutor(), Catastro(), Sigpac(), Cache(tmp_path / ".cache2"), aprovechamiento=parcial)
    for nombre in ("a", "b"):
        ejecutar(fincas, Opciones(nombre, salidas=tmp_path, fecha="2026-01-01", mapa=False, informe=False), srv2, Registro(eco=None))
    assert parcial.n == 2 * (parcial.n // 2) and parcial.n > n   # se vuelve a consultar: no se cacheó


def test_capas_del_registro_forestal_fajas_energia_e_incendios():
    f = interpretar_identify("Afeccions_Forestal", {"results": [
        {"layerName": "Montes Veciñais Man Común", "attributes": {"NOMEMONTE": "ARBO", "TIPO": "M.V.M.C."}},
        {"layerName": "Masas Frondosas Consolidadas Autóctonas", "attributes": {"MONTE": "Serra do Pozo"}},
        {"layerName": "Montes Ordenados", "attributes": {"NOMEMONTE": "CARBALLOSA", "TIPOINSTR": "Proxecto de Ordenacion"}}]})
    assert f[0]["tipo"].startswith("Monte vecinal en mano común") and f[0]["detalle"] == "Monte: ARBO"
    assert "Xurado Provincial" in f[0]["organismo"]
    assert f[1]["tipo"].startswith("Masa consolidada de frondosas") and f[1]["detalle"] == "Monte: Serra do Pozo"
    assert f[2]["detalle"] == "Monte: CARBALLOSA · Proxecto de Ordenacion"
    fx = interpretar_identify("Afeccions_outras_Forestal_Faixas", {"results": [
        {"layerName": "Faixa de protección 50m", "attributes": {"PARROQUIA": "Abegondo (Santaia)"}}]})
    assert fx[0]["detalle"] == "Faja de 50 m (parroquia de Abegondo (Santaia))" and fx[0]["organismo"] == "Concello"
    e = interpretar_identify("Afeccions_Enerxia", {"results": [
        {"layerName": "Liñas eléctricas. Existentes", "attributes": {}},
        {"layerName": "Liñas eléctricas. PIA/PRS", "attributes": {"NOME": "LAT 132 Kv"}}]})
    assert [x["detalle"] for x in e] == ["Línea eléctrica", "Línea eléctrica prevista: LAT 132 Kv"]
    c = interpretar_identify("Afeccions_ConcentracionsParcelarias", {"results": [
        {"layerName": "Concentracións parcelarias acordo firme", "attributes": {"ZONA": "Dombodán", "FASE_ULT": "Rematada"}}]})
    assert c[0]["detalle"] == "Zona Dombodán (rematada)"
    i = interpretar_identify("CAMPANHAS_INCENDIOS", {"results": [
        {"layerName": "x", "attributes": {"CODIGO": "2020150001", "DATA": "14/01/2020"}}]})
    j = interpretar_identify("Afeccions_outras_AreasIncendiadas", {"results": [
        {"layerName": "Lumes 2010_2020", "attributes": {"CODIGO": "2015150123"}}]})
    assert i[0]["detalle"] == "Incendio del 14/01/2020 (código 2020150001)"
    assert j[0]["detalle"] == "Incendio de 2015 (código 2015150123)" and j[0]["tipo"] == i[0]["tipo"]


def test_el_mismo_incendio_en_dos_capas_sale_una_vez():
    class H(Http):
        def post(self, url, data=None):
            if "INCENDIOS" in url or "AreasIncendiadas" in url:
                return _R({"results": [{"layerName": "x", "attributes": {"CODIGO": "2020150001", "DATA": "14/01/2020"}
                                        if "INCENDIOS" in url else {"CODIGO": "2020150001"}}]})
            return super().post(url, data)

    d = ServicioAprovechamiento(H(), base="http://x").consultar("15062A03400176")
    assert [a["detalle"] for a in d.afecciones if "Incendio" in a["tipo"]] == ["Incendio del 14/01/2020 (código 2020150001)"]
