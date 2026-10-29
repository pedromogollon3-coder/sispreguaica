"""Modelo de datos del presupuesto.

Los saldos de cada partida se guardan en la propia fila para consultar la
disponibilidad al instante; `servicios.presupuesto.verificar_saldos` los
recalcula desde los movimientos para comprobar que no se hayan desviado.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONTO = Numeric(18, 2)
CERO = Decimal("0.00")


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(40), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    clave_hash: Mapped[str] = mapped_column(String(200))
    # admin: todo, incluida la aprobación de modificaciones y los usuarios.
    # analista: registra comprobantes y prepara modificaciones.
    # consulta: solo ve y descarga reportes.
    rol: Mapped[str] = mapped_column(String(10), default="consulta")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (CheckConstraint("rol IN ('admin','analista','consulta')"),)


class Ejercicio(Base):
    __tablename__ = "ejercicio"

    id: Mapped[int] = mapped_column(primary_key=True)
    anio: Mapped[int] = mapped_column(Integer, unique=True)
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    abierto: Mapped[bool] = mapped_column(Boolean, default=True)


class FuenteFinanciamiento(Base):
    __tablename__ = "fuente_financiamiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(10), unique=True)
    denominacion: Mapped[str] = mapped_column(String(200))


class Estructura(Base):
    """Estructura programática (p. ej. Sector.Programa.Subprograma.Proyecto.Actividad)."""

    __tablename__ = "estructura"

    id: Mapped[int] = mapped_column(primary_key=True)
    ejercicio_id: Mapped[int] = mapped_column(ForeignKey("ejercicio.id"))
    codigo: Mapped[str] = mapped_column(String(40))
    denominacion: Mapped[str] = mapped_column(String(300))
    unidad_ejecutora: Mapped[str] = mapped_column(String(200), default="")

    ejercicio: Mapped[Ejercicio] = relationship()

    __table_args__ = (UniqueConstraint("ejercicio_id", "codigo"),)


class Cuenta(Base):
    """Cuenta del clasificador presupuestario (3.xx ingresos, 4.xx egresos)."""

    __tablename__ = "cuenta"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    denominacion: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[str] = mapped_column(String(1))  # G = gasto, I = ingreso
    # Solo las cuentas de último nivel (sin hijas) reciben movimientos.
    imputable: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (CheckConstraint("tipo IN ('G','I')"),)


class PartidaGasto(Base):
    __tablename__ = "partida_gasto"

    id: Mapped[int] = mapped_column(primary_key=True)
    ejercicio_id: Mapped[int] = mapped_column(ForeignKey("ejercicio.id"))
    estructura_id: Mapped[int] = mapped_column(ForeignKey("estructura.id"))
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuenta.id"))
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuente_financiamiento.id"))

    asignado: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    aumento: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    disminucion: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    precomprometido: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    comprometido: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    causado: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    pagado: Mapped[Decimal] = mapped_column(MONTO, default=CERO)

    estructura: Mapped[Estructura] = relationship()
    cuenta: Mapped[Cuenta] = relationship()
    fuente: Mapped[FuenteFinanciamiento] = relationship()

    __table_args__ = (UniqueConstraint("ejercicio_id", "estructura_id", "cuenta_id", "fuente_id"),)

    @property
    def actualizado(self) -> Decimal:
        return self.asignado + self.aumento - self.disminucion

    @property
    def disponible(self) -> Decimal:
        return self.actualizado - self.precomprometido - self.comprometido

    @property
    def clave(self) -> str:
        return f"{self.estructura.codigo}|{self.cuenta.codigo}|{self.fuente.codigo}"


class PartidaIngreso(Base):
    __tablename__ = "partida_ingreso"

    id: Mapped[int] = mapped_column(primary_key=True)
    ejercicio_id: Mapped[int] = mapped_column(ForeignKey("ejercicio.id"))
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuenta.id"))

    previsto: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    aumento: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    disminucion: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    devengado: Mapped[Decimal] = mapped_column(MONTO, default=CERO)
    cobrado: Mapped[Decimal] = mapped_column(MONTO, default=CERO)

    cuenta: Mapped[Cuenta] = relationship()

    __table_args__ = (UniqueConstraint("ejercicio_id", "cuenta_id"),)

    @property
    def actualizado(self) -> Decimal:
        return self.previsto + self.aumento - self.disminucion

    @property
    def por_devengar(self) -> Decimal:
        return self.actualizado - self.devengado

    @property
    def clave(self) -> str:
        return self.cuenta.codigo


class Comprobante(Base):
    __tablename__ = "comprobante"

    id: Mapped[int] = mapped_column(primary_key=True)
    ejercicio_id: Mapped[int] = mapped_column(ForeignKey("ejercicio.id"))
    numero: Mapped[int] = mapped_column(Integer)
    tipo: Mapped[str] = mapped_column(String(1))  # G = gasto, I = ingreso
    # APE = apertura, MOD = modificación presupuestaria, MAN = registro manual.
    procedencia: Mapped[str] = mapped_column(String(3))
    fecha: Mapped[date] = mapped_column(Date)
    descripcion: Mapped[str] = mapped_column(Text)
    beneficiario: Mapped[str] = mapped_column(String(200), default="")
    documento: Mapped[str] = mapped_column(String(60), default="")
    estado: Mapped[str] = mapped_column(String(10), default="activo")
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    motivo_anulacion: Mapped[str] = mapped_column(Text, default="")

    usuario: Mapped[Usuario] = relationship()
    movimientos: Mapped[list["Movimiento"]] = relationship(
        back_populates="comprobante", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ejercicio_id", "numero"),
        CheckConstraint("estado IN ('activo','anulado')"),
    )

    @property
    def total(self) -> Decimal:
        return sum((m.monto for m in self.movimientos), CERO)


class Movimiento(Base):
    __tablename__ = "movimiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    comprobante_id: Mapped[int] = mapped_column(ForeignKey("comprobante.id"))
    partida_gasto_id: Mapped[int | None] = mapped_column(ForeignKey("partida_gasto.id"))
    partida_ingreso_id: Mapped[int | None] = mapped_column(ForeignKey("partida_ingreso.id"))
    operacion: Mapped[str] = mapped_column(String(3))
    monto: Mapped[Decimal] = mapped_column(MONTO)

    comprobante: Mapped[Comprobante] = relationship(back_populates="movimientos")
    partida_gasto: Mapped[PartidaGasto | None] = relationship()
    partida_ingreso: Mapped[PartidaIngreso | None] = relationship()

    __table_args__ = (
        CheckConstraint("monto > 0"),
        CheckConstraint("(partida_gasto_id IS NULL) <> (partida_ingreso_id IS NULL)"),
    )


class Modificacion(Base):
    """Modificación presupuestaria: traspaso, crédito adicional, rectificación o insubsistencia."""

    __tablename__ = "modificacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    ejercicio_id: Mapped[int] = mapped_column(ForeignKey("ejercicio.id"))
    numero: Mapped[int] = mapped_column(Integer)
    tipo: Mapped[str] = mapped_column(String(12))
    fecha: Mapped[date] = mapped_column(Date)
    descripcion: Mapped[str] = mapped_column(Text)
    documento_soporte: Mapped[str] = mapped_column(String(200), default="")
    estado: Mapped[str] = mapped_column(String(10), default="borrador")
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    aprobado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    comprobante_gasto_id: Mapped[int | None] = mapped_column(ForeignKey("comprobante.id"))
    comprobante_ingreso_id: Mapped[int | None] = mapped_column(ForeignKey("comprobante.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lineas: Mapped[list["ModificacionLinea"]] = relationship(
        back_populates="modificacion", cascade="all, delete-orphan"
    )
    usuario: Mapped[Usuario] = relationship(foreign_keys=[usuario_id])
    aprobado_por: Mapped[Usuario | None] = relationship(foreign_keys=[aprobado_por_id])
    comprobante_gasto: Mapped[Comprobante | None] = relationship(foreign_keys=[comprobante_gasto_id])
    comprobante_ingreso: Mapped[Comprobante | None] = relationship(
        foreign_keys=[comprobante_ingreso_id]
    )

    __table_args__ = (
        UniqueConstraint("ejercicio_id", "numero"),
        CheckConstraint("estado IN ('borrador','aprobada','anulada')"),
        CheckConstraint(
            "tipo IN ('TRASPASO','CREDITO','RECTIFICA','INSUBSIST')"
        ),
    )


class ModificacionLinea(Base):
    __tablename__ = "modificacion_linea"

    id: Mapped[int] = mapped_column(primary_key=True)
    modificacion_id: Mapped[int] = mapped_column(ForeignKey("modificacion.id"))
    partida_gasto_id: Mapped[int | None] = mapped_column(ForeignKey("partida_gasto.id"))
    partida_ingreso_id: Mapped[int | None] = mapped_column(ForeignKey("partida_ingreso.id"))
    accion: Mapped[str] = mapped_column(String(2))  # AU = aumento, DI = disminución
    monto: Mapped[Decimal] = mapped_column(MONTO)

    modificacion: Mapped[Modificacion] = relationship(back_populates="lineas")
    partida_gasto: Mapped[PartidaGasto | None] = relationship()
    partida_ingreso: Mapped[PartidaIngreso | None] = relationship()

    __table_args__ = (
        CheckConstraint("monto > 0"),
        CheckConstraint("accion IN ('AU','DI')"),
    )


class Bitacora(Base):
    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    accion: Mapped[str] = mapped_column(String(40))
    detalle: Mapped[str] = mapped_column(Text, default="")

    usuario: Mapped[Usuario | None] = relationship()


class Ajuste(Base):
    """Datos de configuración que el administrador cambia desde el programa."""

    __tablename__ = "ajuste"

    clave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")
