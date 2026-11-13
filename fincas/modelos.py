"""Estructuras de datos compartidas entre módulos."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .utils import slug


@dataclass
class Cuota:
    texto: str = "completa"
    numerador: int = 1
    denominador: int = 1
    completa: bool = True
    valida: bool = True  # False si el texto no se pudo interpretar


@dataclass
class Finca:
    municipio: str
    provincia: str
    poligono: str
    parcela: str
    paraje: str = ""
    superficie_m2: Optional[float] = None
    cuota: Cuota = field(default_factory=Cuota)
    notas: str = ""
    fila: int = 0  # fila de origen en el fichero (para el registro)
    sin_datos: str = ""  # motivo si no hay polígono/parcela: la finca se conserva en el Excel pero no se busca
    refcat: str = ""     # referencia catastral del cliente cuando NO es rústica (inmueble urbano): se busca por ella

    @property
    def por_referencia(self) -> bool:
        """Inmueble sin polígono/parcela (urbano) que se busca por su referencia catastral."""
        return bool(self.refcat) and not (self.poligono and self.parcela)

    @property
    def clave_parcela(self) -> str:
        """Parte de la clave de caché que identifica la finca dentro del municipio."""
        return f"rc_{self.refcat[:14]}" if self.por_referencia else f"{self.poligono}_{self.parcela}"

    @property
    def etiqueta(self) -> str:
        if self.por_referencia:
            return f"{self.municipio} ref {self.refcat[:14]}"
        return f"{self.municipio} pol {self.poligono} parc {self.parcela}"

    @property
    def nombre_base(self) -> str:
        """municipio_poligonoXX_parcelaXXX (sin extensión)."""
        if self.por_referencia:
            return f"{slug(self.municipio)}_ref{self.refcat[:14]}"
        if not self.poligono or not self.parcela:
            return f"{slug(self.municipio)}_fila{self.fila}"
        pol = self.poligono.zfill(2) if self.poligono.isdigit() else self.poligono
        par = self.parcela.zfill(3) if self.parcela.isdigit() else self.parcela
        return f"{slug(self.municipio)}_poligono{pol}_parcela{par}"


@dataclass
class Codigos:
    cod_provincia: str          # INE, 2 dígitos
    nombre_provincia: str       # nombre oficial en Catastro
    cod_municipio_ine: str      # 3 dígitos (lo usa SIGPAC)
    cod_municipio_catastro: str  # código de municipio en Catastro
    nombre_municipio: str       # nombre oficial en Catastro

    @property
    def clave(self) -> str:
        return f"{self.cod_provincia}{self.cod_municipio_ine}"


@dataclass
class DatosCatastro:
    refcat: str                 # 14 caracteres (parcela)
    superficie_m2: Optional[float] = None
    uso: str = ""
    localizacion: str = ""
    paraje: str = ""
    referencias_completas: List[str] = field(default_factory=list)  # 20 car. por subparcela
    avisos: List[str] = field(default_factory=list)
    consultado: str = ""        # fecha y hora de la consulta al Catastro (ISO, minutos)
    clase: str = ""             # 'Urbano' | 'Rústico' ('' en entradas antiguas de caché)
    construida_m2: Optional[float] = None   # superficie construida (inmuebles urbanos)
    anio: str = ""              # año de construcción
    construcciones: List[str] = field(default_factory=list)   # 'VIVIENDA 306 m²'...
    lon: Optional[float] = None  # centro de la parcela (solo se pide para inmuebles urbanos: sitúa el mapa)
    lat: Optional[float] = None

    @property
    def uso_detallado(self) -> str:
        """Uso o cultivo y, si hay construcción, superficie construida, año y destinos."""
        partes = [self.uso]
        if self.construida_m2:
            partes.append(f"{self.construida_m2:,.0f} m² construidos".replace(",", "."))
        if self.anio:
            partes.append(f"año {self.anio}")
        if self.construcciones:
            partes.append(", ".join(self.construcciones))
        return " · ".join(p for p in partes if p)


@dataclass
class ResultadoSigpac:
    estado: str                 # "ENCONTRADA" | "NO ENCONTRADA" | "ERROR"
    refcat: str = ""
    pdf: str = ""
    detalle: str = ""
    consultado: str = ""        # fecha y hora de la consulta al visor (ISO, minutos)
    respaldo: bool = False      # True si la ficha lleva la captura del visor (relleno) y no el plano de contorno


@dataclass
class DatosInventario:
    """Cobertura actual de la parcela según el Inventario Forestal Continuo de Galicia (última edición)."""
    anio: int = 0                       # edición del inventario
    superficie_m2: float = 0.0          # superficie con datos
    coberturas: Dict[str, float] = field(default_factory=dict)   # 'Eucaliptos' -> m²
    dominante: str = ""
    uso: str = ""                       # p. ej. 'FORESTAL ARBOLADO'
    volumen_m3: Optional[float] = None  # estimación PROVISIONAL del propio inventario (suele venir vacía)
    recintos: int = 0
    historico: Dict[str, Dict[str, float]] = field(default_factory=dict)   # '2022' -> coberturas
    consultado: str = ""                # fecha y hora de la consulta (ISO, minutos)
    monte_vecinal: Optional[bool] = None   # el inventario lo marca como monte vecinal o público (None = no consta)


@dataclass
class DatosAprovechamiento:
    """Condicionantes de una corta de madera: afecciones sectoriales que toca la parcela y dificultad de saca."""
    afecciones: List[Dict[str, str]] = field(default_factory=list)   # {'tipo', 'detalle', 'organismo'}
    sin_consultar: List[str] = field(default_factory=list)           # capas que fallaron: NO se dan por limpias
    saca: Dict[str, float] = field(default_factory=dict)             # 'Muy desfavorable' -> fracción de la parcela
    integrada: Dict[str, float] = field(default_factory=dict)        # integración técnica y legal (Xunta)
    consultado: str = ""


@dataclass
class Resultado:
    finca: Finca
    codigos: Optional[Codigos] = None
    catastro: Optional[DatosCatastro] = None
    pdf_catastro: str = ""
    sigpac: Optional[ResultadoSigpac] = None
    control_superficie: str = ""
    coincide: str = ""          # SI | NO | NO ENCONTRADA
    inventario: Optional[DatosInventario] = None
    contraste: str = ""         # cultivo Catastro vs inventario: COHERENTE | REVISAR | NO COMPARABLE | SIN DATO
    contraste_detalle: str = ""
    aprovechamiento: Optional[DatosAprovechamiento] = None   # condicionantes de corta (solo Galicia)
    avisos: List[str] = field(default_factory=list)
    errores: List[str] = field(default_factory=list)


@dataclass
class Meta:
    """Datos de emisión del informe (trazabilidad)."""
    cliente: str = ""
    expediente: str = ""
    fecha: str = ""             # fecha de emisión dd/mm/aaaa


def fecha_legible(iso: str) -> str:
    """'2026-11-12T09:01' -> '12/11/2026 09:01'. Vacío si no hay dato."""
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return ""
