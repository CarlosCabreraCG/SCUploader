# SensorCloud Sync

Herramienta para sincronizar datos entre dispositivos de **MicroStrain
SensorCloud**: descarga datos crudos de un dispositivo origen, les aplica
una calibración lineal por canal y ecuaciones compuestas entre canales, y
sube el resultado a un dispositivo destino (con opciones de media móvil,
"demeaning" y exportación a CSV).

Este proyecto reemplaza a los tres scripts sueltos anteriores
(`SensorCloudClient.py`, `SensorCloudUploader.py`,
`SensorCloudViewerCSV.py`), que duplicaban el mismo cliente HTTP y la
misma lógica de transformación. Ahora todo vive en un solo paquete
(`sensorcloud/`) y se usa a través de `python main.py <comando>`.

## Estructura del proyecto

```
sensorcloud_sync/
├── main.py                     # CLI (python main.py <comando> ...)
├── sensorcloud/
│   ├── client.py                # SensorCloudClient: auth, sensores/canales, subida/descarga/borrado
│   ├── config.py                # Carga de .env y de config/sensors_config.json
│   ├── transforms.py            # Intervalos, media móvil, demeaning, calibración, ecuaciones compuestas
│   ├── pipeline.py              # Orquesta: descargar -> calibrar -> combinar -> subir/exportar
│   ├── scheduler.py             # Ejecución periódica del pipeline
│   ├── csv_export.py            # Exportación de series a CSV
│   └── datastore.py              # Almacenamiento local (CSV por canal) que lee la GUI
│   
├── config/
│   └── sensors_config.json      # Canales, ecuaciones y opciones (datos NO sensibles)
├── data/                         # Se crea automáticamente: histórico local por canal (no subir a git)
├── .env.example                 # Plantilla de variables de entorno sensibles
├── requirements.txt
└── README.md
```

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuración

### 1. Datos sensibles → `.env`

Un solo origen: 
Copia `.env.example` a `.env` y completa tus credenciales reales:

```bash
cp .env.example .env
```

Un mismo .env puede declarar una o varias fuentes. Los campos
API_KEY_SOURCE, SOURCE_DEVICE y CONFIG_FILE son listas paralelas
separadas por comas (deben tener el mismo número de elementos). Los
destinos son compartidos por todas las fuentes.

```ini
API_KEY_SOURCE=tu_api_key_origen
API_KEY_DEST=tu_api_key_destino
SOURCE_DEVICE=W020000000198806
DEST_DEVICE=OAPI007627ZU9AWY
CONFIG_FILE=config/sensors_config.json
```
Varios orígenes en un solo .env

```ini
API_KEY_SOURCE=clave_fuente_1,clave_fuente_2,clave_fuente_3
SOURCE_DEVICE=38401,38402,38403
CONFIG_FILE=config/chinalco.json,config/planta_a.json,config/planta_b.json

API_KEY_DEST=clave_destino_comun
DEST_DEVICE=99999
```

Destino simple (subida doble)

```ini
API_KEY_DEST=clave_destino_completo
DEST_DEVICE=99999

API_KEY_DEST_SIMPLE=clave_destino_simple
DEST_DEVICE_SIMPLE=99998
```

El archivo `.env` está en `.gitignore` y nunca debe compartirse. Puedes
apuntar a otro archivo con `--env-file ruta/otro.env` en cualquier
comando.

### 2. Canales y ecuaciones → `config/sensors_config.json`

Este archivo (no sensible) define todo lo demás:

```json
{
  "options": {
    "minutes_back": 4320,
    "minutes_back_end": 0,
    "interval_scheduler": 60,
    "moving_average": true,
    "demeaning": true,
    "ma_window": 5,
    "ma_max_gap_minutes": 1.0
  },
  "sensors_channel": {
    "16748_ch1": { "var": "a", "slope": -1.7612e-3, "offset": 12452.0176, "divisor": 5 }
  },
  "composite_equations": {
    "a,c": "(a + 0.3*c) / (1 - 0.09)"
  },
  "output_nodes": {
    "16748L_ch1": "a,c"
  }
}
```

- **`sensors_channel`**: por cada canal fuente (`"sensor_canal"`), la letra
  de variable asociada y los parámetros de la ecuación lineal de
  calibración, siempre de la forma:

  ```
  (slope * valor + offset) / divisor
  ```

- **`composite_equations`**: ecuación completa por grupo de variables
  (`"letra1,letra2,..."` → fórmula en Python usando esos nombres).
- **`output_nodes`**: por cada canal del dispositivo destino
  (`API_KEY_DEST`), qué grupo de variables/ecuación compuesta le
  corresponde.
- **`options`**: parámetros generales del pipeline (ventana de tiempo,
  intervalo del scheduler, media móvil y demeaning).

Puedes usar un archivo de configuración distinto con `--config ruta.json`.

## Subida doble
Por defecto, transfer sube dos versiones de cada serie de salida:

La serie completa (tras calibración, ecuaciones, media móvil y/o
demeaning) al dispositivo DEST_DEVICE.

