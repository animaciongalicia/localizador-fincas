"""Control de superficie. Solo avisa: nunca descarta parcelas."""
from __future__ import annotations

from typing import Optional

from . import config
from .modelos import Cuota


def _m2(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".") + " m²"


def explicar_control(catastral: Optional[float], cliente: Optional[float], cuota: Cuota,
                     control: str) -> str:
    """Texto breve que explica un control distinto de OK (vacío si es OK). Solo informa."""
    if control == "OK":
        return ""
    if control == "NO APLICA":
        return "Inmueble urbano: la superficie de la parcela figura en la ficha catastral"
    if catastral is None:
        return "Sin superficie catastral: no se pudo comparar"
    if control == "SIN DATO CLIENTE" or cliente is None or cliente <= 0:
        return f"El cliente no indicó superficie (Catastro: {_m2(catastral)})"
    dif = (catastral - cliente) / cliente * 100
    txt = f"Catastro {_m2(catastral)} vs cliente {_m2(cliente)} ({dif:+.0f} %)"
    if cuota.valida and not cuota.completa and cuota.denominador > 1 and cuota.numerador > 0:
        total = cliente * cuota.denominador / cuota.numerador
        txt += f"; con cuota {cuota.texto} la finca entera serían {_m2(total)}"
    return txt


def control_superficie(catastral: Optional[float], cliente: Optional[float], cuota: Cuota,
                       margen: float = config.MARGEN_SUPERFICIE) -> str:
    """'OK' si la sup. catastral coincide (±margen) con la del cliente o con
    cliente x denominador de la cuota; 'REVISAR' si no.
    'SIN DATO CLIENTE' si el cliente no dio superficie (no hay nada que comparar).
    """
    if catastral is None:
        return "REVISAR"
    if cliente is None or cliente <= 0:
        return "SIN DATO CLIENTE"
    candidatos = [cliente]
    if cuota.valida and not cuota.completa and cuota.denominador > 1 and cuota.numerador > 0:
        candidatos.append(cliente * cuota.denominador / cuota.numerador)  # 2/5 -> total = cliente x 2,5
    for ref in candidatos:
        if abs(catastral - ref) / ref <= margen:
            return "OK"
    return "REVISAR"
