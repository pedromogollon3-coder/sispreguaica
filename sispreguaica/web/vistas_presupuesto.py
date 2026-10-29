"""Partidas, apertura, comprobantes, modificaciones y reportes."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import operaciones
from ..models import (
    Comprobante,
    Ejercicio,
    Estructura,
    FuenteFinanciamiento,
    Modificacion,
    ModificacionLinea,
    Movimiento,
    PartidaGasto,
    PartidaIngreso,
    Usuario,
)
from ..servicios import ajustes, catalogos, modificaciones, reportes
from ..servicios.montos import leer_monto
from ..servicios.presupuesto import (
    ErrorPresupuestario,
    Linea,
    anular_comprobante,
    registrar_comprobante,
)
from .base import (
    nombre_ente,
    avisar,
    ejercicio_actual,
    ir,
    mostrar,
    obtener_sesion,
    puede_registrar,
    solo_admin,
    usuario_actual,
    verificar_ficha,
)

rutas = APIRouter(dependencies=[Depends(verificar_ficha)])


def _fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ErrorPresupuestario(f"Fecha no válida: {texto!r}.") from None


def _partidas_gasto(sesion: Session, ejercicio: Ejercicio) -> list[PartidaGasto]:
    return list(
        sesion.scalars(
            select(PartidaGasto)
            .join(Estructura)
            .join(PartidaGasto.cuenta)
            .where(PartidaGasto.ejercicio_id == ejercicio.id)
            .order_by(Estructura.codigo, PartidaGasto.cuenta_id)
        )
    )


def _encabezado_ente(request: Request, sesion: Session) -> str:
    rif = ajustes.leer(sesion, "rif")
    ente = nombre_ente(request, sesion)
    return f"{ente} · RIF {rif}" if rif else ente


def _datos_partidas(partidas, tipo: str) -> dict[str, dict]:
    """Saldos que la pantalla usa para avisar, antes de guardar, si alcanza la disponibilidad."""
    if tipo == "G":
        return {
            p.clave: {
                "nombre": p.cuenta.denominacion,
                "estructura": p.estructura.denominacion,
                "disponible": str(p.disponible),
                "precomprometido": str(p.precomprometido),
                "por_causar": str(p.comprometido - p.causado),
                "por_pagar": str(p.causado - p.pagado),
            }
            for p in partidas
        }
    return {
        p.clave: {
            "nombre": p.cuenta.denominacion,
            "por_devengar": str(p.por_devengar),
            "por_cobrar": str(p.devengado - p.cobrado),
        }
        for p in partidas
    }


def _estructuras(sesion: Session, ejercicio: Ejercicio) -> list[Estructura]:
    return list(
        sesion.scalars(select(Estructura).where(Estructura.ejercicio_id == ejercicio.id).order_by(Estructura.codigo))
    )


def _partidas_ingreso(sesion: Session, ejercicio: Ejercicio) -> list[PartidaIngreso]:
    return list(
        sesion.scalars(
            select(PartidaIngreso)
            .join(PartidaIngreso.cuenta)
            .where(PartidaIngreso.ejercicio_id == ejercicio.id)
        )
    )


# --- Partidas ---------------------------------------------------------------


@rutas.get("/gastos")
def partidas_gasto(
    request: Request,
    estructura: str = "",
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = [p for p in _partidas_gasto(sesion, ejercicio) if p.estructura.codigo.startswith(estructura)]
    estructuras = list(
        sesion.scalars(select(Estructura).where(Estructura.ejercicio_id == ejercicio.id).order_by(Estructura.codigo))
    )
    fuentes = list(sesion.scalars(select(FuenteFinanciamiento).order_by(FuenteFinanciamiento.codigo)))
    return mostrar(
        request,
        "partidas_gasto.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        lista=lista,
        estructuras=estructuras,
        fuentes=fuentes,
        filtro=estructura,
    )


@rutas.post("/gastos")
def crear_partida_gasto(
    request: Request,
    estructura: str = Form(...),
    cuenta: str = Form(...),
    fuente: str = Form(...),
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        p = catalogos.partida_gasto(sesion, ejercicio, estructura, cuenta, fuente, crear=True)
        sesion.commit()
        avisar(request, f"Partida {p.clave} lista (sin asignación; se le dan recursos con una modificación).")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir("/gastos")


@rutas.get("/ingresos")
def partidas_ingreso(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = sorted(_partidas_ingreso(sesion, ejercicio), key=lambda p: p.cuenta.codigo)
    return mostrar(
        request, "partidas_ingreso.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=lista
    )


@rutas.post("/ingresos")
def crear_partida_ingreso(
    request: Request,
    cuenta: str = Form(...),
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        p = catalogos.partida_ingreso(sesion, ejercicio, cuenta, crear=True)
        sesion.commit()
        avisar(request, f"Partida de ingreso {p.clave} lista.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir("/ingresos")


@rutas.get("/mayor/{tipo}/{pid}")
def mayor(
    request: Request,
    tipo: str,
    pid: int,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    modelo, columna = (
        (PartidaGasto, Movimiento.partida_gasto_id) if tipo == "G" else (PartidaIngreso, Movimiento.partida_ingreso_id)
    )
    partida = sesion.get(modelo, pid)
    if partida is None or partida.ejercicio_id != ejercicio.id:
        return ir("/gastos" if tipo == "G" else "/ingresos")
    movimientos = list(
        sesion.scalars(
            select(Movimiento)
            .join(Comprobante)
            .where(columna == pid)
            .order_by(Comprobante.fecha, Comprobante.numero)
        )
    )
    return mostrar(
        request,
        "mayor.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        partida=partida,
        tipo=tipo,
        movimientos=movimientos,
    )


# --- Apertura ---------------------------------------------------------------


@rutas.get("/apertura")
def apertura_form(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    hechas = list(
        sesion.scalars(
            select(Comprobante).where(
                Comprobante.ejercicio_id == ejercicio.id,
                Comprobante.procedencia == "APE",
            )
        )
    )
    return mostrar(
        request,
        "apertura.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        hechas=hechas,
        hoy=date(ejercicio.anio, 1, 1).isoformat(),
    )


@rutas.post("/apertura")
async def apertura(
    request: Request,
    tipo: str = Form(...),
    fecha: str = Form(...),
    archivo: UploadFile = File(...),
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        contenido = await archivo.read()
        if tipo == "G":
            filas = catalogos.leer_csv(contenido, ["estructura", "cuenta", "fuente", "monto"])
            c = catalogos.cargar_apertura_gastos(sesion, ejercicio, filas, _fecha(fecha), usuario)
        else:
            filas = catalogos.leer_csv(contenido, ["cuenta", "monto"])
            c = catalogos.cargar_apertura_ingresos(sesion, ejercicio, filas, _fecha(fecha), usuario)
        sesion.commit()
        avisar(request, f"Apertura registrada en el comprobante N° {c.numero} ({len(filas)} partidas).")
        return ir(f"/comprobantes/{c.id}")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
        return ir("/apertura")


# --- Comprobantes -----------------------------------------------------------


@rutas.get("/comprobantes")
def comprobantes(
    request: Request,
    tipo: str = "",
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    consulta = select(Comprobante).where(Comprobante.ejercicio_id == ejercicio.id)
    if tipo in ("G", "I"):
        consulta = consulta.where(Comprobante.tipo == tipo)
    lista = list(sesion.scalars(consulta.order_by(Comprobante.numero.desc()).limit(500)))
    return mostrar(
        request, "comprobantes.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=lista, tipo=tipo
    )


@rutas.get("/comprobantes/nuevo")
def comprobante_form(
    request: Request,
    tipo: str = "G",
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    tipo = "I" if tipo == "I" else "G"
    partidas = _partidas_gasto(sesion, ejercicio) if tipo == "G" else _partidas_ingreso(sesion, ejercicio)
    manuales = operaciones.MANUALES_GASTO if tipo == "G" else operaciones.MANUALES_INGRESO
    return mostrar(
        request,
        "comprobante_nuevo.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        tipo=tipo,
        partidas=partidas,
        manuales=[operaciones.catalogo(tipo)[c] for c in manuales],
        datos=_datos_partidas(partidas, tipo),
        menu=f"registrar-{tipo}",
        hoy=min(date.today(), date(ejercicio.anio, 12, 31)).isoformat(),
    )


@rutas.post("/comprobantes")
async def registrar(
    request: Request,
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    formulario = await request.form()
    tipo = "I" if formulario.get("tipo") == "I" else "G"
    manuales = operaciones.MANUALES_GASTO if tipo == "G" else operaciones.MANUALES_INGRESO
    try:
        lineas = []
        claves = formulario.getlist("partida")
        # La pantalla elige una operación para todo el comprobante; también se acepta una por línea.
        comun = str(formulario.get("operacion_comun", ""))
        ops = [comun] * len(claves) if comun else formulario.getlist("operacion")
        for clave, op, monto in zip(claves, ops, formulario.getlist("monto")):
            if not clave.strip() and not monto.strip():
                continue
            if op not in manuales:
                raise ErrorPresupuestario(f"La operación {op} no se registra a mano.")
            if tipo == "G":
                partida = catalogos.partida_gasto_por_clave(sesion, ejercicio, clave)
            else:
                partida = catalogos.partida_ingreso(sesion, ejercicio, clave)
            try:
                importe = leer_monto(monto)
            except ValueError as e:
                raise ErrorPresupuestario(str(e)) from None
            lineas.append(Linea(partida, op, importe))
        c = registrar_comprobante(
            sesion,
            ejercicio=ejercicio,
            tipo=tipo,
            procedencia="MAN",
            fecha=_fecha(str(formulario.get("fecha", ""))),
            descripcion=str(formulario.get("descripcion", "")),
            beneficiario=str(formulario.get("beneficiario", "")),
            documento=str(formulario.get("documento", "")),
            lineas=lineas,
            usuario=usuario,
        )
        sesion.commit()
        avisar(request, f"Comprobante N° {c.numero} registrado.")
        return ir(f"/comprobantes/{c.id}")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
        return ir(f"/comprobantes/nuevo?tipo={tipo}")


@rutas.get("/comprobantes/{cid}")
def comprobante(
    request: Request,
    cid: int,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    c = sesion.get(Comprobante, cid)
    if c is None or c.ejercicio_id != ejercicio.id:
        return ir("/comprobantes")
    origen = sesion.scalar(
        select(Modificacion).where(
            (Modificacion.comprobante_gasto_id == c.id) | (Modificacion.comprobante_ingreso_id == c.id)
        )
    )
    return mostrar(
        request, "comprobante.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, c=c, origen=origen
    )


@rutas.post("/comprobantes/{cid}/anular")
def anular(
    request: Request,
    cid: int,
    motivo: str = Form(""),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    c = sesion.get(Comprobante, cid)
    try:
        if c is None:
            raise ErrorPresupuestario("Comprobante no encontrado.")
        if c.procedencia == "MOD":
            raise ErrorPresupuestario("Este comprobante sale de una modificación; anule la modificación.")
        anular_comprobante(sesion, c, usuario, motivo)
        sesion.commit()
        avisar(request, f"Comprobante N° {c.numero} anulado.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir(f"/comprobantes/{cid}")


# --- Modificaciones ---------------------------------------------------------


@rutas.get("/modificaciones")
def lista_modificaciones(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = list(
        sesion.scalars(
            select(Modificacion).where(Modificacion.ejercicio_id == ejercicio.id).order_by(Modificacion.numero.desc())
        )
    )
    return mostrar(
        request,
        "modificaciones.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        lista=lista,
        tipos=modificaciones.TIPOS,
    )


@rutas.get("/modificaciones/nueva")
def modificacion_form(
    request: Request,
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    partidas_g = _partidas_gasto(sesion, ejercicio)
    partidas_i = _partidas_ingreso(sesion, ejercicio)
    datos = _datos_partidas(partidas_g, "G")
    datos.update({"I:" + k: v for k, v in _datos_partidas(partidas_i, "I").items()})
    return mostrar(
        request,
        "modificacion_nueva.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        tipos=modificaciones.TIPOS,
        datos=datos,
        partidas_g=partidas_g,
        partidas_i=partidas_i,
        hoy=min(date.today(), date(ejercicio.anio, 12, 31)).isoformat(),
    )


@rutas.post("/modificaciones")
async def crear_modificacion(
    request: Request,
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    formulario = await request.form()
    try:
        lineas = []
        for clave, accion, monto in zip(
            formulario.getlist("partida"), formulario.getlist("accion"), formulario.getlist("monto")
        ):
            if not clave.strip() and not monto.strip():
                continue
            if accion not in ("AU", "DI"):
                raise ErrorPresupuestario("Acción no válida.")
            try:
                importe = leer_monto(monto)
            except ValueError as e:
                raise ErrorPresupuestario(str(e)) from None
            if clave.startswith("I:"):
                p = catalogos.partida_ingreso(sesion, ejercicio, clave[2:])
                lineas.append(ModificacionLinea(partida_ingreso=p, accion=accion, monto=importe))
            else:
                p = catalogos.partida_gasto_por_clave(sesion, ejercicio, clave)
                lineas.append(ModificacionLinea(partida_gasto=p, accion=accion, monto=importe))
        m = modificaciones.crear_modificacion(
            sesion,
            ejercicio=ejercicio,
            tipo=str(formulario.get("tipo", "")),
            fecha=_fecha(str(formulario.get("fecha", ""))),
            descripcion=str(formulario.get("descripcion", "")),
            documento_soporte=str(formulario.get("documento_soporte", "")),
            lineas=lineas,
            usuario=usuario,
        )
        sesion.commit()
        avisar(request, f"Modificación N° {m.numero} guardada en borrador. Falta la aprobación.")
        return ir(f"/modificaciones/{m.id}")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
        return ir("/modificaciones/nueva")


@rutas.get("/modificaciones/{mid}")
def ver_modificacion(
    request: Request,
    mid: int,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    m = sesion.get(Modificacion, mid)
    if m is None or m.ejercicio_id != ejercicio.id:
        return ir("/modificaciones")
    return mostrar(
        request,
        "modificacion.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        m=m,
        tipos=modificaciones.TIPOS,
    )


@rutas.post("/modificaciones/{mid}/aprobar")
def aprobar_modificacion(
    request: Request,
    mid: int,
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    m = sesion.get(Modificacion, mid)
    try:
        if m is None:
            raise ErrorPresupuestario("Modificación no encontrada.")
        modificaciones.aprobar(sesion, m, usuario)
        sesion.commit()
        avisar(request, f"Modificación N° {m.numero} aprobada; los saldos ya están actualizados.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir(f"/modificaciones/{mid}")


@rutas.post("/modificaciones/{mid}/anular")
def anular_modificacion(
    request: Request,
    mid: int,
    motivo: str = Form(""),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    m = sesion.get(Modificacion, mid)
    try:
        if m is None:
            raise ErrorPresupuestario("Modificación no encontrada.")
        modificaciones.anular(sesion, m, usuario, motivo)
        sesion.commit()
        avisar(request, f"Modificación N° {m.numero} anulada.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir(f"/modificaciones/{mid}")


# --- Reportes ---------------------------------------------------------------


@rutas.get("/reportes/{tipo}")
def reporte(
    request: Request,
    tipo: str,
    nivel: str = "detalle",
    hasta: str = "",
    estructura: str = "",
    formato: str = "html",
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    corte = None
    if hasta:
        try:
            corte = date.fromisoformat(hasta)
        except ValueError:
            avisar(request, "Fecha de corte no válida.", "error")
    nivel = nivel if nivel in reportes.NIVELES else "detalle"
    if tipo == "ingresos":
        r = reportes.ejecucion_ingresos(sesion, ejercicio, nivel, corte)
    else:
        tipo = "gastos"
        r = reportes.ejecucion_gastos(sesion, ejercicio, nivel, corte, estructura.strip())
    nombre = f"ejecucion_{tipo}_{ejercicio.anio}"
    if formato == "csv":
        return Response(
            reportes.a_csv(r),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'},
        )
    if formato == "xlsx":
        return Response(
            reportes.a_excel(r, _encabezado_ente(request, sesion)),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nombre}.xlsx"'},
        )
    return mostrar(
        request,
        "reporte.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        r=r,
        tipo=tipo,
        nivel=nivel,
        estructuras=_estructuras(sesion, ejercicio),
        hasta=hasta,
        estructura=estructura,
    )
