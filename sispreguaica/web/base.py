"""Piezas comunes de la interfaz web: sesión de base de datos, usuario, permisos y plantillas."""

import hmac
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ejercicio, Usuario
from ..operaciones import GASTO, INGRESO
from ..seguridad import nueva_ficha
from ..servicios import ajustes
from ..servicios.montos import formatear

plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "plantillas"))
plantillas.env.filters["bs"] = formatear
plantillas.env.globals["OPERACIONES"] = {"G": GASTO, "I": INGRESO}


class NoAutenticado(Exception):
    pass


class Prohibido(Exception):
    pass


class FichaInvalida(Exception):
    pass


def obtener_sesion(request: Request) -> Iterator[Session]:
    sesion = request.app.state.sesiones()
    try:
        yield sesion
    finally:
        sesion.close()


def usuario_actual(request: Request, sesion: Session = Depends(obtener_sesion)) -> Usuario:
    uid = request.session.get("uid")
    usuario = sesion.get(Usuario, uid) if uid else None
    if usuario is None or not usuario.activo:
        request.session.pop("uid", None)
        raise NoAutenticado()
    return usuario


def requiere(*roles: str):
    def dependencia(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
        if usuario.rol not in roles:
            raise Prohibido()
        return usuario

    return dependencia


puede_registrar = requiere("admin", "analista")
solo_admin = requiere("admin")


def ejercicio_actual(request: Request, sesion: Session = Depends(obtener_sesion)) -> Ejercicio:
    eid = request.session.get("ejercicio_id")
    ejercicio = sesion.get(Ejercicio, eid) if eid else None
    if ejercicio is None:
        ejercicio = sesion.scalar(select(Ejercicio).order_by(Ejercicio.anio.desc()))
        if ejercicio is None:
            raise RuntimeError("No hay ejercicios; ejecute `sispreguaica iniciar`.")
        request.session["ejercicio_id"] = ejercicio.id
    return ejercicio


def ficha(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = nueva_ficha()
    return request.session["csrf"]


async def verificar_ficha(request: Request) -> None:
    """Todo formulario enviado por POST debe traer la ficha de la sesión."""
    if request.method != "POST":
        return
    formulario = await request.form()
    enviada = str(formulario.get("csrf", ""))
    esperada = request.session.get("csrf", "")
    if not esperada or not hmac.compare_digest(enviada, esperada):
        raise FichaInvalida()


def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
    request.session.setdefault("avisos", []).append([tipo, texto])


def ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def nombre_ente(request: Request, sesion: Session) -> str:
    """El nombre guardado en Configuración; si no hay, el de la variable SISPREGUAICA_ENTE."""
    return ajustes.leer(sesion, "ente", request.app.state.config.nombre_ente)


def mostrar(request: Request, plantilla: str, status_code: int = 200, **contexto):
    sesion = contexto.pop("sesion", None)
    if sesion is not None:
        contexto.setdefault(
            "ejercicios", list(sesion.scalars(select(Ejercicio).order_by(Ejercicio.anio.desc())))
        )
        contexto["ente"] = nombre_ente(request, sesion)
    else:
        with request.app.state.sesiones() as propia:
            contexto["ente"] = nombre_ente(request, propia)
    contexto.setdefault("usuario", None)
    contexto["csrf"] = ficha(request)
    contexto["avisos"] = request.session.pop("avisos", [])
    contexto["ruta"] = request.url.path
    return plantillas.TemplateResponse(request, plantilla, contexto, status_code=status_code)
