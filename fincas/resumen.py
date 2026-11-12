"""Cifras de resumen de un lote (puras, sin red): las usan la hoja Resumen del Excel y el informe PDF."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .modelos import Resultado

SIN_USO = "Sin dato de uso"


@dataclass
class Resumen:
    total: int = 0
    con_catastro: int = 0                 # fincas con datos del Catastro
    superficie_m2: float = 0.0            # suma de la superficie catastral
    por_municipio: List[Tuple[str, int, float]] = field(default_factory=list)  # (nombre, nº, m²)
    por_uso: List[Tuple[str, int, float]] = field(default_factory=list)
    control: Dict[str, int] = field(default_factory=dict)    # OK / REVISAR / SIN DATO CLIENTE
    coincide: Dict[str, int] = field(default_factory=dict)   # SI / NO / NO ENCONTRADA ...
    cuotas_parciales: int = 0
    con_errores: int = 0


def _ordenado(d: Dict[str, List[float]]) -> List[Tuple[str, int, float]]:
    filas = [(k, int(v[0]), float(v[1])) for k, v in d.items()]
    return sorted(filas, key=lambda t: (-t[2], t[0]))


def calcular(resultados: Sequence[Resultado]) -> Resumen:
    r = Resumen(total=len(resultados))
    mun: Dict[str, List[float]] = {}
    uso: Dict[str, List[float]] = {}
    for x in resultados:
        c = x.catastro
        sup = float(c.superficie_m2) if c and c.superficie_m2 else 0.0
        if c:
            r.con_catastro += 1
        r.superficie_m2 += sup
        m = mun.setdefault(x.finca.municipio or "Sin municipio", [0, 0.0])
        m[0] += 1
        m[1] += sup
        u = uso.setdefault((c.uso if c and c.uso else SIN_USO), [0, 0.0])
        u[0] += 1
        u[1] += sup
        if x.control_superficie:
            r.control[x.control_superficie] = r.control.get(x.control_superficie, 0) + 1
        if x.coincide:
            r.coincide[x.coincide] = r.coincide.get(x.coincide, 0) + 1
        if not x.finca.cuota.completa:
            r.cuotas_parciales += 1
        if x.errores:
            r.con_errores += 1
    r.por_municipio = _ordenado(mun)
    r.por_uso = _ordenado(uso)
    return r


def formato_m2(v: float) -> str:
    """12345.6 -> '12.346 m²' (separador de miles español)."""
    return f"{v:,.0f}".replace(",", ".") + " m²"


def formato_ha(v: float) -> str:
    """Hectáreas con 4 decimales y coma decimal."""
    return f"{v / 10_000:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") + " ha"
