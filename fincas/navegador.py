"""Navegador Playwright compartido (un solo Chromium para Catastro y SIGPAC), arranque perezoso."""
from __future__ import annotations


class Navegador:
    def __init__(self, visible: bool = False):
        self.visible = visible
        self._pw = None
        self._browser = None
        self._ctx = None

    def _abrir(self) -> None:
        if self._ctx is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Falta playwright: pip install playwright && playwright install chromium") from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self.visible)
        self._ctx = self._browser.new_context(locale="es-ES", viewport={"width": 1400, "height": 900})
        self._ctx.set_default_timeout(45_000)

    def pagina(self):
        if self._ctx is not None and not self._vivo():
            self.cerrar()  # Chromium murió (memoria, crash): se relanza en vez de fallar todas las parcelas
        self._abrir()
        self._usos = getattr(self, "_usos", 0) + 1
        if self._usos % 60 == 0:  # reciclar el contexto: acumula cookies/popups durante lotes largos
            self.cerrar()
            self._abrir()
        return self._ctx.new_page()

    def _vivo(self) -> bool:
        try:
            return bool(self._browser and self._browser.is_connected())
        except Exception:  # noqa: BLE001
            return False

    def cerrar(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()
