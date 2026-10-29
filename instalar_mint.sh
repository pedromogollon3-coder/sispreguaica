#!/usr/bin/env bash
# Instala Sispreguaica en Linux Mint (21 o 22) o Ubuntu.
#
# Uso, desde la carpeta del programa:
#   bash instalar_mint.sh
#
# Qué hace:
#   1. Instala Python y sus herramientas (pide la clave de sudo).
#   2. Crea un entorno aislado (.venv) e instala el programa.
#   3. Crea el archivo .env con una clave secreta nueva.
#   4. Crea la base de datos, el clasificador ONAPRE 2025 y el usuario admin.
#   5. Deja el sistema arrancando solo al encender el equipo (servicio systemd)
#      y pone un acceso directo en el menú de aplicaciones.
#
# Se puede ejecutar de nuevo sin perder datos: solo actualiza el programa.
set -euo pipefail

CARPETA="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUERTO="${PUERTO:-8000}"
USUARIO="$(id -un)"
cd "$CARPETA"

paso() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
falla() { printf '\n\033[1;31mError: %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || falla "Ejecute el script con su usuario normal, no como root (pedirá sudo cuando haga falta)."
[ -f pyproject.toml ] || falla "Ejecute el script desde la carpeta de Sispreguaica."

paso "Instalando Python y herramientas del sistema"
sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip

python3 - <<'PY' || falla "Se necesita Python 3.10 o más reciente."
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

paso "Instalando Sispreguaica en $CARPETA/.venv"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet .

if [ ! -f .env ]; then
  paso "Creando la configuración (.env)"
  read -r -p "Nombre del ente (puede cambiarlo luego en el programa): " ENTE
  SECRETO="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  {
    echo "SISPREGUAICA_SECRETO=$SECRETO"
    echo "DATABASE_URL=sqlite:///$CARPETA/sispreguaica.db"
    echo "SISPREGUAICA_COOKIE_SEGURA=0"
  } > .env
  chmod 600 .env
else
  ENTE=""
fi

paso "Preparando la base de datos"
ANIO="$(date +%Y)"
if [ -f sispreguaica.db ]; then
  echo "La base ya existe; se conserva."
  .venv/bin/sispreguaica iniciar --anio "$ANIO"
else
  echo "Escriba la clave del usuario admin (mínimo 10 caracteres)."
  .venv/bin/sispreguaica iniciar --anio "$ANIO" ${ENTE:+--ente "$ENTE"}
  read -r -p "¿Cargar datos de demostración para practicar? [s/N] " DEMO
  if [[ "${DEMO,,}" == s* ]]; then
    .venv/bin/sispreguaica demo --anio "$ANIO"
  fi
fi

paso "Creando el servicio para que arranque solo"
sudo tee /etc/systemd/system/sispreguaica.service >/dev/null <<UNIDAD
[Unit]
Description=Sispreguaica - presupuesto público
After=network.target

[Service]
User=$USUARIO
WorkingDirectory=$CARPETA
EnvironmentFile=$CARPETA/.env
ExecStart=$CARPETA/.venv/bin/sispreguaica servir --host 127.0.0.1 --puerto $PUERTO
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIDAD
sudo systemctl daemon-reload
sudo systemctl enable --now sispreguaica.service
sudo systemctl restart sispreguaica.service

paso "Agregando Sispreguaica al menú de aplicaciones"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/sispreguaica.desktop" <<ACCESO
[Desktop Entry]
Type=Application
Name=Sispreguaica
Comment=Presupuesto de gastos e ingresos
Exec=xdg-open http://localhost:$PUERTO
Icon=accessories-calculator
Terminal=false
Categories=Office;Finance;
ACCESO
if [ -d "$HOME/Escritorio" ]; then
  cp "$HOME/.local/share/applications/sispreguaica.desktop" "$HOME/Escritorio/"
  chmod +x "$HOME/Escritorio/sispreguaica.desktop"
elif [ -d "$HOME/Desktop" ]; then
  cp "$HOME/.local/share/applications/sispreguaica.desktop" "$HOME/Desktop/"
  chmod +x "$HOME/Desktop/sispreguaica.desktop"
fi

sleep 2
if systemctl is-active --quiet sispreguaica.service; then
  printf '\n\033[1;32mListo.\033[0m Abra http://localhost:%s y entre con el usuario admin.\n' "$PUERTO"
  echo "También está en el menú de aplicaciones como «Sispreguaica»."
else
  falla "El servicio no arrancó. Revise con: journalctl -u sispreguaica -n 50"
fi
