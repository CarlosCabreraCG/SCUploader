"""Exportación de series de tiempo a archivos CSV."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import List, Tuple

Point = Tuple[int, float]


def save_to_csv(data_points: List[Point], output_path: str, include_metadata: bool = True) -> None:
    """
    Guarda una lista de (timestamp_ns, valor) en un archivo CSV con columnas
    timestamp_ns, timestamp_iso, valor. Opcionalmente añade un encabezado
    con metadatos (fecha de generación, total de puntos, rango de tiempo).
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        if include_metadata:
            writer.writerow(["# Archivo generado:", datetime.now().isoformat()])
            writer.writerow(["# Total puntos:", len(data_points)])
            if data_points:
                writer.writerow(
                    [
                        "# Rango timestamps:",
                        f"{data_points[0][0]} - {data_points[-1][0]}",
                        f"({(data_points[-1][0] - data_points[0][0]) / 1e9:.1f} segundos)",
                    ]
                )
            writer.writerow(["#"])

        writer.writerow(["timestamp_ns", "timestamp_iso", "valor"])
        for ts, val in data_points:
            dt_str = datetime.fromtimestamp(ts / 1e9).isoformat()
            writer.writerow([ts, dt_str, f"{val:.6f}"])

    print(f"  ✓ CSV guardado: {output_path} ({len(data_points)} puntos)")
