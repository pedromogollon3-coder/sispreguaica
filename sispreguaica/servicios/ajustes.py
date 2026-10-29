"""Configuración del ente que se edita desde la pantalla Configuración."""

from sqlalchemy.orm import Session

from ..models import Ajuste, Usuario
from .presupuesto import ErrorPresupuestario, anotar

# clave: (título, largo máximo)
CAMPOS = {
    "ente": ("Nombre del ente", 200),
    "rif": ("RIF", 20),
}


def leer(sesion: Session, clave: str, defecto: str = "") -> str:
    ajuste = sesion.get(Ajuste, clave)
    return ajuste.valor if ajuste and ajuste.valor else defecto


def guardar(sesion: Session, valores: dict[str, str], usuario: Usuario) -> None:
    for clave, valor in valores.items():
        if clave not in CAMPOS:
            continue
        titulo, largo = CAMPOS[clave]
        valor = " ".join(valor.split())
        if len(valor) > largo:
            raise ErrorPresupuestario(f"{titulo}: máximo {largo} caracteres.")
        if clave == "ente" and not valor:
            raise ErrorPresupuestario("El nombre del ente no puede quedar vacío.")
        ajuste = sesion.get(Ajuste, clave)
        if ajuste is None:
            sesion.add(Ajuste(clave=clave, valor=valor))
        elif ajuste.valor != valor:
            ajuste.valor = valor
        else:
            continue
        anotar(sesion, usuario, "configuracion", f"{clave} = {valor}")
