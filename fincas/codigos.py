"""Resolución de provincia/municipio (nombre -> códigos) con el callejero del Catastro.

Tolera tildes, mayúsculas, artículos ('A Coruña' / 'Coruña, A' / 'La Coruña') y
nombres en gallego o castellano. Si hay ambigüedad lanza Ambiguo (la CLI pregunta).
"""
from __future__ import annotations

import difflib
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .modelos import Codigos
from .utils import normalizar

ARTICULOS = {"a", "o", "as", "os", "la", "el", "las", "los", "l", "els", "les", "lo", "sa", "es"}

# Alias de provincias que no coinciden con ninguna parte del nombre oficial del Catastro.
ALIAS_PROVINCIA = {
    "la coruna": "coruna", "orense": "ourense", "baleares": "illes balears",
    "islas baleares": "illes balears", "guipuzcoa": "gipuzkoa", "vizcaya": "bizkaia",
    "gerona": "girona", "lerida": "lleida",
}


class Ambiguo(Exception):
    def __init__(self, texto: str, candidatos: Sequence[str]):
        super().__init__(f"'{texto}' es ambiguo: {', '.join(candidatos)}")
        self.texto, self.candidatos = texto, list(candidatos)


class NoEncontrado(Exception):
    pass


def clave_nombre(nombre: str) -> str:
    """Normaliza 'CORUÑA, A' y 'A Coruña' a la misma clave ('coruna')."""
    n = nombre.strip()
    m = re.match(r"^(.*?),\s*([A-Za-zÀ-ÿ']{1,3})$", n)  # 'CORUÑA, A' -> 'A CORUÑA'
    if m and normalizar(m.group(2)) in ARTICULOS:
        n = f"{m.group(2)} {m.group(1)}"
    palabras = normalizar(n).split()
    while palabras and palabras[0] in ARTICULOS and len(palabras) > 1:
        palabras.pop(0)
    return " ".join(palabras)


def _partes(oficial: str) -> List[str]:
    """'ARABA/ÁLAVA' -> ['araba', 'alava']; 'CORUÑA, A' -> ['coruna']."""
    return [clave_nombre(p) for p in oficial.split("/") if p.strip()]


def elegir(texto: str, opciones: Dict[str, str], es_provincia: bool = False) -> List[str]:
    """Devuelve los nombres oficiales de `opciones` que encajan con `texto` (0, 1 o varios)."""
    k = clave_nombre(texto)
    if es_provincia:
        k = ALIAS_PROVINCIA.get(k, k)
    # 1) coincidencia exacta con el nombre o con una de sus formas bilingües
    exactos = [o for o in opciones if k in _partes(o)]
    if exactos:
        return exactos
    # 2) prefijo de palabra completa ('Vilanova' -> 'Vilanova de Arousa', 'Vilanova de Lourenzá')
    pref = [o for o in opciones if any(p.startswith(k + " ") for p in _partes(o))]
    if pref:
        return pref
    # 3) errata tipográfica
    todas = {p: o for o in opciones for p in _partes(o)}
    cerca = difflib.get_close_matches(k, list(todas), n=3, cutoff=0.85)
    return list(dict.fromkeys(todas[c] for c in cerca))


class ResolutorCodigos:
    """Usa un ServicioCatastro (inyectado) y memoriza resultados."""

    def __init__(self, catastro, cache=None):
        self.catastro = catastro
        self.cache = cache
        self._provincias: Optional[Dict[str, str]] = None   # nombre -> código
        self._municipios: Dict[str, List[dict]] = {}        # cod_prov -> lista
        self._memo: Dict[Tuple[str, str], Codigos] = {}

    def _cargar_provincias(self) -> Dict[str, str]:
        if self._provincias is None:
            datos = self.cache.leer("codigos", "provincias") if self.cache else None
            if not datos or not datos.get("provincias"):
                datos = {"provincias": self.catastro.provincias()}
                if self.cache and datos["provincias"]:  # nunca cachear una lista vacía (error transitorio)
                    self.cache.guardar("codigos", "provincias", datos)
            self._provincias = {n: c for c, n in datos["provincias"]}
        return self._provincias

    def _cargar_municipios(self, cod_prov: str, nombre_prov: str) -> List[dict]:
        if cod_prov not in self._municipios:
            clave = f"municipios_{cod_prov}"
            datos = self.cache.leer("codigos", clave) if self.cache else None
            if not datos or not datos.get("municipios"):
                datos = {"municipios": self.catastro.municipios(nombre_prov)}
                if self.cache and datos["municipios"]:
                    self.cache.guardar("codigos", clave, datos)
            self._municipios[cod_prov] = datos["municipios"]
        return self._municipios[cod_prov]

    def resolver(self, provincia: str, municipio: str,
                 preguntar: Optional[Callable[[str, Sequence[str]], str]] = None) -> Codigos:
        memo_k = (normalizar(provincia), normalizar(municipio))
        if memo_k in self._memo:
            return self._memo[memo_k]

        provs = self._cargar_provincias()
        nombre_prov = self._elegir_uno(provincia, {n: n for n in provs}, preguntar, "provincia")
        cod_prov = provs[nombre_prov]

        munis = self._cargar_municipios(cod_prov, nombre_prov)
        por_nombre: Dict[str, List[dict]] = {}
        for m in munis:
            por_nombre.setdefault(m["nombre"], []).append(m)
        nombre_mun = self._elegir_uno(municipio, {n: n for n in por_nombre}, preguntar, "municipio")
        m = por_nombre[nombre_mun][0]
        cod = Codigos(cod_provincia=cod_prov, nombre_provincia=nombre_prov,
                      cod_municipio_ine=m["cod_ine"], cod_municipio_catastro=m["cod_catastro"],
                      nombre_municipio=nombre_mun)
        self._memo[memo_k] = cod
        return cod

    @staticmethod
    def _elegir_uno(texto: str, opciones: Dict[str, str], preguntar, tipo: str) -> str:
        coinc = elegir(texto, opciones, es_provincia=(tipo == "provincia"))
        if len(coinc) == 1:
            return coinc[0]
        if not coinc:
            raise NoEncontrado(f"No encuentro {tipo} '{texto}' en el Catastro")
        if preguntar is None:
            raise Ambiguo(texto, coinc)
        return preguntar(f"{tipo} '{texto}'", coinc)
