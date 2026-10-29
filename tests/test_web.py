import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sispreguaica.config import Config
from sispreguaica.db import crear_motor, crear_tablas, fabrica_sesiones
from sispreguaica.models import Comprobante, Modificacion, Usuario
from sispreguaica.seguridad import cifrar_clave
from sispreguaica.servicios.inicio import cargar_demo, inicializar
from sispreguaica.web import vistas
from sispreguaica.web.app import crear_app

from .conftest import ANIO, CLAVE


@pytest.fixture
def entorno():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    s = fabrica_sesiones(motor)()
    inicializar(s, CLAVE, ANIO)
    cargar_demo(s, ANIO)
    s.add(Usuario(login="lector", nombre="Lector", rol="consulta", clave_hash=cifrar_clave(CLAVE)))
    s.commit()
    s.close()
    vistas._fallos.clear()
    app = crear_app(Config("sqlite://", "secreto-de-prueba", False, "Alcaldía de prueba"), motor)
    yield app, fabrica_sesiones(motor)
    motor.dispose()


def ficha(cliente, url="/"):
    html = cliente.get(url).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def entrar(app, login="admin"):
    c = TestClient(app)
    r = c.post("/entrar", data={"login": login, "clave": CLAVE, "csrf": ficha(c, "/entrar")})
    assert r.status_code == 200 and "Ejercicio" in r.text
    return c


