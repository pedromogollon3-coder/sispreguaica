"""Clasificador de cuentas, estructuras, partidas y carga de la apertura."""

import csv
import io
import re
from datetime import date
from importlib import resources

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Cuenta,
    Ejercicio,
    Estructura,
    FuenteFinanciamiento,
    PartidaGasto,
    PartidaIngreso,
    Usuario,
)
from .montos import leer_monto
from .presupuesto import ErrorPresupuestario, Linea, anotar, registrar_comprobante

PATRON_CUENTA = re.compile(r"^[34](\.\d{2}){4}$")
PATRON_ESTRUCTURA = re.compile(r"^\d+(\.\d+)*$")


def padre(codigo: str) -> str | None:
    """4.02.05.03.00 -> 4.02.05.00.00 -> 4.02.00.00.00 -> 4.00.00.00.00 -> None."""
    partes = codigo.split(".")
    for i in range(len(partes) - 1, 0, -1):
        if partes[i] != "00":
            partes[i] = "00"
            return ".".join(partes)
    return None


def agrupar(codigo: str, segmentos: int) -> str:
    """Corta el código al nivel pedido: agrupar("4.02.05.03.00", 2) -> "4.02.00.00.00"."""
    partes = codigo.split(".")
    return ".".join(partes[:segmentos] + ["00"] * (len(partes) - segmentos))


def marcar_imputables(sesion: Session) -> None:
    """Una cuenta es imputable si ninguna otra cuelga de ella."""
    cuentas = list(sesion.scalars(select(Cuenta)))
    con_hijas = {padre(c.codigo) for c in cuentas}
    for c in cuentas:
        c.imputable = c.codigo not in con_hijas


def importar_cuentas(sesion: Session, filas) -> tuple[int, int]:
    """Carga filas (codigo, denominacion). Devuelve (nuevas, actualizadas)."""
    existentes = {c.codigo: c for c in sesion.scalars(select(Cuenta))}
    nuevas = actualizadas = 0
    for n, (codigo, denominacion) in enumerate(filas, start=1):
        codigo, denominacion = codigo.strip(), denominacion.strip()
        if not PATRON_CUENTA.match(codigo):
            raise ErrorPresupuestario(
                f"Fila {n}: el código {codigo!r} no tiene el formato 4.01.01.01.00."
            )
        if not denominacion:
            raise ErrorPresupuestario(f"Fila {n}: falta la denominación de {codigo}.")
        if codigo in existentes:
            if existentes[codigo].denominacion != denominacion:
                existentes[codigo].denominacion = denominacion
                actualizadas += 1
        else:
            cuenta = Cuenta(
                codigo=codigo, denominacion=denominacion, tipo="G" if codigo[0] == "4" else "I"
            )
            sesion.add(cuenta)
            existentes[codigo] = cuenta
            nuevas += 1
    sesion.flush()
    marcar_imputables(sesion)
    return nuevas, actualizadas


def leer_csv(contenido: bytes | str, columnas: list[str]) -> list[dict[str, str]]:
    """Lee un CSV separado por coma o punto y coma (como lo guarda Excel en español)."""
    if isinstance(contenido, bytes):
        for codificacion in ("utf-8-sig", "latin-1"):
            try:
                contenido = contenido.decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
    muestra = contenido[:2048]
    separador = ";" if muestra.count(";") > muestra.count(",") else ","
    lector = csv.DictReader(io.StringIO(contenido), delimiter=separador)
    encabezados = [h.strip().lower() for h in (lector.fieldnames or [])]
    faltan = [c for c in columnas if c not in encabezados]
    if faltan:
        raise ErrorPresupuestario(
            f"Al archivo le faltan las columnas: {', '.join(faltan)}. "
            f"Debe tener: {', '.join(columnas)}."
        )
    filas = []
    for fila in lector:
        normal = {(k or "").strip().lower(): (v or "").strip() for k, v in fila.items()}
        if any(normal.get(c) for c in columnas):
            filas.append(normal)
    return filas


def clasificador_egresos_incluido() -> list[tuple[str, str]]:
    texto = (
        resources.files("sispreguaica")
        .joinpath("datos/clasificador_egresos_2025.csv")
        .read_text(encoding="utf-8")
    )
    return [(f["codigo"], f["denominacion"]) for f in csv.DictReader(io.StringIO(texto))]


def cuenta_por_codigo(sesion: Session, codigo: str, tipo: str) -> Cuenta:
    cuenta = sesion.scalar(select(Cuenta).where(Cuenta.codigo == codigo.strip()))
    if cuenta is None:
        raise ErrorPresupuestario(f"La cuenta {codigo} no está en el clasificador.")
    if cuenta.tipo != tipo:
        clase = "gasto (4.xx)" if tipo == "G" else "ingreso (3.xx)"
        raise ErrorPresupuestario(f"La cuenta {codigo} no es de {clase}.")
    if not cuenta.imputable:
        raise ErrorPresupuestario(
            f"La cuenta {codigo} tiene subcuentas; use una cuenta de último nivel."
        )
    return cuenta


def estructura_por_codigo(sesion: Session, ejercicio: Ejercicio, codigo: str) -> Estructura:
    e = sesion.scalar(
        select(Estructura).where(
            Estructura.ejercicio_id == ejercicio.id, Estructura.codigo == codigo.strip()
        )
    )
    if e is None:
        raise ErrorPresupuestario(
            f"La estructura {codigo} no existe en el ejercicio {ejercicio.anio}."
        )
    return e


