"""Mapa de conjunto: todas las fincas de un lote en un mismo plano sobre la ortofoto de SIXPAC (Galicia).

Por finca solo hace UNA petición ligera (`parcelabox`: caja en lon/lat), que se guarda en la caché. Con
eso se calculan los centros, las distancias en línea recta y las agrupaciones; la ortofoto se descarga
una vez por mapa. Si las fincas están muy repartidas, se hace un mapa por grupo cercano.

Lo geométrico (distancias, agrupar, dibujo SVG, HTML) son funciones puras y se prueban sin red. Las
dependencias de red/navegador se inyectan en `ServicioMapa`.
"""
from __future__ import annotations

import html as _h
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config, plano
from .modelos import Resultado
from .resumen import formato_m2
from .sigpac import GALICIA

R_TIERRA_M = 6371008.8
UMBRAL_GRUPO_M = 2500.0     # fincas a menos de esto (encadenadas) van en el mismo mapa
MAX_MAPAS = 8
LIENZO = (1100, 700)


@dataclass
class Punto:
    n: int                                   # nº de la finca en el lote (el mismo que en el índice del informe)
    etiqueta: str
    refcat: str
    sup: Optional[float]
    lon: float
    lat: float
    bbox: Tuple[float, float, float, float]  # lon1, lat1, lon2, lat2


@dataclass
class Conjunto:
    puntos: List[Punto] = field(default_factory=list)
    grupos: List[List[Punto]] = field(default_factory=list)     # con mapa (2 o más fincas cercanas)
    aisladas: List[Punto] = field(default_factory=list)         # sin mapa: lejos de las demás
    pdf: Optional[Path] = None


# ------------------------------------------------------------------ geometría pura
def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((f2 - f1) / 2) ** 2
         + math.cos(f1) * math.cos(f2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R_TIERRA_M * math.asin(min(1.0, math.sqrt(a)))


def distancia_m(a: Punto, b: Punto) -> float:
    return haversine_m(a.lon, a.lat, b.lon, b.lat)


def matriz_distancias(puntos: Sequence[Punto]) -> List[List[float]]:
    return [[0.0 if a is b else distancia_m(a, b) for b in puntos] for a in puntos]


def agrupar(puntos: Sequence[Punto], umbral_m: float = UMBRAL_GRUPO_M) -> List[List[Punto]]:
    """Grupos por encadenamiento: dos fincas a <= umbral comparten grupo. Del mayor al menor."""
    padre = list(range(len(puntos)))

    def raiz(i: int) -> int:
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(puntos)):
        for j in range(i + 1, len(puntos)):
            if distancia_m(puntos[i], puntos[j]) <= umbral_m:
                padre[raiz(i)] = raiz(j)
    grupos: Dict[int, List[Punto]] = {}
    for i, p in enumerate(puntos):
        grupos.setdefault(raiz(i), []).append(p)
    return sorted(grupos.values(), key=lambda g: (-len(g), g[0].n))


def vecina_mas_cercana(p: Punto, grupo: Sequence[Punto]) -> Optional[Tuple[Punto, float]]:
    otros = [q for q in grupo if q is not p]
    if not otros:
        return None
    q = min(otros, key=lambda x: distancia_m(p, x))
    return q, distancia_m(p, q)


def formato_dist(m: float) -> str:
    return f"{m:,.0f} m".replace(",", ".") if m < 1000 else f"{m / 1000:,.1f} km".replace(".", ",")


def _bbox_merc(grupo: Sequence[Punto]) -> Tuple[float, float, float, float]:
    xs, ys = [], []
    for p in grupo:
        for lon, lat in ((p.bbox[0], p.bbox[1]), (p.bbox[2], p.bbox[3])):
            x, y = plano.lonlat_a_merc(lon, lat)
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def vista_para(grupo: Sequence[Punto]) -> plano.Vista:
    x1, y1, x2, y2 = _bbox_merc(grupo)
    w, h = LIENZO
    z = plano.elegir_zoom(x2 - x1, y2 - y1, w, h, margen=0.3, zmin=12, zmax=18)
    return plano.Vista((x1 + x2) / 2, (y1 + y2) / 2, z, w, h)


