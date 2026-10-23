#!/usr/bin/env python3
"""
SensorCloud Sync - CLI
=======================

Herramienta de línea de comandos para sincronizar datos entre dispositivos
de MicroStrain SensorCloud, aplicando calibración lineal y ecuaciones
compuestas definidas en config/sensors_config.json.

Ejemplos:
    python main.py transfer
    python main.py transfer --minutes-back 120 --no-upload --export-csv
    python main.py schedule --interval 60
    python main.py setup-nodes
    python main.py delete-channel --device dest --sensor 16752 --channel ch1
    python main.py delete-points --device dest --sensor 16752 --channel ch1 --minutes-back 60
    python main.py download --device source --sensor 16748 --channel ch1 --minutes-back 60 --output datos.csv
    python main.py show-config

Configura las credenciales en un archivo .env (ver .env.example) y los
canales/ecuaciones en config/sensors_config.json (ver README.md).
"""
# main.py
from __future__ import annotations

import argparse
import sys
import time

from sensorcloud.client import SensorCloudClient, create_sensors_and_channels, parse_channel_path
from sensorcloud.config import Credentials, SensorsConfig, load_credentials, load_sensors_config
from sensorcloud.csv_export import save_to_csv
from sensorcloud.pipeline import transfer_complex_data_between_devices
from sensorcloud.scheduler import run_scheduler


def _build_dest_clients(creds: Credentials) -> SensorCloudClient:
    """Autentica el/los dispositivo(s) destino UNA sola vez.

    De momento solo se devuelve el completo; el simple se añadirá en la parte 2.
    """
    dest_client = SensorCloudClient(
        device_id=creds.dest_device, api_key=creds.api_key_dest, server=creds.server
    )
    dest_client.authenticate()
    return dest_client


def _build_source_client(creds: Credentials) -> SensorCloudClient:
    source_client = SensorCloudClient(
        device_id=creds.source_device, api_key=creds.api_key_source, server=creds.server
    )
    source_client.authenticate()
    return source_client


def _first_env_file(args) -> str | None:
    """Para comandos de un solo dispositivo: usa el primer --env-file (o el default '.env')."""
    env_files = getattr(args, "env_file", None)
    return env_files[0] if env_files else None


def _load_profile(env_file: str | None, config_override: str | None) -> list[tuple[Credentials, SensorsConfig]]:
    """Carga un .env (puede contener varias fuentes) y devuelve un par por fuente."""
    profiles = load_credentials(env_file)
    result: list[tuple[Credentials, SensorsConfig]] = []
    for creds in profiles:
        cfg = load_sensors_config(config_override or creds.config_file)
        result.append((creds, cfg))
    return result


def _load_all(args) -> tuple[Credentials, SensorsConfig]:
    """Para comandos de un solo dispositivo: primer perfil del primer .env."""
    profiles = _load_profile(_first_env_file(args), getattr(args, "config", None))
    return profiles[0]


def _load_profiles(args) -> list[tuple[Credentials, SensorsConfig]]:
    """Un (Credentials, SensorsConfig) por fuente, agregando todos los .env indicados."""
    env_files = getattr(args, "env_file", None) or [None]
    all_profiles: list[tuple[Credentials, SensorsConfig]] = []
    for ef in env_files:
        all_profiles.extend(_load_profile(ef, getattr(args, "config", None)))
    return all_profiles


def _client_for_device(creds: Credentials, device: str) -> SensorCloudClient:
    if device == "source":
        return _build_source_client(creds)
    return _build_dest_clients(creds)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_transfer(args) -> None:
    profiles = _load_profiles(args)
    if not profiles:
        print("No hay perfiles para procesar.")
        return

    # Destino compartido: se autentica UNA sola vez.
    dest_creds = profiles[0][0]
    dest_client = _build_dest_clients(dest_creds)
    print(f"\n=== Destino: {dest_creds.dest_device} (autenticado una vez) ===")

    for creds, config in profiles:
        print(f"\n--- Fuente: {creds.source_device} (config: {creds.config_file}) ---")
        source_client = _build_source_client(creds)
        transfer_complex_data_between_devices(
            source_client, dest_client, config,
            minutes_back=args.minutes_back,
            minutes_back_end=args.minutes_back_end,
            upload_to_dest=not args.no_upload,
            export_csv=args.export_csv,
            csv_output_dir=args.output_dir,
            save_to_store=args.store,
            data_dir=args.data_dir,
            from_last_point=args.fromlastpoint,
        )
    print(f"\n--- Proceso completado exitosamente ({len(profiles)} fuente(s)) ---")


