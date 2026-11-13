"""Plano de la parcela dibujado a partir de los servicios del propio visor SIXPAC (sin capturas de pantalla).

Servicios que usa el visor (verificados 2026-11-12):
  - {base}/servis/query/parcelabox/{prov}/{mun}/{agr}/{zona}/{pol}/{parc}/   -> bbox lon/lat de la parcela
  - {base}/sdg-vector/vector/parcela@3857/{z}.{x}.{y}.geojson               -> parcelas (EPSG:3857), z=15 fijo, y TMS
  - {base}/sdg-raster/wmts?layer=ortofotos&...TileMatrix=z&TileCol=x&TileRow=y -> ortofoto (XYZ estándar, hasta z19)

Con eso se calcula el encuadre exacto (parcela + margen), se dibuja el contorno sin relleno, los vecinos,
la escala y el norte, y se devuelve un SVG con la ortofoto incrustada. Todo lo geométrico son funciones
puras (testeables sin red). Si algo falla, quien llama cae a la captura de pantalla antigua.
"""
from __future__ import annotations

import base64
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config

ORIGEN = 20037508.342789244          # semieje de Web Mercator (m)
TESELA = 256
Z_VECTOR = 15                         # zoom fijo de las teselas vectoriales de SIXPAC
Anillo = List[Tuple[float, float]]


class PlanoNoDisponible(Exception):
    """No se pudo construir el plano (parcela sin geometría, servicio caído...). Se usa la captura."""


# ------------------------------------------------------------------ geometría pura
def lonlat_a_merc(lon: float, lat: float) -> Tuple[float, float]:
    x = lon * ORIGEN / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * 6378137.0
    return x, y


def resolucion(z: int) -> float:
    """Metros Mercator por píxel en el nivel z (teselas de 256 px)."""
    return 2 * ORIGEN / (TESELA * (2 ** z))


