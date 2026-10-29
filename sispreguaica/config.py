"""Configuración leída de variables de entorno."""

import os
import secrets
import warnings
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    secreto: str
    cookie_segura: bool
    nombre_ente: str


def cargar() -> Config:
    secreto = os.environ.get("SISPREGUAICA_SECRETO", "")
    if not secreto:
        # Sin secreto fijo las sesiones se pierden al reiniciar; sirve para desarrollo.
        warnings.warn("SISPREGUAICA_SECRETO no está definido; se usa uno temporal.")
        secreto = secrets.token_urlsafe(32)
    return Config(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./sispreguaica.db"),
        secreto=secreto,
        cookie_segura=os.environ.get("SISPREGUAICA_COOKIE_SEGURA", "0") == "1",
        nombre_ente=os.environ.get("SISPREGUAICA_ENTE", "Ente público"),
    )
