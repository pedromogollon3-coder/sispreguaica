"""Registro de comprobantes y control de la disponibilidad presupuestaria."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import operaciones
from ..models import (
    CERO,
    Bitacora,
    Comprobante,
    Ejercicio,
    Movimiento,
    PartidaGasto,
    PartidaIngreso,
    Usuario,
)
from .montos import formatear

SALDOS_GASTO = (
    "asignado",
    "aumento",
    "disminucion",
    "precomprometido",
    "comprometido",
    "causado",
    "pagado",
)
SALDOS_INGRESO = ("previsto", "aumento", "disminucion", "devengado", "cobrado")


class ErrorPresupuestario(Exception):
    """Operación que el presupuesto no permite; el mensaje es para el usuario."""


@dataclass
class Linea:
    partida: PartidaGasto | PartidaIngreso
    operacion: str
    monto: Decimal


def anotar(sesion: Session, usuario: Usuario | None, accion: str, detalle: str) -> None:
    sesion.add(Bitacora(usuario_id=usuario.id if usuario else None, accion=accion, detalle=detalle))


def _bs(valor: Decimal) -> str:
    return f"Bs. {formatear(valor)}"


def validar_partida_gasto(p: PartidaGasto) -> None:
    """Reglas que toda partida de gasto debe cumplir después de cada registro."""
    clave = p.clave
    for saldo in SALDOS_GASTO:
        if getattr(p, saldo) < 0:
            raise ErrorPresupuestario(f"{clave}: el saldo {saldo} quedaría negativo.")
    if p.actualizado < 0:
        raise ErrorPresupuestario(
            f"{clave}: la disminución supera la asignación ({_bs(p.actualizado)})."
        )
    if p.disponible < 0:
        raise ErrorPresupuestario(
            f"{clave}: sin disponibilidad presupuestaria. "
            f"Faltan {_bs(-p.disponible)}."
        )
    if p.causado > p.comprometido:
        raise ErrorPresupuestario(
            f"{clave}: el causado ({_bs(p.causado)}) superaría el comprometido "
            f"({_bs(p.comprometido)})."
        )
    if p.pagado > p.causado:
        raise ErrorPresupuestario(
            f"{clave}: el pagado ({_bs(p.pagado)}) superaría el causado ({_bs(p.causado)})."
        )


def validar_partida_ingreso(p: PartidaIngreso) -> None:
    clave = p.clave
    for saldo in SALDOS_INGRESO:
        if getattr(p, saldo) < 0:
            raise ErrorPresupuestario(f"{clave}: el saldo {saldo} quedaría negativo.")
    if p.actualizado < 0:
        raise ErrorPresupuestario(f"{clave}: la disminución supera lo previsto.")
    if p.cobrado > p.devengado:
        raise ErrorPresupuestario(
            f"{clave}: lo cobrado ({_bs(p.cobrado)}) superaría lo devengado "
            f"({_bs(p.devengado)})."
        )


def _aplicar(partida, operacion: operaciones.Operacion, monto: Decimal, signo: int) -> None:
    for saldo, efecto in operacion.efectos:
        setattr(partida, saldo, getattr(partida, saldo) + efecto * signo * monto)


def _bloquear(sesion: Session, partidas) -> None:
    """Bloquea las filas de las partidas en PostgreSQL mientras dura la transacción."""
    if sesion.get_bind().dialect.name != "postgresql":
        return
    por_modelo = defaultdict(list)
    for p in partidas:
        por_modelo[type(p)].append(p.id)
    for modelo, ids in por_modelo.items():
        sesion.execute(select(modelo).where(modelo.id.in_(ids)).with_for_update())
        for p in partidas:
            if isinstance(p, modelo):
                sesion.refresh(p)


def registrar_comprobante(
    sesion: Session,
    *,
    ejercicio: Ejercicio,
    tipo: str,
    procedencia: str,
    fecha: date,
    descripcion: str,
    lineas: list[Linea],
    usuario: Usuario,
    beneficiario: str = "",
    documento: str = "",
) -> Comprobante:
    """Registra un comprobante y actualiza los saldos.

    Si alguna partida queda fuera de las reglas, lanza ErrorPresupuestario y
    el llamador debe deshacer la transacción (rollback).
    """
    if not ejercicio.abierto:
        raise ErrorPresupuestario(f"El ejercicio {ejercicio.anio} está cerrado.")
    if fecha.year != ejercicio.anio:
        raise ErrorPresupuestario(f"La fecha debe estar dentro del ejercicio {ejercicio.anio}.")
    if not descripcion.strip():
        raise ErrorPresupuestario("El comprobante necesita una descripción.")
    if not lineas:
        raise ErrorPresupuestario("El comprobante no tiene líneas.")

    catalogo = operaciones.catalogo(tipo)
    modelo = PartidaGasto if tipo == "G" else PartidaIngreso
    for linea in lineas:
        if linea.operacion not in catalogo:
            raise ErrorPresupuestario(f"Operación desconocida: {linea.operacion}.")
        if not isinstance(linea.partida, modelo):
            raise ErrorPresupuestario("Hay líneas de gasto e ingreso mezcladas.")
        if linea.partida.ejercicio_id != ejercicio.id:
            raise ErrorPresupuestario("Hay partidas de otro ejercicio.")
        if linea.monto <= 0:
            raise ErrorPresupuestario("Los montos deben ser mayores que cero.")

    partidas = {id(l.partida): l.partida for l in lineas}.values()
    _bloquear(sesion, list(partidas))

    numero = (
        sesion.scalar(
            select(func.max(Comprobante.numero)).where(Comprobante.ejercicio_id == ejercicio.id)
        )
        or 0
    ) + 1
    comprobante = Comprobante(
        ejercicio_id=ejercicio.id,
        numero=numero,
        tipo=tipo,
        procedencia=procedencia,
        fecha=fecha,
        descripcion=descripcion.strip(),
        beneficiario=beneficiario.strip(),
        documento=documento.strip(),
        usuario_id=usuario.id,
    )
    for linea in lineas:
        _aplicar(linea.partida, catalogo[linea.operacion], linea.monto, +1)
        comprobante.movimientos.append(
            Movimiento(
                partida_gasto_id=linea.partida.id if tipo == "G" else None,
                partida_ingreso_id=linea.partida.id if tipo == "I" else None,
                operacion=linea.operacion,
                monto=linea.monto,
            )
        )
    validar = validar_partida_gasto if tipo == "G" else validar_partida_ingreso
    for p in partidas:
        validar(p)

    sesion.add(comprobante)
    sesion.flush()
    anotar(
        sesion,
        usuario,
        "comprobante.registrar",
        f"N° {numero} ({tipo}/{procedencia}) por {_bs(sum(l.monto for l in lineas))}",
    )
    return comprobante


def anular_comprobante(
    sesion: Session, comprobante: Comprobante, usuario: Usuario, motivo: str
) -> None:
    """Revierte los efectos del comprobante. Falla si otro registro depende de él."""
    if comprobante.estado == "anulado":
        raise ErrorPresupuestario("El comprobante ya está anulado.")
    ejercicio = sesion.get(Ejercicio, comprobante.ejercicio_id)
    if not ejercicio.abierto:
        raise ErrorPresupuestario(f"El ejercicio {ejercicio.anio} está cerrado.")
    if not motivo.strip():
        raise ErrorPresupuestario("Indique el motivo de la anulación.")

    catalogo = operaciones.catalogo(comprobante.tipo)
    partidas = {}
    for m in comprobante.movimientos:
        p = m.partida_gasto or m.partida_ingreso
        partidas[(type(p), p.id)] = p
    _bloquear(sesion, list(partidas.values()))
    for m in comprobante.movimientos:
        _aplicar(m.partida_gasto or m.partida_ingreso, catalogo[m.operacion], m.monto, -1)
    validar = validar_partida_gasto if comprobante.tipo == "G" else validar_partida_ingreso
    for p in partidas.values():
        try:
            validar(p)
        except ErrorPresupuestario as e:
            raise ErrorPresupuestario(
                f"No se puede anular: hay registros posteriores que dependen de este. {e}"
            ) from None
    comprobante.estado = "anulado"
    comprobante.motivo_anulacion = motivo.strip()
    anotar(sesion, usuario, "comprobante.anular", f"N° {comprobante.numero}: {motivo.strip()}")


def recalcular(sesion: Session, ejercicio: Ejercicio, hasta: date | None = None):
    """Saldos de cada partida calculados desde los movimientos activos.

    Devuelve ({partida_gasto_id: {saldo: monto}}, {partida_ingreso_id: {...}}).
    """
    gastos: dict[int, dict[str, Decimal]] = defaultdict(lambda: dict.fromkeys(SALDOS_GASTO, CERO))
    ingresos: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: dict.fromkeys(SALDOS_INGRESO, CERO)
    )
    consulta = (
        select(
            Comprobante.tipo,
            Movimiento.partida_gasto_id,
            Movimiento.partida_ingreso_id,
            Movimiento.operacion,
            func.sum(Movimiento.monto),
        )
        .join(Comprobante)
        .where(Comprobante.ejercicio_id == ejercicio.id, Comprobante.estado == "activo")
        .group_by(
            Comprobante.tipo,
            Movimiento.partida_gasto_id,
            Movimiento.partida_ingreso_id,
            Movimiento.operacion,
        )
    )
    if hasta:
        consulta = consulta.where(Comprobante.fecha <= hasta)
    for tipo, pg_id, pi_id, op, total in sesion.execute(consulta):
        destino = gastos[pg_id] if tipo == "G" else ingresos[pi_id]
        for saldo, signo in operaciones.catalogo(tipo)[op].efectos:
            destino[saldo] += signo * Decimal(total)
    return gastos, ingresos


def verificar_saldos(sesion: Session, ejercicio: Ejercicio) -> list[str]:
    """Compara los saldos guardados con los recalculados. Lista vacía = todo cuadra."""
    gastos, ingresos = recalcular(sesion, ejercicio)
    diferencias = []
    for p in sesion.scalars(select(PartidaGasto).where(PartidaGasto.ejercicio_id == ejercicio.id)):
        for saldo in SALDOS_GASTO:
            esperado = gastos.get(p.id, {}).get(saldo, CERO)
            if getattr(p, saldo) != esperado:
                diferencias.append(f"{p.clave} {saldo}: {getattr(p, saldo)} ≠ {esperado}")
    for p in sesion.scalars(
        select(PartidaIngreso).where(PartidaIngreso.ejercicio_id == ejercicio.id)
    ):
        for saldo in SALDOS_INGRESO:
            esperado = ingresos.get(p.id, {}).get(saldo, CERO)
            if getattr(p, saldo) != esperado:
                diferencias.append(f"{p.clave} {saldo}: {getattr(p, saldo)} ≠ {esperado}")
    return diferencias
