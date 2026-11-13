"""Contexto geológico y minero de una finca (orientativo).

Fuentes, todas públicas y verificadas con consultas reales (2026-11-13):
  IGME-CSIC, servicios ArcGIS REST (`config.IGME_SERVICIOS`):
    Cartografia_Geologica/IGME_MAGNA_50/MapServer/0   hoja 1:50.000 (NUM, NOMBRE)
    Cartografia_Geologica/IGME_MAGNA_50/MapServer/11  litología bajo el punto (DLO)
    BasesDatos/IGME_BDMIN_Indicios/MapServer/1        indicios de minerales metálicos (BDMIN)
    BasesDatos/IGME_BDMIN_Explotaciones/MapServer/1   explotaciones e indicios de rocas y minerales industriales
    AtlasGeoquimico/IGME_MapaIsovalores2012_<El>/MapServer  Atlas Geoquímico de España 2012 (capa 0: sedimentos,
        extracción total). `identify` da el valor interpolado y la clase (empieza en 0); `legend` da los tramos.
  Xunta de Galicia (`config.INVEGA_SERVICIOS`), solo Galicia:
    PBA/Afeccions_outras_ConcesionsMineiras/MapServer/0  derechos mineros VIGENTES (permisos, concesiones, autorizaciones)

Todo se consulta por un punto (centro de la parcela, lon/lat) y un radio. Solo informa: un indicio cercano o un valor
alto del atlas (interpolado a escala nacional) NO significan que haya mineral en la finca. No se guarda el titular
de los derechos mineros.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence

from . import config
from .modelos import DatosGeologia

RADIO_M = 5000
# elementos del atlas que se consultan: oro, plata, estaño, wolframio y cerio (la tierra rara más abundante)
ELEMENTOS = {"Au": "oro", "Ag": "plata", "Sn": "estaño", "W": "wolframio", "Ce": "cerio (tierra rara)"}
SIN_INDICIOS, COMARCA, ANOMALIA = "Sin indicios", "Indicios en la comarca", "Anomalía detectada"


def distancia_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distancia en línea recta entre dos puntos lon/lat (haversine)."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _atr(f: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in (f.get("attributes") or {}).items()}


def _dist(f: dict, lon: float, lat: float) -> Optional[float]:
    g = f.get("geometry") or {}
    if "x" not in g or "y" not in g:
        return None
    return distancia_m(lon, lat, float(g["x"]), float(g["y"]))


def interpretar_hoja(resp: dict) -> str:
    """'68 Camariñas' (hoja MAGNA 1:50.000); '' si no hay."""
    for f in resp.get("features") or []:
        a = _atr(f)
        if a.get("NUM") not in (None, ""):
            return f"{a['NUM']} {str(a.get('NOMBRE') or '').title()}".strip()
    return ""


def interpretar_litologia(resp: dict) -> str:
    textos = [str(_atr(f).get("DLO") or "") for f in resp.get("features") or []]
    return "; ".join(dict.fromkeys(t for t in textos if t))


def interpretar_indicios(resp: dict, lon: float, lat: float) -> List[Dict[str, object]]:
    """Indicios BDMIN ordenados por distancia. Las columnas X/Y del servicio no son fiables: se usa la geometría."""
    out = []
    for f in resp.get("features") or []:
        a, d = _atr(f), _dist(f, lon, lat)
        if d is None:
            continue
        out.append({"nombre": str(a.get("Nombre_mina") or "").title() or "(sin nombre)",
                    "sustancia": str(a.get("Sustancia") or ""), "asociacion": str(a.get("Asociacion_mineral") or ""),
                    "morfologia": str(a.get("Morfologia") or ""), "tamano": str(a.get("Tamaño_indicio") or ""),
                    "codigo": str(a.get("Codigo_indicio") or ""), "distancia_m": round(d)})
    return sorted(out, key=lambda x: x["distancia_m"])


def interpretar_explotaciones(resp: dict, lon: float, lat: float) -> List[Dict[str, object]]:
    out = []
    for f in resp.get("features") or []:
        a, d = _atr(f), _dist(f, lon, lat)
        if d is None:
            continue
        out.append({"sustancia": str(a.get("Sustancia") or ""), "estado": str(a.get("Estado_Explotacion") or ""),
                    "usos": str(a.get("Usos") or ""), "codigo": str(a.get("Codigo_roca") or ""),
                    "distancia_m": round(d)})
    return sorted(out, key=lambda x: x["distancia_m"])


def interpretar_derechos(resp: dict) -> List[Dict[str, str]]:
    """Derechos mineros vigentes (Xunta). No se recoge el titular."""
    out = []
    for f in resp.get("features") or []:
        a = _atr(f)
        tipo = str(a.get("TIPO_LARGO") or "")
        out.append({"numero": str(a.get("NUMERO") or ""), "nombre": str(a.get("NOMBRE") or "").title(),
                    "tipo": tipo.split(" / ", 1)[-1], "recursos": str(a.get("RECURSOS") or "").capitalize(),
                    "seccion": str(a.get("SECCION") or ""), "estado": str(a.get("ESTADO") or "")})
    return list({d["numero"] or d["nombre"]: d for d in out}.values())


def interpretar_geoquimica(elemento: str, identify: dict, leyenda: dict) -> Optional[Dict[str, object]]:
    """Valor interpolado del atlas en sedimentos (capa 0) y tramo de la leyenda que le corresponde.
    La clase que devuelve el servicio empieza en 0. None si el punto cae fuera del mapa."""
    res = next((x for x in identify.get("results") or [] if x.get("layerId") == 0), None)
    if res is None:
        return None
    a = res.get("attributes") or {}
    try:
        valor = float(str(a.get("Classify.Valor de píxel")).replace(",", "."))
        clase = int(float(str(a.get("Classify.Valor de clase"))))
    except (TypeError, ValueError):
        return None
    capa = next((c for c in leyenda.get("layers") or [] if c.get("layerId") == 0), {})
    tramos = [str(x.get("label") or "") for x in capa.get("legend") or []]
    if not tramos or not 0 <= clase < len(tramos):
        return None
    return {"elemento": elemento, "nombre": ELEMENTOS.get(elemento, elemento), "valor": valor,
            "tramo": clase + 1, "tramos": len(tramos), "rango": tramos[clase]}


def veredicto(d: DatosGeologia) -> str:
    """Una línea. 'Anomalía detectada': algún elemento en el tramo más alto del atlas. 'Indicios en la comarca':
    hay indicios BDMIN dentro del radio. 'Sin indicios': nada de lo anterior en las fuentes consultadas."""
    altos = [g for g in d.geoquimica if g["tramo"] == g["tramos"]]
    partes = []
    if d.indicios:
        c = d.indicios[0]
        partes.append(f"{len(d.indicios)} indicio(s) minero(s) a menos de {d.radio_km:g} km; el más cercano, "
                      f"{c['nombre']} ({str(c['sustancia']).strip().lower()}), a {km(c['distancia_m'])}")
    if altos:
        txt = (ANOMALIA + ": " + ", ".join(str(g["nombre"]) for g in altos)
               + " en el tramo más alto del Atlas Geoquímico de España (dato regional, no de la finca)")
        return txt + (". Además, " + partes[0] if partes else "")
    if partes:
        return COMARCA + ": " + partes[0]
    if any("indicios" in s or "atlas" in s for s in d.sin_consultar):
        return "Sin verificar: no se pudieron consultar " + ", ".join(d.sin_consultar)
    return SIN_INDICIOS + f" en las fuentes consultadas (radio de {d.radio_km:g} km)"


def estado(d: DatosGeologia) -> str:
    """Solo la etiqueta del veredicto (para el Excel)."""
    return d.veredicto.split(":")[0].split(" en las fuentes")[0]


def km(m: object) -> str:
    return f"{float(m) / 1000:.1f} km".replace(".", ",")


def texto_indicios(d: DatosGeologia, maximo: int = 6) -> str:
    if not d.indicios:
        return "Ninguno"
    t = "; ".join(f"{i['nombre']} ({str(i['sustancia']).strip()}; {km(i['distancia_m'])})" for i in d.indicios[:maximo])
    return t + (f"; y {len(d.indicios) - maximo} más" if len(d.indicios) > maximo else "")


def texto_derechos(lista: Sequence[Dict[str, str]]) -> str:
    return "; ".join(f"{x['numero']} {x['nombre']} ({x['tipo']}; {x['recursos'] or 'recurso no indicado'})"
                     for x in lista) or "Ninguno"


def texto_geoquimica(d: DatosGeologia) -> str:
    return " · ".join(f"{str(g['nombre']).capitalize()}: tramo {g['tramo']} de {g['tramos']}" for g in d.geoquimica)


class ServicioGeologia:
    """`http` = ClienteHttp (pausa y reintentos compartidos). Si una fuente falla se anota en `sin_consultar`
    y se sigue: nunca se da por limpia una fuente no consultada."""

    def __init__(self, http, igme: str = config.IGME_SERVICIOS, xunta: str = config.INVEGA_SERVICIOS,
                 log: Optional[Callable[[str], None]] = None, radio_m: int = RADIO_M):
        self.http, self.igme, self.xunta = http, igme.rstrip("/"), xunta.rstrip("/")
        self.log, self.radio_m = log, radio_m
        self._leyendas: Dict[str, dict] = {}

    def _json(self, r) -> dict:
        datos = r.json()
        if not isinstance(datos, dict) or "error" in datos:
            raise RuntimeError(f"el servicio devolvió un error: {str(datos)[:200]}")
        return datos

    def _query(self, url: str, lon: float, lat: float, radio: bool, geometria: bool = True) -> dict:
        p = {"f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
             "outSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
             "returnGeometry": "true" if geometria else "false"}
        if radio:
            p.update(distance=str(self.radio_m), units="esriSRUnit_Meter")
        return self._json(self.http.get(url + "/query", params=p))

    def _paso(self, d: DatosGeologia, nombre: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            d.sin_consultar.append(nombre)
            if self.log:
                self.log(f"geología: no se pudo consultar {nombre} ({e})")

    def consultar(self, lon: float, lat: float, galicia: bool = False) -> DatosGeologia:
        d = DatosGeologia(radio_km=self.radio_m / 1000)
        magna = f"{self.igme}/Cartografia_Geologica/IGME_MAGNA_50/MapServer"
        bdmin = f"{self.igme}/BasesDatos"

        def _magna() -> None:
            d.hoja = interpretar_hoja(self._query(f"{magna}/0", lon, lat, False, False))
            d.litologia = interpretar_litologia(self._query(f"{magna}/11", lon, lat, False, False))

        def _indicios() -> None:
            d.indicios = interpretar_indicios(
                self._query(f"{bdmin}/IGME_BDMIN_Indicios/MapServer/1", lon, lat, True), lon, lat)

        def _explotaciones() -> None:
            d.explotaciones = interpretar_explotaciones(
                self._query(f"{bdmin}/IGME_BDMIN_Explotaciones/MapServer/1", lon, lat, True), lon, lat)

        def _derechos() -> None:
            capa = f"{self.xunta}/PBA/Afeccions_outras_ConcesionsMineiras/MapServer/0"
            d.derechos_parcela = interpretar_derechos(self._query(capa, lon, lat, False, False))
            sobre = {x["numero"] for x in d.derechos_parcela}
            d.derechos_cerca = [x for x in interpretar_derechos(self._query(capa, lon, lat, True, False))
                                if x["numero"] not in sobre]
            d.derechos_consultados = True

        def _geoquimica() -> None:
            for el in ELEMENTOS:
                s = f"{self.igme}/AtlasGeoquimico/IGME_MapaIsovalores2012_{el}/MapServer"
                if el not in self._leyendas:
                    self._leyendas[el] = self._json(self.http.get(s + "/legend", params={"f": "json"}))
                r = self._json(self.http.get(s + "/identify", params={
                    "f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "sr": "4326",
                    "layers": "all:0", "tolerance": "1", "returnGeometry": "false", "imageDisplay": "400,400,96",
                    "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}"}))
                g = interpretar_geoquimica(el, r, self._leyendas[el])
                if g:
                    d.geoquimica.append(g)

        self._paso(d, "geología (MAGNA, IGME)", _magna)
        self._paso(d, "indicios mineros (BDMIN, IGME)", _indicios)
        self._paso(d, "explotaciones de rocas (BDMIN, IGME)", _explotaciones)
        if galicia:
            self._paso(d, "derechos mineros (Xunta)", _derechos)
        self._paso(d, "atlas geoquímico (IGME)", _geoquimica)
        d.veredicto = veredicto(d)
        return d