Una serie reducida al dispositivo DEST_DEVICE_SIMPLE, pensada
para dashboards: por cada intervalo continuo (mismo criterio de gap
que la media móvil, ma_max_gap_minutes) se guardan solo el primer
punto, el máximo, el mínimo y el último, deduplicados por timestamp
y ordenados. Se preservan los timestamps originales, así que
--fromlastpoint sigue funcionando de forma coherente.

Flags:

--dont-upload-simple: sube solo al dispositivo completo.

--only-simple: sube solo al dispositivo simple (requiere
DEST_DEVICE_SIMPLE y API_KEY_DEST_SIMPLE).

Ambos son mutuamente excluyentes.

En local, la serie reducida se guarda con el sufijo __simple en los
CSV (<canal>__simple.csv) y en el datastore (<canal>__simple), para
no mezclarse con la completa.

Con --fromlastpoint y subida doble, el umbral por canal se toma del
dispositivo completo; si el canal aún no existe allí, se cae al
simple. Esto evita que el simple vaya por delante y bloquee puntos que
todavía faltan en el completo.

## Uso (CLI)

Todos los comandos se ejecutan como `python main.py <comando> [opciones]`.

### Transferencia única

```bash
python main.py transfer
python main.py transfer --minutes-back 120
python main.py transfer --no-upload --export-csv --output-dir salida
```

### Subida doble (comportamiento por defecto cuando el .env define DEST_DEVICE_SIMPLE):
```bash
python main.py transfer                       # completo + simple
python main.py transfer --dont-upload-simple  # solo completo
python main.py transfer --only-simple         # solo simple
```

### Transferencia para varios .env
```bash
python main.py --env-file .env.p1 --env-file .env.p2 transfer
```
### Transferencia desde el último punto registrado
```bash
python main.py --env-file .env.p1 transfer --fromlastpoint
python main.py --env-file .env.p1 --env-file .env.p2 transfer --fromlastpoint
```

### Ejecución periódica (scheduler)

```bash
python main.py schedule
python main.py schedule --interval 30
python main.py --env-file .env.p1 --env-file .env.p2 schedule
python main.py --env-file .env.p1 --env-file .env.p2 schedule --fromlastpoint
```
Se detiene con `Ctrl+C`.

### Crear sensores/canales en el dispositivo destino

```bash
python main.py setup-nodes
```
Crea, si no existen, los sensores y canales listados en `output_nodes`.

### Eliminar un canal

```bash
python main.py delete-channel --device dest --sensor 16752 --channel ch1
python main.py --env-file .env.mo delete-channel --device dest --sensor 57619T --channel ch1
```

### Descargar datos crudos de un canal a CSV

```bash
python main.py download --device source --sensor 16748 --channel ch1 --minutes-back 60 --output datos.csv
```

### Ver la configuración cargada

```bash
python main.py show-config
```

`python main.py transfer` y `python main.py schedule` guardan automáticamente
los resultados de cada nodo de salida en `data/<canal>.csv` (histórico
local, además de subirlos a SensorCloud). La interfaz gráfica **solo lee
esos archivos**: no vuelve a llamar a la API, así que ábrela después de
haber corrido al menos un `transfer`.

## Uso como librería

El pipeline también se puede usar directamente desde código Python:

```python
from sensorcloud.client import SensorCloudClient
from sensorcloud.config import load_credentials, load_sensors_config
from sensorcloud.pipeline import transfer_complex_data_between_devices

creds = load_credentials()
config = load_sensors_config()

source = SensorCloudClient(creds.source_device, creds.api_key_source)
dest = SensorCloudClient(creds.dest_device, creds.api_key_dest)
source.authenticate()
dest.authenticate()

transfer_complex_data_between_devices(source, dest, config)
```

## Qué cambió respecto a los scripts originales

- Se unificó una sola clase `SensorCloudClient` (antes estaba duplicada,
  casi idéntica, en los tres archivos).
- Se eliminaron funciones de prueba/no usadas (p. ej. generación de onda
  senoidal) y bloques de código comentados.
- Las ecuaciones lineales de calibración pasaron de strings evaluados con
  `eval` a parámetros explícitos (`slope`, `offset`, `divisor`), más
  seguros y más fáciles de editar.
- API keys e IDs de dispositivo salieron del JSON y ahora viven en `.env`.
- Se agregó una CLI real con subcomandos (`transfer`, `schedule`,
  `setup-nodes`, `delete-channel`, `delete-points`, `download`,
  `show-config`, `gui`) en lugar de un único `main()` con flags
  hardcodeados en el código.

## Gestión de Modal (`sensorcloud-sync`)

Este proyecto se ejecuta de forma automatizada en [Modal.com](https://modal.com) mediante un trigger de tipo Cron (cada 30 minutos).

### 1. Desplegar o Actualizar la App
Cada vez que realices cambios en el código local o en este archivo de configuración, puedes actualizar la aplicación en la nube ejecutando el siguiente comando en tu terminal:

```bash
modal deploy modal_app.py
```

### 2. Pausar la App
Dado que Modal ejecuta funciones programadas (como cron jobs), la forma de "pausar" la sincronización automática es eliminando la aplicación de los servidores de Modal. Esto detendrá inmediatamente las ejecuciones programadas sin perder tu código local:

```bash
modal app stop sensorcloud-sync
```

### 3. Borrar por completo

```bash
modal app delete sensorcloud-sync
```