def svg_mapa(vista: plano.Vista, teselas: Dict[Tuple[int, int], str],
             grupo: Sequence[Punto]) -> Tuple[str, List[Punto]]:
    """SVG con la ortofoto, el recuadro de cada finca y su número. Devuelve (svg, fincas que quedan fuera)."""
    imgs = "".join(
        f'<image x="{l:.2f}" y="{t:.2f}" width="{plano.TESELA + 0.6}" height="{plano.TESELA + 0.6}" '
        f'href="{teselas.get((c, f), "")}"/>' for c, f, l, t in vista.teselas_ortofoto())
    marcas, fuera = "", []
    for p in grupo:
        xa, ya = vista.a_px(*plano.lonlat_a_merc(p.bbox[0], p.bbox[3]))   # esquina NO
        xb, yb = vista.a_px(*plano.lonlat_a_merc(p.bbox[2], p.bbox[1]))   # esquina SE
        cx, cy = (xa + xb) / 2, (ya + yb) / 2
        if not (0 <= cx <= vista.w and 0 <= cy <= vista.h):
            fuera.append(p)
            continue
        if abs(xb - xa) >= 6 or abs(yb - ya) >= 6:
            marcas += (f'<rect x="{min(xa, xb):.1f}" y="{min(ya, yb):.1f}" width="{abs(xb - xa):.1f}" '
                       f'height="{abs(yb - ya):.1f}" fill="#ffd400" fill-opacity=".18" stroke="#ffd400" '
                       f'stroke-width="2"/>')
        marcas += (f'<circle cx="{cx:.1f}" cy="{cy - 16:.1f}" r="13" fill="#e53935" stroke="#fff" '
                   f'stroke-width="2"/><path d="M{cx - 5:.1f},{cy - 5:.1f} L{cx:.1f},{cy:.1f} '
                   f'L{cx + 5:.1f},{cy - 5:.1f}" fill="#e53935"/>'
                   f'<text x="{cx:.1f}" y="{cy - 11.5:.1f}" text-anchor="middle" fill="#fff" font-size="13" '
                   f'font-weight="bold" font-family="Helvetica,Arial">{p.n}</text>')
    lat = sum(p.lat for p in grupo) / len(grupo)
    largo, metros = plano.barra_escala(vista.res, lat, vista.w * 0.25)
    x, y = 24, vista.h - 26
    txt = f"{metros} m" if metros < 1000 else f"{metros // 1000} km"
    escala = (f'<rect x="{x - 10}" y="{y - 24}" width="{largo + 60:.1f}" height="44" fill="#000" '
              f'fill-opacity=".55" rx="4"/><path d="M{x},{y} h{largo:.1f}" stroke="#fff" stroke-width="3"/>'
              f'<text x="{x}" y="{y - 10}" fill="#fff" font-size="15" font-family="Helvetica,Arial">{txt}</text>'
              f'<g transform="translate({vista.w - 38},40)"><circle r="22" fill="#000" fill-opacity=".55"/>'
              f'<path d="M0,-15 L6,7 L0,3 L-6,7 Z" fill="#fff"/></g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vista.w} {vista.h}" width="100%" '
           f'style="display:block;border:1px solid #444;background:#222">{imgs}{marcas}{escala}</svg>')
    return svg, fuera


def _esc(s: object) -> str:
    return _h.escape(str(s))