def test_sin_sesion_redirige(entorno):
    app, _ = entorno
    r = TestClient(app).get("/gastos", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


def test_clave_errada_y_bloqueo(entorno):
    app, _ = entorno
    c = TestClient(app)
    for _ in range(5):
        r = c.post("/entrar", data={"login": "admin", "clave": "mala", "csrf": ficha(c, "/entrar")})
        assert "incorrectos" in r.text
    r = c.post("/entrar", data={"login": "admin", "clave": CLAVE, "csrf": ficha(c, "/entrar")})
    assert "Demasiados intentos" in r.text


def test_formulario_sin_ficha_rechazado(entorno):
    app, _ = entorno
    c = TestClient(app)
    r = c.post("/entrar", data={"login": "admin", "clave": CLAVE}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/gastos", follow_redirects=False).status_code == 303  # no entró


def test_todas_las_paginas(entorno):
    app, sesiones = entorno
    c = entrar(app)
    with sesiones() as s:
        comp = s.scalar(select(Comprobante).limit(1))
        mod = s.scalar(select(Modificacion).limit(1))
    for url in [
        "/", "/gastos", "/ingresos", "/comprobantes", "/comprobantes?tipo=G",
        "/comprobantes/nuevo?tipo=G", "/comprobantes/nuevo?tipo=I", f"/comprobantes/{comp.id}",
        "/modificaciones", "/modificaciones/nueva", f"/modificaciones/{mod.id}",
        "/reportes/gastos", "/reportes/gastos?nivel=partida&hasta=2026-03-31",
        "/reportes/ingresos", "/estructuras", "/cuentas?q=vigilancia", "/fuentes",
        "/apertura", "/ejercicios", "/usuarios", "/bitacora", "/mayor/G/1", "/mayor/I/1",
        "/plantillas/apertura_gastos.csv", "/configuracion",
    ]:
        r = c.get(url)
        assert r.status_code == 200, url
    assert "Servicios de vigilancia" in c.get("/cuentas?q=vigilancia").text
    assert c.get("/").headers["X-Frame-Options"] == "DENY"


def test_registrar_comprobante_por_formulario(entorno):
    app, _ = entorno
    c = entrar(app)
    datos = {
        "csrf": ficha(c),
        "tipo": "G",
        "fecha": f"{ANIO}-05-02",
        "descripcion": "Compra de papel",
        "beneficiario": "Papelería X",
        "partida": ["01.01.00.00.51|4.02.05.03.00|01", ""],
        "operacion": ["CCP", "CS"],
        "monto": ["1.500,50", ""],
    }
    r = c.post("/comprobantes", data=datos)
    assert "registrado" in r.text and "1.500,50" in r.text

    datos["monto"] = ["999.999,00", ""]
    r = c.post("/comprobantes", data=datos)
    assert "sin disponibilidad" in r.text


def test_operacion_no_manual_rechazada(entorno):
    app, _ = entorno
    c = entrar(app)
    r = c.post(
        "/comprobantes",
        data={
            "csrf": ficha(c),
            "tipo": "G",
            "fecha": f"{ANIO}-05-02",
            "descripcion": "Intento de aumento directo",
            "partida": ["01.01.00.00.51|4.02.05.03.00|01"],
            "operacion": ["AU"],
            "monto": ["10"],
        },
    )
    assert "no se registra a mano" in r.text


def test_modificacion_flujo_web(entorno):
    app, sesiones = entorno
    c = entrar(app)
    r = c.post(
        "/modificaciones",
        data={
            "csrf": ficha(c),
            "tipo": "RECTIFICA",
            "fecha": f"{ANIO}-06-01",
            "descripcion": "Refuerzo de combustible",
            "documento_soporte": "Acuerdo 45",
            "partida": ["01.03.00.00.51|4.98.01.01.00|01", "01.02.00.00.51|4.02.06.06.00|01"],
            "accion": ["DI", "AU"],
            "monto": ["20.000,00", "20.000,00"],
        },
    )
    assert "borrador" in r.text
    with sesiones() as s:
        m = s.scalar(select(Modificacion).order_by(Modificacion.id.desc()))
    r = c.post(f"/modificaciones/{m.id}/aprobar", data={"csrf": ficha(c)})
    assert "aprobada" in r.text
    r = c.get("/reportes/gastos?nivel=detalle")
    assert "80.000,00" in r.text  # 60.000 de combustible + 20.000


def test_consulta_no_registra(entorno):
    app, _ = entorno
    c = entrar(app, "lector")
    assert c.get("/comprobantes/nuevo").status_code == 403
    assert c.get("/usuarios").status_code == 403
    assert c.get("/reportes/gastos").status_code == 200


def test_exportar(entorno):
    app, _ = entorno
    c = entrar(app)
    r = c.get("/reportes/gastos?nivel=partida&formato=xlsx")
    assert r.content[:2] == b"PK"
    r = c.get("/reportes/ingresos?formato=csv")
    assert "Cobrado" in r.content.decode("utf-8-sig")


def test_apertura_por_archivo(entorno):
    app, _ = entorno
    c = entrar(app)
    c.post("/ejercicios", data={"csrf": ficha(c), "anio": str(ANIO + 1)})
    eid = re.search(r'<option value="(\d+)"[^>]*>' + str(ANIO + 1), c.get("/").text).group(1)
    c.post("/ejercicio/elegir", data={"csrf": ficha(c), "ejercicio_id": eid})
    c.post("/estructuras", data={"csrf": ficha(c), "codigo": "01.01.00.00.51", "denominacion": "Despacho"})
    csv = "estructura;cuenta;fuente;monto\n01.01.00.00.51;4.01.01.01.00;01;2.000.000,00\n"
    r = c.post(
        "/apertura",
        data={"csrf": ficha(c), "tipo": "G", "fecha": f"{ANIO + 1}-01-02"},
        files={"archivo": ("apertura.csv", csv.encode(), "text/csv")},
    )
    assert "Apertura registrada" in r.text
    assert "2.000.000,00" in c.get("/gastos").text


def test_configuracion_del_ente(entorno):
    app, _ = entorno
    c = entrar(app)
    assert "Alcaldía de prueba" in c.get("/").text  # sin configurar: el de la variable de entorno
    r = c.post(
        "/configuracion",
        data={"csrf": ficha(c), "ente": "Concejo Municipal de Guaicaipuro", "rif": "G-20000000-1"},
    )
    assert "Configuración guardada" in r.text
    assert "Concejo Municipal de Guaicaipuro" in c.get("/").text
    assert "Concejo Municipal de Guaicaipuro" in c.get("/entrar").text
    r = c.post("/configuracion", data={"csrf": ficha(c), "ente": "  ", "rif": ""})
    assert "no puede quedar vacío" in r.text
    assert entrar(app, "lector").get("/configuracion").status_code == 403


def test_registro_guiado_una_operacion_para_todas_las_lineas(entorno):
    app, sesiones = entorno
    c = entrar(app)
    html = c.get("/comprobantes/nuevo?tipo=G").text
    assert 'name="operacion_comun"' in html and "datos-partidas" in html and "Todo de una vez" in html
    r = c.post(
        "/comprobantes",
        data={
            "csrf": ficha(c),
            "tipo": "G",
            "operacion_comun": "CS",
            "fecha": f"{ANIO}-03-10",
            "descripcion": "Resmas de papel",
            "partida": ["01.01.00.00.51|4.02.05.03.00|01", "01.02.00.00.51|4.03.18.01.00|01"],
            "monto": ["1.000,00", "160,00"],
        },
    )
    assert "registrado" in r.text
    with sesiones() as s:
        c_ = s.scalars(select(Comprobante).order_by(Comprobante.id.desc())).first()
        assert [m.operacion for m in c_.movimientos] == ["CS", "CS"]
