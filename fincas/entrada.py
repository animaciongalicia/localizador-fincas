"""Lectura de la lista de fincas desde CSV, XLSX o DOCX (tabla de Word).

Solo conoce el formato de entrada. No sabe nada de Catastro ni SIGPAC.
"""
from __future__ import annotations

import csv
import io
import re
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .modelos import Cuota, Finca
from .utils import a_numero, limpiar_codigo, normalizar

# Nombre canónico -> variantes (ya normalizadas: sin tildes, minúsculas, sin símbolos)
ALIAS: Dict[str, List[str]] = {
    "municipio": ["municipio", "concello", "concejo", "ayuntamiento", "municipi", "termino municipal",
                  "localidad"],
    "provincia": ["provincia", "provincia catastral"],
    "poligono": ["poligono", "poligon", "pol", "poligono catastral", "n poligono"],
    "parcela": ["parcela", "parc", "parcela catastral", "n parcela", "num parcela"],
    "paraje": ["paraje", "paraxe", "lugar", "sitio", "toponimo", "nombre paraje"],
    "superficie_m2": ["superficie m2", "superficie", "sup", "sup m2", "m2", "superficie cliente",
                      "metros", "superficie m"],
    "cuota": ["cuota", "participacion", "porcentaje", "cota", "quota", "propiedad"],
    "notas": ["notas", "nota", "observaciones", "observacions", "comentarios"],
}
OBLIGATORIAS = ("poligono", "parcela")  # municipio/provincia pueden venir por parámetro
_INVERSO = {v: k for k, vs in ALIAS.items() for v in vs}


class ErrorEntrada(Exception):
    pass


def parsear_cuota(texto: object) -> Cuota:
    """'1/5' -> 1/5; '20%' -> 1/5; vacío/'completa' -> completa."""
    t = "" if texto is None else str(texto).strip()
    if not t:
        return Cuota()
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        n, d = int(m.group(1)), int(m.group(2))
        if d == 0:
            return Cuota(t, 0, 0, completa=False, valida=False)
        if n == d:
            return Cuota("completa")
        f = Fraction(n, d)
        return Cuota(t, f.numerator, f.denominator, completa=False)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    if m:
        p = float(m.group(1).replace(",", "."))
        if p >= 100:
            return Cuota("completa")
        if p <= 0:
            return Cuota(t, 0, 0, completa=False, valida=False)
        f = Fraction(p / 100).limit_denominator(100)
        return Cuota(t, f.numerator, f.denominator, completa=False)
    if normalizar(t) in {"completa", "completo", "total", "entera", "100", "1", "pleno dominio",
                         "plena propiedad", "full"}:
        return Cuota("completa")
    return Cuota(t, 0, 0, completa=False, valida=False)


# ---------------------------------------------------------------- filas -> fincas
def _mapear_cabecera(fila: Sequence[object]) -> Dict[str, int]:
    mapa: Dict[str, int] = {}
    for i, celda in enumerate(fila):
        canon = _INVERSO.get(normalizar(celda))
        if canon and canon not in mapa:
            mapa[canon] = i
    return mapa


def _buscar_cabecera(filas: Sequence[Sequence[object]]) -> Optional[Tuple[int, Dict[str, int]]]:
    for i, fila in enumerate(filas[:15]):
        mapa = _mapear_cabecera(fila)
        if all(c in mapa for c in OBLIGATORIAS):
            return i, mapa
    return None


