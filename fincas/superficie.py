"""Control de superficie. Solo avisa: nunca descarta parcelas."""
from __future__ import annotations

from typing import Optional

from . import config
from .modelos import Cuota


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
    if cuota.valida and not cuota.completa and cuota.denominador > 1:
        candidatos.append(cliente * cuota.denominador)
    for ref in candidatos:
        if abs(catastral - ref) / ref <= margen:
            return "OK"
    return "REVISAR"
