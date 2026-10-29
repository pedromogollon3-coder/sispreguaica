"""Conexión a la base de datos."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def crear_motor(url: str) -> Engine:
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    motor = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(motor, "connect")
        def _activar_fk(conexion, _):
            conexion.execute("PRAGMA foreign_keys=ON")

    return motor


def crear_tablas(motor: Engine) -> None:
    Base.metadata.create_all(motor)


def fabrica_sesiones(motor: Engine) -> sessionmaker:
    return sessionmaker(bind=motor, expire_on_commit=False)
