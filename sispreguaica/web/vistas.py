"""Entrada, inicio, ejercicios, catálogos, usuarios y bitácora."""

import time
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Bitacora,
    Comprobante,
    Cuenta,
    Ejercicio,
    Estructura,
    FuenteFinanciamiento,
    Modificacion,
    PartidaGasto,
    PartidaIngreso,
    Usuario,
)
from ..seguridad import cifrar_clave, clave_valida, verificar_clave
from ..servicios import ajustes, catalogos, reportes
from ..servicios.presupuesto import ErrorPresupuestario, anotar
from .base import (
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

# Intentos fallidos de entrada por login, para frenar la adivinación de claves.
_fallos: dict[str, list[float]] = defaultdict(list)
MAX_FALLOS, VENTANA = 5, 300

# Una partida con menos de esta porción de su crédito libre aparece en Pendientes.
PORCION_ESCASA = Decimal("0.10")


@rutas.get("/entrar")
def entrar_form(request: Request):
    return mostrar(request, "entrar.html")


@rutas.post("/entrar")
def entrar(
    request: Request,
    login: str = Form(...),
    clave: str = Form(...),
    sesion: Session = Depends(obtener_sesion),
):
    login = login.strip().lower()
    ahora = time.monotonic()
    _fallos[login] = [t for t in _fallos[login] if ahora - t < VENTANA]
    if len(_fallos[login]) >= MAX_FALLOS:
        avisar(request, "Demasiados intentos fallidos. Espere unos minutos.", "error")
        return ir("/entrar")
    usuario = sesion.scalar(select(Usuario).where(Usuario.login == login))
    if usuario is None or not usuario.activo or not verificar_clave(clave, usuario.clave_hash):
        _fallos[login].append(ahora)
        avisar(request, "Usuario o clave incorrectos.", "error")
        return ir("/entrar")
    _fallos.pop(login, None)
    request.session.clear()
    request.session["uid"] = usuario.id
    anotar(sesion, usuario, "sesion.entrar", "")
    sesion.commit()
    return ir("/")


@rutas.post("/salir")
def salir(request: Request):
    request.session.clear()
    return ir("/entrar")


@rutas.get("/")
def inicio(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    g = sesion.execute(
        select(
            func.coalesce(func.sum(PartidaGasto.asignado + PartidaGasto.aumento - PartidaGasto.disminucion), 0),
            func.coalesce(func.sum(PartidaGasto.comprometido), 0),
            func.coalesce(func.sum(PartidaGasto.causado), 0),
            func.coalesce(func.sum(PartidaGasto.pagado), 0),
            func.coalesce(func.sum(PartidaGasto.precomprometido), 0),
        ).where(PartidaGasto.ejercicio_id == ejercicio.id)
    ).one()
    i = sesion.execute(
        select(
            func.coalesce(func.sum(PartidaIngreso.previsto + PartidaIngreso.aumento - PartidaIngreso.disminucion), 0),
            func.coalesce(func.sum(PartidaIngreso.devengado), 0),
            func.coalesce(func.sum(PartidaIngreso.cobrado), 0),
        ).where(PartidaIngreso.ejercicio_id == ejercicio.id)
    ).one()
    resumen = {
        "actualizado": g[0],
        "comprometido": g[1],
        "causado": g[2],
        "pagado": g[3],
        "disponible": g[0] - g[1] - g[4],
        "ing_actualizado": i[0],
        "devengado": i[1],
        "cobrado": i[2],
    }
    # Una barra por partida (4.01, 4.02…): pagado, causado sin pagar y comprometido sin causar.
    barras = []
    for f in reportes.ejecucion_gastos(sesion, ejercicio, "partida").filas:
        v = f.valores
        if v["actualizado"] <= 0:
            continue
        base = v["actualizado"]
        barras.append(
            {
                "codigo": f.codigo,
                "nombre": f.denominacion,
                "actualizado": base,
                "disponible": v["disponible"],
                "pag": float(v["pagado"] / base * 100),
                "cau": float((v["causado"] - v["pagado"]) / base * 100),
                "com": float((v["comprometido"] - v["causado"]) / base * 100),
                "pct": float(v["comprometido"] / base * 100),
            }
        )
    borradores = list(
        sesion.scalars(
            select(Modificacion)
            .where(Modificacion.ejercicio_id == ejercicio.id, Modificacion.estado == "borrador")
            .order_by(Modificacion.numero)
        )
    )
    escasas = [
        p
        for p in sesion.scalars(
            select(PartidaGasto).where(PartidaGasto.ejercicio_id == ejercicio.id)
        )
        if p.actualizado > 0 and p.disponible < p.actualizado * PORCION_ESCASA
    ]
    escasas.sort(key=lambda p: p.disponible / p.actualizado)
    ultimos = list(
        sesion.scalars(
            select(Comprobante)
            .where(Comprobante.ejercicio_id == ejercicio.id)
            .order_by(Comprobante.id.desc())
            .limit(6)
        )
    )
    return mostrar(
        request,
        "inicio.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        r=resumen,
        barras=barras,
        borradores=borradores,
        escasas=escasas[:5],
        ultimos=ultimos,
    )


@rutas.post("/ejercicio/elegir")
def elegir_ejercicio(
    request: Request,
    ejercicio_id: int = Form(...),
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    if sesion.get(Ejercicio, ejercicio_id):
        request.session["ejercicio_id"] = ejercicio_id
    # Solo la ruta de la página de origen, para no redirigir a otro sitio.
    return ir(urlparse(request.headers.get("referer", "")).path or "/")


@rutas.get("/ejercicios")
def ejercicios(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return mostrar(request, "ejercicios.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion)


@rutas.post("/ejercicios")
def crear_ejercicio(
    request: Request,
    anio: int = Form(...),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    if not 2000 <= anio <= 2100:
        avisar(request, "Año no válido.", "error")
    elif sesion.scalar(select(Ejercicio).where(Ejercicio.anio == anio)):
        avisar(request, f"El ejercicio {anio} ya existe.", "error")
    else:
        sesion.add(Ejercicio(anio=anio, descripcion=f"Ejercicio fiscal {anio}"))
        anotar(sesion, usuario, "ejercicio.crear", str(anio))
        sesion.commit()
        avisar(request, f"Ejercicio {anio} creado.")
    return ir("/ejercicios")


@rutas.post("/ejercicios/{eid}/estado")
def cambiar_estado_ejercicio(
    request: Request,
    eid: int,
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    e = sesion.get(Ejercicio, eid)
    if e:
        e.abierto = not e.abierto
        anotar(sesion, usuario, "ejercicio.estado", f"{e.anio}: {'abierto' if e.abierto else 'cerrado'}")
        sesion.commit()
        avisar(request, f"Ejercicio {e.anio} {'abierto' if e.abierto else 'cerrado'}.")
    return ir("/ejercicios")


@rutas.get("/estructuras")
def estructuras(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = sesion.scalars(
        select(Estructura).where(Estructura.ejercicio_id == ejercicio.id).order_by(Estructura.codigo)
    )
    return mostrar(
        request, "estructuras.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=list(lista)
    )


@rutas.post("/estructuras")
def crear_estructura(
    request: Request,
    codigo: str = Form(...),
    denominacion: str = Form(...),
    unidad: str = Form(""),
    usuario: Usuario = Depends(puede_registrar),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        catalogos.crear_estructura(sesion, ejercicio, codigo, denominacion, unidad, usuario)
        sesion.commit()
        avisar(request, f"Estructura {codigo.strip()} creada.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir("/estructuras")


@rutas.get("/fuentes")
def fuentes(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = sesion.scalars(select(FuenteFinanciamiento).order_by(FuenteFinanciamiento.codigo))
    return mostrar(
        request, "fuentes.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=list(lista)
    )


@rutas.post("/fuentes")
def crear_fuente(
    request: Request,
    codigo: str = Form(...),
    denominacion: str = Form(...),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    codigo = codigo.strip()
    if not codigo or not denominacion.strip():
        avisar(request, "Indique código y denominación.", "error")
    elif sesion.scalar(select(FuenteFinanciamiento).where(FuenteFinanciamiento.codigo == codigo)):
        avisar(request, f"La fuente {codigo} ya existe.", "error")
    else:
        sesion.add(FuenteFinanciamiento(codigo=codigo, denominacion=denominacion.strip()))
        anotar(sesion, usuario, "fuente.crear", codigo)
        sesion.commit()
        avisar(request, f"Fuente {codigo} creada.")
    return ir("/fuentes")


@rutas.get("/cuentas")
def cuentas(
    request: Request,
    q: str = "",
    tipo: str = "",
    usuario: Usuario = Depends(usuario_actual),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    consulta = select(Cuenta).order_by(Cuenta.codigo)
    if tipo in ("G", "I"):
        consulta = consulta.where(Cuenta.tipo == tipo)
    if q.strip():
        patron = f"%{q.strip()}%"
        consulta = consulta.where(or_(Cuenta.codigo.like(patron), Cuenta.denominacion.ilike(patron)))
    lista = list(sesion.scalars(consulta.limit(500)))
    return mostrar(
        request,
        "cuentas.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        lista=lista,
        q=q,
        tipo=tipo,
    )


@rutas.post("/cuentas/importar")
async def importar_cuentas(
    request: Request,
    archivo: UploadFile = File(...),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        filas = catalogos.leer_csv(await archivo.read(), ["codigo", "denominacion"])
        nuevas, cambiadas = catalogos.importar_cuentas(
            sesion, [(f["codigo"], f["denominacion"]) for f in filas]
        )
        anotar(sesion, usuario, "cuentas.importar", f"{nuevas} nuevas, {cambiadas} actualizadas")
        sesion.commit()
        avisar(request, f"Clasificador actualizado: {nuevas} cuentas nuevas, {cambiadas} actualizadas.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir("/cuentas")


@rutas.get("/usuarios")
def usuarios(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = sesion.scalars(select(Usuario).order_by(Usuario.login))
    return mostrar(
        request, "usuarios.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=list(lista)
    )


@rutas.post("/usuarios")
def guardar_usuario(
    request: Request,
    login: str = Form(...),
    nombre: str = Form(""),
    rol: str = Form("consulta"),
    clave: str = Form(""),
    activo: str = Form(""),
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    login = login.strip().lower()
    if not login.isidentifier() or rol not in ("admin", "analista", "consulta"):
        avisar(request, "Login o rol no válido (use letras, números y _).", "error")
        return ir("/usuarios")
    otro = sesion.scalar(select(Usuario).where(Usuario.login == login))
    if clave and (motivo := clave_valida(clave)):
        avisar(request, motivo, "error")
        return ir("/usuarios")
    if otro is None:
        if not clave:
            avisar(request, "Un usuario nuevo necesita clave.", "error")
            return ir("/usuarios")
        sesion.add(Usuario(login=login, nombre=nombre.strip() or login, rol=rol, clave_hash=cifrar_clave(clave)))
        anotar(sesion, usuario, "usuario.crear", f"{login} ({rol})")
        avisar(request, f"Usuario {login} creado.")
    else:
        if otro.id == usuario.id and (rol != "admin" or not activo):
            avisar(request, "No puede quitarse a sí mismo el rol de administrador ni desactivarse.", "error")
            return ir("/usuarios")
        otro.rol = rol
        otro.activo = bool(activo)
        if nombre.strip():
            otro.nombre = nombre.strip()
        if clave:
            otro.clave_hash = cifrar_clave(clave)
        anotar(sesion, usuario, "usuario.modificar", f"{login} ({rol}, {'activo' if otro.activo else 'inactivo'})")
        avisar(request, f"Usuario {login} actualizado.")
    sesion.commit()
    return ir("/usuarios")


@rutas.get("/bitacora")
def bitacora(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    lista = sesion.scalars(select(Bitacora).order_by(Bitacora.id.desc()).limit(300))
    return mostrar(
        request, "bitacora.html", usuario=usuario, ejercicio=ejercicio, sesion=sesion, lista=list(lista)
    )


@rutas.get("/plantillas/{nombre}.csv")
def plantilla_csv(nombre: str, usuario: Usuario = Depends(usuario_actual)):
    contenido = {
        "apertura_gastos": "estructura;cuenta;fuente;monto\n01.01.00.00.51;4.01.01.01.00;01;1.200.000,00\n",
        "apertura_ingresos": "cuenta;monto\n3.01.00.00.00;3.170.000,00\n",
        "cuentas": "codigo;denominacion\n3.01.00.00.00;Ingresos ordinarios\n",
    }.get(nombre)
    if contenido is None:
        return Response(status_code=404)
    return Response(
        ("﻿" + contenido).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'},
    )


@rutas.get("/configuracion")
def configuracion(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    ejercicio: Ejercicio = Depends(ejercicio_actual),
    sesion: Session = Depends(obtener_sesion),
):
    valores = {clave: ajustes.leer(sesion, clave) for clave in ajustes.CAMPOS}
    valores["ente"] = valores["ente"] or request.app.state.config.nombre_ente
    return mostrar(
        request,
        "configuracion.html",
        usuario=usuario,
        ejercicio=ejercicio,
        sesion=sesion,
        campos=ajustes.CAMPOS,
        valores=valores,
    )


@rutas.post("/configuracion")
async def guardar_configuracion(
    request: Request,
    usuario: Usuario = Depends(solo_admin),
    sesion: Session = Depends(obtener_sesion),
):
    formulario = await request.form()
    try:
        ajustes.guardar(sesion, {c: str(formulario.get(c, "")) for c in ajustes.CAMPOS}, usuario)
        sesion.commit()
        avisar(request, "Configuración guardada.")
    except ErrorPresupuestario as e:
        sesion.rollback()
        avisar(request, str(e), "error")
    return ir("/configuracion")
