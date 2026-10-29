# Sispreguaica

Sistema de presupuesto público de gastos e ingresos para entes venezolanos. Trae el Clasificador Presupuestario ONAPRE 2025 de egresos.

Lleva cada partida desde la apertura hasta el pago. Controla la disponibilidad en el momento de registrar y aplica las modificaciones presupuestarias con sus reglas. Los reportes de ejecución se descargan en Excel o CSV.

Autor: Dr. Pedro Mogollón.

## Qué hace

- **Apertura del presupuesto.** Se carga desde un archivo CSV: estructura programática, cuenta, fuente de financiamiento y monto.
- **Registro de gastos** en lenguaje sencillo. Primero se elige qué se registra: precompromiso, compromiso, causado, pago, o todo de una vez. Luego se escoge la partida, y el sistema avisa al instante si alcanza la disponibilidad.
- **Registro de ingresos:** devengado, recaudado, o ambos.
- **Modificaciones presupuestarias.** Cada tipo tiene su regla:

  | Tipo | Regla |
  |---|---|
  | Traspaso | Lo que se cede es igual a lo que se recibe. |
  | Crédito adicional | Solo hay aumentos. Si se indica la partida de ingreso que lo financia, deben cuadrar. |
  | Rectificación | Sale solo de la 4.98 y cuadra con lo que se aumenta. |
  | Insubsistencia | Solo hay disminuciones. |

  Una modificación se guarda en borrador y cambia los saldos cuando un administrador la aprueba.
- **Controles que no se pueden saltar:**
  - no se compromete sin disponibilidad;
  - no se causa más de lo comprometido;
  - no se paga más de lo causado;
  - no se recauda más de lo devengado.
- **Anulación de comprobantes** con su motivo. No se puede anular si un registro posterior depende del comprobante.
- **Reportes de ejecución** por partida, genérica, específica o detalle por estructura, con fecha de corte. Se exportan a Excel o CSV.
- **Mayor analítico** de cada partida.
- **Usuarios** con tres roles:

  | Rol | Puede |
  |---|---|
  | Administrador | Todo, incluido aprobar modificaciones y anular comprobantes. |
  | Analista | Registrar. |
  | Consulta | Ver. |

- **Bitácora** de todo lo que se hace.
- **Configuración.** En Administración, Configuración, se cambian el nombre del ente y el RIF. El nombre aparece en todas las pantallas y en los reportes, y el RIF en el encabezado del Excel.

## Instalación en Linux Mint

Probado para Mint 21 y 22, y también sirve en Ubuntu.

1. Descargue el programa:

   ```bash
   git clone https://github.com/pedromogollon3-coder/sispreguaica.git
   ```

2. Entre a la carpeta y ejecute el instalador:

   ```bash
   cd sispreguaica && bash instalar_mint.sh
   ```

El instalador pide la clave de `sudo`, el nombre del ente y la clave del usuario `admin`. Esa clave debe tener al menos 10 caracteres. También pregunta si quiere cargar datos de demostración para practicar.

Al terminar, el sistema queda funcionando en <http://localhost:8000> y arranca solo cada vez que se enciende el equipo. También aparece como **Sispreguaica** en el menú de aplicaciones.

Algunas órdenes útiles:

| Para | Orden |
|---|---|
| Ver si está funcionando | `systemctl status sispreguaica` |
| Ver los mensajes de error | `journalctl -u sispreguaica -n 50` |
| Reiniciar | `sudo systemctl restart sispreguaica` |
| Actualizar a una versión nueva | `git pull && bash instalar_mint.sh` (los datos se conservan) |
| Abrir un ejercicio nuevo | `.venv/bin/sispreguaica iniciar --anio 2027` |
| Comprobar que los saldos cuadran con los movimientos | `.venv/bin/sispreguaica verificar --anio 2026` |
| Respaldar la base | `cp sispreguaica.db respaldo-$(date +%F).db` |

## Instalación con Docker

Esta opción usa PostgreSQL y conviene para un servidor compartido por varias personas.

1. Copie el ejemplo de configuración:

   ```bash
   cp .env.example .env
   ```

2. Edite `.env`. Ponga una clave larga en `SISPREGUAICA_SECRETO`, la clave del admin en `SISPREGUAICA_ADMIN_CLAVE` y una clave en `POSTGRES_PASSWORD`.

3. Arranque los contenedores:

   ```bash
   docker compose up -d
   ```

El sistema queda en <http://localhost:8000>. Los datos se guardan en el volumen `datos-db`.

Para respaldar la base:

```bash
docker compose exec db pg_dump -U sispreguaica sispreguaica > respaldo.sql
```

Si lo va a usar desde otras computadoras de la red, póngale delante un servidor con HTTPS, como Caddy o nginx, y en `.env` ponga `SISPREGUAICA_COOKIE_SEGURA=1`.

## Primeros pasos en el programa

1. Entre como `admin`.
2. En **Configuración**, escriba el nombre del ente y el RIF.
3. En **Estructura programática**, cree los proyectos y acciones centralizadas, por ejemplo `01.01.00.00.51`.
4. En **Apertura del presupuesto**, descargue la plantilla CSV, llénela con la ley de presupuesto y súbala. Haga lo mismo con los ingresos.
   - El clasificador de **ingresos** (3.xx) no viene incluido. Cárguelo antes, en **Clasificador de cuentas**, con un CSV de dos columnas: `codigo;denominacion`.
5. En **Usuarios**, cree a los analistas y a los usuarios de consulta.
6. Desde ahí, el día a día se hace en **Registrar gasto**, **Registrar ingreso** y **Modificaciones**.

## Para desarrolladores

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[pruebas]"
pytest
SISPREGUAICA_SECRETO=dev sispreguaica iniciar --anio 2026
sispreguaica demo --anio 2026
sispreguaica servir --puerto 8000
```

### Tecnología

FastAPI, SQLAlchemy 2, Jinja2 y openpyxl. La base es SQLite por defecto o PostgreSQL.

### Cómo se guardan los saldos

Cada partida guarda sus saldos para responder la disponibilidad al instante. La orden `sispreguaica verificar` los recalcula desde los movimientos y avisa si algo no cuadra.

### Seguridad

- Las claves se guardan con scrypt.
- Todos los formularios llevan una ficha CSRF.
- Las entradas se bloquean tras 5 intentos fallidos.
- Las respuestas llevan cabeceras de seguridad, entre ellas CSP.
- Todo queda registrado en la bitácora.

La lógica presupuestaria toma como referencia las operaciones de SIGESP: precompromiso, compromiso, causado, pagado y sus combinaciones. Es un desarrollo nuevo e independiente de SIGESP.
