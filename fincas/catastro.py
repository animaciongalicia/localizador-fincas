"""Todo lo que toca al Catastro. Si el Catastro cambia, solo se edita este fichero (y config.py).

Dos partes:
  1. Servicios públicos de datos no protegidos (XML): provincias, municipios, Consulta_DNPPP.
  2. PDF de la consulta descriptiva y gráfica en la Sede Electrónica (directo o con Playwright).

Los parsers son funciones puras (texto XML -> objetos) para poder probarlos sin red.
NOTA: el esquema XML que se asume aquí (etiquetas cp, cm, cmc, nm, pc1, pc2, ssp, dcc...) está
escrito de memoria y se debe contrastar con la primera respuesta real (--prueba 1).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from . import config
from .modelos import Codigos, DatosCatastro
from .red import Limitador, reintentar
from .utils import a_numero


class CatastroError(Exception):
    pass


class ParcelaNoEncontrada(CatastroError):
    pass


class RequiereCaptcha(CatastroError):
    """La Sede pide captcha. No se intenta saltar: se registra y se deja para hacer a mano."""


# ------------------------------------------------------------------ parsers (puros)
def _raiz(xml: Union[str, bytes]) -> ET.Element:
    datos = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as e:
        raise CatastroError(f"Respuesta XML no válida: {e}") from e
    for el in raiz.iter():  # quitar namespaces
        if isinstance(el.tag, str):
            el.tag = el.tag.split("}")[-1]
    return raiz


def _txt(el: Optional[ET.Element], ruta: str) -> str:
    if el is None:
        return ""
    sub = el.find(ruta)
    return (sub.text or "").strip() if sub is not None else ""


def _errores(raiz: ET.Element) -> List[Tuple[str, str]]:
    return [(_txt(e, "cod"), _txt(e, "des")) for e in raiz.iter("err")]


def parsear_provincias(xml: Union[str, bytes]) -> List[Tuple[str, str]]:
    """-> [(código, nombre)]"""
    raiz = _raiz(xml)
    if _errores(raiz):
        raise CatastroError("; ".join(d for _, d in _errores(raiz)))
    return [(_txt(p, "cpine").zfill(2), _txt(p, "np")) for p in raiz.iter("prov")]


def parsear_municipios(xml: Union[str, bytes]) -> List[dict]:
    """-> [{'nombre', 'cod_ine', 'cod_catastro'}]"""
    raiz = _raiz(xml)
    errs = _errores(raiz)
    if errs:
        raise CatastroError("; ".join(d for _, d in errs))
    salida = []
    for m in raiz.iter("muni"):
        salida.append({
            "nombre": _txt(m, "nm"),
            "cod_ine": _txt(m, "loine/cm").zfill(3),
            "cod_catastro": _txt(m, "locat/cmc") or _txt(m, "loine/cm"),
        })
    return salida


def parsear_dnppp(xml: Union[str, bytes]) -> DatosCatastro:
    """Respuesta de Consulta_DNPPP -> DatosCatastro. No toca datos protegidos."""
    raiz = _raiz(xml)
    errs = _errores(raiz)
    if errs:
        msg = "; ".join(f"{c} {d}".strip() for c, d in errs)
        raise ParcelaNoEncontrada(msg)

    # En la respuesta-lista (varios inmuebles en la parcela) solo interesan los rústicos (locs/lors);
    # los demás (p. ej. una construcción) llevan sufijo propio y no definen la finca.
    bloques = list(raiz.iter("rcdnp")) or [raiz]
    rusticos = [b for b in bloques if b.find(".//lors") is not None]
    completas: List[str] = []
    for bloque in (rusticos or bloques):
        for rc in bloque.iter("rc"):
            ref = _txt(rc, "pc1") + _txt(rc, "pc2") + _txt(rc, "car") + _txt(rc, "cc1") + _txt(rc, "cc2")
            if len(ref) >= 14 and ref not in completas:
                completas.append(ref)
    if not completas:
        raise CatastroError("La respuesta no contiene ninguna referencia catastral")

    parcelas = list(dict.fromkeys(r[:14] for r in completas))
    avisos: List[str] = []
    if len(parcelas) > 1:
        avisos.append(f"La consulta devolvió {len(parcelas)} parcelas distintas ({', '.join(parcelas)}); "
                      "se usa la primera. REVISAR A MANO.")
    refcat = parcelas[0]
    completas = [r for r in completas if r.startswith(refcat)]

    # superficie: suma de subparcelas (ssp); si no hay, la superficie del bien (sfc)
    ssp = [a_numero(e.text) for e in raiz.iter("ssp")]
    ssp = [v for v in ssp if v is not None]
    if ssp:
        sup: Optional[float] = float(sum(ssp))
    else:
        sfc = next((a_numero(e.text) for e in raiz.iter("sfc") if a_numero(e.text) is not None), None)
        sup = sfc

    usos = list(dict.fromkeys(
        [(e.text or "").strip() for e in raiz.iter("dcc") if (e.text or "").strip()]
        or [(e.text or "").strip() for e in raiz.iter("luso") if (e.text or "").strip()]
    ))
    localizacion = next(((e.text or "").strip() for e in raiz.iter("ldt") if (e.text or "").strip()), "")
    paraje = next(((e.text or "").strip() for e in raiz.iter("npa") if (e.text or "").strip()), "")

    return DatosCatastro(refcat=refcat, superficie_m2=sup, uso="; ".join(usos),
                         localizacion=localizacion, paraje=paraje,
                         referencias_completas=completas, avisos=avisos)


def es_pdf(contenido: bytes) -> bool:
    return contenido[:5] == b"%PDF-"


def hay_captcha(html: str) -> bool:
    h = html.lower()
    return "captcha" in h or "recaptcha" in h


# ------------------------------------------------------------------ servicio (con red)
class ServicioCatastro:
    def __init__(self, cliente, limitador: Limitador, navegador=None,
                 base: str = config.CATASTRO_BASE, log: Optional[Callable[[str], None]] = None):
        self.cliente = cliente      # ClienteHttp
        self.limitador = limitador
        self.navegador = navegador  # Navegador (Playwright), solo para el fallback del PDF
        self.base = base.rstrip("/")
        self.log = log

    # --- códigos
    def provincias(self) -> List[Tuple[str, str]]:
        r = self.cliente.get(f"{self.base}/ConsultaProvincia")
        return parsear_provincias(r.content)

    def municipios(self, nombre_provincia: str) -> List[dict]:
        r = self.cliente.get(f"{self.base}/ConsultaMunicipio",
                             params={"Provincia": nombre_provincia, "Municipio": ""})
        return parsear_municipios(r.content)

    # --- datos de la parcela
    def consulta_parcela(self, codigos: Codigos, poligono: str, parcela: str) -> DatosCatastro:
        r = self.cliente.get(f"{self.base}/Consulta_DNPPP", params={
            "Provincia": codigos.nombre_provincia,
            "Municipio": codigos.nombre_municipio,
            "Poligono": poligono,
            "Parcela": parcela,
        })
        datos = parsear_dnppp(r.content)
        if datos.superficie_m2 is None and not datos.uso:
            # La respuesta-lista no trae superficie ni cultivos: se pide el detalle por referencia.
            r2 = self.cliente.get(f"{self.base}/Consulta_DNPRC", params={
                "Provincia": "", "Municipio": "", "RC": datos.referencias_completas[0]})
            detalle = parsear_dnppp(r2.content)
            detalle.avisos = datos.avisos + detalle.avisos
            return detalle
        return datos

    # --- PDF
    def descargar_pdf(self, datos: DatosCatastro, codigos: Codigos, destino: Path) -> Path:
        """Abre la ficha pública del inmueble rústico y pulsa 'Consulta descriptiva y gráfica'."""
        if self.navegador is None:
            raise CatastroError("Descargar el PDF requiere navegador (Playwright)")
        destino.parent.mkdir(parents=True, exist_ok=True)
        url = config.SEDE_CONSULTA_URL.format(
            prov=codigos.cod_provincia, mun=codigos.cod_municipio_catastro,
            rc20=datos.referencias_completas[0] if datos.referencias_completas else datos.refcat)

        def _una() -> Path:
            pagina = self.navegador.pagina()
            try:
                pagina.goto(url, wait_until="networkidle", timeout=60_000)
                try:  # privacidad: rechazar cookies no esenciales
                    pagina.click(config.SEDE_BOTON_COOKIES, timeout=3_000)
                except Exception:  # noqa: BLE001
                    pass
                if pagina.query_selector(config.SEDE_BOTON_PDF) is None:
                    if hay_captcha(pagina.inner_text("body")):
                        raise RequiereCaptcha("La Sede pide captcha. Hazlo a mano con --visible.")
                    raise CatastroError(f"No encuentro el botón {config.SEDE_BOTON_PDF}: ¿cambió la Sede?")
                with pagina.expect_download(timeout=45_000) as d:
                    pagina.click(config.SEDE_BOTON_PDF)
                d.value.save_as(str(destino))
            finally:
                pagina.close()
            if not es_pdf(destino.read_bytes()[:8]):
                raise CatastroError("El fichero descargado no es un PDF")
            return destino

        return reintentar(_una, self.limitador, config.REINTENTOS, no_reintentar=(RequiereCaptcha,),
                          log=self.log)
