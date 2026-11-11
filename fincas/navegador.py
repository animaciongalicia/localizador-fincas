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
        self._abrir()
        return self._ctx.new_page()

    def cerrar(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()
