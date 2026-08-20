"""Ejecución periódica del pipeline de transferencia."""

from __future__ import annotations

import threading
import time
from typing import Optional

from .client import SensorCloudClient
from .config import SensorsConfig
from .pipeline import transfer_complex_data_between_devices


def run_scheduler(
    source_client: SensorCloudClient,
    dest_client: SensorCloudClient,
    config: SensorsConfig,
    interval_seconds: Optional[int] = None,
    minutes_back: Optional[int] = None,
    minutes_back_end: Optional[int] = None,
) -> threading.Thread:
    """Lanza un hilo en segundo plano que ejecuta el pipeline cada `interval_seconds`."""
    interval_seconds = interval_seconds or config.options.interval_scheduler

    def job():
        while True:
            start = time.time()
            try:
                transfer_complex_data_between_devices(
                    source_client,
                    dest_client,
                    config,
                    minutes_back=minutes_back,
                    minutes_back_end=minutes_back_end,
                )
            except Exception as e:
                print(f"✗ Error en transferencia: {e}")
            elapsed = time.time() - start
            sleep_time = max(0, interval_seconds - elapsed)
            print(f"  Próxima ejecución en {sleep_time:.1f}s (ejecución tomó {elapsed:.1f}s)")
            time.sleep(sleep_time)

    thread = threading.Thread(target=job, daemon=True)
    thread.start()
    return thread
