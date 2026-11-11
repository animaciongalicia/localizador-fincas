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


# ---- entrada por referencia catastral y fincas sin datos
def test_pol_par_desde_refcat():
    from fincas.entrada import pol_par_desde_refcat
    assert pol_par_desde_refcat("99999A001000120000AB") == ("1", "12")
    assert pol_par_desde_refcat("99999A00100012") == ("1", "12")
    assert pol_par_desde_refcat(" 99999a001-00007 0000zz ") == ("1", "7")
    assert pol_par_desde_refcat("9872023VH5797S0001WX") == ("", "")  # urbana
    assert pol_par_desde_refcat(None) == ("", "")


def test_solo_refcat_sin_poligono_parcela():
    from fincas.entrada import filas_a_fincas
    filas = [["Concello", "Provincia", "Ref. Catastral", "Nombre"],
             ["Villaejemplo", "Provincia X", "99999A001000120000AB", "Finca A"],
             ["Villaejemplo", "Provincia X", "", "Finca B"]]
    fincas, problemas = filas_a_fincas(filas)
    assert len(fincas) == 2  # nunca se descarta una finca
    assert (fincas[0].poligono, fincas[0].parcela) == ("1", "12") and not fincas[0].sin_datos
    assert fincas[1].sin_datos and fincas[1].poligono == ""
    assert fincas[1].nombre_base == "villaejemplo_fila3"
    assert any("SIN DATOS" in p for p in problemas)


def test_refcat_discrepante_se_avisa_y_manda_la_fila():
    from fincas.entrada import filas_a_fincas
    filas = [["Concello", "Provincia", "Polígono", "Parcela", "Referencia catastral"],
             ["Villaejemplo", "Provincia X", "1", "99", "99999A001000120000AB"]]
    fincas, problemas = filas_a_fincas(filas)
    assert fincas[0].parcela == "99" and any("indica pol 1/parc 12" in p for p in problemas)
