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
    "refcat": ["referencia catastral", "ref catastral", "refcat", "ref cat", "rc", "referencia",
               "ref catastro", "referencia catastro"],
    "paraje": ["paraje", "paraxe", "lugar", "sitio", "toponimo", "nombre paraje", "nombre finca", "nombre", "finca",
               "denominacion"],
    "superficie_m2": ["superficie m2", "superficie", "sup", "sup m2", "m2", "superficie cliente",
                      "metros", "superficie m"],
    "cuota": ["cuota", "participacion", "porcentaje", "cota", "quota", "propiedad"],
    "notas": ["notas", "nota", "observaciones", "observacions", "comentarios"],
}
OBLIGATORIAS = ("poligono", "parcela")  # municipio/provincia pueden venir por parámetro; sin ellas vale refcat
# Referencia catastral rústica: prov(2) mun(3) sector(1) pol(3) parcela(5) [instancia(4) control(2)]
_RE_RC_RUSTICA = re.compile(r"^(\d{2})(\d{3})([A-Z])(\d{3})(\d{5})(?:\d{4}[A-Z]{2})?$")


_RE_CODIGO_SEGURO = re.compile(r"^[0-9A-Za-z]{1,6}(?:[-_.][0-9A-Za-z]{1,6})?$")


_RE_RC = re.compile(r"^[0-9A-Z]{14}(?:\d{4}[A-Z]{2})?$")   # cualquier referencia catastral (14 o 20 caracteres)


def refcat_no_rustica(texto: object) -> str:
    """Referencia catastral válida que NO es rústica (inmueble urbano), en mayúsculas y sin separadores; '' si no."""
    t = re.sub(r"[\s\-.]", "", str(texto or "")).upper()
    return t if _RE_RC.match(t) and not _RE_RC_RUSTICA.match(t) else ""


def pol_par_desde_refcat(texto: object) -> Tuple[str, str]:
    """'99999A001000120000AB' -> ('1', '12'). Si no es una RC rústica reconocible: ('', '')."""
    t = re.sub(r"[\s\-.]", "", str(texto or "")).upper()
    m = _RE_RC_RUSTICA.match(t)
    if not m:
        return "", ""
    return str(int(m.group(4))), str(int(m.group(5)))
_INVERSO = {v: k for k, vs in ALIAS.items() for v in vs}


class ErrorEntrada(Exception):
    pass


_CUOTA_PALABRAS = {"mitad": (1, 2), "la mitad": (1, 2), "medio": (1, 2), "tercio": (1, 3), "un tercio": (1, 3),
                   "tercera parte": (1, 3), "cuarto": (1, 4), "un cuarto": (1, 4), "cuarta parte": (1, 4),
                   "quinto": (1, 5), "un quinto": (1, 5), "quinta parte": (1, 5), "sexta parte": (1, 6),
                   "dos tercios": (2, 3), "tres cuartos": (3, 4)}


def parsear_cuota(texto: object, en_porcentaje: bool = False) -> Cuota:
    """'1/5' -> 1/5; '20%' -> 1/5; 'mitad' -> 1/2; '0,5' -> 1/2; vacío/'completa' -> completa.
    `en_porcentaje`: la columna se titula '%' o 'porcentaje', así que '50' significa 50 %."""
    if isinstance(texto, float) and texto.is_integer():
        texto = int(texto)
    t = "" if texto is None else str(texto).strip()
    if not t:
        return Cuota()
    if normalizar(t) in _CUOTA_PALABRAS:
        n, d = _CUOTA_PALABRAS[normalizar(t)]
        return Cuota(t, n, d, completa=False)
    if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
        v = float(t.replace(",", "."))
        if 0 < v < 1:  # 0,5 -> 1/2
            f = Fraction(v).limit_denominator(100)
            return Cuota(t, f.numerator, f.denominator, completa=False)
        if en_porcentaje and 1 <= v <= 100:
            t_pct = f"{t}%"
            c = parsear_cuota(t_pct)
            return c
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        n, d = int(m.group(1)), int(m.group(2))
        if d == 0:
            return Cuota(t, 0, 0, completa=False, valida=False)
        if n == d:
            return Cuota("completa")
        if n > d or n == 0:  # 3/2 no es una cuota: se marca como no interpretable
            return Cuota(t, 0, 0, completa=False, valida=False)
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
# Palabras sueltas que identifican una columna aunque el rótulo no sea un alias exacto
# ('Polígono nº', 'Sup. catastral (ha)', '% propiedad'). Orden = prioridad.
_CLAVES = [  # superficie y cuota antes que parcela: 'Superficie parcela' es una superficie, no una parcela
    ("refcat", {"refcat", "rc"}),
    ("superficie_m2", {"superficie", "sup", "m2", "hectareas", "ha", "ferrados"}),
    ("cuota", {"cuota", "participacion", "porcentaje", "propiedad"}),
    ("municipio", {"municipio", "concello", "concejo", "ayuntamiento"}), ("provincia", {"provincia"}),
    ("poligono", {"poligono", "poligon", "pol"}), ("parcela", {"parcela", "parc"}),
    ("notas", {"notas", "observaciones", "comentarios"}), ("paraje", {"paraje", "paraxe", "lugar"}),
]
_UNIDADES = {"ha": 10_000.0, "has": 10_000.0, "hectareas": 10_000.0, "hectarea": 10_000.0,
             "a": 100.0, "areas": 100.0, "area": 100.0, "m2": 1.0, "m": 1.0, "metros": 1.0}