def cmd_schedule(args) -> None:
    profiles = _load_profiles(args)
    if not profiles:
        print("No hay perfiles para procesar.")
        return

    dest_creds = profiles[0][0]
    dest_client = _build_dest_clients(dest_creds)

    threads = []
    for creds, config in profiles:
        source_client = _build_source_client(creds)
        interval = args.interval or config.options.interval_scheduler
        thread = run_scheduler(
            source_client, dest_client, config,
            interval_seconds=args.interval,
            minutes_back=args.minutes_back,
            minutes_back_end=args.minutes_back_end,
            from_last_point=args.fromlastpoint,
        )
        threads.append(thread)
        print(f"  Scheduler iniciado: {creds.source_device} -> {creds.dest_device} cada {interval}s (config: {creds.config_file})")

    print(f"\n{len(threads)} scheduler(s) corriendo. Presiona Ctrl+C para detener.")
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo scheduler(s)...")


def cmd_setup_nodes(args) -> None:
    creds, config = _load_all(args)
    dest_client = _client_for_device(creds, "dest")
    create_sensors_and_channels(dest_client, list(config.output_nodes.keys()))
    print("\n--- Nodos creados en el dispositivo destino ---")


def cmd_delete_channel(args) -> None:
    creds, _ = _load_all(args)
    client = _client_for_device(creds, args.device)
    client.delete_channel(args.sensor, args.channel)


def cmd_delete_points(args) -> None:
    creds, _ = _load_all(args)
    client = _client_for_device(creds, args.device)
    client.delete_data_points(args.sensor, args.channel, minutes_back=args.minutes_back)


def cmd_download(args) -> None:
    creds, _ = _load_all(args)
    client = _client_for_device(creds, args.device)

    end_time_ns = time.time_ns()
    start_time_ns = end_time_ns - int(args.minutes_back * 60 * 1_000_000_000)

    data = client.download_data_xdr(args.sensor, args.channel, start_time_ns, end_time_ns)
    if not data:
        print("No se descargaron datos.")
        return
    save_to_csv(data, args.output)


