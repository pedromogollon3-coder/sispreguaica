"""Claves de usuario (scrypt) y fichas contra falsificación de formularios."""

import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1


def cifrar_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    resumen = hashlib.scrypt(clave.encode(), salt=sal, n=_N, r=_R, p=_P)
    return f"scrypt${sal.hex()}${resumen.hex()}"


def verificar_clave(clave: str, guardada: str) -> bool:
    try:
        esquema, sal, resumen = guardada.split("$")
    except ValueError:
        return False
    if esquema != "scrypt":
        return False
    calculado = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=_N, r=_R, p=_P)
    return hmac.compare_digest(calculado.hex(), resumen)


def clave_valida(clave: str) -> str | None:
    """Devuelve el motivo si la clave no sirve, o None si sirve."""
    if len(clave) < 10:
        return "La clave debe tener al menos 10 caracteres."
    return None


def nueva_ficha() -> str:
    return secrets.token_urlsafe(32)
