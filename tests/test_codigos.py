import pytest

from fincas.codigos import Ambiguo, NoEncontrado, ResolutorCodigos, clave_nombre

PROVS = [("15", "CORUÑA, A"), ("32", "OURENSE"), ("36", "PONTEVEDRA"), ("01", "ARABA/ÁLAVA")]
MUNIS = [
    {"nombre": "VILANOVA DE AROUSA", "cod_ine": "060", "cod_catastro": "060"},
    {"nombre": "VILANOVA DE LOURENZÁ", "cod_ine": "999", "cod_catastro": "999"},
    {"nombre": "RIBADUMIA", "cod_ine": "045", "cod_catastro": "045"},
    {"nombre": "CORUÑA, A", "cod_ine": "030", "cod_catastro": "030"},
]


class FakeCatastro:
    def provincias(self):
        return PROVS

    def municipios(self, prov):
        return MUNIS


def r():
    return ResolutorCodigos(FakeCatastro())


def test_claves():
    assert clave_nombre("CORUÑA, A") == clave_nombre("A Coruña") == "coruna"


@pytest.mark.parametrize("texto", ["Orense", "ourense", "OURENSE"])
def test_provincia_alias(texto):
    assert r().resolver(texto, "Ribadumia").nombre_provincia == "OURENSE"


def test_provincia_coruna():
    assert r().resolver("La Coruña", "A Coruña").cod_provincia == "15"


def test_municipio_con_tildes():
    assert r().resolver("pontevedra", "ribadumía").cod_municipio_ine == "045"


def test_ambiguo():
    with pytest.raises(Ambiguo):
        r().resolver("Pontevedra", "Vilanova")


def test_ambiguo_resuelto_por_pregunta():
    c = r().resolver("Pontevedra", "Vilanova", preguntar=lambda q, ops: ops[0])
    assert c.nombre_municipio == "VILANOVA DE AROUSA"


def test_no_encontrado():
    with pytest.raises(NoEncontrado):
        r().resolver("Pontevedra", "Atlantida")


def test_bilingue():
    assert r().resolver("Álava", "Ribadumia").cod_provincia == "01"