def filas_a_fincas(filas: Sequence[Sequence[object]], municipio: str = "", provincia: str = "",
                   origen: str = "") -> Tuple[List[Finca], List[str]]:
    """Devuelve (fincas, problemas). Una fila mala no impide leer las demás."""
    hallado = _buscar_cabecera(filas)
    if not hallado:
        raise ErrorEntrada(
            f"{origen}: no encuentro la cabecera. Hacen falta al menos las columnas "
            f"poligono y parcela (nombres admitidos: {', '.join(ALIAS['poligono'] + ALIAS['parcela'])})."
        )
    idx, mapa = hallado
    fincas: List[Finca] = []
    problemas: List[str] = []
    mun_prev, prov_prev = municipio, provincia

    def celda(fila, campo) -> object:
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    for n, fila in enumerate(filas[idx + 1:], start=idx + 2):
        pol = limpiar_codigo(celda(fila, "poligono"))
        par = limpiar_codigo(celda(fila, "parcela"))
        if not pol and not par:
            continue  # fila vacía
        mun = str(celda(fila, "municipio") or "").strip() or mun_prev
        prov = str(celda(fila, "provincia") or "").strip() or prov_prev
        mun_prev, prov_prev = mun, prov  # relleno hacia abajo
        etiqueta = f"{origen} fila {n}"
        if not pol or not par:
            problemas.append(f"{etiqueta}: falta polígono o parcela (pol='{pol}', parc='{par}')")
            continue
        if not mun:
            problemas.append(f"{etiqueta}: sin municipio (usa --municipio)")
            continue
        if not prov:
            problemas.append(f"{etiqueta}: sin provincia (usa --provincia)")
            continue
        cuota = parsear_cuota(celda(fila, "cuota"))
        if not cuota.valida:
            problemas.append(f"{etiqueta}: cuota '{cuota.texto}' no interpretable (se marca, no se descarta)")
        fincas.append(Finca(
            municipio=mun, provincia=prov, poligono=pol, parcela=par,
            paraje=str(celda(fila, "paraje") or "").strip(),
            superficie_m2=a_numero(celda(fila, "superficie_m2")),
            cuota=cuota, notas=str(celda(fila, "notas") or "").strip(), fila=n,
        ))
    return fincas, problemas


# ---------------------------------------------------------------- lectores
def _leer_csv(ruta: Path) -> List[List[str]]:
    raw = ruta.read_bytes()
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("cp1252")
    try:
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
    return [list(f) for f in csv.reader(io.StringIO(texto), dialecto)]


def _leer_xlsx(ruta: Path) -> List[List[object]]:
    from openpyxl import load_workbook

    wb = load_workbook(ruta, data_only=True, read_only=True)
    hojas = []
    for ws in wb.worksheets:
        hojas.append([list(r) for r in ws.iter_rows(values_only=True)])
    return hojas  # type: ignore[return-value]


def _leer_docx(ruta: Path) -> List[List[List[str]]]:
    from docx import Document  # python-docx

    doc = Document(str(ruta))
    tablas = []
    for t in doc.tables:
        filas = []
        for r in t.rows:
            filas.append([c.text.strip() for c in r.cells])
        tablas.append(filas)
    return tablas


def leer_fincas(ruta: str, municipio: str = "", provincia: str = "") -> Tuple[List[Finca], List[str]]:
    """Punto de entrada del módulo. Devuelve (fincas, problemas)."""
    p = Path(ruta)
    if not p.exists():
        raise ErrorEntrada(f"No existe el fichero: {p}")
    ext = p.suffix.lower()
    if ext in (".csv", ".txt"):
        bloques = [_leer_csv(p)]
    elif ext in (".xlsx", ".xlsm"):
        bloques = _leer_xlsx(p)
    elif ext == ".docx":
        bloques = _leer_docx(p)
    else:
        raise ErrorEntrada(f"Formato no soportado: {ext} (usa CSV, XLSX o DOCX)")

    fincas: List[Finca] = []
    problemas: List[str] = []
    errores_cabecera: List[str] = []
    for i, filas in enumerate(bloques, start=1):
        try:
            f, pr = filas_a_fincas(filas, municipio, provincia, origen=f"{p.name}[{i}]")
        except ErrorEntrada as e:
            errores_cabecera.append(str(e))  # hoja/tabla sin cabecera: se ignora si otra sirve
            continue
        fincas.extend(f)
        problemas.extend(pr)
    if not fincas and errores_cabecera:
        raise ErrorEntrada(errores_cabecera[0])
    return fincas, problemas
