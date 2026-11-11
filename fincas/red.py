"""Pausas entre consultas, reintentos y cliente HTTP. Común a Catastro y SIGPAC."""
from __future__ import annotations

import random
import time
from typing import Callable, Optional, Tuple, TypeVar

from . import config

T = TypeVar("T")


class Limitador:
    """Garantiza una pausa aleatoria (3-5 s por defecto) entre consultas reales."""

    def __init__(self, minimo: float = config.PAUSA_MIN_S, maximo: float = config.PAUSA_MAX_S,
                 dormir: Callable[[float], None] = time.sleep):
        self.minimo, self.maximo = minimo, max(minimo, maximo)
        self._dormir = dormir
        self._primera = True

    def esperar(self) -> None:
        if self._primera:
            self._primera = False
            return
        if self.maximo > 0:
            self._dormir(random.uniform(self.minimo, self.maximo))


def reintentar(fn: Callable[[], T], limitador: Limitador, reintentos: int = config.REINTENTOS,
               excepciones: Tuple[type, ...] = (Exception,),
               no_reintentar: Tuple[type, ...] = (),
               log: Optional[Callable[[str], None]] = None) -> T:
    """Ejecuta fn con pausa previa; ante error reintenta hasta `reintentos` veces."""
    ultimo: Optional[BaseException] = None
    for intento in range(reintentos + 1):
        limitador.esperar()
        try:
            return fn()
        except no_reintentar:
            raise
        except excepciones as e:  # noqa: PERF203
            ultimo = e
            if log:
                log(f"intento {intento + 1}/{reintentos + 1} fallido: {e}")
    assert ultimo is not None
    raise ultimo


class ClienteHttp:
    """Envoltorio fino de requests con pausa y reintentos."""

    def __init__(self, limitador: Limitador, reintentos: int = config.REINTENTOS, log=None):
        import requests  # import perezoso: los tests de parseo no lo necesitan

        self._s = requests.Session()
        self._s.headers["User-Agent"] = config.USER_AGENT
        self.limitador, self.reintentos, self.log = limitador, reintentos, log

    def get(self, url: str, params: Optional[dict] = None):
        def _una():
            r = self._s.get(url, params=params, timeout=config.TIMEOUT_HTTP_S)
            r.raise_for_status()
            return r

        return reintentar(_una, self.limitador, self.reintentos, log=self.log)