def cmd_show_config(args) -> None:
    profiles = _load_profiles(args)
    for creds, config in profiles:
        opts = config.options
        print(f"\n=== Perfil: fuente {creds.source_device} -> destino {creds.dest_device} ===")
        print(f"  Servidor:            {creds.server}")
        print(f"  Config file:         {creds.config_file}")
        print(f"  Destino simple:      {creds.dest_device_simple or '(no configurado)'}")
        print(f"  Canales fuente:      {list(config.sensors_channel.keys())}")
        print(f"  Ecuaciones comp.:    {list(config.composite_equations.keys())}")
        print(f"  Nodos destino:       {list(config.output_nodes.keys())}")
        print(f"  Minutos hacia atrás: {opts.minutes_back} (fin: {opts.minutes_back_end})")
        print(f"  Intervalo scheduler: {opts.interval_scheduler}s")
        print(f"  Media móvil:         {opts.moving_average} (ventana={opts.ma_window})")
        print(f"  Demeaning:           {opts.demeaning}")
        print(f"  Gap máximo:          {opts.ma_max_gap_minutes} min")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Sincroniza y transforma datos entre dispositivos de SensorCloud.",
    )
    parser.add_argument(
        "--env-file", action="append", default=None,
        help="Ruta a un archivo .env (default: .env). Repetible para combinar varios .env.",
    )
    parser.add_argument("--config", default=None, help="Ruta al JSON de configuración; sobreescribe el CONFIG_FILE de cada .env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_transfer = subparsers.add_parser("transfer", help="Ejecuta una transferencia única de datos.")
    p_transfer.add_argument("--fromlastpoint", action="store_true", help="Usa el último punto ya subido en cada canal destino como inicio de la ventana, en vez de minutes_back/minutes_back_end (evita huecos y resubidas innecesarias).")
    p_transfer.add_argument("--minutes-back", type=int, default=None, help="Minutos hacia atrás a descargar (default: valor en config)")
    p_transfer.add_argument("--minutes-back-end", type=int, default=None, help="Offset en minutos para el fin de la ventana (default: valor en config)")
    p_transfer.add_argument("--no-upload", action="store_true", help="No subir los resultados al dispositivo destino")
    p_transfer.add_argument("--export-csv", action="store_true", help="Exportar los resultados a CSV localmente")
    p_transfer.add_argument("--output-dir", default="datos_procesados", help="Directorio donde guardar los CSV exportados")
    p_transfer.add_argument("--store", action="store_true", help="Guardar los resultados en el almacenamiento local usado por la interfaz Flet")
    p_transfer.add_argument("--data-dir", default="data", help="Directorio del almacenamiento local para la interfaz Flet (default: data/)")
    p_transfer.set_defaults(func=cmd_transfer)

    # schedule
    p_schedule = subparsers.add_parser("schedule", help="Ejecuta la transferencia periódicamente (scheduler).")
    p_schedule.add_argument("--fromlastpoint", action="store_true", help="En cada ciclo, usa el último punto ya subido en cada canal destino como inicio, en vez de una ventana fija.")
    p_schedule.add_argument("--interval", type=int, default=None, help="Intervalo en segundos entre ejecuciones (default: valor en config)")
    p_schedule.add_argument("--minutes-back", type=int, default=None, help="Minutos hacia atrás a descargar en cada ciclo")
    p_schedule.add_argument("--minutes-back-end", type=int, default=None, help="Offset en minutos para el fin de la ventana")
    p_schedule.set_defaults(func=cmd_schedule)

    # setup-nodes
    p_setup = subparsers.add_parser("setup-nodes", help="Crea los sensores/canales de salida en el dispositivo destino.")
    p_setup.set_defaults(func=cmd_setup_nodes)

    # delete-channel
    p_del_ch = subparsers.add_parser("delete-channel", help="Elimina un canal de un sensor.")
    p_del_ch.add_argument("--device", choices=["source", "dest"], default="dest", help="Dispositivo objetivo (default: dest)")
    p_del_ch.add_argument("--sensor", required=True, help="Nombre del sensor")
    p_del_ch.add_argument("--channel", required=True, help="Nombre del canal")
    p_del_ch.set_defaults(func=cmd_delete_channel)

    # delete-points
    p_del_pts = subparsers.add_parser("delete-points", help="Elimina puntos de datos recientes de un canal.")
    p_del_pts.add_argument("--device", choices=["source", "dest"], default="dest", help="Dispositivo objetivo (default: dest)")
    p_del_pts.add_argument("--sensor", required=True, help="Nombre del sensor")
    p_del_pts.add_argument("--channel", required=True, help="Nombre del canal")
    p_del_pts.add_argument("--minutes-back", type=int, default=60, help="Minutos hacia atrás a eliminar (default: 60)")
    p_del_pts.set_defaults(func=cmd_delete_points)

    # download
    p_download = subparsers.add_parser("download", help="Descarga datos crudos de un canal y los guarda en CSV.")
    p_download.add_argument("--device", choices=["source", "dest"], default="source", help="Dispositivo objetivo (default: source)")
    p_download.add_argument("--sensor", required=True, help="Nombre del sensor")
    p_download.add_argument("--channel", required=True, help="Nombre del canal")
    p_download.add_argument("--minutes-back", type=int, default=60, help="Minutos hacia atrás a descargar (default: 60)")
    p_download.add_argument("--output", default="descarga.csv", help="Ruta del archivo CSV de salida")
    p_download.set_defaults(func=cmd_download)

    # show-config
    p_show = subparsers.add_parser("show-config", help="Muestra un resumen de la configuración cargada.")
    p_show.set_defaults(func=cmd_show_config)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()