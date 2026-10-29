import pytest
from sqlalchemy import select

from sispreguaica.db import crear_motor, crear_tablas, fabrica_sesiones
from sispreguaica.models import Ejercicio, Usuario
from sispreguaica.servicios.inicio import inicializar

ANIO = 2026
CLAVE = "clave-de-prueba-123"


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    s = fabrica_sesiones(motor)()
    inicializar(s, CLAVE, ANIO)
    s.commit()
    yield s
    s.close()
    motor.dispose()


@pytest.fixture
def ejercicio(sesion):
    return sesion.scalar(select(Ejercicio).where(Ejercicio.anio == ANIO))


@pytest.fixture
def admin(sesion):
    return sesion.scalar(select(Usuario).where(Usuario.login == "admin"))
