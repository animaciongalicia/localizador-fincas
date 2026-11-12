from pathlib import Path

from fincas.cache import Cache
from fincas.entrada import filas_a_fincas, parsear_cuota
from fincas.excel import _seguro
from fincas.superficie import control_superficie
from fincas.utils import limpiar_codigo


def test_cuota_no_unitaria_total_correcto():
    # cliente declara 1.000 m2 de su 2/5 -> total 2.500 m2
    assert control_superficie(2500, 1000, parsear_cuota("2/5")) == "OK"
    assert control_superficie(5000, 1000, parsear_cuota("2/5")) == "REVISAR"
    assert control_superficie(5000, 1000, parsear_cuota("1/5")) == "OK"  # caso con numerador 1 sigue igual


def test_cache_corrupta_y_atomica(tmp_path: Path):
    c = Cache(tmp_path)
    c.guardar("x", "k", {"a": 1})
    assert c.leer("x", "k") == {"a": 1} and not list(tmp_path.rglob("*.tmp"))
    (tmp_path / "x" / "k.json").write_text("[1, 2]")          # JSON válido pero no es dict
    assert c.leer("x", "k") is None
    (tmp_path / "x" / "k.json").write_text("{trunc")           # truncado
    assert c.leer("x", "k") is None
    c.borrar("x", "k")
    assert not (tmp_path / "x" / "k.json").exists()


def test_cache_pdf_valido(tmp_path: Path):
    c = Cache(tmp_path)
    mal, bien = tmp_path / "mal.pdf", tmp_path / "bien.pdf"
    mal.write_bytes(b"<html>error</html>")
    bien.write_bytes(b"%PDF-1.4 ...")
    c.guardar_fichero("p", "mal", mal)
    c.guardar_fichero("p", "bien", bien)
    assert c.fichero("p", "mal") is None and c.fichero("p", "bien") is not None


def test_excel_sin_formulas_ni_caracteres_ilegales():
    assert _seguro("=HYPERLINK(\"http://x\")").startswith("'=")
    assert _seguro("@SUM(A1)").startswith("'@")
    assert _seguro("error\x07raro") == "errorraro"
    assert _seguro(12.5) == 12.5 and _seguro("normal") == "normal"


def test_codigos_ceros_y_letras():
    assert limpiar_codigo("05") == "5" and limpiar_codigo("5.0") == "5"
    assert limpiar_codigo("12a") == "12A"


def test_codigo_peligroso_no_se_usa_en_ficheros():
    filas = [["Concello", "Provincia", "Polígono", "Parcela"],
             ["Villaejemplo", "Provincia X", "../../x", "12/3"],
             ["Villaejemplo", "Provincia X", "5", "12"]]
    fincas, problemas = filas_a_fincas(filas)
    assert len(fincas) == 2 and fincas[0].sin_datos and fincas[0].poligono == ""
    assert fincas[1].poligono == "5" and any("no válidos" in p for p in problemas)


def test_carpeta_libre_no_pisa_lotes(tmp_path: Path):
    from fincas.pipeline import Opciones, carpeta_libre, carpeta_trabajo
    op = Opciones(nombre_trabajo="lote x", salidas=tmp_path, fecha="2026-01-01")
    primera = carpeta_libre(op)
    assert primera == carpeta_trabajo(op) and not primera.exists()
    primera.mkdir(parents=True)
    assert carpeta_libre(op) == primera  # vacía: se reutiliza
    (primera / "resultados.xlsx").write_text("x")
    segunda = carpeta_libre(op)
    assert segunda.name.endswith("lote-x_2")
    segunda.mkdir()
    (segunda / "a").write_text("x")
    assert carpeta_libre(op).name.endswith("lote-x_3")
