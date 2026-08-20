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

Incluye además una pequeña interfaz gráfica (Flet) para **visualizar**
esos datos ya guardados: selección de canales con checkboxes, rango de
fecha/hora, gráfico interactivo y descarga a Excel.

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
│   └── csv_export.py            # Exportación de series a CSV
│   ├── datastore.py              # Almacenamiento local (CSV por canal) que lee la GUI
│   └── gui.py                    # Interfaz Flet (visor de gráficos + descarga a Excel)
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

Copia `.env.example` a `.env` y completa tus credenciales reales:

```bash
cp .env.example .env
```

```ini
API_KEY_SOURCE=tu_api_key_origen
API_KEY_DEST=tu_api_key_destino
SOURCE_DEVICE=W020000000198806
DEST_DEVICE=OAPI007627ZU9AWY
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

## Uso (CLI)

Todos los comandos se ejecutan como `python main.py <comando> [opciones]`.

### Transferencia única

```bash
python main.py transfer
python main.py transfer --minutes-back 120
python main.py transfer --no-upload --export-csv --output-dir salida
```

### Ejecución periódica (scheduler)

```bash
python main.py schedule
python main.py schedule --interval 30
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
```

### Eliminar puntos de datos recientes

```bash
python main.py delete-points --device dest --sensor 16752 --channel ch1 --minutes-back 60
```

### Descargar datos crudos de un canal a CSV

```bash
python main.py download --device source --sensor 16748 --channel ch1 --minutes-back 60 --output datos.csv
```

### Ver la configuración cargada

```bash
python main.py show-config
```

## Interfaz gráfica (Flet)

`python main.py transfer` y `python main.py schedule` guardan automáticamente
los resultados de cada nodo de salida en `data/<canal>.csv` (histórico
local, además de subirlos a SensorCloud). La interfaz gráfica **solo lee
esos archivos**: no vuelve a llamar a la API, así que ábrela después de
haber corrido al menos un `transfer`.

```bash
python main.py gui
```

Esto abre una ventana de escritorio con:

- **Canales**: un checkbox por cada canal con datos guardados en `data/`
  (con un botón para recargar la lista si corriste otro `transfer` mientras
  la interfaz estaba abierta).
- **Rango de tiempo**: selectores de fecha y hora para el inicio y el fin
  del período a graficar (por defecto, todo el rango disponible).
- **Graficar**: dibuja un gráfico de líneas interactivo (con tooltip al
  pasar el mouse) con los canales seleccionados en ese rango.
- **Descargar Excel**: guarda los datos actualmente graficados en un
  archivo `.xlsx` (una hoja "Combinado" con todos los canales alineados
  por tiempo, más una hoja por canal), eligiendo la ubicación con el
  diálogo nativo de guardado.

Para abrirla en el navegador en vez de como ventana de escritorio:

```bash
python main.py gui --web
```

Para visualizar un directorio de datos distinto al por defecto (`data/`):

```bash
python main.py gui --data-dir otra_carpeta
```

> `flet`, `flet-web` (solo si usas `--web`) y `openpyxl` son necesarios
> únicamente para este comando; el resto de la CLI funciona sin ellos.

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
- Se agregó un almacenamiento local por canal (`data/`) que alimenta la
  nueva interfaz gráfica en Flet, para visualizar y exportar a Excel sin
  volver a consultar la API.