def fuente_por_codigo(sesion: Session, codigo: str) -> FuenteFinanciamiento:
    f = sesion.scalar(
        select(FuenteFinanciamiento).where(FuenteFinanciamiento.codigo == codigo.strip())
    )
    if f is None:
        raise ErrorPresupuestario(f"La fuente de financiamiento {codigo} no existe.")
    return f


def partida_gasto(
    sesion: Session,
    ejercicio: Ejercicio,
    estructura: str,
    cuenta: str,
    fuente: str,
    crear: bool = False,
) -> PartidaGasto:
    e = estructura_por_codigo(sesion, ejercicio, estructura)
    c = cuenta_por_codigo(sesion, cuenta, "G")
    f = fuente_por_codigo(sesion, fuente)
    p = sesion.scalar(
        select(PartidaGasto).where(
            PartidaGasto.ejercicio_id == ejercicio.id,
            PartidaGasto.estructura_id == e.id,
            PartidaGasto.cuenta_id == c.id,
            PartidaGasto.fuente_id == f.id,
        )
    )
    if p is None:
        if not crear:
            raise ErrorPresupuestario(f"No existe la partida {e.codigo}|{c.codigo}|{f.codigo}.")
        p = PartidaGasto(ejercicio_id=ejercicio.id, estructura=e, cuenta=c, fuente=f)
        sesion.add(p)
        sesion.flush()
    return p


def partida_gasto_por_clave(sesion: Session, ejercicio: Ejercicio, clave: str) -> PartidaGasto:
    """Clave con la forma ESTRUCTURA|CUENTA|FUENTE."""
    partes = [x.strip() for x in clave.split("|")]
    if len(partes) != 3:
        raise ErrorPresupuestario(
            f"Partida {clave!r} no válida; use ESTRUCTURA|CUENTA|FUENTE."
        )
    return partida_gasto(sesion, ejercicio, *partes)


def partida_ingreso(
    sesion: Session, ejercicio: Ejercicio, cuenta: str, crear: bool = False
) -> PartidaIngreso:
    c = cuenta_por_codigo(sesion, cuenta, "I")
    p = sesion.scalar(
        select(PartidaIngreso).where(
            PartidaIngreso.ejercicio_id == ejercicio.id, PartidaIngreso.cuenta_id == c.id
        )
    )
    if p is None:
        if not crear:
            raise ErrorPresupuestario(f"No existe la partida de ingreso {c.codigo}.")
        p = PartidaIngreso(ejercicio_id=ejercicio.id, cuenta=c)
        sesion.add(p)
        sesion.flush()
    return p


def cargar_apertura_gastos(
    sesion: Session, ejercicio: Ejercicio, filas: list[dict], fecha: date, usuario: Usuario
):
    """Filas con estructura, cuenta, fuente y monto. Crea las partidas y el comprobante AAP."""
    lineas = []
    for n, f in enumerate(filas, start=2):
        try:
            p = partida_gasto(
                sesion, ejercicio, f["estructura"], f["cuenta"], f["fuente"], crear=True
            )
            lineas.append(Linea(p, "AAP", leer_monto(f["monto"])))
        except (ErrorPresupuestario, ValueError) as e:
            raise ErrorPresupuestario(f"Línea {n} del archivo: {e}") from None
    return registrar_comprobante(
        sesion,
        ejercicio=ejercicio,
        tipo="G",
        procedencia="APE",
        fecha=fecha,
        descripcion=f"Apertura del presupuesto de gastos {ejercicio.anio}",
        lineas=lineas,
        usuario=usuario,
    )


def cargar_apertura_ingresos(
    sesion: Session, ejercicio: Ejercicio, filas: list[dict], fecha: date, usuario: Usuario
):
    lineas = []
    for n, f in enumerate(filas, start=2):
        try:
            p = partida_ingreso(sesion, ejercicio, f["cuenta"], crear=True)
            lineas.append(Linea(p, "PRE", leer_monto(f["monto"])))
        except (ErrorPresupuestario, ValueError) as e:
            raise ErrorPresupuestario(f"Línea {n} del archivo: {e}") from None
    return registrar_comprobante(
        sesion,
        ejercicio=ejercicio,
        tipo="I",
        procedencia="APE",
        fecha=fecha,
        descripcion=f"Apertura del presupuesto de ingresos {ejercicio.anio}",
        lineas=lineas,
        usuario=usuario,
    )


def crear_estructura(
    sesion: Session,
    ejercicio: Ejercicio,
    codigo: str,
    denominacion: str,
    unidad: str,
    usuario: Usuario,
) -> Estructura:
    codigo = codigo.strip()
    if not PATRON_ESTRUCTURA.match(codigo):
        raise ErrorPresupuestario("El código de estructura debe ser numérico, p. ej. 01.02.00.00.51.")
    if not denominacion.strip():
        raise ErrorPresupuestario("Falta la denominación de la estructura.")
    if sesion.scalar(
        select(Estructura).where(Estructura.ejercicio_id == ejercicio.id, Estructura.codigo == codigo)
    ):
        raise ErrorPresupuestario(f"La estructura {codigo} ya existe.")
    e = Estructura(
        ejercicio_id=ejercicio.id,
        codigo=codigo,
        denominacion=denominacion.strip(),
        unidad_ejecutora=unidad.strip(),
    )
    sesion.add(e)
    anotar(sesion, usuario, "estructura.crear", codigo)
    return e


def tiene_partidas(sesion: Session, ejercicio: Ejercicio) -> bool:
    return any(
        sesion.scalar(select(modelo.id).where(modelo.ejercicio_id == ejercicio.id).limit(1))
        for modelo in (PartidaGasto, PartidaIngreso)
    )
