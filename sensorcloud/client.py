"""
Cliente HTTP para la API de MicroStrain SensorCloud.

Consolida en una sola clase la autenticación, gestión de sensores/canales
y subida/descarga/borrado de datos, que antes estaba duplicada casi
idénticamente en tres scripts distintos del proyecto.
"""

from __future__ import annotations

import http.client
import time
from typing import Dict, List, Optional, Tuple

import xdrlib3 as xdrlib


class SensorCloudError(Exception):
    """Error genérico al comunicarse con la API de SensorCloud."""


class SensorCloudClient:
    """
    Cliente para interactuar con la API de SensorCloud.

    Maneja autenticación, creación de sensores/canales, subida de datos
    y descarga/borrado de datos existentes.
    """

    HERTZ = 1
    SECONDS = 0

    def __init__(self, device_id: str, api_key: str, server: str = "sensorcloud.microstrain.com"):
        self.device_id = device_id
        self.api_key = api_key
        self.auth_server = server
        self.auth_token: Optional[str] = None
        self.server: Optional[str] = None
        self.is_authenticated = False

    # ------------------------------------------------------------------
    # Conexión / autenticación
    # ------------------------------------------------------------------

    def _get_connection(self, server: Optional[str] = None) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection(server or self.auth_server)

    def _check_authenticated(self) -> None:
        if not self.is_authenticated:
            raise RuntimeError("Cliente no autenticado. Llama a authenticate() primero.")

    def authenticate(self) -> bool:
        """Autentica contra SensorCloud y obtiene el auth_token y el servidor asignado."""
        conn = self._get_connection()
        headers = {"Accept": "application/xdr"}
        url = f"/SensorCloud/devices/{self.device_id}/authenticate/?version=1&key={self.api_key}"

        print(f"Autenticando con SensorCloud (dispositivo: {self.device_id})...")
        conn.request("GET", url=url, headers=headers)
        response = conn.getresponse()

        if response.status == http.client.OK:
            data = response.read()
            unpacker = xdrlib.Unpacker(data)
            self.auth_token = unpacker.unpack_string().decode("utf-8")
            self.server = unpacker.unpack_string().decode("utf-8")
            self.is_authenticated = True
            print(f"✓ Autenticación exitosa para dispositivo {self.device_id}")
            return True

        error_msg = response.read().decode("utf-8", errors="ignore")
        raise SensorCloudError(f"Error de autenticación ({response.status}): {error_msg}")

    # ------------------------------------------------------------------
    # Gestión de sensores / canales
    # ------------------------------------------------------------------

    def add_sensor(self, sensor_name: str, sensor_type: str = "", sensor_label: str = "", sensor_desc: str = "") -> bool:
        self._check_authenticated()
        conn = self._get_connection(self.server)
        url = f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}/?version=1&auth_token={self.auth_token}"
        headers = {"Content-type": "application/xdr"}

        packer = xdrlib.Packer()
        packer.pack_int(1)
        packer.pack_string(sensor_type.encode("utf-8"))
        packer.pack_string(sensor_label.encode("utf-8"))
        packer.pack_string(sensor_desc.encode("utf-8"))

        print(f"  Creando sensor '{sensor_name}'...")
        conn.request("PUT", url=url, body=packer.get_buffer(), headers=headers)
        response = conn.getresponse()

        if response.status == http.client.CREATED:
            print(f"  ✓ Sensor '{sensor_name}' creado exitosamente")
            return True
        print(f"  ✗ Error creando sensor: {response.read().decode('utf-8', errors='ignore')}")
        return False

    def add_channel(self, sensor_name: str, channel_name: str, channel_label: str = "", channel_desc: str = "") -> bool:
        self._check_authenticated()
        conn = self._get_connection(self.server)
        url = (
            f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}"
            f"/channels/{channel_name}/?version=1&auth_token={self.auth_token}"
        )
        headers = {"Content-type": "application/xdr"}

        packer = xdrlib.Packer()
        packer.pack_int(1)
        packer.pack_string(channel_label.encode("utf-8"))
        packer.pack_string(channel_desc.encode("utf-8"))

        print(f"  Creando canal '{channel_name}' en sensor '{sensor_name}'...")
        conn.request("PUT", url=url, body=packer.get_buffer(), headers=headers)
        response = conn.getresponse()

        if response.status == http.client.CREATED:
            print(f"  ✓ Canal '{channel_name}' creado exitosamente")
            return True
        print(f"  ✗ Error creando canal: {response.read().decode('utf-8', errors='ignore')}")
        return False

    def delete_channel(self, sensor_name: str, channel_name: str = "ch1") -> bool:
        self._check_authenticated()
        conn = self._get_connection(self.server)
        url = (
            f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}"
            f"/channels/{channel_name}/?version=1&auth_token={self.auth_token}"
        )
        headers = {"Accept": "application/xdr"}

        print(f"  Eliminando canal '{channel_name}' del sensor '{sensor_name}'...")
        conn.request("DELETE", url=url, headers=headers)
        response = conn.getresponse()

        if response.status in (http.client.OK, http.client.NO_CONTENT):
            print(f"  ✓ Canal '{channel_name}' eliminado")
            return True
        print(f"  ✗ Error eliminando canal: {response.status} {response.reason}")
        return False

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------

    def upload_data(
        self,
        sensor_name: str,
        channel_name: str,
        data_points: List[Tuple[int, float]],
        sample_rate: int = 10,
        sample_rate_type: Optional[int] = None,
    ) -> bool:
        """Sube una lista de puntos (timestamp_ns, valor) a un canal."""
        self._check_authenticated()
        if sample_rate_type is None:
            sample_rate_type = self.HERTZ
        if not data_points:
            print(f"  No hay datos para subir a {sensor_name}/{channel_name}")
            return False

        conn = self._get_connection(self.server)
        url = (
            f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}"
            f"/channels/{channel_name}/streams/timeseries/data/?version=1&auth_token={self.auth_token}"
        )

        packer = xdrlib.Packer()
        packer.pack_int(1)
        packer.pack_enum(sample_rate_type)
        packer.pack_int(sample_rate)
        packer.pack_int(len(data_points))

        print(f"  Subiendo {len(data_points)} puntos a {sensor_name}/{channel_name}...")
        for ts, value in data_points:
            packer.pack_hyper(int(ts))
            packer.pack_float(float(value))

        headers = {"Content-type": "application/xdr"}
        conn.request("POST", url=url, body=packer.get_buffer(), headers=headers)
        response = conn.getresponse()

        if response.status == http.client.CREATED:
            print(f"  ✓ {len(data_points)} puntos subidos exitosamente")
            return True
        print(f"  ✗ Error subiendo datos: {response.read().decode('utf-8', errors='ignore')}")
        return False

    def download_data_xdr(
        self, sensor_name: str, channel_name: str, start_time_ns: int, end_time_ns: int
    ) -> List[Tuple[int, float]]:
        """Descarga datos en formato XDR (rápido, formato binario)."""
        self._check_authenticated()
        conn = self._get_connection(self.server)
        url = (
            f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}"
            f"/channels/{channel_name}/streams/timeseries/data/"
            f"?version=1&auth_token={self.auth_token}"
            f"&startTime={start_time_ns}&endTime={end_time_ns}"
        )
        headers = {"Accept": "application/xdr"}

        print(f"  Descargando datos de {sensor_name}/{channel_name}...")
        conn.request("GET", url=url, headers=headers)
        response = conn.getresponse()

        data: List[Tuple[int, float]] = []
        if response.status == http.client.OK:
            unpacker = xdrlib.Unpacker(response.read())
            try:
                while True:
                    timestamp = unpacker.unpack_uhyper()
                    value = unpacker.unpack_float()
                    data.append((timestamp, value))
            except Exception:
                pass
            print(f"  ✓ {len(data)} puntos descargados")
            return data

        print(f"  ✗ Error descargando datos: {response.status} {response.reason}")
        return []

    def download_data_csv(self, sensor_name: str, channel_name: str, start_time: int, end_time: int) -> Optional[str]:
        """Descarga datos en formato CSV ya generado por el servidor."""
        self._check_authenticated()
        conn = self._get_connection(self.server)

        selector_ts = f"{sensor_name}({channel_name})"
        url = (
            f"/SensorCloud/devices/{self.device_id}/download/timeseries/csv/"
            f"?selector_ts={selector_ts}&startTime={start_time}&endTime={end_time}"
            f"&nan=0&timeFmt=unix&version=1&auth_token={self.auth_token}"
        )
        headers = {"Accept": "text/csv"}

        print(f"  Descargando CSV de {sensor_name}/{channel_name}...")
        conn.request("GET", url=url, headers=headers)
        response = conn.getresponse()

        if response.status == http.client.OK:
            raw_data = response.read().decode("utf-8")
            print("  ✓ Descarga CSV completa")
            return raw_data
        print(f"  ✗ Error descargando CSV: {response.status} {response.reason}")
        return None

    def delete_data_points(self, sensor_name: str, channel_name: str = "ch1", minutes_back: int = 60) -> bool:
        """Elimina los puntos de datos de un canal desde hace `minutes_back` minutos hasta ahora."""
        self._check_authenticated()
        end_time_ns = int(time.time() * 1_000_000_000)
        start_time_ns = end_time_ns - int(minutes_back * 60 * 1_000_000_000)

        url = (
            f"/SensorCloud/devices/{self.device_id}/sensors/{sensor_name}"
            f"/channels/{channel_name}/streams/timeseries/data/{start_time_ns}/"
            f"?version=1&auth_token={self.auth_token}"
        )
        headers = {"Accept": "application/xdr"}
        conn = self._get_connection(self.server)

        print(f"  Eliminando puntos de {sensor_name}/{channel_name} desde hace {minutes_back} min...")
        conn.request("DELETE", url=url, headers=headers)
        response = conn.getresponse()

        if response.status in (http.client.OK, http.client.NO_CONTENT):
            print("  ✓ Puntos eliminados")
            return True
        print(f"  ✗ Error eliminando puntos: {response.status} {response.reason}")
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_channel_path(channel_path: str) -> Tuple[str, str]:
    """
    Parsea "sensor_canal" -> (sensor, canal).

    Usa el ÚLTIMO guión bajo como separador para soportar nombres de
    sensor que también contienen guiones bajos (p. ej. "sigma_ch1").
    """
    idx = channel_path.rfind("_")
    if idx == -1:
        raise ValueError(f"Formato de canal inválido: '{channel_path}'. Debe contener '_'.")
    return channel_path[:idx], channel_path[idx + 1:]


def create_sensors_and_channels(client: SensorCloudClient, channel_paths: List[str]) -> None:
    """Crea, en el dispositivo destino, los sensores y canales listados en `channel_paths`."""
    print("\n--- Creando estructura en dispositivo destino ---")
    sensors_created = set()
    for channel_path in channel_paths:
        sensor, channel = parse_channel_path(channel_path)
        if sensor not in sensors_created:
            client.add_sensor(sensor)
            sensors_created.add(sensor)
        client.add_channel(sensor, channel)
