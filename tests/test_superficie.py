from fincas.entrada import parsear_cuota
from fincas.superficie import control_superficie


def test_ok_directo():
    assert control_superficie(1000, 1050, parsear_cuota("completa")) == "OK"


def test_ok_por_cuota():
    assert control_superficie(5000, 1000, parsear_cuota("1/5")) == "OK"


def test_revisar():
    assert control_superficie(5000, 1000, parsear_cuota("completa")) == "REVISAR"
    assert control_superficie(None, 1000, parsear_cuota("completa")) == "REVISAR"


def test_sin_dato_cliente():
    assert control_superficie(1000, None, parsear_cuota("1/2")) == "SIN DATO CLIENTE"
