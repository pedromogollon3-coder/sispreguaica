"""Preparación de una base nueva y datos de demostración."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ejercicio, FuenteFinanciamiento, ModificacionLinea, Usuario
from ..seguridad import cifrar_clave, clave_valida
from . import catalogos, modificaciones
from .montos import leer_monto
from .presupuesto import ErrorPresupuestario, Linea, registrar_comprobante

FUENTES = [("01", "Ingresos ordinarios"), ("02", "Ingresos extraordinarios")]


def inicializar(sesion: Session, admin_clave: str, anio: int) -> list[str]:
    """Deja la base lista para usar. Se puede repetir sin duplicar nada."""
    hecho = []
    nuevas, _ = catalogos.importar_cuentas(sesion, catalogos.clasificador_egresos_incluido())
    if nuevas:
        hecho.append(f"Clasificador de egresos ONAPRE 2025: {nuevas} cuentas cargadas.")
    for codigo, nombre in FUENTES:
        if not sesion.scalar(
            select(FuenteFinanciamiento).where(FuenteFinanciamiento.codigo == codigo)
        ):
            sesion.add(FuenteFinanciamiento(codigo=codigo, denominacion=nombre))
            hecho.append(f"Fuente de financiamiento {codigo} creada.")
    if not sesion.scalar(select(Ejercicio).where(Ejercicio.anio == anio)):
        sesion.add(Ejercicio(anio=anio, descripcion=f"Ejercicio fiscal {anio}"))
        hecho.append(f"Ejercicio {anio} abierto.")
    if not sesion.scalar(select(Usuario).where(Usuario.login == "admin")):
        motivo = clave_valida(admin_clave)
        if motivo:
            raise ErrorPresupuestario(f"Clave del administrador: {motivo}")
        sesion.add(
            Usuario(
                login="admin",
                nombre="Administrador",
                clave_hash=cifrar_clave(admin_clave),
                rol="admin",
            )
        )
        hecho.append("Usuario admin creado.")
    sesion.flush()
    return hecho


# Cuentas de ingreso solo para la demostración: el clasificador de ingresos
# (3.xx) oficial se carga con `sispreguaica importar-cuentas`.
CUENTAS_INGRESO_DEMO = [
    ("3.01.00.00.00", "Ingresos ordinarios"),
    ("3.02.00.00.00", "Ingresos extraordinarios"),
]


def cargar_demo(sesion: Session, anio: int) -> None:
    ejercicio = sesion.scalar(select(Ejercicio).where(Ejercicio.anio == anio))
    admin = sesion.scalar(select(Usuario).where(Usuario.login == "admin"))
    if ejercicio is None or admin is None:
        raise ErrorPresupuestario("Ejecute primero `sispreguaica iniciar`.")
    if catalogos.tiene_partidas(sesion, ejercicio):
        raise ErrorPresupuestario(f"El ejercicio {anio} ya tiene partidas; la demo no se carga.")

    catalogos.importar_cuentas(sesion, CUENTAS_INGRESO_DEMO)
    for codigo, nombre, unidad in [
        ("01.01.00.00.51", "Dirección superior", "Despacho"),
        ("01.02.00.00.51", "Administración y finanzas", "Dirección de Administración"),
        ("01.03.00.00.51", "Servicios públicos municipales", "Dirección de Servicios"),
    ]:
        catalogos.crear_estructura(sesion, ejercicio, codigo, nombre, unidad, admin)
    sesion.flush()

    inicio = date(anio, 1, 2)
    catalogos.cargar_apertura_gastos(
        sesion,
        ejercicio,
        [
            dict(estructura=e, cuenta=c, fuente="01", monto=m)
            for e, c, m in [
                ("01.01.00.00.51", "4.01.01.01.00", "1200000"),
                ("01.01.00.00.51", "4.02.05.03.00", "80000"),
                ("01.02.00.00.51", "4.01.01.01.00", "950000"),
                ("01.02.00.00.51", "4.02.06.06.00", "60000"),
                ("01.02.00.00.51", "4.03.10.10.00", "240000"),
                ("01.02.00.00.51", "4.03.18.01.00", "90000"),
                ("01.03.00.00.51", "4.03.11.02.00", "150000"),
                ("01.03.00.00.51", "4.04.09.02.00", "300000"),
                ("01.03.00.00.51", "4.98.01.01.00", "100000"),
            ]
        ],
        inicio,
        admin,
    )
    catalogos.cargar_apertura_ingresos(
        sesion,
        ejercicio,
        [dict(cuenta="3.01.00.00.00", monto="3170000")],
        inicio,
        admin,
    )

    def pg(e, c):
        return catalogos.partida_gasto(sesion, ejercicio, e, c, "01")

    seguridad = pg("01.02.00.00.51", "4.03.10.10.00")
    iva = pg("01.02.00.00.51", "4.03.18.01.00")
    for fecha, op, desc in [
        (date(anio, 2, 1), "CS", "Contrato de vigilancia, febrero a diciembre"),
        (date(anio, 3, 5), "GC", "Factura de vigilancia, febrero"),
        (date(anio, 3, 20), "PG", "Pago de vigilancia, febrero"),
    ]:
        monto = {"CS": "220000", "GC": "20000", "PG": "20000"}[op]
        iva_monto = {"CS": "35200", "GC": "3200", "PG": "3200"}[op]
        registrar_comprobante(
            sesion,
            ejercicio=ejercicio,
            tipo="G",
            procedencia="MAN",
            fecha=fecha,
            descripcion=desc,
            beneficiario="Seguridad Integral, C.A.",
            lineas=[
                Linea(seguridad, op, leer_monto(monto)),
                Linea(iva, op, leer_monto(iva_monto)),
            ],
            usuario=admin,
        )
    registrar_comprobante(
        sesion,
        ejercicio=ejercicio,
        tipo="I",
        procedencia="MAN",
        fecha=date(anio, 1, 31),
        descripcion="Recaudación de enero",
        lineas=[
            Linea(
                catalogos.partida_ingreso(sesion, ejercicio, "3.01.00.00.00"),
                "DC",
                leer_monto("410000"),
            )
        ],
        usuario=admin,
    )

    receptora = catalogos.partida_gasto(
        sesion, ejercicio, "01.03.00.00.51", "4.02.08.09.00", "01", crear=True
    )
    m = modificaciones.crear_modificacion(
        sesion,
        ejercicio=ejercicio,
        tipo="TRASPASO",
        fecha=date(anio, 4, 10),
        descripcion="Repuestos para la flota de aseo urbano",
        documento_soporte="Resolución N° 012 (ejemplo)",
        lineas=[
            ModificacionLinea(
                partida_gasto=pg("01.03.00.00.51", "4.04.09.02.00"),
                accion="DI",
                monto=leer_monto("45000"),
            ),
            ModificacionLinea(
                partida_gasto=receptora, accion="AU", monto=leer_monto("45000")
            ),
        ],
        usuario=admin,
    )
    modificaciones.aprobar(sesion, m, admin)
