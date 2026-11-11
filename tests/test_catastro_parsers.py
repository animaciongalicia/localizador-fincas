"""XML ILUSTRATIVOS escritos a mano (no capturados del servicio real).
Cuando se haga la primera consulta real, sustituir por respuestas reales."""
import pytest

from fincas.catastro import (CatastroError, ParcelaNoEncontrada, es_pdf, hay_captcha,
                             parsear_dnppp, parsear_municipios, parsear_provincias)

PROV = """<?xml version="1.0" encoding="utf-8"?>
<consulta_provinciero xmlns="http://www.catastro.meh.es/"><control><cuprov>2</cuprov></control>
<provinciero><prov><cpine>15</cpine><np>CORUÑA, A</np></prov><prov><cpine>1</cpine><np>ARABA/ÁLAVA</np></prov></provinciero>
</consulta_provinciero>"""

MUNI = """<consulta_municipiero><control><cumun>1</cumun></control><municipiero>
<muni><loine><cp>36</cp><cm>60</cm></loine><locat><cd>36</cd><cmc>60</cmc></locat><nm>VILANOVA DE AROUSA</nm></muni>
</municipiero></consulta_municipiero>"""

DNP = """<consulta_dnp><control><cudnp>1</cudnp></control><bico><bi><idbi><cn>RU</cn>
<rc><pc1>36060A0</pc1><pc2>0500123</pc2><car>0000</car><cc1>X</cc1><cc2>Y</cc2></rc></idbi>
<dt><locs><lors><lorus><cpp><cpo>5</cpo><cpa>123</cpa></cpp><npa>PAZOS</npa></lorus></lors></locs></dt>
<debi><luso>Agrario</luso><sfc>2600</sfc></debi></bi>
<lspr><spr><dspr><ccc>C-</ccc><dcc>C- Labor o labradío secano</dcc><ssp>1600</ssp></dspr></spr>
<spr><dspr><ccc>E-</ccc><dcc>E- Pastos</dcc><ssp>1000</ssp></dspr></spr></lspr></bico></consulta_dnp>"""

ERR = """<consulta_dnp><control><cuerr>1</cuerr></control><lerr><err><cod>43</cod>
<des>LA PARCELA NO EXISTE</des></err></lerr></consulta_dnp>"""


def test_provincias():
    assert parsear_provincias(PROV) == [("15", "CORUÑA, A"), ("01", "ARABA/ÁLAVA")]


def test_municipios():
    m = parsear_municipios(MUNI)[0]
    assert m == {"nombre": "VILANOVA DE AROUSA", "cod_ine": "060", "cod_catastro": "60"}


def test_dnppp():
    d = parsear_dnppp(DNP)
    assert d.refcat == "36060A00500123"
    assert d.superficie_m2 == 2600 and "Pastos" in d.uso and d.paraje == "PAZOS"


def test_dnppp_error():
    with pytest.raises(ParcelaNoEncontrada):
        parsear_dnppp(ERR)


def test_xml_roto():
    with pytest.raises(CatastroError):
        parsear_dnppp("<a>")


def test_helpers():
    assert es_pdf(b"%PDF-1.4 ...") and not es_pdf(b"<html>")
    assert hay_captcha("<div class='g-recaptcha'>")


# ---- respuestas REALES (capturadas del Catastro) ----
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def test_real_provincias_municipio():
    p = parsear_provincias((FIX / "provincias_real.xml").read_bytes())
    assert len(p) == 48 and ("15", "A CORUÑA") in p
    m = parsear_municipios((FIX / "municipio_real.xml").read_bytes())[0]
    assert (m["nombre"], m["cod_ine"], m["cod_catastro"]) == ("VILANOVA DE AROUSA", "061", "61")


def test_real_error():
    with pytest.raises(ParcelaNoEncontrada):
        parsear_dnppp((FIX / "dnppp_error_real.xml").read_bytes())


def test_real_lista_solo_rustica_sin_superficie():
    d = parsear_dnppp((FIX / "dnppp_lista_real.xml").read_bytes())
    assert d.refcat == "36061A02800236" and d.paraje == "MONTE DE EIVIÑO"
    assert d.referencias_completas == ["36061A028002360000LY"]   # excluye el 0001 (urbano)
    assert d.superficie_m2 is None and d.uso == ""                # por eso hace falta DNPRC


def test_real_detalle_dnprc():
    d = parsear_dnppp((FIX / "dnprc_real.xml").read_bytes())
    assert d.refcat == "36061A02800236" and d.superficie_m2 == 393
    assert "PINO" in d.uso and "Polígono 28 Parcela 236" in d.localizacion
