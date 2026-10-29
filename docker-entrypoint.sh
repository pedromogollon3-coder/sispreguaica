#!/bin/sh
# Prepara la base (es seguro repetirlo) y arranca el servidor.
set -e
sispreguaica iniciar ${SISPREGUAICA_ANIO:+--anio "$SISPREGUAICA_ANIO"}
exec "$@"
