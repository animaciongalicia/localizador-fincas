"""Inventario Forestal Continuo de Galicia (Xunta, INVEGA): cobertura ACTUAL de cada parcela.

El cultivo del Catastro puede llevar años sin actualizarse. Este módulo pregunta al servicio público
del inventario (ArcGIS REST, capa de recintos) por la referencia catastral y devuelve los m² de cada
cobertura (eucaliptos, coníferas, frondosas, cortas, matorral, prado...) en la última edición anual.

VERIFICADO 2026-11-12 con parcelas reales de Ortigueira (pol. 34):
  {base}/INVEGA/InvegaRecintos/MapServer/0/query?where=REFCAT='<14 car.>'&outFields=*&returnGeometry=false&f=json
- Un registro por recinto y por edición (ADM_FECINI = 1 de enero del año). Una parcela puede tener varios recintos.
- Columnas S01..S12 y derivadas (S41.., S51.., S111.., S121..): m² de cada cobertura; su suma = SUTIL.
  Significado tomado de los textos del propio visor (appdata/locales/es.json).
- MVMC_MP: 1 = "Propiedad de Monte Vecinal/Monte Público" (sí/no en el visor); 0 = no.
- Vm3 / Vm3_ha: el visor las rotula "estimaciones provisionales pendientes de validar"; suelen venir vacías.
- Solo Galicia. Es teledetección (píxel ~1 m agregado por recinto): orientativo, no sustituye una visita.

Funciones puras (interpretar, contrastar...) + ServicioInventario con el cliente HTTP inyectado.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from .modelos import DatosInventario
from .utils import quitar_tildes

# nombre de la cobertura -> (columna agregada, columnas de detalle "que provienen de...")
GRUPOS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("Eucaliptos", "S01", ()),
    ("Coníferas", "S02", ()),
    ("Frondosas", "S03", ()),
    ("Cortas", "S04", ("S41", "S42", "S43")),
    ("Regeneración", "S05", ("S51", "S52", "S53")),
    ("Prado o cultivo", "S06", ()),
    ("Matorral", "S07", ()),
    ("Suelo desnudo", "S08", ()),
    ("Antrópico", "S09", ()),
    ("Agua", "S10", ()),
    ("Incendio", "S11", ("S111", "S112", "S113")),
    ("Área quemada", "S12", ("S121", "S122", "S123")),
]
ARBOLADO = ("Eucaliptos", "Coníferas", "Frondosas")
ALTERACION = ("Cortas", "Incendio", "Área quemada")   # lo que el Catastro no refleja hasta años después

# palabras del cultivo catastral (sin tildes, mayúsculas) -> cobertura del inventario que le corresponde
_EQUIVALENCIAS: List[Tuple[Tuple[str, ...], str]] = [
    (("EUCALIPT",), "Eucaliptos"),
    (("PINAR", "PINO", "CONIFERA", "ABETO"), "Coníferas"),
    (("ROBLE", "RIBERA", "CASTAN", "FRONDOSA", "SOUTO", "ABEDUL", "HAYA", "ENCINA", "ALCORNO", "CHOPO",
      "ARBOLADO"), "Frondosas"),
    (("MATORRAL", "MONTE BAJO"), "Matorral"),
    (("PRADO", "PRADERA", "PASTO", "LABOR", "LABRADIO", "VINA", "HUERTA", "FRUTAL", "CULTIVO", "OLIVAR",
      "INVERNADERO"), "Prado o cultivo"),
    (("IMPRODUCTIVO",), "Suelo desnudo"),
]
MINIMO_COHERENTE = 0.50     # las coberturas esperadas deben sumar al menos la mitad de la parcela
MINIMO_ALTERACION = 0.20    # cortas/incendio por encima de esto se avisan siempre
MINIMO_CAMBIO = 0.10        # cambio entre ediciones que merece contarse (fracción de la parcela)


def _num(v: object) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _anio(ms: object) -> int:
    """ADM_FECINI en milisegundos (1 de enero, hora peninsular) -> año de la edición."""
    try:
        return datetime.fromtimestamp(float(ms) / 1000 + 43200, tz=timezone.utc).year   # +12 h: evita el borde de año
    except (TypeError, ValueError, OSError, OverflowError):
        return 0


def _coberturas(recintos: Sequence[dict]) -> Dict[str, float]:
    """Suma los recintos de una edición. Si agregada y detalle vienen las dos y sumarlas pasa de la
    superficie con datos, es que el detalle ya está dentro de la agregada: se toma la mayor."""
    util = sum(_num(a.get("SUTIL")) for a in recintos)
    crudo = {n: (sum(_num(a.get(agr)) for a in recintos),
                 sum(_num(a.get(c)) for a in recintos for c in det)) for n, agr, det in GRUPOS}
    out = {n: agr + det for n, (agr, det) in crudo.items()}
    if util and sum(out.values()) > util * 1.02:
        out = {n: max(agr, det) for n, (agr, det) in crudo.items()}
    return {n: round(v, 1) for n, v in out.items() if v > 0}


def interpretar(atributos: Sequence[dict]) -> Optional[DatosInventario]:
    """Atributos de todos los recintos/ediciones de UNA referencia -> datos de la edición más reciente."""
    por_anio: Dict[int, List[dict]] = {}
    for a in atributos:
        por_anio.setdefault(_anio(a.get("ADM_FECINI")), []).append(a)
    por_anio.pop(0, None)
    if not por_anio:
        return None
    historico = {str(y): _coberturas(rs) for y, rs in sorted(por_anio.items())}
    ultimo = max(por_anio)
    recintos = por_anio[ultimo]
    cob = historico[str(ultimo)]
    if not cob:
        return None
    vol = sum(_num(a.get("Vm3")) for a in recintos)
    marcas = [a.get("MVMC_MP") for a in recintos if a.get("MVMC_MP") is not None]
    usos = list(dict.fromkeys(str(a.get("USO") or "").strip() for a in recintos if a.get("USO")))
    return DatosInventario(
        anio=ultimo, superficie_m2=round(sum(_num(a.get("SUTIL")) for a in recintos), 1),
        coberturas=cob, dominante=max(cob, key=lambda k: cob[k]), uso="; ".join(usos),
        volumen_m3=round(vol, 1) if vol > 0 else None, recintos=len(recintos), historico=historico,
        monte_vecinal=any(_num(m) == 1 for m in marcas) if marcas else None)


def _total(d: DatosInventario) -> float:
    return sum(d.coberturas.values()) or 0.0


def reparto(d: DatosInventario, minimo: float = 0.03) -> List[Tuple[str, float, float]]:
    """[(cobertura, m², fracción)] de mayor a menor; se omiten las de menos del 3 %."""
    t = _total(d)
    filas = [(n, m, m / t) for n, m in d.coberturas.items() if t and m / t >= minimo]
    return sorted(filas, key=lambda x: -x[1])


def _m2(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".") + " m²"


def texto_reparto(d: DatosInventario) -> str:
    """'Eucaliptos 77 % (1.326 m²) · Coníferas 11 % (196 m²) · Frondosas 12 % (202 m²)'."""
    return " · ".join(f"{n} {f * 100:.0f} % ({_m2(m)})" for n, m, f in reparto(d))


def evolucion(d: DatosInventario) -> str:
    """Cambio más destacado entre la primera y la última edición ('' si no llega al 10 % de la parcela)."""
    anios = sorted(d.historico)
    t = _total(d)
    if len(anios) < 2 or not t:
        return ""
    ini, fin = d.historico[anios[0]], d.historico[anios[-1]]
    cambios = [(n, fin.get(n, 0.0) - ini.get(n, 0.0)) for n in set(ini) | set(fin)]
    n, dif = max(cambios, key=lambda x: (round(abs(x[1])), x[1]))   # a igual tamaño, lo que crece
    if abs(dif) / t < MINIMO_CAMBIO:
        return ""
    return (f"{n}: {_m2(ini.get(n, 0.0))} en {anios[0]} → {_m2(fin.get(n, 0.0))} en {anios[-1]}")


def esperadas(uso_catastro: str) -> List[str]:
    """Coberturas del inventario que corresponden al cultivo que declara el Catastro (sin repetir)."""
    out: List[str] = []
    for trozo in re.split(r"[;,/]", quitar_tildes(uso_catastro or "").upper()):
        for claves, cobertura in _EQUIVALENCIAS:
            if any(c in trozo for c in claves) and cobertura not in out:
                out.append(cobertura)
                break
    return out


def contrastar(uso_catastro: str, d: Optional[DatosInventario]) -> Tuple[str, str]:
    """(estado, detalle). Solo informa, nunca descarta.
    'COHERENTE' / 'REVISAR' / 'NO COMPARABLE' (cultivo catastral no reconocido) / 'SIN DATO' (no está en el inventario).
    """
    if d is None:
        return "SIN DATO", ""
    t = _total(d)
    esp = esperadas(uso_catastro)
    if not esp or not t:
        return "NO COMPARABLE", ""
    cubierto = sum(d.coberturas.get(n, 0.0) for n in esp) / t
    alterado = sum(d.coberturas.get(n, 0.0) for n in ALTERACION) / t
    arbolado_esperado = any(n in ARBOLADO for n in esp)
    if alterado >= MINIMO_ALTERACION and arbolado_esperado:
        causas = ", ".join(f"{n.lower()} {d.coberturas[n] / t * 100:.0f} %" for n in ALTERACION if d.coberturas.get(n))
        return "REVISAR", (f"El Catastro declara {uso_catastro}; el inventario {d.anio} detecta {causas}: "
                           "posible corta o incendio posterior al dato catastral")
    if cubierto >= MINIMO_COHERENTE:
        return "COHERENTE", ""
    return "REVISAR", (f"El Catastro declara {uso_catastro}, que en el inventario {d.anio} ocupa solo el "
                       f"{cubierto * 100:.0f} % de la parcela; domina {d.dominante.lower()}")


def aviso_alteracion(d: Optional[DatosInventario]) -> str:
    """Aviso si hay cortas/incendio apreciables, diga lo que diga el Catastro. '' si no."""
    if d is None or not _total(d):
        return ""
    t = _total(d)
    partes = [f"{n.lower()} {d.coberturas[n] / t * 100:.0f} %" for n in ALTERACION
              if d.coberturas.get(n, 0.0) / t >= MINIMO_ALTERACION]
    return f"Inventario forestal {d.anio}: {', '.join(partes)} de la parcela" if partes else ""


class ServicioInventario:
    """Consulta por referencia catastral (14 car.). `http` = ClienteHttp (pausa y reintentos compartidos)."""

    CAPA = "INVEGA/InvegaRecintos/MapServer/0/query"

    def __init__(self, http, base: str = config.INVEGA_SERVICIOS, log: Optional[Callable[[str], None]] = None):
        self.http, self.base, self.log = http, base.rstrip("/"), log

    def consultar(self, refcat: str) -> Optional[DatosInventario]:
        """None si la parcela no figura en el inventario. Lanza si el servicio falla."""
        rc = (refcat or "").strip().upper()[:14]
        if not re.fullmatch(r"[0-9A-Z]{14}", rc):
            raise ValueError(f"referencia catastral no válida para el inventario: {refcat!r}")
        r = self.http.get(f"{self.base}/{self.CAPA}", params={
            "where": f"REFCAT='{rc}'", "outFields": "*", "returnGeometry": "false", "f": "json"})
        datos = r.json()
        if not isinstance(datos, dict) or "error" in datos:
            raise RuntimeError(f"el servicio del inventario devolvió un error: {str(datos)[:200]}")
        return interpretar([f.get("attributes") or {} for f in datos.get("features") or []])
