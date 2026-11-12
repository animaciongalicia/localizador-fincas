import math

from fincas import plano
from fincas.plano import (Vista, anillos, barra_escala, elegir_zoom, lonlat_a_merc, resolucion,
                          ruta_svg, teselas_vector)


def test_lonlat_merc_conocido():
    x, y = lonlat_a_merc(-3.70, 40.41)
    assert abs(x - (-411_882)) < 5 and abs(y - 4_925_702) < 5  # Madrid aprox.


def test_teselas_vector_cubre_la_parcela():
    # bbox de ~200 m que cruza una frontera de tesela z15 (1223 m): hacen falta las dos
    x, y = lonlat_a_merc(-3.70, 40.41)
    lado = 2 * plano.ORIGEN / (2 ** plano.Z_VECTOR)
    borde = (int((x + plano.ORIGEN) // lado) + 1) * lado - plano.ORIGEN
    t = teselas_vector(borde - 100, y, borde + 100, y + 50)
    assert len({tx for tx, _ in t}) == 2


def test_teselas_vector_sin_duplicados():
    t = teselas_vector(-946_300, 5_311_000, -945_900, 5_311_100)
    assert len(t) == len(set(t)) and len(t) >= 1


def test_zoom_pequena_y_grande():
    assert elegir_zoom(30, 30, 1100, 760) == 19
    z = elegir_zoom(250, 600, 1100, 760)
    assert z < 19 and 600 * 1.35 / resolucion(z) <= 760
    assert elegir_zoom(50_000, 50_000, 1100, 760) == 12  # nunca por debajo del mínimo


def test_vista_centro_y_teselas():
    cx, cy = lonlat_a_merc(-3.70, 40.41)
    v = Vista(cx, cy, 17, 1100, 760)
    px, py = v.a_px(cx, cy)
    assert abs(px - 550) < 1e-6 and abs(py - 380) < 1e-6
    ts = v.teselas_ortofoto()
    assert ts and all(-256 <= l <= 1100 and -256 <= t <= 760 for _, _, l, t in ts)
    assert min(l for _, _, l, t in ts) <= 0 and max(l for _, _, l, t in ts) + 256 >= 1100


def test_anillos_polygon_y_multipolygon():
    p = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]], [[.2, .2], [.3, .2], [.3, .3]]]}
    assert len(anillos(p)) == 1 and len(anillos(p)[0]) == 4
    m = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1]]], [[[5, 5], [6, 5], [6, 6]]]]}
    assert len(anillos(m)) == 2
    assert anillos({}) == []


def test_barra_escala_redonda_y_cabe():
    largo, metros = barra_escala(resolucion(17), 43.0, 300)
    assert metros in (1000, 500, 200, 100, 50, 20, 10, 5) and largo <= 300


def test_ruta_svg():
    v = Vista(0, 0, 10, 100, 100)
    d = ruta_svg([(0, 0), (v.res * 10, 0), (v.res * 10, v.res * 10)], v)
    assert d.startswith("M50.0,50.0") and d.endswith("Z")


def test_bbox_merc_sin_geometria_usa_lonlat():
    d = plano.DatosParcela((-8.49, 42.99, -8.48, 43.0), [], [])
    x1, y1, x2, y2 = d.bbox_merc
    assert x2 > x1 and y2 > y1 and math.isfinite(x1)


def test_tesela_vector_usa_el_zoom_no_la_provincia():
    # regresión: con la provincia en la URL solo funcionaba A Coruña (15 = zoom) y el resto caía a la captura azul
    from fincas.plano import Z_VECTOR, es_la_parcela, url_tesela_vector
    assert url_tesela_vector("https://x", 15625, 20616).endswith(f"/parcela@3857/{Z_VECTOR}.15625.20616.geojson")
    props = {"provincia": 36, "municipio": 1, "poligono": 42, "parcela": 362}
    assert es_la_parcela(props, "36", "1", "42", "362")
    assert es_la_parcela({k: str(v) for k, v in props.items()}, "36", "001", "042", "00362")
    assert not es_la_parcela(props, "15", "1", "42", "362")        # misma numeración en otra provincia
    assert not es_la_parcela(props, "36", "1", "42", "363")
    assert not es_la_parcela({"poligono": None}, "36", "1", "42", "362")
