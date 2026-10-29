"""Modificaciones presupuestarias: se preparan en borrador y producen movimientos al aprobarse."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CERO, Ejercicio, Modificacion, ModificacionLinea, Usuario
from .presupuesto import (
    ErrorPresupuestario,
    Linea,
    anotar,
    anular_comprobante,
    registrar_comprobante,
)

TIPOS = {
    "TRASPASO": "Traspaso de créditos",
    "CREDITO": "Crédito adicional",
    "RECTIFICA": "Rectificación (desde 4.98)",
    "INSUBSIST": "Insubsistencia",
}
CUENTA_RECTIFICACIONES = "4.98."


def _suma(lineas, accion: str, de_gasto: bool) -> Decimal:
    return sum(
        (
            l.monto
            for l in lineas
            if l.accion == accion and (l.partida_gasto is not None) == de_gasto
        ),
        CERO,
    )


def validar_reglas(tipo: str, lineas: list[ModificacionLinea]) -> None:
    """Reglas de cada tipo de modificación. Lanza ErrorPresupuestario si no se cumplen."""
    if tipo not in TIPOS:
        raise ErrorPresupuestario(f"Tipo de modificación desconocido: {tipo}.")
    if not lineas:
        raise ErrorPresupuestario("La modificación no tiene líneas.")

    gasto = [l for l in lineas if l.partida_gasto is not None]
    ingreso = [l for l in lineas if l.partida_ingreso is not None]
    au_gasto, di_gasto = _suma(lineas, "AU", True), _suma(lineas, "DI", True)
    au_ingreso, di_ingreso = _suma(lineas, "AU", False), _suma(lineas, "DI", False)

    vistas = {}
    for l in gasto:
        if vistas.setdefault(l.partida_gasto.id, l.accion) != l.accion:
            raise ErrorPresupuestario(
                f"La partida {l.partida_gasto.clave} no puede aumentar y disminuir a la vez."
            )

    if tipo == "TRASPASO":
        if ingreso:
            raise ErrorPresupuestario("Un traspaso solo mueve créditos entre partidas de gasto.")
        if not au_gasto or not di_gasto:
            raise ErrorPresupuestario("Un traspaso necesita partidas cedentes y receptoras.")
        if au_gasto != di_gasto:
            raise ErrorPresupuestario(
                f"El traspaso no cuadra: aumentos {au_gasto} y disminuciones {di_gasto}."
            )
        if any(
            l.partida_gasto.cuenta.codigo.startswith(CUENTA_RECTIFICACIONES) for l in gasto
        ):
            raise ErrorPresupuestario(
                "Los créditos de la 4.98 se mueven con una rectificación, no con un traspaso."
            )
    elif tipo == "CREDITO":
        if di_gasto or di_ingreso:
            raise ErrorPresupuestario("Un crédito adicional solo aumenta partidas.")
        if not au_gasto:
            raise ErrorPresupuestario("El crédito adicional necesita al menos una partida de gasto.")
        if ingreso and au_ingreso != au_gasto:
            raise ErrorPresupuestario(
                f"El crédito no cuadra con su fuente: gasto {au_gasto}, ingreso {au_ingreso}."
            )
    elif tipo == "RECTIFICA":
        if ingreso:
            raise ErrorPresupuestario("Una rectificación no afecta el presupuesto de ingresos.")
        for l in gasto:
            es_498 = l.partida_gasto.cuenta.codigo.startswith(CUENTA_RECTIFICACIONES)
            if l.accion == "DI" and not es_498:
                raise ErrorPresupuestario(
                    f"En una rectificación solo se disminuye la 4.98; "
                    f"{l.partida_gasto.clave} no lo es."
                )
            if l.accion == "AU" and es_498:
                raise ErrorPresupuestario("Una rectificación no puede aumentar la 4.98.")
        if not au_gasto or au_gasto != di_gasto:
            raise ErrorPresupuestario(
                f"La rectificación no cuadra: aumentos {au_gasto}, disminución de 4.98 {di_gasto}."
            )
    elif tipo == "INSUBSIST":
        if au_gasto or au_ingreso:
            raise ErrorPresupuestario("Una insubsistencia solo disminuye partidas.")
        if not di_gasto:
            raise ErrorPresupuestario("La insubsistencia necesita al menos una partida de gasto.")


def crear_modificacion(
    sesion: Session,
    *,
    ejercicio: Ejercicio,
    tipo: str,
    fecha: date,
    descripcion: str,
    documento_soporte: str,
    lineas: list[ModificacionLinea],
    usuario: Usuario,
) -> Modificacion:
    if not ejercicio.abierto:
        raise ErrorPresupuestario(f"El ejercicio {ejercicio.anio} está cerrado.")
    if fecha.year != ejercicio.anio:
        raise ErrorPresupuestario(f"La fecha debe estar dentro del ejercicio {ejercicio.anio}.")
    if not descripcion.strip():
        raise ErrorPresupuestario("La modificación necesita una descripción.")
    validar_reglas(tipo, lineas)
    numero = (
        sesion.scalar(
            select(func.max(Modificacion.numero)).where(Modificacion.ejercicio_id == ejercicio.id)
        )
        or 0
    ) + 1
    m = Modificacion(
        ejercicio_id=ejercicio.id,
        numero=numero,
        tipo=tipo,
        fecha=fecha,
        descripcion=descripcion.strip(),
        documento_soporte=documento_soporte.strip(),
        usuario_id=usuario.id,
        lineas=lineas,
    )
    sesion.add(m)
    sesion.flush()
    anotar(sesion, usuario, "modificacion.crear", f"N° {numero} {TIPOS[tipo]}")
    return m


def aprobar(sesion: Session, m: Modificacion, usuario: Usuario) -> None:
    if usuario.rol != "admin":
        raise ErrorPresupuestario("Solo un administrador puede aprobar modificaciones.")
    if m.estado != "borrador":
        raise ErrorPresupuestario(f"La modificación está {m.estado}.")
    validar_reglas(m.tipo, m.lineas)
    ejercicio = sesion.get(Ejercicio, m.ejercicio_id)
    descripcion = f"{TIPOS[m.tipo]} N° {m.numero}: {m.descripcion}"
    gasto = [Linea(l.partida_gasto, l.accion, l.monto) for l in m.lineas if l.partida_gasto]
    ingreso = [Linea(l.partida_ingreso, l.accion, l.monto) for l in m.lineas if l.partida_ingreso]
    comunes = dict(
        ejercicio=ejercicio,
        procedencia="MOD",
        fecha=m.fecha,
        descripcion=descripcion,
        usuario=usuario,
        documento=m.documento_soporte[:60],
    )
    if gasto:
        m.comprobante_gasto = registrar_comprobante(sesion, tipo="G", lineas=gasto, **comunes)
    if ingreso:
        m.comprobante_ingreso = registrar_comprobante(sesion, tipo="I", lineas=ingreso, **comunes)
    m.estado = "aprobada"
    m.aprobado_por_id = usuario.id
    anotar(sesion, usuario, "modificacion.aprobar", f"N° {m.numero}")


def anular(sesion: Session, m: Modificacion, usuario: Usuario, motivo: str) -> None:
    if usuario.rol != "admin":
        raise ErrorPresupuestario("Solo un administrador puede anular modificaciones.")
    if m.estado == "anulada":
        raise ErrorPresupuestario("La modificación ya está anulada.")
    if not motivo.strip():
        raise ErrorPresupuestario("Indique el motivo de la anulación.")
    for c in (m.comprobante_gasto, m.comprobante_ingreso):
        if c is not None and c.estado == "activo":
            anular_comprobante(sesion, c, usuario, f"Anulación de la modificación N° {m.numero}: {motivo}")
    m.estado = "anulada"
    anotar(sesion, usuario, "modificacion.anular", f"N° {m.numero}: {motivo.strip()}")
