"""Todo lo que toca al visor SIGPAC. Si SIGPAC cambia su web, solo se edita este fichero.

Flujo VERIFICADO (2026-11-11, visor FEGA "Visor SigPac V 4.20", que también contiene Galicia):
  1. Se cierra el "Aviso Legal".
  2. Prismáticos -> pestaña "Directa": provincia (INE) y municipio (código del CATASTRO, no el INE), agregado 0, zona 0, polígono, parcela.
  3. Botón imprimir -> Aceptar: el propio visor descarga un PDF (ortofoto + parcelario, ~1,5 MB).
  4. Consultas > Parcela (A) y clic dentro de la parcela: aparece la etiqueta "prov:mun:agr:zona:pol:parc (m²)",
     que confirma que el punto está dentro de la parcela buscada.
  5. Consultas > Catastro (U) y clic en ese punto: abre la Sede con RefC=<referencia catastral>.
     Esa es la referencia catastral que SIGPAC asocia a la parcela.

El visor se maneja con coordenadas de pantalla (viewport fijo 1400x900, ver navegador.py), porque sus
controles no tienen identificadores estables. Todo está en COORD/ids para recalibrar en un sitio.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import config
from .modelos import Codigos, Finca, ResultadoSigpac
from .red import Limitador, reintentar

# Coordenadas (px) en viewport 1400x900
COORD = {
    "cerrar_aviso": (1259, 142),
    "buscar": (68, 15),            # prismáticos
    "boton_buscar_panel": (635, 530),
    "cerrar_panel": (888, 189),
    "consultas": (200, 15),
    "imprimir": (369, 15),
    "imprimir_aceptar": (622, 505),
    "centro_mapa": (700, 477),
}
CAMPOS = {"prov": "#dirProv", "mun": "#dirMun", "agr": "#dirAgr", "zona": "#dirZona",
          "pol": "#dirPol", "par": "#dirPar"}
CLIP_MAPA = {"x": 0, "y": 56, "width": 1170, "height": 640}  # mapa sin paneles
MENU_PARCELA = "text=Parcela (A)"
MENU_CATASTRO = "text=Catastro (U)"

_RE_ETIQUETA = re.compile(r"(\d+):(\d+):(\d+):(\d+):(\d+):(\d+)\s*\(([\d.,]+)\s*(m|ha)")
_RE_REFC = re.compile(r"RefC=([0-9A-Z]{14,20})", re.I)
_RE_REFCAT_14 = re.compile(r"\b[0-9A-Z]{14}(?:[0-9A-Z]{6})?\b")


GALICIA = {"15", "27", "32", "36"}  # A Coruña, Lugo, Ourense, Pontevedra -> SIXPAC (FOGGA/Xunta)


def url_visor(cod_provincia: str) -> str:
    """SIXPAC es el mismo visor (v4.18) con los datos de Galicia; para el resto, el visor del FEGA."""
    return config.SIXPAC_VISOR_URL if cod_provincia in GALICIA else config.SIGPAC_VISOR_URL


class SigpacCambioWeb(Exception):
    """El visor no responde como se espera: habrá cambiado. Recalibrar COORD/ids."""


def puntos_busqueda(cx: int, cy: int) -> List[Tuple[int, int]]:
    """Puntos alrededor del centro para dar con uno dentro de la parcela (el centro puede caer fuera)."""
    pts = [(cx, cy)]
    for r in (25, 50, 90, 140, 200):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, r), (r, -r), (-r, -r)):
            pts.append((cx + dx, cy + dy))
    return pts


def etiqueta_coincide(texto: str, poligono: str, parcela: str) -> Optional[float]:
    """Si el texto trae la etiqueta de la parcela buscada devuelve su superficie en m², si no None."""
    if not (poligono.isdigit() and parcela.isdigit()):
        return None
    for m in _RE_ETIQUETA.finditer(texto):
        if int(m.group(5)) == int(poligono) and int(m.group(6)) == int(parcela):
            valor = float(m.group(7).replace(",", "."))
            return valor * 10_000 if m.group(8) == "ha" else valor  # el visor da "2.0601 ha" en las grandes
    return None


def refcat_desde_url(url: str) -> str:
    m = _RE_REFC.search(url)
    return m.group(1).upper()[:14] if m else ""


def extraer_refcat(texto: str) -> str:
    limpio = re.sub(r"[\s\-]", "", texto).upper()
    h = _RE_REFCAT_14.search(limpio)
    return h.group(0)[:14] if h else ""


def coincide_refcat(catastro: str, sigpac: str) -> str:
    """'SI' | 'NO' | 'NO ENCONTRADA' (cuando SIGPAC no dio referencia)."""
    if not sigpac:
        return "NO ENCONTRADA"
    if not catastro:
        return "NO"
    return "SI" if catastro[:14].upper() == sigpac[:14].upper() else "NO"


class ServicioSigpac:
    def __init__(self, navegador, limitador: Limitador, log: Optional[Callable[[str], None]] = None):
        self.navegador, self.limitador, self.log = navegador, limitador, log

    def consultar(self, finca: Finca, codigos: Codigos, refcat_catastro: str,
                  destino_pdf: Path) -> ResultadoSigpac:
        destino_pdf.parent.mkdir(parents=True, exist_ok=True)
        return reintentar(lambda: self._una_vez(finca, codigos, destino_pdf), self.limitador,
                          config.REINTENTOS, no_reintentar=(SigpacCambioWeb,), log=self.log)

    # ------------------------------------------------------------------
    def _una_vez(self, finca: Finca, codigos: Codigos, destino_pdf: Path) -> ResultadoSigpac:
        if not (finca.poligono.isdigit() and finca.parcela.isdigit()):
            return ResultadoSigpac("NO ENCONTRADA", detalle="polígono/parcela no numéricos")
        pg = self.navegador.pagina()
        try:
            pg.goto(url_visor(codigos.cod_provincia), wait_until="networkidle", timeout=60_000)
            try:
                pg.wait_for_selector("text=Aviso Legal >> visible=true", timeout=20_000)
            except Exception:  # noqa: BLE001  (si no aparece, seguimos)
                pass
            pg.mouse.click(*COORD["cerrar_aviso"])
            pg.wait_for_timeout(800)

            # 2) búsqueda directa
            pg.mouse.click(*COORD["buscar"])
            try:
                pg.wait_for_selector(CAMPOS["prov"], state="visible", timeout=15_000)
            except Exception as e:  # noqa: BLE001
                raise SigpacCambioWeb(f"No se abre el panel de búsqueda: {e}") from e
            valores = {"prov": codigos.cod_provincia, "mun": str(int(codigos.cod_municipio_catastro)),
                       "agr": "0", "zona": "0", "pol": finca.poligono, "par": finca.parcela}
            for k, sel in CAMPOS.items():
                pg.fill(sel, valores[k])
            pg.mouse.click(*COORD["boton_buscar_panel"])
            pg.wait_for_timeout(6_000)
            if "no se encontraron elementos" in pg.inner_text("body").lower():
                return ResultadoSigpac("NO ENCONTRADA", detalle="SIGPAC: no se encontraron elementos")
            pg.mouse.click(*COORD["buscar"])  # alternar: oculta el panel (sirve en SIGPAC y SIXPAC)
            pg.wait_for_timeout(1_000)

            # 4) localizar un punto dentro de la parcela (etiqueta pol:parc) y comprobarla
            punto, sup = self._punto_en_parcela(pg, finca)
            if punto is None:
                destino_pdf.unlink(missing_ok=True)
                return ResultadoSigpac("NO ENCONTRADA", detalle="El visor no localiza esa parcela")

            # 4b) captura del mapa con la parcela resaltada (herramienta Parcela activa)
            captura = pg.screenshot(clip=CLIP_MAPA)

            # 5) referencia catastral vía Catastro (U)
            refcat = self._refcat_en_punto(pg, punto)
            pdf_ok = self._ficha_pdf(pg.context, destino_pdf, captura, finca, codigos, refcat, sup,
                                     url_visor(codigos.cod_provincia))
            det = f"superficie SIGPAC {sup:.1f} m²" if sup else ""
            if not pdf_ok:
                det += " | PDF no generado"
            return ResultadoSigpac("ENCONTRADA", refcat=refcat, pdf=destino_pdf.name if pdf_ok else "",
                                   detalle=det)
        finally:
            pg.close()

    def _ficha_pdf(self, ctx, destino: Path, captura: bytes, finca: Finca, codigos: Codigos,
                   refcat: str, sup: Optional[float], visor: str) -> bool:
        """PDF propio: ortofoto con la parcela resaltada y rotulada + datos. Una sola página A4."""
        import base64
        from datetime import date

        nombre = "SIXPAC" if "sixpac" in visor else "SIGPAC"
        img = base64.b64encode(captura).decode()
        html = f"""<html><head><meta charset="utf-8"><style>
        body{{font-family:Helvetica,Arial,sans-serif;margin:0}} h1{{font-size:18px;margin:0 0 6px}}
        table{{border-collapse:collapse;width:100%;margin:8px 0}} td{{border:1px solid #999;padding:4px 8px;font-size:12px}}
        td.k{{background:#eee;width:30%;font-weight:bold}} img{{width:100%;border:1px solid #444}}
        small{{color:#555;font-size:10px}}</style></head><body>
        <h1>Ficha {nombre} — ortofoto y parcelario con la parcela resaltada</h1>
        <table>
        <tr><td class="k">Municipio</td><td>{codigos.nombre_municipio} ({codigos.nombre_provincia})</td></tr>
        <tr><td class="k">Polígono / Parcela</td><td>{finca.poligono} / {finca.parcela}</td></tr>
        <tr><td class="k">Referencia catastral ({nombre})</td><td>{refcat or "no disponible"}</td></tr>
        <tr><td class="k">Superficie {nombre}</td><td>{f"{sup:,.1f}".replace(",", " ") + " m²" if sup else "—"}</td></tr>
        <tr><td class="k">Fecha de consulta</td><td>{date.today().strftime("%d/%m/%Y")}</td></tr></table>
        <img src="data:image/png;base64,{img}"/>
        <small>Captura del visor {nombre} ({visor}). La parcela consultada aparece resaltada en color.</small>
        </body></html>"""
        pg = ctx.new_page()
        try:
            pg.set_content(html)
            pg.pdf(path=str(destino), format="A4", print_background=True,
                   margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            return destino.exists() and destino.read_bytes()[:5] == b"%PDF-"
        except Exception as e:  # noqa: BLE001
            if self.log:
                self.log(f"SIGPAC: no se pudo generar la ficha PDF ({e})")
            return False
        finally:
            pg.close()

    def _menu(self, pg, item: str) -> None:
        pg.mouse.click(*COORD["consultas"])
        pg.wait_for_timeout(600)
        pg.click(item, timeout=10_000)
        pg.wait_for_timeout(600)

    def _punto_en_parcela(self, pg, finca: Finca) -> Tuple[Optional[Tuple[int, int]], Optional[float]]:
        self._menu(pg, MENU_PARCELA)
        for x, y in puntos_busqueda(*COORD["centro_mapa"]):
            pg.mouse.click(x, y)
            pg.wait_for_timeout(1_200)
            sup = etiqueta_coincide(pg.inner_text("body"), finca.poligono, finca.parcela)
            if sup is not None:
                return (x, y), sup
        return None, None

    def _refcat_en_punto(self, pg, punto: Tuple[int, int]) -> str:
        self._menu(pg, MENU_CATASTRO)
        try:
            with pg.context.expect_page(timeout=20_000) as ev:
                pg.mouse.click(*punto)
            nueva = ev.value
            try:
                return refcat_desde_url(nueva.url) or extraer_refcat(nueva.url)
            finally:
                nueva.close()
        except Exception as e:  # noqa: BLE001
            if self.log:
                self.log(f"SIGPAC: Catastro(U) no devolvió referencia ({e})")
            return ""
