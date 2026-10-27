"""
Orquestación del flujo principal: descargar datos crudos del dispositivo
origen, aplicar calibración lineal por canal, evaluar las ecuaciones
compuestas, aplicar (opcionalmente) media móvil y demeaning, y finalmente
subir los resultados al dispositivo destino y/o exportarlos a CSV.
"""
# sensorcloud/pipeline.py
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
    summarize_intervals,
)


def _latest_for_channel(
    dest_client: Optional[SensorCloudClient],
    dest_client_simple: Optional[SensorCloudClient],
    dest_channel_path: str,
) -> Optional[int]:
    """Último timestamp subido para un canal destino.

    Prioriza el dispositivo completo; si no tiene datos todavía, consulta el
    simple. Devuelve None si ninguno tiene datos.
    """
    sensor, channel = parse_channel_path(dest_channel_path)

    if dest_client is not None:
        latest = dest_client.get_latest_data_point(sensor, channel)
        if latest:
            return latest[0]

    if dest_client_simple is not None:
        latest = dest_client_simple.get_latest_data_point(sensor, channel)
        if latest:
            return latest[0]

    return None


def transfer_complex_data_between_devices(
    source_client: SensorCloudClient,
    dest_client: Optional[SensorCloudClient],
    config: SensorsConfig,
    minutes_back: Optional[int] = None,
    minutes_back_end: Optional[int] = None,
    upload_to_dest: bool = True,
    upload_to_simple: bool = False,
    dest_client_simple: Optional[SensorCloudClient] = None,
    export_csv: bool = False,
    csv_output_dir: str = "datos_procesados",
    save_to_store: bool = True,
    data_dir: str = datastore.DEFAULT_DATA_DIR,
    from_last_point: bool = False,
) -> None:
    """
    Ejecuta un ciclo completo de sincronización:

    1. Descarga los canales fuente necesarios del dispositivo origen.
    2. Aplica la calibración lineal (slope, offset, divisor) por canal.
    3. Evalúa las ecuaciones compuestas para cada nodo de salida.
    4. Aplica media móvil centrada y/o demeaning por intervalo (opcional).
    5. Sube los resultados al dispositivo destino (completo y, si se pide,
       reducido por intervalo al dispositivo simple), los exporta a CSV
       y/o los guarda en el almacenamiento local (`data/`).
    """
    opts = config.options
    minutes_back = opts.minutes_back if minutes_back is None else minutes_back
    minutes_back_end = opts.minutes_back_end if minutes_back_end is None else minutes_back_end

    end_time_ns = time.time_ns() - int(minutes_back_end * 60 * 1_000_000_000)

    last_ts_by_channel: dict = {}
    if from_last_point:
        print("\n--- Modo 'desde el último punto': consultando último dato en destino ---")
        for dest_channel_path in config.output_nodes:
            latest_ts = _latest_for_channel(dest_client, dest_client_simple, dest_channel_path)
            last_ts_by_channel[dest_channel_path] = latest_ts
            if latest_ts:
                print(f"  {dest_channel_path}: último punto en {latest_ts}")
            else:
                print(f"  {dest_channel_path}: sin datos previos, se usará minutes_back={minutes_back} como respaldo")

        known_starts = [ts for ts in last_ts_by_channel.values() if ts is not None]
        start_time_ns = min(known_starts) if known_starts else end_time_ns - int(minutes_back * 60 * 1_000_000_000)
    else:
        start_time_ns = end_time_ns - int(minutes_back * 60 * 1_000_000_000)

    print(f"\n--- Transfiriendo datos de los últimos {minutes_back} minutos antes de {minutes_back_end} minutos ---")
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

        if from_last_point:
            threshold = last_ts_by_channel.get(dest_channel_path)
            if threshold is not None:
                before = len(upload_data)
                upload_data = [(ts, val) for ts, val in upload_data if ts > threshold]
                if len(upload_data) != before:
                    print(f"  Filtrados {before - len(upload_data)} puntos ya existentes (<= último punto subido)")
                if not upload_data:
                    print("  ⚠ No hay puntos nuevos después del último punto ya subido; se omite este canal")
                    continue

        # Serie reducida (por intervalo) para el dispositivo simple
        summary_data: list = []
        if upload_to_simple and dest_client_simple is not None and upload_data:
            summary_data = summarize_intervals(upload_data, opts.ma_max_gap_ns)
            print(f"  Puntos resumen (primer/máx/mín/último por intervalo): {len(summary_data)}")

        if export_csv and csv_dir:
            import os

            safe_channel = dest_channel_path.replace("/", "_").replace("\\", "_")
            save_to_csv(upload_data, os.path.join(csv_dir, f"{safe_channel}.csv"))
            if summary_data:
                save_to_csv(summary_data, os.path.join(csv_dir, f"{safe_channel}__simple.csv"))

        if save_to_store:
            datastore.append_points(dest_channel_path, upload_data, data_dir=data_dir)
            if summary_data:
                datastore.append_points(f"{dest_channel_path}__simple", summary_data, data_dir=data_dir)

        if upload_to_dest and dest_client is not None and upload_data:
            dest_client.upload_data(sensor_dest, channel_dest, upload_data)

        if upload_to_simple and dest_client_simple is not None and summary_data:
            dest_client_simple.upload_data(sensor_dest, channel_dest, summary_data)

    if export_csv and csv_dir:
        print(f"\n  📊 Todos los CSVs guardados en: {csv_dir}")