def html_pagina(titulo: str, subtitulo: str, svg: str, grupo: Sequence[Punto], fuera: Sequence[Punto]) -> str:
    filas = ""
    for p in sorted(grupo, key=lambda x: x.n):
        v = vecina_mas_cercana(p, grupo)
        cerca = f"nº {v[0].n} · {formato_dist(v[1])}" if v else "—"
        filas += (f"<tr><td><b>{p.n}</b></td><td>{_esc(p.etiqueta)}</td><td>{_esc(p.refcat)}</td>"
                  f"<td>{_esc(formato_m2(p.sup)) if p.sup else '—'}</td><td>{_esc(cerca)}</td></tr>")
    nota = ""
    if fuera:
        nota = ("<p class='n'>Fuera del encuadre: " + ", ".join(f"nº {p.n}" for p in fuera) + "</p>")
    return (f"<section><h1>{_esc(titulo)}</h1><p class='s'>{_esc(subtitulo)}</p>{svg}"
            f"<table><tr><th>Nº</th><th>Finca</th><th>Ref. catastral</th><th>Superficie</th>"
            f"<th>Vecina más cercana</th></tr>{filas}</table>{nota}"
            f"<p class='n'>Distancias en línea recta entre centros de parcela. Ortofoto y parcelario: "
            f"SIXPAC (Xunta de Galicia). La posición es orientativa (centro de la caja de cada parcela).</p>"
            f"</section>")


CSS = """body{font-family:Helvetica,Arial,sans-serif;margin:0}
section{page-break-after:always} section:last-child{page-break-after:auto}
h1{font-size:17px;margin:0 0 2px;color:#1F4E78} .s{font-size:11px;color:#444;margin:0 0 6px}
table{border-collapse:collapse;width:100%;margin-top:8px;font-size:9px}
td,th{border:1px solid #aaa;padding:2px 5px} th{background:#1F4E78;color:#fff;text-align:left}
tr{page-break-inside:avoid} .n{font-size:9px;color:#555;margin:4px 0}"""


def documento(paginas: Sequence[str]) -> str:
    return f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(paginas)}</body></html>'


# ------------------------------------------------------------------ servicio (red + navegador)
def _bbox_parcela(base: str, prov: str, mun: str, pol: str, par: str, sesion) -> Tuple[float, float, float, float]:
    r = sesion.get(f"{base}/servis/query/parcelabox/{int(prov)}/{int(mun)}/0/0/{int(pol)}/{int(par)}/",
                   timeout=config.TIMEOUT_HTTP_S)
    r.raise_for_status()
    feats = r.json().get("features") or []
    if not feats:
        raise ValueError("parcelabox sin resultados")
    p = feats[0]["properties"]
    return float(p["x1"]), float(p["y1"]), float(p["x2"]), float(p["y2"])


