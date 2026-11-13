"""Parcelas colindantes de una finca rústica (solo Galicia).

Servicio público de la Xunta, verificado con una parcela real (2026-11-13):
  <IDEG>/ParcelasCatastrais/SIXPAC_<año>/MapServer/1  (parcelas de SIXPAC; hay un servicio por año)
  1) `query` con where PROVINCIA/MUNICIPIO/POLIGONO/PARCELA (MUNICIPIO = código del CATASTRO) -> geometría (EPSG:25829)
  2) `query` espacial (POST, esriSpatialRelIntersects, distance=1 m) -> parcelas que la tocan, con REFERENCIA_CAT.
     `esriSpatialRelTouches` devuelve 0: los contornos no casan al milímetro, por eso se usa el margen de 1 m.
Las parcelas 9000 y siguientes son terreno de dominio público (caminos, vías, cauces): indican acceso, no vecinos.
Nunca se obtienen titulares: solo referencias.
"""
from __future__ import annotations

import json
import re
from typing import Callable, List, Optional

from . import config
from .modelos import DatosColindantes

MARGEN_M = 1
PUBLICO_DESDE = 9000


def interpretar(resp: dict, poligono: str, parcela: str) -> DatosColindantes:
    """Respuesta de la consulta espacial -> colindantes (sin la propia finca), ordenadas por polígono y parcela."""
    d = DatosColindantes()
    vistas = set()
    for f in resp.get("features") or []:
        a = f.get("attributes") or {}
        try:
            pol, par = int(a.get("POLIGONO")), int(a.get("PARCELA"))
        except (TypeError, ValueError):
            continue
        if (str(pol), str(par)) == (str(int(poligono)), str(int(parcela))):
            continue
        if par >= PUBLICO_DESDE:
            d.dominio_publico += 1
            continue
        if (pol, par) in vistas:
            continue
        vistas.add((pol, par))
        d.parcelas.append({"poligono": str(pol), "parcela": str(par),
                           "refcat": str(a.get("REFERENCIA_CAT") or "").strip()[:14],
                           "superficie_m2": round(float(a.get("DN_SURFACE") or 0))})
    d.parcelas.sort(key=lambda x: (int(x["poligono"]), int(x["parcela"])))
    return d


def texto(d: DatosColindantes, maximo: int = 12) -> str:
    """'pol 23: parc 297, 314 · pol 24: parc 5' ; 'ninguna' si no hay."""
    if not d.parcelas:
        return "ninguna parcela"
    por_pol: dict = {}
    for p in d.parcelas[:maximo]:
        por_pol.setdefault(p["poligono"], []).append(p["parcela"])
    t = " · ".join(f"Polígono {pol}: parcelas {', '.join(ps)}" for pol, ps in por_pol.items())
    return t + (f" · y {len(d.parcelas) - maximo} más" if len(d.parcelas) > maximo else "")


class ServicioColindantes:
    """`http` = ClienteHttp (pausa y reintentos compartidos). 2 peticiones por finca (+1 por lote para elegir el año)."""

    def __init__(self, http, base: str = config.INVEGA_SERVICIOS, log: Optional[Callable[[str], None]] = None):
        self.http, self.base, self.log = http, base.rstrip("/"), log
        self._anio: Optional[int] = None

    def _json(self, r) -> dict:
        datos = r.json()
        if not isinstance(datos, dict) or "error" in datos:
            raise RuntimeError(f"el servicio devolvió un error: {str(datos)[:200]}")
        return datos

    def anio(self) -> int:
        """Edición más reciente de SIXPAC publicada en el servidor."""
        if self._anio is None:
            d = self._json(self.http.get(f"{self.base}/ParcelasCatastrais", params={"f": "json"}))
            anios = [int(m.group(1)) for s in d.get("services") or []
                     for m in [re.search(r"SIXPAC_(\d{4})$", s.get("name", ""))] if m]
            if not anios:
                raise RuntimeError("no hay servicios SIXPAC en el servidor")
            self._anio = max(anios)
        return self._anio

    def consultar(self, cod_provincia: str, cod_municipio_catastro: str, poligono: str,
                  parcela: str) -> Optional[DatosColindantes]:
        """None si la parcela no está en SIXPAC."""
        valores = [cod_provincia, cod_municipio_catastro, poligono, parcela]
        if not all(str(v).isdigit() for v in valores):
            raise ValueError("provincia, municipio, polígono y parcela deben ser numéricos")
        capa = f"{self.base}/ParcelasCatastrais/SIXPAC_{self.anio()}/MapServer/1/query"
        r = self._json(self.http.get(capa, params={
            "f": "json", "outFields": "POLIGONO,PARCELA", "returnGeometry": "true", "outSR": "25829",
            "where": (f"PROVINCIA={int(cod_provincia)} AND MUNICIPIO={int(cod_municipio_catastro)} "
                      f"AND POLIGONO={int(poligono)} AND PARCELA={int(parcela)}")}))
        anillos = [a for f in r.get("features") or [] for a in (f.get("geometry") or {}).get("rings") or []]
        if not anillos:
            return None
        geom = {"rings": anillos, "spatialReference": {"wkid": 25829}}
        v = self._json(self.http.post(capa, data={
            "f": "json", "geometry": json.dumps(geom), "geometryType": "esriGeometryPolygon", "inSR": "25829",
            "spatialRel": "esriSpatialRelIntersects", "distance": str(MARGEN_M), "units": "esriSRUnit_Meter",
            "outFields": "POLIGONO,PARCELA,REFERENCIA_CAT,DN_SURFACE", "returnGeometry": "false"}))
        d = interpretar(v, poligono, parcela)
        d.anio = self.anio()
        return d
