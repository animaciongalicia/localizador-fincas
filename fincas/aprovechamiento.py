"""Condicionantes de una corta de madera (solo Galicia): afecciones sectoriales y dificultad de saca.

No dice si se puede cortar ni qué trámite toca: solo señala qué capas oficiales toca la parcela y a qué
organismo corresponde cada una, para que el propietario sepa qué preguntar antes de vender la madera.

VERIFICADO 2026-11-12 con la parcela 15062A03400176 (Ortigueira), servidor ArcGIS de la Xunta ({base}):
1. Geometría de la parcela (EPSG:25829): INVEGA/InvegaRecintos/MapServer/0/query con returnGeometry=true.
2. Afecciones (las capas del visor de aprovechamientos forestales de la Xunta): VISAF/<servicio>/MapServer/identify,
   POST con el polígono, layers=all, tolerance=0 -> lista de capas que toca la parcela. Un servicio por petición.
3. Dificultad de saca: INVEGA/InvegaRestriccionesTecnicas/ImageServer/computeHistograms, POST con el polígono ->
   recuento de píxeles (10 m) por clase: 1 favorable, 2 intermedia, 3 desfavorable, 4 muy desfavorable.
   InvegaRestriccionesTecLeg (integración técnica y legal): 1 favorable, 2 intermedio, 3 desfavorable.
   InvegaRestriccionesLegais (0..6) NO se usa: su leyenda no explica qué significa cada valor.

Funciones puras (interpretar_*) + ServicioAprovechamiento con el cliente HTTP inyectado.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from .modelos import DatosAprovechamiento

MEDIO_RURAL = "Consellería do Medio Rural (distrito forestal)"
INCENDIO = "Incendio registrado en la parcela (registros de 2010 a 2021)"
TECNICAS = {1: "Favorable", 2: "Intermedia", 3: "Desfavorable", 4: "Muy desfavorable"}
INTEGRADA = {1: "Favorable", 2: "Intermedia", 3: "Desfavorable"}

# servicio de VISAF -> (afección, organismo al que corresponde informar)
SERVICIOS: List[Tuple[str, str, str]] = [
    ("Augas", "Aguas (cauces y su zona de afección)", ""),   # el organismo de cuenca sale del atributo
    ("Estradas", "Carreteras", ""),                           # el titular sale del nombre de la capa
    ("Costas_DPMT", "Costas (dominio público marítimo-terrestre y servidumbre de protección)",
     "Administración de costas"),
    ("PatrimonioCultural", "Patrimonio cultural", "Dirección Xeral de Patrimonio Cultural (Xunta de Galicia)"),
    ("CaminosSantiago", "Camino de Santiago", "Dirección Xeral de Patrimonio Cultural (Xunta de Galicia)"),
    ("PatrimonioNatural_autorizacion_declaracion", "Espacios naturales protegidos",
     "Dirección Xeral de Patrimonio Natural (Xunta de Galicia)"),
    ("ZonasDemarcadas", "Zona demarcada por el nematodo del pino",
     "Sanidad vegetal (Consellería do Medio Rural)"),
    # --- capas del Plan Básico Autonómico (PBA) y del registro de incendios; tipo y organismo salen de cada capa
    ("Afeccions_Forestal", "Registro forestal", MEDIO_RURAL),
    ("Afeccions_outras_Forestal_Faixas", "Faja de protección de 50 m (gestión de la biomasa frente a incendios)",
     "Concello"),
    ("Afeccions_Enerxia", "Líneas eléctricas, gasoducto u oleoducto", "Titular de la instalación"),
    ("Afeccions_ConcentracionsParcelarias", "Concentración parcelaria", MEDIO_RURAL),
    ("CAMPANHAS_INCENDIOS", INCENDIO, "Dato informativo"),                 # 2020-2021, con fecha
    ("Afeccions_outras_AreasIncendiadas", INCENDIO, "Dato informativo"),   # 2010-2020, solo año
]
# carpeta del servidor de cada servicio (por defecto VISAF)
CARPETAS = {"Afeccions_Forestal": "PBA", "Afeccions_outras_Forestal_Faixas": "PBA", "Afeccions_Enerxia": "PBA",
            "Afeccions_ConcentracionsParcelarias": "PBA", "Afeccions_outras_AreasIncendiadas": "PBA",
            "CAMPANHAS_INCENDIOS": "SIRFOG"}
# capa del registro forestal (trozo del nombre, minúsculas) -> (afección, organismo)
_FORESTAL: List[Tuple[str, str, str]] = [
    ("veci", "Monte vecinal en mano común (registro de la Xunta)",
     "Comunidad de montes titular / Xurado Provincial de Montes Veciñais en Man Común"),
    ("utilidade", "Monte de utilidad pública", MEDIO_RURAL),
    ("ordenados", "Monte con instrumento de ordenación o gestión", MEDIO_RURAL),
    ("frondosas", "Masa consolidada de frondosas autóctonas (registro de la Xunta)", MEDIO_RURAL),
    ("agrupaci", "Agrupación forestal de gestión conjunta", MEDIO_RURAL),
    ("fomento", "Sociedad de fomento forestal", MEDIO_RURAL),
]
_NOMBRES = ("NOME", "NAME", "ZONA", "IDE_DENOM", "NOM_RIO", "NOMBRE", "AMBITO", "NOMEMONTE", "MONTE")


def _atr(atributos: dict, *claves: str) -> str:
    """Valor del primer atributo presente (identify devuelve alias: se compara sin mayúsculas)."""
    m = {str(k).upper(): v for k, v in atributos.items()}
    for c in claves:
        v = str(m.get(c.upper(), "") or "").strip()
        if v and v not in ("Null", "None", "-", "."):
            return v
    return ""


def _organismo_aguas(atributos: dict) -> str:
    conf = next((str(v).strip() for k, v in atributos.items() if k.upper().startswith("CONFEDERAC") and v), "")
    if not conf:
        return "Organismo de cuenca"
    if "galicia" in conf.lower():
        return f"Augas de Galicia (demarcación {conf})"
    return f"Confederación Hidrográfica ({conf})"


def _detalle_carreteras(capa: str) -> str:
    """Nombre de capa del servicio (gallego) -> texto legible."""
    c = capa.lower()
    red = ("estatal" if "estatais" in c else "autonómica" if "auton" in c else
           "de la Deputación" if "deputaci" in c else "")
    tipo = " de alta capacidad" if "alta capacidade" in c else " convencional" if "convencionais" in c else ""
    if not red:
        return capa
    return f"{'Zona de afección de carretera' if 'afecci' in c else 'Carretera'} {red}{tipo}"


def _organismo_carreteras(capa: str) -> str:
    c = capa.lower()
    if "estatais" in c:
        return "Demarcación de Carreteras del Estado en Galicia"
    if "auton" in c:
        return "Axencia Galega de Infraestruturas (Xunta de Galicia)"
    if "deputaci" in c:
        return "Deputación provincial"
    return "Titular de la carretera"


def interpretar_identify(servicio: str, respuesta: dict) -> List[Dict[str, str]]:
    """Respuesta de MapServer/identify -> [{'tipo','detalle','organismo'}], sin repetir capa."""
    fila = next((x for x in SERVICIOS if x[0] == servicio), None)
    if fila is None:
        return []
    _, tipo, organismo = fila
    out: List[Dict[str, str]] = []
    vistos = set()
    for r in respuesta.get("results") or []:
        capa = str(r.get("layerName") or "").strip()
        atr = r.get("attributes") or {}
        nombre = next((str(atr[k]).strip() for k in atr if k.upper() in _NOMBRES
                       and str(atr[k]).strip() not in ("", "Null", "None")), "")
        detalle = f"{capa}: {nombre}" if nombre and nombre.lower() not in capa.lower() else capa
        org, tipo_fila = organismo, tipo
        if servicio == "Augas":
            org = _organismo_aguas(atr)
            if capa.lower().startswith("afeccion"):      # dos capas del servicio dicen lo mismo: una sola fila
                detalle = "Zona de afección de cauces"
        elif servicio == "Estradas":
            org = _organismo_carreteras(capa)
            detalle = _detalle_carreteras(capa)
        elif servicio == "Afeccions_Forestal":
            regla = next((x for x in _FORESTAL if x[0] in capa.lower()), None)
            monte = _atr(atr, "NOMEMONTE", "MONTE", "NOME")
            if regla:
                tipo_fila, org = regla[1], regla[2]
                detalle = " · ".join(x for x in [f"Monte: {monte}" if monte else "", _atr(atr, "TIPOINSTR")] if x) or capa
        elif servicio == "Afeccions_outras_Forestal_Faixas":
            parroquia = _atr(atr, "PARROQUIA")
            detalle = "Faja de 50 m" + (f" (parroquia de {parroquia})" if parroquia else "")
        elif servicio == "Afeccions_Enerxia":
            c = capa.lower()
            clase = ("Línea eléctrica" if "ctrica" in c else "Gasoducto" if "gasodu" in c else
                     "Oleoducto" if "oleodu" in c else capa)
            if "pia/prs" in c:
                clase += " prevista"
            detalle = clase + (f": {nombre}" if nombre else "")
        elif servicio == "Afeccions_ConcentracionsParcelarias":
            fase = _atr(atr, "FASE_ULT")
            detalle = f"Zona {nombre or 'de concentración'}" + (f" ({fase.lower()})" if fase else "")
        elif tipo == INCENDIO:
            codigo, fecha = _atr(atr, "CODIGO"), _atr(atr, "DATA")
            if not codigo:
                continue
            cuando = f"del {fecha}" if fecha else f"de {codigo[:4]}"
            detalle = f"Incendio {cuando} (código {codigo})"
        if (detalle, org) in vistos:
            continue
        vistos.add((detalle, org))
        out.append({"tipo": tipo_fila, "detalle": detalle, "organismo": org})
    return out


def interpretar_histograma(respuesta: dict, clases: Dict[int, str]) -> Dict[str, float]:
    """computeHistograms -> {'Muy desfavorable': 1.0}: fracción de la parcela en cada clase ({} si no hay píxeles)."""
    hs = respuesta.get("histograms") or []
    if not hs:
        return {}
    h = hs[0]
    cuentas = h.get("counts") or []
    n = len(cuentas)
    if not n:
        return {}
    ancho = (float(h.get("max", n)) - float(h.get("min", 0))) / n
    suma: Dict[str, float] = {}
    for i, c in enumerate(cuentas):
        valor = int(round(float(h.get("min", 0)) + (i + 0.5) * ancho))
        if c and valor in clases:
            suma[clases[valor]] = suma.get(clases[valor], 0.0) + float(c)
    total = sum(suma.values())
    return {k: round(v / total, 3) for k, v in suma.items()} if total else {}


def texto_clases(reparto: Dict[str, float]) -> str:
    """'Muy desfavorable 80 % · Desfavorable 20 %' (de mayor a menor)."""
    return " · ".join(f"{k} {v * 100:.0f} %" for k, v in sorted(reparto.items(), key=lambda x: -x[1]))


def geometria_ultima_edicion(features: Sequence[dict]) -> Optional[dict]:
    """Anillos (EPSG:25829) de los recintos de la edición más reciente; None si no hay geometría."""
    ultimo = max((f.get("attributes", {}).get("ADM_FECINI") or 0 for f in features), default=0)
    anillos = [a for f in features if (f.get("attributes", {}).get("ADM_FECINI") or 0) == ultimo
               for a in (f.get("geometry") or {}).get("rings") or []]
    if not anillos:
        return None
    return {"rings": anillos, "spatialReference": {"wkid": 25829}}


def resumen_afecciones(d: DatosAprovechamiento) -> str:
    """'Aguas; Carreteras' para el Excel; 'Ninguna detectada' si no hay."""
    tipos = list(dict.fromkeys(a["tipo"].split(" (")[0] for a in d.afecciones))
    return "; ".join(tipos) if tipos else "Ninguna detectada"


class ServicioAprovechamiento:
    """Consulta por referencia catastral (14 car.). `http` = ClienteHttp (pausa y reintentos compartidos)."""

    def __init__(self, http, base: str = config.INVEGA_SERVICIOS, log: Optional[Callable[[str], None]] = None):
        self.http, self.base, self.log = http, base.rstrip("/"), log

    def _json(self, r) -> dict:
        datos = r.json()
        if not isinstance(datos, dict) or "error" in datos:
            raise RuntimeError(f"el servicio devolvió un error: {str(datos)[:200]}")
        return datos

    def consultar(self, refcat: str) -> Optional[DatosAprovechamiento]:
        """None si la parcela no tiene geometría en el inventario. Lanza si falla la geometría; si falla una
        capa suelta, se anota en `sin_consultar` y se sigue (nunca se da por limpia una capa no consultada)."""
        rc = (refcat or "").strip().upper()[:14]
        if not re.fullmatch(r"[0-9A-Z]{14}", rc):
            raise ValueError(f"referencia catastral no válida: {refcat!r}")
        datos = self._json(self.http.get(f"{self.base}/INVEGA/InvegaRecintos/MapServer/0/query", params={
            "where": f"REFCAT='{rc}'", "outFields": "REFCAT,ADM_FECINI", "returnGeometry": "true", "f": "json"}))
        geom = geometria_ultima_edicion(datos.get("features") or [])
        if geom is None:
            return None
        xs = [p[0] for a in geom["rings"] for p in a]
        ys = [p[1] for a in geom["rings"] for p in a]
        comun = {"geometry": json.dumps(geom), "geometryType": "esriGeometryPolygon", "f": "json"}
        d = DatosAprovechamiento()
        for servicio, tipo, _ in SERVICIOS:
            try:
                r = self._json(self.http.post(f"{self.base}/{CARPETAS.get(servicio, 'VISAF')}/{servicio}/MapServer/identify", data=dict(
                    comun, sr="25829", layers="all", tolerance="0", returnGeometry="false",
                    mapExtent=f"{min(xs) - 50},{min(ys) - 50},{max(xs) + 50},{max(ys) + 50}",
                    imageDisplay="400,400,96")))
                for a in interpretar_identify(servicio, r):
                    codigo = re.search(r"\(código (\w+)\)", a["detalle"])     # el mismo incendio sale en dos capas
                    if codigo and any(codigo.group(0) in x["detalle"] for x in d.afecciones):
                        continue
                    d.afecciones.append(a)
            except Exception as e:  # noqa: BLE001
                if tipo.split(" (")[0] not in d.sin_consultar:
                    d.sin_consultar.append(tipo.split(" (")[0])
                if self.log:
                    self.log(f"aprovechamiento {rc}: no se pudo consultar {servicio} ({e})")
        for capa, clases, campo in (("InvegaRestriccionesTecnicas", TECNICAS, "saca"),
                                    ("InvegaRestriccionesTecLeg", INTEGRADA, "integrada")):
            try:
                r = self._json(self.http.post(f"{self.base}/INVEGA/{capa}/ImageServer/computeHistograms", data=comun))
                setattr(d, campo, interpretar_histograma(r, clases))
            except Exception as e:  # noqa: BLE001
                if self.log:
                    self.log(f"aprovechamiento {rc}: no se pudo consultar {capa} ({e})")
        return d
