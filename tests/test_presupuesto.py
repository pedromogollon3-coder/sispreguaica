from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from sispreguaica.models import Cuenta, ModificacionLinea
from sispreguaica.servicios import catalogos, modificaciones, reportes
from sispreguaica.servicios.inicio import cargar_demo
from sispreguaica.servicios.montos import formatear, leer_monto
from sispreguaica.servicios.presupuesto import (
    ErrorPresupuestario,
    Linea,
    anular_comprobante,
    registrar_comprobante,
    verificar_saldos,
)

from .conftest import ANIO

E = "01.02.00.00.51"


@pytest.fixture
def partidas(sesion, ejercicio, admin):
    catalogos.crear_estructura(sesion, ejercicio, E, "Administración", "", admin)
    sesion.flush()
    catalogos.cargar_apertura_gastos(
        sesion,
        ejercicio,
        [
            dict(estructura=E, cuenta="4.02.05.03.00", fuente="01", monto="1.000,00"),
            dict(estructura=E, cuenta="4.03.10.10.00", fuente="01", monto="500"),
            dict(estructura=E, cuenta="4.98.01.01.00", fuente="01", monto="300"),
        ],
        date(ANIO, 1, 2),
        admin,
    )
    sesion.commit()
    return {
        "papel": catalogos.partida_gasto(sesion, ejercicio, E, "4.02.05.03.00", "01"),
        "vigilancia": catalogos.partida_gasto(sesion, ejercicio, E, "4.03.10.10.00", "01"),
        "rectif": catalogos.partida_gasto(sesion, ejercicio, E, "4.98.01.01.00", "01"),
    }


def gasto(sesion, ejercicio, admin, *lineas, fecha=None):
    return registrar_comprobante(
        sesion,
        ejercicio=ejercicio,
        tipo="G",
        procedencia="MAN",
        fecha=fecha or date(ANIO, 3, 1),
        descripcion="prueba",
        lineas=[Linea(p, op, leer_monto(m)) for p, op, m in lineas],
        usuario=admin,
    )


def test_montos():
    assert leer_monto("1.234.567,89") == Decimal("1234567.89")
    assert leer_monto("1234567.89") == Decimal("1234567.89")
    assert leer_monto("1.000.000") == Decimal("1000000.00")
    assert formatear(Decimal("1234567.8")) == "1.234.567,80"
    with pytest.raises(ValueError):
        leer_monto("abc")


def test_clasificador_cargado(sesion):
    total = len(list(sesion.scalars(select(Cuenta))))
    assert total == 1332
    generica = sesion.scalar(select(Cuenta).where(Cuenta.codigo == "4.02.05.00.00"))
    especifica = sesion.scalar(select(Cuenta).where(Cuenta.codigo == "4.02.05.03.00"))
    assert not generica.imputable and especifica.imputable


def test_cuenta_no_imputable_rechazada(sesion, ejercicio, admin, partidas):
    with pytest.raises(ErrorPresupuestario, match="subcuentas"):
        catalogos.partida_gasto(sesion, ejercicio, E, "4.02.05.00.00", "01", crear=True)


def test_ciclo_del_gasto(sesion, ejercicio, admin, partidas):
    papel = partidas["papel"]
    gasto(sesion, ejercicio, admin, (papel, "CS", "600"))
    gasto(sesion, ejercicio, admin, (papel, "GC", "400"))
    gasto(sesion, ejercicio, admin, (papel, "PG", "250"))
    sesion.commit()
    assert (papel.comprometido, papel.causado, papel.pagado) == (600, 400, 250)
    assert papel.disponible == Decimal("400.00")
    assert verificar_saldos(sesion, ejercicio) == []


def test_sin_disponibilidad(sesion, ejercicio, admin, partidas):
    with pytest.raises(ErrorPresupuestario, match="sin disponibilidad"):
        gasto(sesion, ejercicio, admin, (partidas["papel"], "CS", "1000,01"))
    sesion.rollback()
    assert partidas["papel"].comprometido == 0


def test_precompromiso_ocupa_disponibilidad(sesion, ejercicio, admin, partidas):
    papel = partidas["papel"]
    gasto(sesion, ejercicio, admin, (papel, "PC", "700"))
    sesion.commit()
    with pytest.raises(ErrorPresupuestario, match="sin disponibilidad"):
        gasto(sesion, ejercicio, admin, (papel, "CS", "400"))
    sesion.rollback()
    gasto(sesion, ejercicio, admin, (papel, "LP", "700"), (papel, "CS", "700"))
    assert papel.precomprometido == 0 and papel.comprometido == 700


def test_orden_del_gasto(sesion, ejercicio, admin, partidas):
    papel = partidas["papel"]
    with pytest.raises(ErrorPresupuestario, match="causado"):
        gasto(sesion, ejercicio, admin, (papel, "GC", "10"))
    sesion.rollback()
    gasto(sesion, ejercicio, admin, (papel, "CG", "100"))
    with pytest.raises(ErrorPresupuestario, match="pagado"):
        gasto(sesion, ejercicio, admin, (papel, "PG", "100,01"))


def test_fecha_fuera_del_ejercicio(sesion, ejercicio, admin, partidas):
    with pytest.raises(ErrorPresupuestario, match="ejercicio"):
        gasto(sesion, ejercicio, admin, (partidas["papel"], "CS", "1"), fecha=date(ANIO + 1, 1, 1))


def test_ejercicio_cerrado(sesion, ejercicio, admin, partidas):
    ejercicio.abierto = False
    with pytest.raises(ErrorPresupuestario, match="cerrado"):
        gasto(sesion, ejercicio, admin, (partidas["papel"], "CS", "1"))