_SIN_CONVERSION = {"ferrados", "ferrado", "fanegas", "fanega", "cuncas", "tegas"}  # varían por concello


def _canon_cabecera(celda: object) -> Optional[str]:
    n = normalizar(celda)
    if not n:
        return None
    if n in _INVERSO:
        return _INVERSO[n]
    palabras = set(n.split())
    if {"referencia", "catastral"} <= palabras or {"ref", "catastral"} <= palabras:
        return "refcat"
    for canon, claves in _CLAVES:
        if palabras & claves:
            return canon
    return None


def _mapear_cabecera(fila: Sequence[object]) -> Dict[str, int]:
    mapa: Dict[str, int] = {}
    for i, celda in enumerate(fila):
        canon = _canon_cabecera(celda)
        if canon and canon not in mapa:
            mapa[canon] = i
    return mapa


def unidad_de(texto: object) -> Tuple[Optional[float], str]:
    """(factor a m², unidad) según las palabras del texto. (None, 'ferrados') si no hay conversión fiable."""
    palabras = normalizar(texto).split()
    for w in palabras:
        if w in _SIN_CONVERSION:
            return None, w
    for w in palabras:
        if w in ("ha", "has", "hectareas", "hectarea", "areas", "area"):
            return _UNIDADES[w], w
    return 1.0, ""


def superficie_a_m2(valor: object, factor_cabecera: Optional[float] = 1.0,
                    unidad_cabecera: str = "") -> Tuple[Optional[float], str]:
    """Superficie del cliente en m² y, si procede, un aviso. Nunca inventa una conversión."""
    if valor is None or str(valor).strip() == "":
        return None, ""
    factor, unidad = factor_cabecera, unidad_cabecera
    if isinstance(valor, str):
        f_celda, u_celda = unidad_de(re.sub(r"[\d.,]", " ", valor))
        if u_celda:
            factor, unidad = f_celda, u_celda
    if factor is None:
        return None, f"superficie '{valor}' en {unidad}: sin conversión fiable a m² (no se compara)"
    if isinstance(valor, str):  # quitar la unidad ('m2', 'm²', 'ha', 'metros') sin comerse dígitos
        valor_num: object = re.sub(r"[A-Za-zÀ-ÿ]+\.?", " ", re.sub(r"(?i)m\s*[2²]", " ", valor))
    else:
        valor_num = valor
    num = a_numero(valor_num)
    if num is None:
        return None, f"superficie '{valor}' no interpretable (no se compara)"
    m2 = num * factor
    if factor != 1.0:
        return m2, f"superficie '{valor}' en {unidad} convertida a {m2:,.0f} m²".replace(",", ".")
    if 0 < m2 < 10:
        return m2, f"superficie '{valor}' = {m2} m²: muy pequeña, ¿está en otra unidad o es un separador de miles?"
    return m2, ""


def _buscar_cabecera(filas: Sequence[Sequence[object]]) -> Optional[Tuple[int, Dict[str, int]]]:
    for i, fila in enumerate(filas[:15]):
        mapa = _mapear_cabecera(fila)
        if all(c in mapa for c in OBLIGATORIAS) or "refcat" in mapa:
            return i, mapa
    return None


