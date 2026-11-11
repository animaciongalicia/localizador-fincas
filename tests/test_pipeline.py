"""Pipeline completo con servicios simulados: cero red."""
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from fincas.cache import Cache
from fincas.catastro import ParcelaNoEncontrada
from fincas.entrada import leer_fincas
from fincas.modelos import Codigos, DatosCatastro, ResultadoSigpac
from fincas.pipeline import Opciones, Servicios, ejecutar
from fincas.registro import Registro

EJEMPLO = Path(__file__).parent / "ejemplo.csv"


class Resolutor:
    def resolver(self, prov, mun, preguntar=None):
        return Codigos("36", "PONTEVEDRA", "060", "60", mun.upper())


class Catastro:
    def consulta_parcela(self, cod, pol, parc):
        if parc == "45":
            raise ParcelaNoEncontrada("43 LA PARCELA NO EXISTE")
        return DatosCatastro(refcat=f"36060A{pol.zfill(3)}{parc.zfill(5)}", superficie_m2=2500.0, uso="Pastos")

    def descargar_pdf(self, datos, cod, destino):
        destino.write_bytes(b"%PDF-1.4 fake")
        return destino


class Sigpac:
    def consultar(self, finca, cod, refcat, destino):
        if finca.parcela == "7":
            return ResultadoSigpac("NO ENCONTRADA")
        destino.write_bytes(b"%PDF-1.4 fake")
        return ResultadoSigpac("ENCONTRADA", refcat=refcat, pdf=destino.name)


def test_pipeline_completo(tmp_path):
    fincas, _ = leer_fincas(str(EJEMPLO))
    op = Opciones("prueba cliente", salidas=tmp_path, fecha="2026-01-01")
    srv = Servicios(Resolutor(), Catastro(), Sigpac(), Cache(tmp_path / ".cache"))
    raiz = ejecutar(fincas, op, srv, Registro(eco=None))

    assert raiz.name == "2026-01-01_prueba-cliente"
    assert (raiz / "resultados.xlsx").exists() and (raiz / "registro.txt").exists()
    assert (tmp_path / "2026-01-01_prueba-cliente.zip").exists()
    assert (raiz / "pdf_catastro" / "vilanova-de-arousa_poligono05_parcela123_catastro.pdf").exists()
    assert (raiz / "pdf_sigpac" / "vilanova-de-arousa_poligono05_parcela123_sigpac.pdf").exists()

    ws = load_workbook(raiz / "resultados.xlsx").active
    filas = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(filas) == 5                                   # nada se descarta
    assert filas[0][9] == "OK" and filas[0][11] == "SI"
    assert filas[1][9] == "OK"                                # 500,1 x 5 ~ 2500
    assert filas[2][11] == "NO ENCONTRADA"                    # parcela 7: SIGPAC no la tiene
    assert not filas[3][5]                                # parcela 45: Catastro falla, continúa
    assert "no encontrada" in open(raiz / "registro.txt", encoding="utf-8").read().lower()
    assert ws.cell(row=3, column=1).fill.fgColor.rgb.endswith("FFF2CC")   # cuota 1/5 resaltada
    assert not ws.cell(row=2, column=1).fill.fgColor.rgb.endswith("FFF2CC")


def test_cache_evita_repetir(tmp_path):
    fincas, _ = leer_fincas(str(EJEMPLO))
    llamadas = []

    class Contador(Catastro):
        def consulta_parcela(self, *a):
            llamadas.append(a)
            return super().consulta_parcela(*a)

    op = Opciones("x", salidas=tmp_path, fecha="2026-01-01", prueba=1)
    srv = Servicios(Resolutor(), Contador(), None, Cache(tmp_path / ".cache"))
    ejecutar(fincas, op, srv, Registro(eco=None))
    ejecutar(fincas, op, srv, Registro(eco=None))
    assert len(llamadas) == 1
