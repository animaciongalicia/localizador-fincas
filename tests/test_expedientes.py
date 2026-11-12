"""Numeración de expedientes y trazabilidad de fechas de consulta. Sin red."""
from datetime import datetime

from openpyxl import load_workbook

from fincas import expedientes
from fincas.cache import Cache
from fincas.entrada import leer_fincas
from fincas.modelos import fecha_legible
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro
from test_pipeline import EJEMPLO, Catastro, Resolutor, Sigpac


def test_expedientes_correlativos_por_anio(tmp_path):
    libro = tmp_path / "sub" / "expedientes.csv"
    a = expedientes.siguiente(libro, "Cliente A", "lote-1", ahora=datetime(2026, 11, 12, 9, 0))
    b = expedientes.siguiente(libro, "Cliente; con punto y coma", "lote-2", ahora=datetime(2026, 11, 12, 9, 5))
    c = expedientes.siguiente(libro, "Cliente C", "lote-3", ahora=datetime(2027, 1, 2, 10, 0))
    assert (a, b, c) == ("FER-2026-0001", "FER-2026-0002", "FER-2027-0001")
    lineas = libro.read_text(encoding="utf-8-sig").splitlines()
    assert lineas[0] == "expediente;fecha;cliente;lote" and len(lineas) == 4
    assert lineas[1] == "FER-2026-0001;2026-11-12 09:00;Cliente A;lote-1"
    assert expedientes.siguiente(libro, prefijo="XX", ahora=datetime(2026, 1, 1)) == "XX-2026-0001"


def test_fecha_legible():
    assert fecha_legible("2026-11-12T09:01") == "12/11/2026 09:01"
    assert fecha_legible("") == "" and fecha_legible("basura") == ""


def test_fecha_de_consulta_se_conserva_al_salir_de_cache(tmp_path):
    fincas, _ = leer_fincas(str(EJEMPLO))
    cache = Cache(tmp_path / ".cache")

    def lanzar(nombre):
        op = Opciones(nombre, salidas=tmp_path, fecha="2026-01-01", prueba=1, cliente="Ana Pérez",
                      expediente="FER-2026-0042")
        raiz = ejecutar(fincas, op, Servicios(Resolutor(), Catastro(), Sigpac(), cache), Registro(eco=None))
        wb = load_workbook(raiz / "resultados.xlsx")
        ws = wb["Fincas"]
        cab = [c.value for c in ws[1]]
        fila = [c.value for c in ws[2]]
        return raiz, wb, fila[cab.index("consulta Catastro")], fila[cab.index("consulta SIGPAC")]

    raiz, wb, f_cat, f_sig = lanzar("a")
    assert f_cat and f_sig
    textos = [c for fila in wb["Resumen"].iter_rows(values_only=True) for c in fila if c]
    assert "FER-2026-0042" in textos and "Ana Pérez" in textos and "ferrados.com" in textos
    assert "Expediente FER-2026-0042" in (raiz / "registro.txt").read_text(encoding="utf-8")
    # segunda ejecución: todo sale de la caché y la fecha sigue siendo la de la consulta original
    import json
    p = next((tmp_path / ".cache" / "catastro").glob("*.json"))
    d = json.loads(p.read_text(encoding="utf-8"))
    d["consultado"] = "2020-02-03T04:05"
    p.write_text(json.dumps(d), encoding="utf-8")
    _, _, f_cat2, _ = lanzar("b")
    assert f_cat2 == "03/02/2020 04:05"
