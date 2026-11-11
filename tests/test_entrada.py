from pathlib import Path

from fincas.entrada import leer_fincas, parsear_cuota, filas_a_fincas

EJEMPLO = Path(__file__).parent / "ejemplo.csv"


def test_cuotas():
    assert parsear_cuota("").completa
    assert parsear_cuota("completa").completa
    assert parsear_cuota("1/1").completa
    c = parsear_cuota("1/5")
    assert (c.numerador, c.denominador, c.completa) == (1, 5, False)
    c = parsear_cuota("20%")
    assert (c.numerador, c.denominador) == (1, 5)
    assert not parsear_cuota("rara").valida


def test_csv_ejemplo():
    fincas, problemas = leer_fincas(str(EJEMPLO))
    assert len(fincas) == 5 and problemas == []
    assert fincas[1].municipio == "Vilanova de Arousa"      # relleno hacia abajo
    assert fincas[1].superficie_m2 == 500.1
    assert fincas[0].superficie_m2 == 2500
    assert fincas[3].cuota.completa                           # cuota vacía = completa
    assert fincas[4].provincia == "Orense"


def test_defaults_y_cabeceras_flexibles():
    filas = [["Polígono", "PARCELA", "Superficie"], ["3", "7.0", "100"]]
    fincas, _ = filas_a_fincas(filas, municipio="Ribadumia", provincia="Pontevedra")
    assert fincas[0].municipio == "Ribadumia" and fincas[0].parcela == "7"


def test_nombre_pdf():
    filas = [["municipio", "provincia", "poligono", "parcela"], ["Vilanova de Arousa", "Pontevedra", "5", "12"]]
    f = filas_a_fincas(filas)[0][0]
    assert f.nombre_base == "vilanova-de-arousa_poligono05_parcela012"
