"""
Carga de configuración de la aplicación.

Datos sensibles (API keys, IDs de dispositivo) se leen desde variables de
entorno / archivo .env. Todo lo demás (mapeo de canales, ecuaciones de
calibración, ecuaciones compuestas, nodos destino, opciones del pipeline)
se guarda localmente en un archivo JSON legible ("almacenamiento local").
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

DEFAULT_ENV_FILE = ".env"
DEFAULT_CONFIG_FILE = "config/sensors_config.json"


@dataclass
class Credentials:
    """Datos sensibles del par de dispositivos origen/destino, vía .env."""

    api_key_source: str
    api_key_dest: str
    source_device: str
    dest_device: str
    server: str = "sensorcloud.microstrain.com"


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


def load_credentials(env_file: Optional[str] = None) -> Credentials:
    """Carga las credenciales y los IDs de dispositivo desde el archivo .env."""
    load_dotenv(env_file or DEFAULT_ENV_FILE)

    required = ["API_KEY_SOURCE", "API_KEY_DEST", "SOURCE_DEVICE", "DEST_DEVICE"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise EnvironmentError(
            "Faltan variables de entorno requeridas: "
            f"{', '.join(missing)}. Revisa tu archivo .env (ver .env.example)."
        )

    return Credentials(
        api_key_source=os.environ["API_KEY_SOURCE"],
        api_key_dest=os.environ["API_KEY_DEST"],
        source_device=os.environ["SOURCE_DEVICE"],
        dest_device=os.environ["DEST_DEVICE"],
        server=os.getenv("SENSORCLOUD_SERVER", "sensorcloud.microstrain.com"),
    )


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