def test_anulacion(sesion, ejercicio, admin, partidas):
    papel = partidas["papel"]
    compromiso = gasto(sesion, ejercicio, admin, (papel, "CS", "300"))
    gasto(sesion, ejercicio, admin, (papel, "GC", "200"))
    sesion.commit()
    with pytest.raises(ErrorPresupuestario, match="dependen"):
        anular_comprobante(sesion, compromiso, admin, "error")
    sesion.rollback()
    assert papel.comprometido == 300
    causado = sesion.scalar(
        select(type(compromiso)).where(type(compromiso).numero == compromiso.numero + 1)
    )
    anular_comprobante(sesion, causado, admin, "factura devuelta")
    anular_comprobante(sesion, compromiso, admin, "contrato rescindido")
    sesion.commit()
    assert papel.comprometido == 0 and papel.causado == 0
    assert verificar_saldos(sesion, ejercicio) == []


def _linea(p, accion, monto):
    return ModificacionLinea(partida_gasto=p, accion=accion, monto=leer_monto(monto))


def test_traspaso(sesion, ejercicio, admin, partidas):
    m = modificaciones.crear_modificacion(
        sesion,
        ejercicio=ejercicio,
        tipo="TRASPASO",
        fecha=date(ANIO, 5, 1),
        descripcion="refuerzo",
        documento_soporte="Res. 1",
        lineas=[_linea(partidas["papel"], "DI", "200"), _linea(partidas["vigilancia"], "AU", "200")],
        usuario=admin,
    )
    assert partidas["papel"].disminucion == 0  # el borrador no mueve saldos
    modificaciones.aprobar(sesion, m, admin)
    sesion.commit()
    assert partidas["papel"].actualizado == 800
    assert partidas["vigilancia"].actualizado == 700
    modificaciones.anular(sesion, m, admin, "revocada")
    assert partidas["papel"].actualizado == 1000


def test_traspaso_descuadrado(sesion, ejercicio, admin, partidas):
    with pytest.raises(ErrorPresupuestario, match="no cuadra"):
        modificaciones.validar_reglas(
            "TRASPASO",
            [_linea(partidas["papel"], "DI", "200"), _linea(partidas["vigilancia"], "AU", "150")],
        )


def test_traspaso_sin_disponibilidad(sesion, ejercicio, admin, partidas):
    gasto(sesion, ejercicio, admin, (partidas["papel"], "CS", "900"))
    m = modificaciones.crear_modificacion(
        sesion,
        ejercicio=ejercicio,
        tipo="TRASPASO",
        fecha=date(ANIO, 5, 1),
        descripcion="refuerzo",
        documento_soporte="",
        lineas=[_linea(partidas["papel"], "DI", "200"), _linea(partidas["vigilancia"], "AU", "200")],
        usuario=admin,
    )
    with pytest.raises(ErrorPresupuestario, match="disponibilidad"):
        modificaciones.aprobar(sesion, m, admin)


def test_rectificacion_solo_desde_498(sesion, ejercicio, admin, partidas):
    with pytest.raises(ErrorPresupuestario, match="4.98"):
        modificaciones.validar_reglas(
            "RECTIFICA",
            [_linea(partidas["papel"], "DI", "100"), _linea(partidas["vigilancia"], "AU", "100")],
        )
    modificaciones.validar_reglas(
        "RECTIFICA",
        [_linea(partidas["rectif"], "DI", "100"), _linea(partidas["vigilancia"], "AU", "100")],
    )
    with pytest.raises(ErrorPresupuestario, match="rectificación"):
        modificaciones.validar_reglas(
            "TRASPASO",
            [_linea(partidas["rectif"], "DI", "100"), _linea(partidas["vigilancia"], "AU", "100")],
        )


def test_solo_admin_aprueba(sesion, ejercicio, admin, partidas):
    m = modificaciones.crear_modificacion(
        sesion,
        ejercicio=ejercicio,
        tipo="INSUBSIST",
        fecha=date(ANIO, 5, 1),
        descripcion="recorte",
        documento_soporte="",
        lineas=[_linea(partidas["papel"], "DI", "100")],
        usuario=admin,
    )
    admin.rol = "analista"
    with pytest.raises(ErrorPresupuestario, match="administrador"):
        modificaciones.aprobar(sesion, m, admin)


def test_demo_y_reportes(sesion, ejercicio):
    cargar_demo(sesion, ANIO)
    sesion.commit()
    assert verificar_saldos(sesion, ejercicio) == []

    detalle = reportes.ejecucion_gastos(sesion, ejercicio)
    t = detalle.totales
    assert t["asignado"] == Decimal("3170000.00")
    assert t["actualizado"] == t["asignado"]  # el traspaso no cambia el total
    assert t["comprometido"] == Decimal("255200.00")
    assert t["pagado"] == Decimal("23200.00")

    por_partida = reportes.ejecucion_gastos(sesion, ejercicio, nivel="partida")
    codigos = [f.codigo for f in por_partida.filas]
    assert "4.01.00.00.00" in codigos and "4.98.00.00.00" in codigos
    assert por_partida.totales == t

    # Corte de fecha: antes del traspaso y de los compromisos solo hay apertura.
    al_corte = reportes.ejecucion_gastos(sesion, ejercicio, hasta=date(ANIO, 1, 15))
    assert al_corte.totales["comprometido"] == 0

    ingresos = reportes.ejecucion_ingresos(sesion, ejercicio)
    assert ingresos.totales["cobrado"] == Decimal("410000.00")
    assert ingresos.totales["por_devengar"] == Decimal("2760000.00")

    assert reportes.a_csv(detalle).startswith("﻿".encode())
    assert reportes.a_excel(detalle, "Ente")[:2] == b"PK"
