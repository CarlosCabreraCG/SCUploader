# sensorcloud/config
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import dotenv_values

DEFAULT_ENV_FILE = ".env"
DEFAULT_CONFIG_FILE = "config/sensors_config.json"

PROCESS_METRICS = {"PROM", "PP", "MAX", "MIN", "START", "END"}
_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*_[A-Za-z]+$")
_GROUP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)_(PROM|PP|MAX|MIN|START|END)$")


@dataclass
class Credentials:
    api_key_source: str
    api_key_dest: str
    source_device: str
    dest_device: str
    api_key_dest_simple: Optional[str] = None
    dest_device_simple: Optional[str] = None
    server: str = "sensorcloud.microstrain.com"
    config_file: str = DEFAULT_CONFIG_FILE

    brevo_api_key: Optional[str] = None
    brevo_sender_email: Optional[str] = None
    brevo_sender_name: str = "SensorCloud Alerts"
    alert_recipients: List[str] = field(default_factory=list)


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
    sensors_channel: Dict[str, Dict] = field(default_factory=dict)
    composite_equations: Dict[str, str] = field(default_factory=dict)
    process_equations: Dict[str, str] = field(default_factory=dict)
    output_nodes: Dict[str, str] = field(default_factory=dict)
    limit_values: Dict[str, str] = field(default_factory=dict)
    sensors_table: Dict[str, object] = field(default_factory=dict)
    options: PipelineOptions = field(default_factory=PipelineOptions)

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
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_credentials(env_file: Optional[str] = None) -> List[Credentials]:
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

    raw_configs = _split_list(values.get("CONFIG_FILE"))
    if not raw_configs:
        config_files = [DEFAULT_CONFIG_FILE] * len(source_devices)
    elif len(raw_configs) == 1 and len(source_devices) > 1:
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

    brevo_api_key = values.get("BREVO_API_KEY") or None
    brevo_sender_email = values.get("BREVO_SENDER_EMAIL") or None
    brevo_sender_name = values.get("BREVO_SENDER_NAME", "SensorCloud Alerts")
    alert_recipients = _split_list(values.get("ALERT_RECIPIENTS"))

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
                brevo_api_key=brevo_api_key,
                brevo_sender_email=brevo_sender_email,
                brevo_sender_name=brevo_sender_name,
                alert_recipients=alert_recipients,
            )
        )
    return profiles


def _extract_vars_from_formula(equation: str, where: str) -> List[str]:
    """Devuelve todos los tokens con forma <var>_<METRIC> que aparecen en la fórmula."""
    # Tokenizar por caracteres no identificadores (operadores, espacios, paréntesis)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", equation)
    vars_used: List[str] = []
    for tok in tokens:
        if not _TOKEN_RE.match(tok):
            raise ValueError(
                f"{where}: token '{tok}' inválido. Usa siempre <var>_<METRIC> "
                f"con METRIC ∈ {sorted(PROCESS_METRICS)}."
            )
        var, metric = tok.rsplit("_", 1)
        if metric not in PROCESS_METRICS:
            raise ValueError(
                f"{where}: token '{tok}' tiene métrica '{metric}' no permitida. "
                f"Permitidas: {sorted(PROCESS_METRICS)}."
            )
        if var not in vars_used:
            vars_used.append(var)
    return vars_used


def _validate_process_equations(config: SensorsConfig) -> None:
    known_vars = set(config.channel_to_var.values())

    if not config.process_equations:
        return

    # Grupo <var>_<METRIC>
    for group_name in config.process_equations:
        m = _GROUP_RE.match(group_name)
        if not m:
            raise ValueError(
                f"process_equations['{group_name}']: nombre inválido. "
                f"Debe ser '<var>_<METRIC>' con METRIC ∈ {sorted(PROCESS_METRICS)}."
            )
        principal_var = m.group(1)
        if principal_var not in known_vars:
            raise ValueError(
                f"process_equations['{group_name}']: la variable principal '{principal_var}' "
                f"no existe en sensors_channel."
            )

    # Tokens de cada fórmula
    for group_name, equation in config.process_equations.items():
        used_vars = _extract_vars_from_formula(equation, f"process_equations['{group_name}']")
        for v in used_vars:
            if v not in known_vars:
                raise ValueError(
                    f"process_equations['{group_name}']: la variable '{v}' "
                    f"no existe en sensors_channel."
                )

    # Disjunción con composite_equations
    overlap = set(config.composite_equations) & set(config.process_equations)
    if overlap:
        raise ValueError(
            f"Los siguientes grupos están en composite_equations y process_equations a la vez: "
            f"{sorted(overlap)}"
        )


def load_sensors_config(config_file: Optional[str] = None) -> SensorsConfig:
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

    cfg = SensorsConfig(
        sensors_channel=data["sensors_channel"],
        composite_equations=data["composite_equations"],
        process_equations=data.get("process_equations", {}),
        output_nodes=data["output_nodes"],
        limit_values=data.get("limit_values", {}),
        sensors_table=data.get("sensors_table", {}),
        options=options,
    )
    _validate_process_equations(cfg)
    return cfg