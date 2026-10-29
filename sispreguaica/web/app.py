"""Aplicación web."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from starlette.middleware.sessions import SessionMiddleware

from .. import config as configuracion
from ..db import crear_motor, fabrica_sesiones
from . import vistas, vistas_presupuesto
from .base import FichaInvalida, NoAutenticado, Prohibido, avisar, ir, mostrar


def crear_app(config: configuracion.Config | None = None, motor: Engine | None = None) -> FastAPI:
    config = config or configuracion.cargar()
    motor = motor or crear_motor(config.database_url)

    app = FastAPI(title="Sispreguaica", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.sesiones = fabrica_sesiones(motor)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secreto,
        session_cookie="sispreguaica",
        max_age=8 * 3600,
        same_site="lax",
        https_only=config.cookie_segura,
    )
    app.mount(
        "/estaticos", StaticFiles(directory=str(Path(__file__).parent / "estaticos")), name="estaticos"
    )
    app.include_router(vistas.rutas)
    app.include_router(vistas_presupuesto.rutas)

    @app.middleware("http")
    async def cabeceras_seguras(request: Request, siguiente):
        respuesta = await siguiente(request)
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "same-origin"
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"
        )
        return respuesta

    @app.exception_handler(NoAutenticado)
    async def _no_autenticado(request: Request, _):
        return ir("/entrar")

    @app.exception_handler(Prohibido)
    async def _prohibido(request: Request, _):
        return mostrar(request, "prohibido.html", status_code=403)

    @app.exception_handler(FichaInvalida)
    async def _ficha(request: Request, _):
        avisar(request, "El formulario venció. Vuelva a intentarlo.", "error")
        return ir("/")

    return app
