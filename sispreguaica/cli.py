"""Órdenes de consola: sispreguaica iniciar | demo | servir | verificar | importar-cuentas."""

import argparse
import getpass
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from . import config as configuracion
from .db import crear_motor, crear_tablas, fabrica_sesiones
from .models import Ajuste, Ejercicio, Usuario
from .servicios import catalogos
from .servicios.inicio import cargar_demo, inicializar
from .servicios.presupuesto import ErrorPresupuestario, verificar_saldos


def _leer_env(ruta: Path = Path(".env")) -> None:
    """Carga un archivo .env (CLAVE=valor) sin pisar variables ya definidas."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def _sesion(config):
    motor = crear_motor(config.database_url)
    crear_tablas(motor)
    return fabrica_sesiones(motor)()


def _iniciar(args, config):
    with _sesion(config) as s:
        # La clave solo hace falta la primera vez, cuando todavía no hay admin.
        clave = ""
        if not s.scalar(select(Usuario).where(Usuario.login == "admin")):
            clave = os.environ.get("SISPREGUAICA_ADMIN_CLAVE") or getpass.getpass(
                "Clave para el usuario admin (mín. 10 caracteres): "
            )
        hecho = inicializar(s, clave, args.anio)
        if args.ente:
            s.merge(Ajuste(clave="ente", valor=args.ente.strip()))
            hecho.append(f"Nombre del ente: {args.ente.strip()}")
        s.commit()
    print("\n".join(hecho) or "La base ya estaba lista; no hubo cambios.")


def _demo(args, config):
    with _sesion(config) as s:
        cargar_demo(s, args.anio)
        s.commit()
    print(f"Datos de demostración cargados en el ejercicio {args.anio}.")


def _verificar(args, config):
    with _sesion(config) as s:
        ejercicio = s.scalar(select(Ejercicio).where(Ejercicio.anio == args.anio))
        if ejercicio is None:
            raise ErrorPresupuestario(f"No existe el ejercicio {args.anio}.")
        diferencias = verificar_saldos(s, ejercicio)
    if diferencias:
        print("Saldos que no cuadran con los movimientos:")
        print("\n".join(diferencias))
        sys.exit(1)
    print(f"Ejercicio {args.anio}: todos los saldos cuadran con los movimientos.")


def _importar(args, config):
    with _sesion(config) as s:
        filas = catalogos.leer_csv(Path(args.archivo).read_bytes(), ["codigo", "denominacion"])
        nuevas, cambiadas = catalogos.importar_cuentas(
            s, [(f["codigo"], f["denominacion"]) for f in filas]
        )
        s.commit()
    print(f"{nuevas} cuentas nuevas, {cambiadas} actualizadas.")


def _servir(args, config):
    import uvicorn

    from .web.app import crear_app

    uvicorn.run(crear_app(config), host=args.host, port=args.puerto, proxy_headers=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog="sispreguaica", description="Sistema de presupuesto público.")
    sub = p.add_subparsers(dest="orden", required=True)

    a = sub.add_parser("iniciar", help="Crea las tablas, el clasificador, el ejercicio y el usuario admin.")
    a.add_argument("--anio", type=int, default=date.today().year)
    a.add_argument("--ente", default="", help="Nombre del ente (también se cambia en Configuración).")
    a.set_defaults(funcion=_iniciar)

    a = sub.add_parser("demo", help="Carga datos de demostración en un ejercicio vacío.")
    a.add_argument("--anio", type=int, default=date.today().year)
    a.set_defaults(funcion=_demo)

    a = sub.add_parser("verificar", help="Recalcula los saldos desde los movimientos y los compara.")
    a.add_argument("--anio", type=int, default=date.today().year)
    a.set_defaults(funcion=_verificar)

    a = sub.add_parser("importar-cuentas", help="Carga cuentas desde un CSV (codigo, denominacion).")
    a.add_argument("archivo")
    a.set_defaults(funcion=_importar)

    a = sub.add_parser("servir", help="Inicia el servidor web.")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--puerto", type=int, default=8000)
    a.set_defaults(funcion=_servir)

    args = p.parse_args(argv)
    _leer_env()
    try:
        args.funcion(args, configuracion.cargar())
    except ErrorPresupuestario as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
