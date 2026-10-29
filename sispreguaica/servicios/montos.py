"""Lectura y presentación de montos en bolívares."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTIMO = Decimal("0.01")


def leer_monto(texto: str | Decimal | int | float) -> Decimal:
    """Acepta "1.234.567,89", "1234567,89" o "1234567.89"."""
    if isinstance(texto, Decimal):
        valor = texto
    elif isinstance(texto, (int, float)):
        valor = Decimal(str(texto))
    else:
        limpio = texto.strip().replace(" ", "").replace("Bs.", "").replace("Bs", "")
        if "," in limpio:
            limpio = limpio.replace(".", "").replace(",", ".")
        elif limpio.count(".") > 1:
            limpio = limpio.replace(".", "")
        try:
            valor = Decimal(limpio)
        except InvalidOperation:
            raise ValueError(f"Monto no válido: {texto!r}") from None
    if not valor.is_finite():
        raise ValueError(f"Monto no válido: {texto!r}")
    return valor.quantize(CENTIMO, rounding=ROUND_HALF_UP)


def formatear(valor: Decimal | None) -> str:
    """1234567.8 -> "1.234.567,80"."""
    if valor is None:
        return ""
    texto = f"{Decimal(valor).quantize(CENTIMO, rounding=ROUND_HALF_UP):,.2f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")
