"""Catálogo de operaciones presupuestarias.

Toma como referencia las operaciones de SIGESP (spg_operaciones y
spi_operaciones). Cada operación suma su monto a uno o varios saldos de la
partida; la liberación de precompromiso (LP) los resta.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Operacion:
    codigo: str
    denominacion: str
    efectos: tuple[tuple[str, int], ...]  # (saldo, signo)


def _op(codigo, denominacion, *saldos, signo=1):
    return Operacion(codigo, denominacion, tuple((s, signo) for s in saldos))


GASTO = {
    o.codigo: o
    for o in [
        _op("AAP", "Asignación de apertura", "asignado"),
        _op("AU", "Aumento de partida", "aumento"),
        _op("DI", "Disminución de partida", "disminucion"),
        _op("PC", "Precompromiso", "precomprometido"),
        _op("LP", "Liberación de precompromiso", "precomprometido", signo=-1),
        _op("CS", "Compromiso", "comprometido"),
        _op("CG", "Compromiso y causado", "comprometido", "causado"),
        _op("GC", "Causado", "causado"),
        _op("CP", "Causado y pagado", "causado", "pagado"),
        _op("PG", "Pagado", "pagado"),
        _op("CCP", "Compromiso, causado y pagado", "comprometido", "causado", "pagado"),
    ]
}

INGRESO = {
    o.codigo: o
    for o in [
        _op("PRE", "Previsto (apertura)", "previsto"),
        _op("AU", "Aumento", "aumento"),
        _op("DI", "Disminución", "disminucion"),
        _op("DEV", "Devengado", "devengado"),
        _op("COB", "Cobrado", "cobrado"),
        _op("DC", "Devengado y cobrado", "devengado", "cobrado"),
    ]
}

# Operaciones que se pueden registrar a mano en un comprobante. Las de
# apertura salen de la carga inicial y las de AU/DI solo de una modificación
# aprobada, para que ningún aumento entre sin su acto administrativo.
MANUALES_GASTO = ("PC", "LP", "CS", "CG", "GC", "CP", "PG", "CCP")
MANUALES_INGRESO = ("DEV", "COB", "DC")


def catalogo(tipo: str) -> dict[str, Operacion]:
    return GASTO if tipo == "G" else INGRESO