def filas_a_fincas(filas: Sequence[Sequence[object]], municipio: str = "", provincia: str = "",
                   origen: str = "") -> Tuple[List[Finca], List[str]]:
    """Devuelve (fincas, problemas). Una fila mala no impide leer las demás."""
    hallado = _buscar_cabecera(filas)
    if not hallado:
        raise ErrorEntrada(
            f"{origen}: no encuentro la cabecera. Hacen falta al menos las columnas "
            f"poligono y parcela, o referencia catastral (nombres admitidos: {', '.join(ALIAS['poligono'] + ALIAS['parcela'])})."
        )
    idx, mapa = hallado
    fincas: List[Finca] = []
    problemas: List[str] = []
    usadas = set(mapa.values())
    ignoradas = [str(c).strip() for i, c in enumerate(filas[idx]) if i not in usadas and str(c or "").strip()]
    if ignoradas:
        problemas.append(f"{origen}: columnas no reconocidas (se ignoran): {', '.join(ignoradas)}")
    i_sup = mapa.get("superficie_m2")
    factor_sup, unidad_sup = unidad_de(filas[idx][i_sup]) if i_sup is not None else (1.0, "")
    i_cuota = mapa.get("cuota")
    cuota_en_pct = i_cuota is not None and ("%" in str(filas[idx][i_cuota]) or
                                           "porcentaje" in normalizar(filas[idx][i_cuota]))
    vistas: Dict[Tuple[str, str, str, str], int] = {}
    mun_prev, prov_prev = municipio, provincia

    def celda(fila, campo) -> object:
        i = mapa.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    for n, fila in enumerate(filas[idx + 1:], start=idx + 2):
        pol = limpiar_codigo(celda(fila, "poligono"))
        par = limpiar_codigo(celda(fila, "parcela"))
        rc = str(celda(fila, "refcat") or "").strip()
        rc_pol, rc_par = pol_par_desde_refcat(rc)
        if not pol and not par and not rc and not str(celda(fila, "paraje") or "").strip():
            continue  # fila vacía
        etiqueta = f"{origen} fila {n}"
        if not pol and not par and rc_pol:
            pol, par = rc_pol, rc_par  # entrada solo por referencia catastral
        elif rc_pol and (pol, par) != (rc_pol, rc_par):
            problemas.append(f"{etiqueta}: la referencia catastral {rc} indica pol {rc_pol}/parc {rc_par} "
                             f"pero la fila dice {pol}/{par} (se usa lo de la fila)")
        mun = str(celda(fila, "municipio") or "").strip() or mun_prev
        prov = str(celda(fila, "provincia") or "").strip() or prov_prev
        mun_prev, prov_prev = mun, prov  # relleno hacia abajo
        sin_datos = ""
        if not mun or not prov:  # nunca se descarta una fila: se conserva en el Excel sin buscarla
            falta = "municipio" if not mun else "provincia"
            sin_datos = f"SIN DATOS: falta {falta} (usa --{falta} o complétalo en la lista)"
            problemas.append(f"{etiqueta}: {sin_datos} (se mantiene en el Excel, no se busca)")
            mun, prov = mun or "sin municipio", prov or ""
        if (pol and not _RE_CODIGO_SEGURO.match(pol)) or (par and not _RE_CODIGO_SEGURO.match(par)):
            problemas.append(f"{etiqueta}: polígono/parcela con caracteres no válidos ('{pol}', '{par}')")
            pol = par = ""
        rc_urb = refcat_no_rustica(rc) if not (pol and par) else ""   # urbano: se busca por su referencia
        if (not pol or not par) and not sin_datos and not rc_urb:
            sin_datos = (f"SIN DATOS: falta polígono/parcela y la referencia catastral "
                         f"{'no es interpretable' if rc else 'no consta'}; hay que pedírsela al cliente")
            problemas.append(f"{etiqueta}: {sin_datos} (se mantiene en el Excel, no se busca)")
        cuota = parsear_cuota(celda(fila, "cuota"), en_porcentaje=cuota_en_pct)
        if not cuota.valida:
            problemas.append(f"{etiqueta}: cuota '{cuota.texto}' no interpretable (se marca, no se descarta)")
        sup, aviso_sup = superficie_a_m2(celda(fila, "superficie_m2"), factor_sup, unidad_sup)
        if aviso_sup:
            problemas.append(f"{etiqueta}: {aviso_sup}")
        if pol and par and not sin_datos:
            k = (normalizar(prov), normalizar(mun), pol, par)
            if k in vistas:
                problemas.append(f"{etiqueta}: parcela repetida (misma que la fila {vistas[k]}); se mantienen las dos")
            else:
                vistas[k] = n
        fincas.append(Finca(
            municipio=mun, provincia=prov, poligono=pol, parcela=par,
            paraje=str(celda(fila, "paraje") or "").strip(),
            superficie_m2=sup,
            cuota=cuota, notas=str(celda(fila, "notas") or "").strip(), fila=n, sin_datos=sin_datos,
            refcat="" if sin_datos else rc_urb,
        ))
    return fincas, problemas


# ---------------------------------------------------------------- lectores
def _leer_csv(ruta: Path) -> List[List[str]]:
    raw = ruta.read_bytes()
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("cp1252")
    return [list(f) for f in csv.reader(io.StringIO(texto), delimiter=separador_csv(texto))]


def separador_csv(texto: str) -> str:
    """El separador que más se repite de forma constante en las primeras líneas con contenido."""
    lineas = [l for l in texto.splitlines()[:30] if l.strip()]
    mejor, puntos = ",", -1.0
    for sep in (";", "\t", "|", ","):
        cuentas = [l.count(sep) for l in lineas]
        con = [c for c in cuentas if c > 0]
        if not con:
            continue
        moda = max(set(con), key=con.count)
        p = con.count(moda) * 10 + moda - (0.5 if sep == "," else 0)  # la coma también es decimal: desempata peor
        if p > puntos:
            mejor, puntos = sep, p
    return mejor


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
