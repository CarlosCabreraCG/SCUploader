"""
Orquestación del flujo principal: descargar datos crudos del dispositivo
origen, aplicar calibración lineal por canal, evaluar las ecuaciones
compuestas, aplicar (opcionalmente) media móvil y demeaning, y finalmente
subir los resultados al dispositivo destino y/o exportarlos a CSV.
"""

from __future__ import annotations

import time
from typing import Optional

from . import datastore
from .client import SensorCloudClient, parse_channel_path
from .config import SensorsConfig
from .csv_export import save_to_csv
from .transforms import (
    apply_centered_moving_average,
    apply_interval_demeaning,
    apply_linear_calibration,
    evaluate_composite_series,
)


def transfer_complex_data_between_devices(
    source_client: SensorCloudClient,
    dest_client: SensorCloudClient,
    config: SensorsConfig,
    minutes_back: Optional[int] = None,
    minutes_back_end: Optional[int] = None,
    upload_to_dest: bool = True,
    export_csv: bool = False,
    csv_output_dir: str = "datos_procesados",
    save_to_store: bool = True,
    data_dir: str = datastore.DEFAULT_DATA_DIR,
) -> None:
    """
    Ejecuta un ciclo completo de sincronización:

    1. Descarga los canales fuente necesarios del dispositivo origen.
    2. Aplica la calibración lineal (slope, offset, divisor) por canal.
    3. Evalúa las ecuaciones compuestas para cada nodo de salida.
    4. Aplica media móvil centrada y/o demeaning por intervalo (opcional).
    5. Sube los resultados al dispositivo destino, los exporta a CSV y/o
       los guarda en el almacenamiento local (`data/`) para que la
       interfaz Flet pueda graficarlos después.
    """
    opts = config.options
    minutes_back = opts.minutes_back if minutes_back is None else minutes_back
    minutes_back_end = opts.minutes_back_end if minutes_back_end is None else minutes_back_end

    end_time_ns = time.time_ns() - int(minutes_back_end * 60 * 1_000_000_000)
    start_time_ns = end_time_ns - int(minutes_back * 60 * 1_000_000_000)

    print(f"\n--- Transfiriendo datos de los últimos {minutes_back} minutos ---")
    print(f"Ventana de tiempo (ns): {start_time_ns} - {end_time_ns}")
    if opts.moving_average:
        print(f"Media móvil activada (ventana={opts.ma_window}, gap_max={opts.ma_max_gap_ns / 1e9:.0f}s)")
    if opts.demeaning:
        print(f"Demeaning por intervalo activado (gap_max={opts.ma_max_gap_ns / 1e9:.0f}s)")

    # ------------------------------------------------------------------
    # PASO 1: Descargar los canales fuente necesarios
    # ------------------------------------------------------------------
    print("\n--- PASO 1: Descargando datos crudos ---")

    needed_vars = set()
    for var_group in config.output_nodes.values():
        for v in var_group.split(","):
            needed_vars.add(v.strip())

    channel_to_var = config.channel_to_var
    var_to_source_channel = {v: ch for ch, v in channel_to_var.items()}

    source_channels_needed = set()
    for var in needed_vars:
        if var in var_to_source_channel:
            source_channels_needed.add(var_to_source_channel[var])
        else:
            print(f"  ⚠ Variable '{var}' no tiene canal fuente asociado en sensors_channel")

    print(f"Canales de origen necesarios: {sorted(source_channels_needed)}")

    raw_data = {}
    for src_channel in source_channels_needed:
        sensor, channel = parse_channel_path(src_channel)
        data = source_client.download_data_xdr(sensor, channel, start_time_ns, end_time_ns)
        raw_data[src_channel] = data or []

    # ------------------------------------------------------------------
    # PASO 2: Calibración lineal por canal
    # ------------------------------------------------------------------
    print("\n--- PASO 2: Aplicando calibración lineal ---")
    calibrated_data = apply_linear_calibration(raw_data, config.channel_calibration)

    var_to_data = {}
    for src_channel, data in calibrated_data.items():
        var = channel_to_var.get(src_channel)
        if var:
            var_to_data[var] = {
                "timestamps": [ts for ts, _ in data],
                "values_dict": {ts: val for ts, val in data},
                "data_points": data,
            }

    # ------------------------------------------------------------------
    # PASO 3: Ecuaciones compuestas + media móvil + demeaning + salida
    # ------------------------------------------------------------------
    print("\n--- PASO 3: Aplicando ecuaciones compuestas y generando salida ---")

    csv_dir = None
    if export_csv:
        import os
        from datetime import datetime

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_dir = f"{csv_output_dir}/ejecucion_{timestamp_str}"
        os.makedirs(csv_dir, exist_ok=True)
        print(f"  📁 Guardando CSVs en: {csv_dir}")

    for dest_channel_path, var_group in config.output_nodes.items():
        sensor_dest, channel_dest = parse_channel_path(dest_channel_path)
        print(f"\nProcesando '{dest_channel_path}' (vars: {var_group}):")

        if var_group not in config.composite_equations:
            print(f"  ✗ No se encontró ecuación compuesta para el grupo '{var_group}'")
            continue

        eq = config.composite_equations[var_group]
        vars_list = [v.strip() for v in var_group.split(",")]
        print(f"  Ecuación: {eq}")

        missing = [v for v in vars_list if v not in var_to_data or not var_to_data[v]["data_points"]]
        if missing:
            print(f"  ✗ Faltan datos para las variables: {missing}")
            continue

        combined_data = evaluate_composite_series(var_to_data, vars_list, eq)
        if not combined_data:
            print("  ✗ No hay timestamps comunes / no se pudieron calcular puntos")
            continue

        print(f"  Puntos calculados: {len(combined_data)}")
        upload_data = combined_data

        if opts.moving_average:
            upload_data = apply_centered_moving_average(
                upload_data, window=opts.ma_window, max_gap_ns=opts.ma_max_gap_ns
            )
            print(f"  Puntos tras media móvil: {len(upload_data)}")
            if not upload_data:
                print("  ⚠ Sin puntos válidos tras aplicar media móvil")
                continue

        if opts.demeaning:
            upload_data = apply_interval_demeaning(upload_data, max_gap_ns=opts.ma_max_gap_ns)
            print(f"  Puntos tras demeaning: {len(upload_data)}")
            if not upload_data:
                print("  ⚠ Sin puntos válidos tras aplicar demeaning")
                continue

        if export_csv and csv_dir:
            import os

            safe_channel = dest_channel_path.replace("/", "_").replace("\\", "_")
            save_to_csv(upload_data, os.path.join(csv_dir, f"{safe_channel}.csv"))

        if save_to_store:
            datastore.append_points(dest_channel_path, upload_data, data_dir=data_dir)

        if upload_to_dest:
            dest_client.upload_data(sensor_dest, channel_dest, upload_data)

    if export_csv and csv_dir:
        print(f"\n  📊 Todos los CSVs guardados en: {csv_dir}")
