"""Estructuras de datos compartidas entre módulos."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

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

    @property
    def etiqueta(self) -> str:
        return f"{self.municipio} pol {self.poligono} parc {self.parcela}"

    @property
    def nombre_base(self) -> str:
        """municipio_poligonoXX_parcelaXXX (sin extensión)."""
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


@dataclass
class ResultadoSigpac:
    estado: str                 # "ENCONTRADA" | "NO ENCONTRADA" | "ERROR"
    refcat: str = ""
    pdf: str = ""
    detalle: str = ""
    respaldo: bool = False      # True si la ficha lleva la captura del visor (relleno) y no el plano de contorno


@dataclass
class Resultado:
    finca: Finca
    codigos: Optional[Codigos] = None
    catastro: Optional[DatosCatastro] = None
    pdf_catastro: str = ""
    sigpac: Optional[ResultadoSigpac] = None
    control_superficie: str = ""
    coincide: str = ""          # SI | NO | NO ENCONTRADA
    avisos: List[str] = field(default_factory=list)
    errores: List[str] = field(default_factory=list)
