from fincas.sigpac import coincide_refcat, extraer_refcat


def test_extraer():
    assert extraer_refcat("Parcela 5/123\nReferencia catastral: 36060A00500123 0000 XY") == "36060A00500123"
    assert extraer_refcat("sin datos") == ""


def test_coincide():
    assert coincide_refcat("36060A00500123", "36060a00500123") == "SI"
    assert coincide_refcat("36060A00500123", "36060A00500999") == "NO"
    assert coincide_refcat("36060A00500123", "") == "NO ENCONTRADA"


def test_etiqueta_y_refc():
    from fincas.sigpac import etiqueta_coincide, puntos_busqueda, refcat_desde_url
    assert etiqueta_coincide("x 36:61:0:0:28:236 (856.315 m²) y", "28", "236") == 856.315
    assert etiqueta_coincide("36:61:0:0:28:237 (10 m²)", "28", "236") is None
    assert abs(etiqueta_coincide("15:62:0:0:34:802 (2.0601 ha)", "34", "802") - 20601) < 1e-6
    assert refcat_desde_url("https://www1.sedecatastro.gob.es/x?RefC=36061A028002360000LY&del=36") == "36061A02800236"
    assert puntos_busqueda(700, 477)[0] == (700, 477) and len(puntos_busqueda(700, 477)) > 20