def teselas_vector(x1: float, y1: float, x2: float, y2: float) -> List[Tuple[int, int]]:
    """Teselas vectoriales (x, y TMS desde el sur) que cubren el rectángulo Mercator dado."""
    lado = 2 * ORIGEN / (2 ** Z_VECTOR)
    xa, xb = sorted((int((x1 + ORIGEN) // lado), int((x2 + ORIGEN) // lado)))
    ya, yb = sorted((int((y1 + ORIGEN) // lado), int((y2 + ORIGEN) // lado)))
    return [(x, y) for x in range(xa, xb + 1) for y in range(ya, yb + 1)]


def elegir_zoom(ancho_m: float, alto_m: float, px_w: int, px_h: int, margen: float = 0.35,
                zmin: int = 12, zmax: int = 19) -> int:
    """Mayor zoom con el que la parcela, con margen, cabe entera en el lienzo."""
    ancho_m, alto_m = max(ancho_m, 20.0), max(alto_m, 20.0)  # parcelas diminutas: no pasarse de zoom
    for z in range(zmax, zmin - 1, -1):
        r = resolucion(z)
        if ancho_m * (1 + margen) / r <= px_w and alto_m * (1 + margen) / r <= px_h:
            return z
    return zmin


class Vista:
    """Lienzo de px_w x px_h px centrado en (cx, cy) Mercator al zoom z."""

    def __init__(self, cx: float, cy: float, z: int, px_w: int, px_h: int):
        self.z, self.w, self.h = z, px_w, px_h
        self.res = resolucion(z)
        self.x0 = cx - px_w / 2 * self.res   # esquina superior izquierda (Mercator)
        self.y0 = cy + px_h / 2 * self.res

    def a_px(self, x: float, y: float) -> Tuple[float, float]:
        return (x - self.x0) / self.res, (self.y0 - y) / self.res

    def teselas_ortofoto(self) -> List[Tuple[int, int, float, float]]:
        """(col, fila, left_px, top_px) de cada tesela XYZ que toca el lienzo."""
        lado = TESELA * self.res
        c0, c1 = int((self.x0 + ORIGEN) // lado), int((self.x0 + self.w * self.res + ORIGEN) // lado)
        f0, f1 = int((ORIGEN - self.y0) // lado), int((ORIGEN - (self.y0 - self.h * self.res)) // lado)
        n = 2 ** self.z
        out = []
        for c in range(c0, c1 + 1):
            for f in range(f0, f1 + 1):
                if 0 <= c < n and 0 <= f < n:
                    left, top = self.a_px(c * lado - ORIGEN, ORIGEN - f * lado)
                    out.append((c, f, left, top))
        return out


def anillos(geometria: dict) -> List[Anillo]:
    """Anillos exteriores de un Polygon/MultiPolygon GeoJSON (los huecos no se dibujan)."""
    t, co = geometria.get("type"), geometria.get("coordinates") or []
    if t == "Polygon":
        return [[(p[0], p[1]) for p in co[0]]] if co else []
    if t == "MultiPolygon":
        return [[(p[0], p[1]) for p in poli[0]] for poli in co if poli]
    return []


def barra_escala(res_merc: float, lat: float, ancho_max_px: float) -> Tuple[float, int]:
    """(longitud_px, metros) de una barra 'redonda' que cabe en ancho_max_px (corrige la distorsión Mercator)."""
    m_por_px = res_merc * math.cos(math.radians(lat))
    for metros in (1000, 500, 200, 100, 50, 20, 10, 5):
        if metros / m_por_px <= ancho_max_px:
            return metros / m_por_px, metros
    return 5 / m_por_px, 5


def url_tesela_vector(base: str, tx: int, ty: int) -> str:
    """El primer número de la tesela es el ZOOM (15), no la provincia. Con la provincia solo funcionaba
    en A Coruña (15) por casualidad: en Lugo/Ourense/Pontevedra devolvía vacío y se caía a la captura."""
    return f"{base}/sdg-vector/vector/parcela@3857/{Z_VECTOR}.{tx}.{ty}.geojson"


def es_la_parcela(props: dict, prov, mun, pol, par) -> bool:
    def igual(clave: str, valor) -> bool:
        try:
            return int(props.get(clave)) == int(valor)
        except (TypeError, ValueError):
            return False

    return (igual("poligono", pol) and igual("parcela", par) and igual("municipio", mun)
            and (props.get("provincia") is None or igual("provincia", prov)))


def ruta_svg(anillo: Anillo, vista: Vista) -> str:
    pts = [vista.a_px(x, y) for x, y in anillo]
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"


# ------------------------------------------------------------------ servicios (red)
class DatosParcela:
    def __init__(self, bbox_lonlat: Tuple[float, float, float, float], objetivo: List[Anillo],
                 vecinos: List[Anillo]):
        self.bbox_lonlat, self.objetivo, self.vecinos = bbox_lonlat, objetivo, vecinos

    @property
    def bbox_merc(self) -> Tuple[float, float, float, float]:
        if self.objetivo:
            xs = [p[0] for a in self.objetivo for p in a]
            ys = [p[1] for a in self.objetivo for p in a]
            return min(xs), min(ys), max(xs), max(ys)
        x1, y1 = lonlat_a_merc(*self.bbox_lonlat[:2])
        x2, y2 = lonlat_a_merc(*self.bbox_lonlat[2:])
        return x1, y1, x2, y2


def _sesion():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def obtener_datos(base: str, prov: str, mun: str, pol: str, par: str, sesion=None) -> DatosParcela:
    """Geometría de la parcela y de sus vecinas. Lanza PlanoNoDisponible si no se puede."""
    s = sesion or _sesion()
    try:
        r = s.get(f"{base}/servis/query/parcelabox/{int(prov)}/{int(mun)}/0/0/{int(pol)}/{int(par)}/",
                  timeout=config.TIMEOUT_HTTP_S)
        r.raise_for_status()
        feats = r.json().get("features") or []
        if not feats:
            raise PlanoNoDisponible("parcelabox sin resultados")
        p = feats[0]["properties"]
        bbox = (float(p["x1"]), float(p["y1"]), float(p["x2"]), float(p["y2"]))
    except PlanoNoDisponible:
        raise
    except Exception as e:  # noqa: BLE001
        raise PlanoNoDisponible(f"parcelabox: {e}") from e

    x1, y1 = lonlat_a_merc(bbox[0], bbox[1])
    x2, y2 = lonlat_a_merc(bbox[2], bbox[3])
    lado = 2 * ORIGEN / (2 ** Z_VECTOR)
    # un colchón de ~un tercio de la parcela para dibujar vecinos alrededor
    mx, my = (x2 - x1) * 0.5 + 30, (y2 - y1) * 0.5 + 30
    teselas = teselas_vector(x1 - mx, y1 - my, x2 + mx, y2 + my)[:9]
    objetivo: List[Anillo] = []
    vecinos: List[Anillo] = []
    for tx, ty in teselas:
        try:
            rr = s.get(url_tesela_vector(base, tx, ty),
                       timeout=config.TIMEOUT_HTTP_S)
            rr.raise_for_status()
            for f in rr.json().get("features", []):
                pr = f.get("properties", {})
                es = es_la_parcela(pr, prov, mun, pol, par)
                (objetivo if es else vecinos).extend(anillos(f.get("geometry") or {}))
        except Exception as e:  # noqa: BLE001
            raise PlanoNoDisponible(f"teselas vectoriales {tx}.{ty}: {e}") from e
    if not objetivo:
        raise PlanoNoDisponible("la parcela no aparece en las teselas vectoriales")
    return DatosParcela(bbox, objetivo, vecinos)


def anillos_wfs(xml: str) -> List[Anillo]:
    """Anillos exteriores (Web Mercator) de una respuesta WFS INSPIRE CadastralParcel en EPSG:4326.
    En ese servicio las coordenadas vienen en orden lat lon."""
    out: List[Anillo] = []
    for bloque in re.findall(r"<gml:exterior>.*?</gml:exterior>", xml, flags=re.S):
        m = re.search(r"<gml:posList[^>]*>([^<]+)</gml:posList>", bloque)
        if not m:
            continue
        nums = [float(n) for n in m.group(1).split()]
        pares = [(nums[i + 1], nums[i]) for i in range(0, len(nums) - 1, 2)]   # (lon, lat)
        if len(pares) >= 3:
            out.append([lonlat_a_merc(lon, lat) for lon, lat in pares])
    return out


def obtener_datos_urbano(base: str, refcat14: str, sesion=None) -> DatosParcela:
    """Como obtener_datos, pero para un inmueble urbano (sin polígono/parcela): el contorno sale del servicio
    INSPIRE de parcelas del Catastro y los vecinos, de las teselas de SIXPAC. Lanza PlanoNoDisponible si falla."""
    s = sesion or _sesion()
    try:
        r = s.get(config.CATASTRO_WFS_PARCELA, params={
            "service": "wfs", "version": "2", "request": "getfeature", "STOREDQUERIE_ID": "GetParcel",
            "refcat": refcat14, "srsname": "EPSG:4326"}, timeout=config.TIMEOUT_HTTP_S)
        r.raise_for_status()
        objetivo = anillos_wfs(r.content.decode("latin-1"))
    except Exception as e:  # noqa: BLE001
        raise PlanoNoDisponible(f"parcela del Catastro: {e}") from e
    if not objetivo:
        raise PlanoNoDisponible("el Catastro no devuelve la geometría de la parcela")
    xs = [p[0] for a in objetivo for p in a]
    ys = [p[1] for a in objetivo for p in a]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    mx, my = (x2 - x1) * 0.5 + 30, (y2 - y1) * 0.5 + 30
    vecinos: List[Anillo] = []
    for tx, ty in teselas_vector(x1 - mx, y1 - my, x2 + mx, y2 + my)[:9]:
        try:
            rr = s.get(url_tesela_vector(base, tx, ty), timeout=config.TIMEOUT_HTTP_S)
            rr.raise_for_status()
            for f in rr.json().get("features", []):
                vecinos.extend(anillos(f.get("geometry") or {}))
        except Exception as e:  # noqa: BLE001
            raise PlanoNoDisponible(f"teselas vectoriales {tx}.{ty}: {e}") from e

    def a_lonlat(x: float, y: float) -> Tuple[float, float]:
        return (math.degrees(x / 6378137.0),
                math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2))
    lo1, la1 = a_lonlat(x1, y1)
    lo2, la2 = a_lonlat(x2, y2)
    return DatosParcela((lo1, la1, lo2, la2), objetivo, vecinos)


def _descargar_teselas(base: str, vista: Vista, sesion=None) -> Dict[Tuple[int, int], str]:
    """Teselas de ortofoto como data URI JPEG. Si falta alguna, PlanoNoDisponible (no se entrega un mapa con huecos)."""
    s = sesion or _sesion()
    lista = vista.teselas_ortofoto()
    if len(lista) > 40:
        raise PlanoNoDisponible(f"demasiadas teselas ({len(lista)})")

    def una(t):
        c, f = t[0], t[1]
        url = (f"{base}/sdg-raster/wmts?layer=ortofotos&style=default&tilematrixset=EPSG3857&time="
               f"&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fjpeg"
               f"&TileMatrix={vista.z}&TileCol={c}&TileRow={f}")
        for _ in range(3):
            try:
                r = s.get(url, timeout=config.TIMEOUT_HTTP_S)
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    return (c, f), "data:image/jpeg;base64," + base64.b64encode(r.content).decode()
            except Exception:  # noqa: BLE001
                pass
        return (c, f), ""

    with ThreadPoolExecutor(max_workers=4) as ex:
        res = dict(ex.map(una, lista))
    if any(not v for v in res.values()):
        raise PlanoNoDisponible("faltan teselas de ortofoto")
    return res


# ------------------------------------------------------------------ dibujo
def _svg_vista(vista: Vista, teselas: Dict[Tuple[int, int], str], datos: DatosParcela, lat: float,
               detalle: bool) -> str:
    imgs = "".join(
        f'<image x="{l:.2f}" y="{t:.2f}" width="{TESELA + 0.6}" height="{TESELA + 0.6}" '
        f'href="{teselas[(c, f)]}"/>' for c, f, l, t in vista.teselas_ortofoto())
    vec = ""
    if detalle:
        for a in datos.vecinos:
            vec += f'<path d="{ruta_svg(a, vista)}" fill="none" stroke="#ffffff" stroke-opacity=".75" stroke-width="1"/>'
    obj = ""
    for a in datos.objetivo:
        d = ruta_svg(a, vista)
        obj += (f'<path d="{d}" fill="#ffd400" fill-opacity=".06" stroke="#000" stroke-opacity=".55" '
                f'stroke-width="{5 if detalle else 4}" stroke-linejoin="round"/>'
                f'<path d="{d}" fill="none" stroke="#ffd400" stroke-width="{2.5 if detalle else 2}" '
                f'stroke-linejoin="round"/>')
    extra = ""
    if detalle:
        largo, metros = barra_escala(vista.res, lat, vista.w * 0.28)
        x, y = 24, vista.h - 26
        txt = f"{metros} m" if metros < 1000 else f"{metros // 1000} km"
        extra = (f'<rect x="{x - 10}" y="{y - 24}" width="{largo + 20 + 40}" height="44" fill="#000" '
                 f'fill-opacity=".55" rx="4"/>'
                 f'<path d="M{x},{y} h{largo:.1f}" stroke="#fff" stroke-width="3"/>'
                 f'<path d="M{x},{y - 6} v12 M{x + largo:.1f},{y - 6} v12" stroke="#fff" stroke-width="2"/>'
                 f'<text x="{x}" y="{y - 10}" fill="#fff" font-size="15" font-family="Helvetica,Arial">{txt}</text>'
                 f'<g transform="translate({vista.w - 38},44)"><circle r="24" fill="#000" fill-opacity=".55"/>'
                 f'<path d="M0,-17 L7,8 L0,3 L-7,8 Z" fill="#fff"/>'
                 f'<text y="22" x="-5" fill="#fff" font-size="13" font-family="Helvetica,Arial" '
                 f'transform="translate(0,-26)">N</text></g>')
    else:  # mapa de situación: aro alrededor de la parcela
        cx, cy = vista.a_px(*_centro(datos))
        extra = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="16" fill="none" stroke="#ff2d2d" stroke-width="3"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vista.w} {vista.h}" '
            f'width="100%" style="display:block;border:1px solid #444;background:#222">'
            f'{imgs}{vec}{obj}{extra}</svg>')


def _centro(datos: DatosParcela) -> Tuple[float, float]:
    x1, y1, x2, y2 = datos.bbox_merc
    return (x1 + x2) / 2, (y1 + y2) / 2


def dibujar(base: str, datos: DatosParcela, sesion=None) -> Tuple[str, str, int]:
    """Devuelve (svg_plano, svg_situacion, zoom). Lanza PlanoNoDisponible si falta algo."""
    x1, y1, x2, y2 = datos.bbox_merc
    cx, cy = _centro(datos)
    lat = math.degrees(2 * math.atan(math.exp(cy / 6378137.0)) - math.pi / 2)
    pw, ph = 1100, 760
    z = elegir_zoom(x2 - x1, y2 - y1, pw, ph)
    v1 = Vista(cx, cy, z, pw, ph)
    v2 = Vista(cx, cy, max(11, z - 4), 520, 330)
    t1 = _descargar_teselas(base, v1, sesion)
    t2 = _descargar_teselas(base, v2, sesion)
    return _svg_vista(v1, t1, datos, lat, True), _svg_vista(v2, t2, datos, lat, False), z
