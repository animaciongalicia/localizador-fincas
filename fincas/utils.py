"""Utilidades de texto y números sin dependencias externas."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def quitar_tildes(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c))


def normalizar(s: object) -> str:
    """minúsculas, sin tildes, solo [a-z0-9] separados por un espacio."""
    if s is None:
        return ""
    t = quitar_tildes(str(s)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def slug(s: str) -> str:
    """Para nombres de fichero: 'Vilanova de Arousa' -> 'vilanova-de-arousa'."""
    return normalizar(s).replace(" ", "-") or "sin-nombre"


def a_numero(valor: object) -> Optional[float]:
    """Interpreta números en formato español o inglés ('1.234,5', '1234.5', '12 345 m2')."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    t = str(valor).strip().lower()
    t = re.sub(r"m\s*[2²]|metros|cuadrados", "", t)
    t = t.replace(" ", "").replace(" ", "")
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif "." in t and re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def limpiar_codigo(valor: object) -> str:
    """Polígono/parcela: '5.0' -> '5', 'Pol. 12' -> '12', '12A' se respeta."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    t = str(valor).strip()
    t = re.sub(r"^(pol[ií]gono|pol|parcela|parc)\.?\s*", "", t, flags=re.I)
    t = re.sub(r"\.0+$", "", t).strip()
    if t.isdigit():
        return str(int(t))  # '05' y '5' son la misma parcela (misma caché y mismos ficheros)
    return t.upper()
