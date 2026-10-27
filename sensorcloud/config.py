"""
Carga de configuración de la aplicación.

Datos sensibles (API keys, IDs de dispositivo) se leen desde variables de
entorno / archivo .env. Todo lo demás (mapeo de canales, ecuaciones de
calibración, ecuaciones compuestas, nodos destino, opciones del pipeline)
se guarda localmente en un archivo JSON legible ("almacenamiento local").
"""
# sensorcloud/config
# sensorcloud/config
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import dotenv_values

DEFAULT_ENV_FILE = ".env"
DEFAULT_CONFIG_FILE = "config/sensors_config.json"


@dataclass
class Credentials:
    """Credenciales de un perfil.

    `source_device` / `api_key_source` / `config_file` identifican UNA fuente.
    Los destinos (`dest_device`, `dest_device_simple`, `api_key_dest`) son
    compartidos por todas las fuentes del mismo .env.
    """

    api_key_source: str
    api_key_dest: str
    source_device: str
    dest_device: str
    api_key_dest_simple: Optional[str] = None
    dest_device_simple: Optional[str] = None
    server: str = "sensorcloud.microstrain.com"
    config_file: str = DEFAULT_CONFIG_FILE


@dataclass
class PipelineOptions:
    minutes_back: int = 60
    minutes_back_end: int = 0
    interval_scheduler: int = 60
    moving_average: bool = False
    demeaning: bool = False
    ma_window: int = 5
    ma_max_gap_minutes: float = 1.0

    @property
    def ma_max_gap_ns(self) -> int:
        return int(self.ma_max_gap_minutes * 60 * 1_000_000_000)


@dataclass
class SensorsConfig:
    """
    Almacenamiento local (no sensible) de la app:

    - sensors_channel : canal fuente -> {var, slope, offset, divisor}
    - composite_equations : grupo de variables -> ecuación compuesta
    - output_nodes : canal destino -> grupo de variables
    - options : parámetros del pipeline (ventanas de tiempo, media móvil, etc.)
    """

    sensors_channel: Dict[str, Dict] = field(default_factory=dict)
    composite_equations: Dict[str, str] = field(default_factory=dict)
    output_nodes: Dict[str, str] = field(default_factory=dict)
    options: PipelineOptions = field(default_factory=PipelineOptions)

    # -- Vistas derivadas, usadas por el pipeline --------------------------

    @property
    def channel_to_var(self) -> Dict[str, str]:
        return {ch: info["var"] for ch, info in self.sensors_channel.items()}

    @property
    def channel_calibration(self) -> Dict[str, Dict[str, float]]:
        return {
            ch: {"slope": info["slope"], "offset": info["offset"], "divisor": info.get("divisor", 1)}
            for ch, info in self.sensors_channel.items()
        }


def _split_list(raw: Optional[str]) -> List[str]:
    """'a,b, c' -> ['a', 'b', 'c']. Cadena vacía / None -> []."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_credentials(env_file: Optional[str] = None) -> List[Credentials]:
    """Carga TODOS los perfiles de un .env.

    Un solo .env puede declarar varias fuentes en listas paralelas
    (separadas por comas):

        API_KEY_SOURCE=key1,key2
        SOURCE_DEVICE=dev1,dev2
        CONFIG_FILE=config/a.json,config/b.json

    Los destinos son compartidos:

        API_KEY_DEST=...
        DEST_DEVICE=...
        DEST_DEVICE_SIMPLE=...   (opcional)

    Devuelve una lista de `Credentials`, una por fuente.
    """
    path = env_file or DEFAULT_ENV_FILE
    file_values = dotenv_values(path)
    values = {**os.environ, **file_values}

    required = ["API_KEY_DEST", "DEST_DEVICE"]
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise EnvironmentError(
            "Faltan variables de entorno requeridas: "
            f"{', '.join(missing)}. Revisa el archivo '{path}' (ver .env.example)."
        )

    api_keys_source = _split_list(values.get("API_KEY_SOURCE"))
    source_devices = _split_list(values.get("SOURCE_DEVICE"))

    if not api_keys_source or not source_devices:
        raise EnvironmentError(
            f"'{path}' debe declarar API_KEY_SOURCE y SOURCE_DEVICE (listas separadas por comas)."
        )
    if len(api_keys_source) != len(source_devices):
        raise EnvironmentError(
            f"En '{path}', API_KEY_SOURCE tiene {len(api_keys_source)} elemento(s) "
            f"pero SOURCE_DEVICE tiene {len(source_devices)}. Deben coincidir."
        )

    # CONFIG_FILE es opcional por fuente. Si no aparece, se usa el default.
    raw_configs = _split_list(values.get("CONFIG_FILE"))
    if not raw_configs:
        config_files = [DEFAULT_CONFIG_FILE] * len(source_devices)
    elif len(raw_configs) == 1 and len(source_devices) > 1:
        # Un solo CONFIG_FILE para varias fuentes: se replica.
        config_files = raw_configs * len(source_devices)
    elif len(raw_configs) != len(source_devices):
        raise EnvironmentError(
            f"En '{path}', CONFIG_FILE tiene {len(raw_configs)} elemento(s) "
            f"pero hay {len(source_devices)} fuente(s). Deben coincidir (o dejar uno solo)."
        )
    else:
        config_files = raw_configs

    server = values.get("SENSORCLOUD_SERVER", "sensorcloud.microstrain.com")
    api_key_dest = values["API_KEY_DEST"]
    api_key_dest_simple = values.get("API_KEY_DEST_SIMPLE") or None
    dest_device = values["DEST_DEVICE"]
    dest_device_simple = values.get("DEST_DEVICE_SIMPLE") or None

    profiles: List[Credentials] = []
    for api_key_src, src_dev, cfg in zip(api_keys_source, source_devices, config_files):
        profiles.append(
            Credentials(
                api_key_source=api_key_src,
                api_key_dest=api_key_dest,
                api_key_dest_simple=api_key_dest_simple,
                source_device=src_dev,
                dest_device=dest_device,
                dest_device_simple=dest_device_simple,
                server=server,
                config_file=cfg,
            )
        )
    return profiles


def load_sensors_config(config_file: Optional[str] = None) -> SensorsConfig:
    """Carga el archivo JSON con el mapeo de canales, ecuaciones y opciones."""
    path = Path(config_file or DEFAULT_CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de configuración: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for section in ("sensors_channel", "composite_equations", "output_nodes"):
        if section not in data:
            raise ValueError(f"El archivo de configuración debe incluir la sección '{section}'.")

    options_data = data.get("options", {})
    options = PipelineOptions(
        minutes_back=options_data.get("minutes_back", 60),
        minutes_back_end=options_data.get("minutes_back_end", 0),
        interval_scheduler=options_data.get("interval_scheduler", 60),
        moving_average=options_data.get("moving_average", False),
        demeaning=options_data.get("demeaning", False),
        ma_window=options_data.get("ma_window", 5),
        ma_max_gap_minutes=options_data.get("ma_max_gap_minutes", 1.0),
    )

    return SensorsConfig(
        sensors_channel=data["sensors_channel"],
        composite_equations=data["composite_equations"],
        output_nodes=data["output_nodes"],
        options=options,
    )
