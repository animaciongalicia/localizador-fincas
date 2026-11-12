"""Constantes y URLs en un único sitio.

Las URLs de Catastro, Sede y visores están verificadas con consultas reales (ver CLAUDE.md).
Se pueden sobreescribir con variables de entorno para no tocar código si cambian.
"""
import os
from pathlib import Path

# --- Catastro: servicios públicos de datos no protegidos (SOAP/HTTP-GET, XML)
CATASTRO_BASE = os.environ.get(
    "FINCAS_CATASTRO_BASE",
    "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx",
)

# --- Catastro: Sede Electrónica (PDF de consulta descriptiva y gráfica)
# VERIFICADO (2026-11-11): la ficha pública del inmueble carga sin login y su botón
# "CONSULTA DESCRIPTIVA Y GRÁFICA" (#BImpCroquisYDatos) descarga el PDF sin captcha.
# Campos: {prov} código provincia, {mun} código municipio catastral, {rc20} ref. de 20 caracteres.
SEDE_CONSULTA_URL = (
    "https://www1.sedecatastro.gob.es/CYCBienInmueble/OVCConCiud.aspx"
    "?del={prov}&mun={mun}&UrbRus=R&RefC={rc20}&Apenom=&esBice=&RCBice1=&RCBice2=&DenoBice="
    "&from=nuevoVisor&ZV=NO&anyoZV="
)
SEDE_BOTON_PDF = "#BImpCroquisYDatos"
SEDE_BOTON_COOKIES = "text=Denegar cookies"

# --- SIGPAC
SIGPAC_VISOR_URL = os.environ.get(
    "FINCAS_SIGPAC_URL", "https://sigpac.mapa.gob.es/fega/visor/"
)

SIXPAC_VISOR_URL = os.environ.get("FINCAS_SIXPAC_URL", "https://sixpac.xunta.gal/visorhtml5/")
# Servicios de datos del visor SIXPAC (geometría de parcela y ortofoto) para dibujar el plano de la ficha
SIXPAC_SERVICIOS = os.environ.get("FINCAS_SIXPAC_SERVICIOS", "https://vispac.xunta.gal")

# --- Comportamiento
PAUSA_MIN_S = 3.0
PAUSA_MAX_S = 5.0
REINTENTOS = 2           # reintentos adicionales ante error (3 intentos en total)
TIMEOUT_HTTP_S = 30
MARGEN_SUPERFICIE = 0.10  # 10 %
USER_AGENT = "fincas-cli/0.1 (consulta de datos publicos; uso moderado)"

# La herramienta vive en una carpeta y cada cliente tiene la suya (entrada/ y salidas/).
CARPETA_HERRAMIENTA = Path(__file__).resolve().parent.parent
CARPETA_CLIENTES = Path(os.environ.get(
    "FINCAS_CLIENTES", str(Path.home() / "Desktop" / "BUSQUEDAS FERRADOS-CLAUDE")))
CARPETA_CACHE = str(CARPETA_HERRAMIENTA / ".cache")  # única para todos los clientes, ejecutes desde donde ejecutes
