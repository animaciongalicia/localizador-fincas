"""Inmuebles urbanos: se buscan por su referencia catastral (no tienen polígono ni parcela)."""
from pathlib import Path

from fincas.cache import Cache
from fincas.catastro import parsear_centro, parsear_dnppp
from fincas.entrada import filas_a_fincas, refcat_no_rustica
from fincas.mapa import ServicioMapa
from fincas.modelos import Codigos, DatosCatastro, Resultado
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro

FIX = Path(__file__).parent / "fixtures"
RC = "999000100XX00X0001AB"


def test_referencia_no_rustica():
    assert refcat_no_rustica(" 9990001 00xx00x 0001 ab ") == RC
    assert refcat_no_rustica("99999A001000120000AB") == ""      # rústica: va por polígono y parcela
    assert refcat_no_rustica("no es una referencia") == ""


def test_entrada_urbano_no_queda_sin_datos():
    filas = [["Concello", "Provincia", "Ref. Catastral", "Nombre"],
             ["Villaejemplo", "Provincia X", RC, "Casa"]]
    fincas, _ = filas_a_fincas(filas)
    f = fincas[0]
    assert not f.sin_datos and f.por_referencia and f.refcat == RC
    assert f.etiqueta == "Villaejemplo ref 999000100XX00X"
    assert f.nombre_base == "villaejemplo_ref999000100XX00X"
    assert f.clave_parcela == "rc_999000100XX00X"


def test_parser_urbano_respuesta_real():
    d = parsear_dnppp((FIX / "dnprc_urbano.xml").read_bytes())
    assert d.clase == "Urbano" and d.refcat == "999000100XX00X"
    assert d.superficie_m2 is None and d.construida_m2 == 706   # 706 m² son construidos, no de parcela
    assert d.uso == "Residencial" and d.anio == "2000"
    assert "VIVIENDA 306 m²" in d.construcciones
    assert "706 m² construidos" in d.uso_detallado and "año 2000" in d.uso_detallado
    assert parsear_centro((FIX / "cpmrc_urbano.xml").read_bytes()) == (-8.5, 43.0)


class _Resolutor:
    def resolver(self, prov, mun, preguntar=None):
        return Codigos("15", "A CORUÑA", "099", "99", "VILLAEJEMPLO")


class _Catastro:
    def __init__(self):
        self.pedidas = []

    def consulta_referencia(self, rc):
        self.pedidas.append(rc)
        d = parsear_dnppp((FIX / "dnprc_urbano.xml").read_bytes())
        d.lon, d.lat = -8.5, 43.0
        return d

    def descargar_pdf(self, datos, cod, destino):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"%PDF-1.4 falso")
        return destino


class _Sigpac:
    def consultar(self, *a):
        raise AssertionError("un inmueble urbano no se busca en SIGPAC")


def test_pipeline_urbano(tmp_path):
    fincas, _ = filas_a_fincas([["Concello", "Provincia", "Ref. Catastral"], ["Villaejemplo", "A Coruña", RC]])
    cat = _Catastro()
    op = Opciones("urbano", salidas=tmp_path, fecha="2026-01-01", informe=False, mapa=False)
    raiz = ejecutar(fincas, op, Servicios(_Resolutor(), cat, _Sigpac(), Cache(tmp_path / ".cache")), Registro(eco=None))
    assert cat.pedidas == [RC]
    assert (raiz / "pdf_catastro" / "villaejemplo_ref999000100XX00X_catastro.pdf").exists()
    registro = (raiz / "registro.txt").read_text(encoding="utf-8")
    assert "no se consulta SIGPAC" in registro and "ERROR" not in registro


def test_mapa_situa_el_urbano_por_el_centro_del_catastro(tmp_path):
    fincas, _ = filas_a_fincas([["Concello", "Provincia", "Ref. Catastral"], ["Villaejemplo", "A Coruña", RC]])
    r = Resultado(finca=fincas[0], codigos=_Resolutor().resolver("", ""),
                  catastro=DatosCatastro(refcat=RC[:14], clase="Urbano", lon=-8.5, lat=43.0))
    pts = ServicioMapa(None, Cache(tmp_path / ".cache"), bbox_fn=lambda *a: 1 / 0).puntos([r])
    assert len(pts) == 1 and (pts[0].lon, pts[0].lat) == (-8.5, 43.0)
