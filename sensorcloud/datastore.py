"""
Almacenamiento local de series de tiempo ya procesadas.

Cada vez que el pipeline calcula los datos de un nodo de salida (después
de calibración, ecuación compuesta, media móvil y demeaning), además de
subirlos a SensorCloud puede guardarlos aquí, en un CSV por canal dentro
de `data/`. La interfaz Flet lee de este almacenamiento local para
graficar: no vuelve a llamar a la API de SensorCloud.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime
from typing import Dict, List, Tuple

Point = Tuple[int, float]

DEFAULT_DATA_DIR = "data"


def _safe_filename(channel: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", channel) + ".csv"


def _channel_path(channel: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    return os.path.join(data_dir, _safe_filename(channel))


def _read_existing(path: str) -> Dict[int, float]:
    points: Dict[int, float] = {}
    if not os.path.exists(path):
        return points
    with open(path, "r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # encabezado
        for row in reader:
            if not row or len(row) < 3:
                continue
            try:
                points[int(row[0])] = float(row[2])
            except ValueError:
                continue
    return points


def append_points(channel: str, points: List[Point], data_dir: str = DEFAULT_DATA_DIR) -> str:
    """
    Agrega (o actualiza, si el timestamp ya existe) puntos al archivo local
    del canal indicado. Devuelve la ruta del archivo.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = _channel_path(channel, data_dir)

    existing = _read_existing(path)
    for ts, val in points:
        existing[int(ts)] = float(val)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ns", "timestamp_iso", "valor"])
        for ts in sorted(existing.keys()):
            dt_str = datetime.fromtimestamp(ts / 1e9).isoformat()
            writer.writerow([ts, dt_str, f"{existing[ts]:.6f}"])

    return path


def load_points(
    channel: str,
    start_ns: int = None,
    end_ns: int = None,
    data_dir: str = DEFAULT_DATA_DIR,
) -> List[Point]:
    """Carga los puntos guardados de un canal, opcionalmente filtrando por rango de tiempo."""
    path = _channel_path(channel, data_dir)
    existing = _read_existing(path)
    result = [(ts, val) for ts, val in existing.items() if (start_ns is None or ts >= start_ns) and (end_ns is None or ts <= end_ns)]
    result.sort(key=lambda p: p[0])
    return result


def list_channels(data_dir: str = DEFAULT_DATA_DIR) -> List[str]:
    """Lista los nombres de canal (sin extensión) que tienen datos guardados localmente."""
    if not os.path.isdir(data_dir):
        return []
    return sorted(f[:-4] for f in os.listdir(data_dir) if f.endswith(".csv"))
