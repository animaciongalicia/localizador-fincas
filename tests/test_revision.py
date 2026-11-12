"""Regresiones de la revisión de código: lectura de listas difíciles y avisos que antes eran silenciosos."""
from pathlib import Path

from fincas.cache import Cache
from fincas.entrada import leer_fincas, parsear_cuota, separador_csv, superficie_a_m2, unidad_de
from fincas.modelos import Codigos, DatosCatastro, ResultadoSigpac
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro
from fincas.sigpac import etiqueta_coincide


def _leer(tmp_path, texto, nombre="l.csv", **kw):
    p = tmp_path / nombre
    p.write_text(texto, encoding="utf-8")
    return leer_fincas(str(p), **kw)


def test_titulo_encima_y_decimales_con_coma(tmp_path):
    fs, pr = _leer(tmp_path, "LISTADO DE FINCAS;;;\n;;;\nConcello;Provincia;Pol.;Parc.;Superficie (ha);Cuota\n"
                             "Arbo;Pontevedra;42;362;0,5;1/2\n")
    assert [(f.poligono, f.parcela, f.superficie_m2) for f in fs] == [("42", "362", 5000.0)]
    assert any("convertida a 5.000 m²" in p for p in pr)


def test_separador_csv():
    assert separador_csv("a;b;c\n1;2,5;3\n") == ";"
    assert separador_csv("a,b,c\n1,2,3\n") == ","
    assert separador_csv("a\tb\n1\t2\n") == "\t"
    assert separador_csv("TITULO, con coma\na;b;c\n1;2;3\n4;5;6\n") == ";"


def test_cabeceras_por_palabra_clave_y_columnas_ignoradas(tmp_path):
    fs, pr = _leer(tmp_path, "Municipio;Provincia;Polígono nº;Nº Parcela;Superficie parcela;% propiedad;Color\n"
                             "Arbo;Pontevedra;1;7;500;50;verde\n")
    f = fs[0]
    assert (f.poligono, f.parcela, f.superficie_m2) == ("1", "7", 500.0)
    assert (f.cuota.numerador, f.cuota.denominador) == (1, 2)          # '50' bajo '% propiedad' = 50 %
    assert any("no reconocidas" in p and "Color" in p for p in pr)


def test_fila_sin_municipio_no_se_descarta(tmp_path):
    fs, pr = _leer(tmp_path, "poligono,parcela\n5,12\n5,13\n")
    assert len(fs) == 2 and all(f.sin_datos for f in fs)
    assert any("falta municipio" in p for p in pr)
    fs, _ = _leer(tmp_path, "poligono,parcela\n5,12\n", municipio="Arbo", provincia="Pontevedra")
    assert not fs[0].sin_datos and fs[0].municipio == "Arbo"


def test_parcelas_repetidas_se_avisan_y_se_mantienen(tmp_path):
    fs, pr = _leer(tmp_path, "municipio,provincia,poligono,parcela\nArbo,Pontevedra,42,362\n"
                             "Arbo,Pontevedra,042,00362\nARBO,pontevedra,42,363\n")
    assert len(fs) == 3
    assert sum("repetida" in p for p in pr) == 1


def test_superficie_unidades():
    assert unidad_de("Superficie (ha)") == (10_000.0, "ha")
    assert unidad_de("Sup. ferrados")[0] is None
    assert superficie_a_m2("2 ha")[0] == 20_000.0
    assert superficie_a_m2("12.345,6 m2") == (12345.6, "")
    assert superficie_a_m2("1.500 m²") == (1500.0, "")
    m2, aviso = superficie_a_m2("3 ferrados")
    assert m2 is None and "sin conversión" in aviso
    assert "muy pequeña" in superficie_a_m2("1,234")[1]
    assert superficie_a_m2(2500) == (2500.0, "") and superficie_a_m2("") == (None, "")
    assert superficie_a_m2(1.5, 10_000.0, "ha")[0] == 15_000.0


def test_cuotas_nuevas():
    def nd(t, **kw):
        c = parsear_cuota(t, **kw)
        return c.numerador, c.denominador, c.valida

    assert nd("mitad") == (1, 2, True) and nd("0,5") == (1, 2, True) and nd(0.25) == (1, 4, True)
    assert nd("50", en_porcentaje=True) == (1, 2, True)
    assert nd("50")[2] is False                      # sin '%' en la cabecera no se adivina
    assert nd("3/2")[2] is False and nd("0/4")[2] is False
    assert parsear_cuota(1).completa and parsear_cuota("100", en_porcentaje=True).completa


def test_etiqueta_exige_provincia_y_municipio():
    t = "36:2:0:0:42:362 (10 m²) 36:1:0:0:42:362 (101.5 m²)"
    assert etiqueta_coincide(t, "42", "362", "36", "1") == 101.5
    assert etiqueta_coincide(t, "42", "362", "36", "002") == 10
    assert etiqueta_coincide(t, "42", "362", "15", "1") is None
    assert etiqueta_coincide(t, "42", "362") == 10          # sin códigos: comportamiento anterior


class _Res:
    def resolver(self, prov, mun, preguntar=None):
        assert mun != "sin municipio", "las filas SIN DATOS no deben resolver códigos"
        return Codigos("36", "PONTEVEDRA", "001", "1", "ARBO")


class _Cat:
    def consulta_parcela(self, cod, pol, parc):
        return DatosCatastro(refcat=f"36001A{pol.zfill(3)}{parc.zfill(5)}", superficie_m2=100.0, uso="Pastos")

    def descargar_pdf(self, datos, cod, destino):
        destino.write_bytes(b"%PDF-1.4 fake")


class _Sig:
    def __init__(self):
        self.n = 0

    def consultar(self, finca, cod, refcat, destino):
        self.n += 1
        destino.write_bytes(b"%PDF-1.4 fake")
        return ResultadoSigpac("ENCONTRADA", refcat=refcat, pdf=destino.name, respaldo=True)


def test_ficha_de_respaldo_avisa_y_no_se_cachea(tmp_path):
    fs, _ = _leer(tmp_path, "municipio,provincia,poligono,parcela\nArbo,Pontevedra,42,362\n,,5,\n")
    fs[1].sin_datos = fs[1].sin_datos or "SIN DATOS"
    sig = _Sig()
    for i in (1, 2):
        reg = Registro(eco=None)
        srv = Servicios(_Res(), _Cat(), sig, Cache(tmp_path / ".cache"))
        raiz = ejecutar(fs, Opciones(f"l{i}", salidas=tmp_path, fecha="2026-01-01", informe=False), srv, reg)
        txt = (raiz / "registro.txt").read_text(encoding="utf-8")
        assert "captura de respaldo" in txt
    assert sig.n == 2                                   # la segunda vez NO sale de la caché: se reintenta


def test_municipio_aproximado_queda_avisado(tmp_path):
    class Res:
        def resolver(self, prov, mun, preguntar=None):
            return Codigos("36", "PONTEVEDRA", "061", "61", "VILANOVA DE AROUSA")

    fs, _ = _leer(tmp_path, "municipio,provincia,poligono,parcela\nVilanova,Pontevedra,5,1\n")
    reg = Registro(eco=None)
    raiz = ejecutar(fs, Opciones("l", salidas=tmp_path, fecha="2026-01-01", informe=False),
                    Servicios(Res(), _Cat(), None, Cache(tmp_path / ".c")), reg)
    assert "interpretado como VILANOVA DE AROUSA" in (raiz / "registro.txt").read_text(encoding="utf-8")
