"""Precio de referencia de la tierra rústica en la comarca de cada finca (solo Galicia). Sin red.

Fuente: Observatorio Galego de Mobilidade de Terras (AGADER, Xunta de Galicia), informe anual 2024: precio medio de
las compraventas declaradas a la Axencia Tributaria de Galicia en 2021-2023, por comarca, separado en monte y
superficie agraria (SAU). Las tablas van en `fincas/datos/` (datos públicos; se actualizan a mano cuando salga un
informe nuevo). NO es una tasación: es un precio medio comarcal, que el propio informe califica de orientativo. Por
eso aquí nunca se multiplica por la superficie de la finca.

DECISIÓN (2026-11-13): el precio medio comarcal YA NO se muestra en el informe (el cliente lo lee como el precio de su
finca y no lo es). Del mismo informe se usa la TASA DE MOVILIDAD (qué parte de la tierra cambia de manos al año), que
sí es un dato objetivo sobre lo que cuesta vender. `madera()` da precios de la madera en pie tomados de la prensa del
sector, siempre como horquilla con su fecha.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from .modelos import Resultado
from .utils import quitar_tildes

DATOS = Path(__file__).parent / "datos"
MONTE, AGRARIO = "monte", "tierra agraria"
_COBERTURA_MONTE = ("Eucaliptos", "Coníferas", "Frondosas", "Matorral", "Cortas", "Regeneración", "Incendio",
                    "Área quemada")
_USO_MONTE = ("PINAR", "PINO", "EUCALIPT", "MATORRAL", "MONTE", "ROBLE", "FRONDOSA", "CASTAN", "ARBOLADO",
              "CONIFERA", "SOUTO", "RIBERA", "ESPECIES MEZCLADAS", "TOJAL", "FORESTAL")
_USO_AGRARIO = ("PRADO", "PRADERA", "LABOR", "LABRADIO", "PASTO", "HUERTA", "CULTIVO", "VINA", "FRUTAL", "REGADIO")


def _clave(nombre: str) -> str:
    return " ".join(quitar_tildes(nombre or "").lower().split())


@lru_cache(maxsize=1)
def _tablas() -> Dict[str, dict]:
    comarcas = json.loads((DATOS / "comarcas_galicia.json").read_text(encoding="utf-8"))
    precios = json.loads((DATOS / "precios_tierra_galicia.json").read_text(encoding="utf-8"))
    precios["por_clave"] = {_clave(k): dict(v, comarca=k) for k, v in precios["precios"].items()}
    return {"comarcas": comarcas["comarcas"], "precios": precios}


def fuente() -> Dict[str, str]:
    p = _tablas()["precios"]
    return {"fuente": p["fuente"], "url": p["url"], "periodo": p["periodo"]}


def comarca_de(cod_provincia: str, cod_municipio_ine: str) -> str:
    """Comarca del concello por su código INE ('15' + '077'); '' si no consta."""
    return _tablas()["comarcas"].get(f"{cod_provincia}{str(cod_municipio_ine).zfill(3)}", "")


def tipo_terreno(r: Resultado) -> str:
    """'monte' o 'tierra agraria' según lo que hay HOY (inventario); si no hay inventario, según el Catastro.
    '' si no se puede decir (p. ej. un inmueble urbano)."""
    c, inv = r.catastro, r.inventario
    if c and c.clase == "Urbano":
        return ""
    if inv and inv.coberturas:
        monte = sum(v for k, v in inv.coberturas.items() if k in _COBERTURA_MONTE)
        agrario = inv.coberturas.get("Prado o cultivo", 0.0)
        if monte or agrario:
            return MONTE if monte >= agrario else AGRARIO
    uso = quitar_tildes(c.uso if c else "").upper()
    if any(p in uso for p in _USO_MONTE):
        return MONTE
    if any(p in uso for p in _USO_AGRARIO):
        return AGRARIO
    return ""


def referencia(r: Resultado) -> Optional[dict]:
    """{'comarca', 'tipo', 'monte', 'agrario', 'precio'} en €/m² (None donde el informe no publica dato).
    None si la finca no está en Galicia o no se conoce su comarca."""
    cod = r.codigos
    if not cod or not r.catastro:
        return None
    comarca = comarca_de(cod.cod_provincia, cod.cod_municipio_ine)
    if not comarca:
        return None
    p = _tablas()["precios"]["por_clave"].get(_clave(comarca), {})
    tipo = tipo_terreno(r)
    monte, agrario = p.get("monte"), p.get("sau")
    return {"comarca": p.get("comarca") or comarca, "tipo": tipo, "monte": monte, "agrario": agrario,
            "precio": monte if tipo == MONTE else agrario if tipo == AGRARIO else None}


def movilidad(r: Resultado) -> Optional[dict]:
    """{'comarca', 'tasa'}: % de la tierra rústica de la comarca que se compra y vende cada año. None si no consta."""
    ref = referencia(r)
    if not ref:
        return None
    p = _tablas()["precios"]["por_clave"].get(_clave(ref["comarca"]), {})
    tasa = p.get("movilidad_total")
    return {"comarca": ref["comarca"], "tasa": tasa} if tasa is not None else None


@lru_cache(maxsize=1)
def madera() -> dict:
    """Precios orientativos de la madera en pie y lo que los hace variar (tabla revisable a mano)."""
    return json.loads((DATOS / "madera_galicia.json").read_text(encoding="utf-8"))


def euros_m2(v: Optional[float]) -> str:
    return "sin dato publicado" if v is None else f"{v:.2f} €/m²".replace(".", ",")


def euros_ha(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 10000:,.0f} €/ha".replace(",", ".")