class ServicioMapa:
    def __init__(self, navegador, cache, base: Optional[str] = None,
                 log: Optional[Callable[[str], None]] = None, pausa_s: float = 0.7,
                 bbox_fn: Optional[Callable[..., Tuple[float, float, float, float]]] = None,
                 teselas_fn: Optional[Callable[..., Dict[Tuple[int, int], str]]] = None,
                 pdf_fn: Optional[Callable[[str, Path], bool]] = None):
        self.nav, self.cache = navegador, cache
        self.base = base or config.SIXPAC_SERVICIOS
        self.log = log or (lambda _m: None)
        self.pausa_s = pausa_s
        self._bbox_fn, self._teselas_fn, self._pdf_fn = bbox_fn, teselas_fn, pdf_fn

    # -- piezas inyectables
    def _bbox(self, cod, f) -> Tuple[float, float, float, float]:
        clave = f"{cod.clave}_{f.clave_parcela}"
        c = self.cache.leer("bbox_sixpac", clave)
        if c and len(c.get("bbox", [])) == 4:
            return tuple(float(v) for v in c["bbox"])  # type: ignore[return-value]
        if self._bbox_fn:
            b = self._bbox_fn(cod.cod_provincia, cod.cod_municipio_catastro, f.poligono, f.parcela)
        else:
            b = _bbox_parcela(self.base, cod.cod_provincia, cod.cod_municipio_catastro,
                              f.poligono, f.parcela, plano._sesion())
        self.cache.guardar("bbox_sixpac", clave, {"bbox": list(b)})
        if self.pausa_s:
            time.sleep(self.pausa_s)
        return b

    def _teselas(self, vista) -> Dict[Tuple[int, int], str]:
        return (self._teselas_fn or plano._descargar_teselas)(self.base, vista)

    def _pdf(self, html: str, destino: Path) -> bool:
        if self._pdf_fn:
            return self._pdf_fn(html, destino)
        pg = self.nav.pagina()
        try:
            pg.set_content(html)
            pg.pdf(path=str(destino), format="A4", print_background=True,
                   margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"})
        finally:
            pg.close()
        return destino.exists() and destino.read_bytes()[:5] == b"%PDF-"

    # -- orquestación
    def puntos(self, resultados: Sequence[Resultado]) -> List[Punto]:
        out: List[Punto] = []
        for n, r in enumerate(resultados, start=1):
            f, cod, c = r.finca, r.codigos, r.catastro
            if not cod or not c or cod.cod_provincia not in GALICIA:
                continue
            if f.por_referencia and c.lon is not None and c.lat is not None:
                d = 0.0003   # inmueble urbano: sin caja de SIXPAC, se sitúa por el centro que da el Catastro
                out.append(Punto(n, f.etiqueta, c.refcat, c.superficie_m2, c.lon, c.lat,
                                 (c.lon - d, c.lat - d, c.lon + d, c.lat + d)))
                continue
            if not (f.poligono.isdigit() and f.parcela.isdigit()):
                continue
            try:
                b = self._bbox(cod, f)
            except Exception as e:  # noqa: BLE001  una finca sin caja no tumba el mapa
                self.log(f"Mapa: sin posición para {f.etiqueta} ({e})")
                continue
            out.append(Punto(n, f.etiqueta, c.refcat, c.superficie_m2, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b))
        return out

    def generar(self, resultados: Sequence[Resultado], raiz: Path, titulo: str) -> Optional[Conjunto]:
        pts = self.puntos(resultados)
        conj = Conjunto(puntos=pts)
        if len(pts) < 2:
            self.log("Mapa de conjunto: hay menos de 2 fincas localizables, no se genera")
            return conj if pts else None
        grupos = agrupar(pts)
        conj.grupos = [g for g in grupos if len(g) >= 2][:MAX_MAPAS]
        conj.aisladas = [p for g in grupos if g not in conj.grupos for p in g]
        if not conj.grupos:
            self.log("Mapa de conjunto: ninguna finca está cerca de otra, no se genera")
            return conj
        paginas = []
        for i, g in enumerate(conj.grupos, start=1):
            try:
                v = vista_para(g)
                svg, fuera = svg_mapa(v, self._teselas(v), g)
            except Exception as e:  # noqa: BLE001
                self.log(f"Mapa: no se pudo dibujar el grupo {i} ({e})")
                continue
            ext = max(distancia_m(a, b) for a in g for b in g)
            sub = (f"{len(g)} fincas · extensión máxima {formato_dist(ext)}"
                   + (f" · grupo {i} de {len(conj.grupos)}" if len(conj.grupos) > 1 else ""))
            paginas.append(html_pagina(f"Mapa de conjunto — {titulo}", sub, svg, g, fuera))
        if conj.aisladas and paginas:
            lista = ", ".join(f"nº {p.n} ({p.etiqueta})" for p in sorted(conj.aisladas, key=lambda x: x.n))
            paginas[-1] = paginas[-1].replace(
                "</section>", f"<p class='n'><b>Fincas lejos de las demás (sin mapa de conjunto):</b> "
                             f"{_esc(lista)}</p></section>")
        if not paginas:
            return conj
        destino = Path(raiz) / "mapa_conjunto.pdf"
        try:
            if self._pdf(documento(paginas), destino):
                conj.pdf = destino
        except Exception as e:  # noqa: BLE001
            self.log(f"Mapa: no se pudo generar el PDF ({e})")
        return conj
