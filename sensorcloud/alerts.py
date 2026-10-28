# sensorcloud/alerts.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from .config import Credentials

PERU_TZ = ZoneInfo("America/Lima")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
MAX_INTENTOS = 4


@dataclass
class Violation:
    sensor_key: str        # "57618F_ch1"
    sensor_name: str       # "Polea 04"
    sensor_side: str       # "derecha" | "izquierda"
    sensor_type: str       # "flexión" | "torsión"
    limit: float
    peak_to_peak: float
    t_start_ns: int


def _fmt_peru(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=PERU_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_num(x: float) -> str:
    if x == int(x):
        return f"{int(x)}"
    return f"{x:.4f}".rstrip("0").rstrip(".")


def render_email_html(violations: List[Violation]) -> str:
    rows = []
    for v in sorted(violations, key=lambda x: (x.t_start_ns, x.sensor_name, x.sensor_key)):
        rows.append(
            f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#111827;font-weight:600;">{v.sensor_name}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#374151;">{v.sensor_side}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#374151;">{v.sensor_type}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#111827;text-align:right;">{_fmt_num(v.limit)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#b91c1c;text-align:right;font-weight:600;">{_fmt_num(v.peak_to_peak)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#374151;white-space:nowrap;">{_fmt_peru(v.t_start_ns)}</td>
            </tr>
            """
        )

    return f"""
    <html>
      <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
        <div style="max-width:860px;margin:24px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
          <div style="background:#295E80;padding:20px 24px;">
            <h1 style="margin:0;color:#ffffff;font-size:18px;font-weight:600;">Alerta de pico-pico</h1>
            <p style="margin:4px 0 0 0;color:#cfe3ee;font-size:13px;">
              Se detectaron {len(violations)} intervalo(s) que superan el límite de diseño.
            </p>
          </div>
          <div style="padding:20px 24px;overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <thead>
                <tr style="background:#f9fafb;">
                  <th style="text-align:left;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Sensor</th>
                  <th style="text-align:left;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Lado</th>
                  <th style="text-align:left;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Tipo</th>
                  <th style="text-align:right;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Límite</th>
                  <th style="text-align:right;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Pico-pico</th>
                  <th style="text-align:left;padding:10px 12px;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;">Inicio (Perú)</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>
          <div style="padding:14px 24px;background:#f9fafb;color:#6b7280;font-size:11px;">
            Generado automáticamente por SensorCloud Sync.
          </div>
        </div>
      </body>
    </html>
    """


def send_email(creds: Credentials, violations: List[Violation]) -> bool:
    if not violations:
        return False

    if not creds.brevo_api_key:
        print("  ⚠ --send-email activo pero falta BREVO_API_KEY en el .env; no se envía.")
        return False
    if not creds.brevo_sender_email:
        print("  ⚠ --send-email activo pero falta BREVO_SENDER_EMAIL en el .env; no se envía.")
        return False
    if not creds.alert_recipients:
        print("  ⚠ --send-email activo pero ALERT_RECIPIENTS está vacío; no se envía.")
        return False

    payload = {
        "sender": {"name": creds.brevo_sender_name, "email": creds.brevo_sender_email},
        "to": [{"email": addr} for addr in creds.alert_recipients],
        "subject": f"[SensorCloud] {len(violations)} alerta(s) de pico-pico",
        "htmlContent": render_email_html(violations),
    }
    headers = {
        "accept": "application/json",
        "api-key": creds.brevo_api_key,
        "content-type": "application/json",
    }

    for intento in range(MAX_INTENTOS + 1):
        try:
            response = requests.post(BREVO_URL, json=payload, headers=headers, timeout=15)
            if response.status_code == 201:
                print(f"  ✓ Email de alerta enviado a {len(creds.alert_recipients)} destinatario(s)")
                return True
            if intento < MAX_INTENTOS:
                continue
            print(f"  ✗ Brevo respondió {response.status_code}: {response.text[:200]}")
            return False
        except Exception as e:
            if intento < MAX_INTENTOS:
                continue
            print(f"  ✗ Error de conexión al enviar email: {e}")
            return False

    return False


def _table_entry(config, channel_path: str):
    """Devuelve (nombre, lado, tipo) o None si no hay entrada válida."""
    raw = config.sensors_table.get(channel_path)
    if not isinstance(raw, dict):
        return None
    nombre = raw.get("nombre")
    lado = raw.get("lado")
    tipo = raw.get("tipo")
    if not nombre or not lado or not tipo:
        return None
    return str(nombre), str(lado), str(tipo)


def collect_violations(
    config,
    channel_path: str,
    summary_points: List[tuple],
    warned_ignored: Optional[set] = None,
) -> List[Violation]:
    """Evalúa un canal contra su límite y devuelve las violaciones de sus intervalos.

    Reglas:
    - `limit_values[canal]` ausente o "-" -> se ignora (warning una vez).
    - `sensors_table[canal]` ausente o no-dict o incompleto -> se omite el canal
      (no se alerta; no se genera fila en el email).
    """
    limit_raw = config.limit_values.get(channel_path)
    if limit_raw is None or limit_raw == "-":
        if warned_ignored is not None and channel_path not in warned_ignored:
            warned_ignored.add(channel_path)
            print(f"  ⚠ {channel_path}: sin límite definido (o '-'), se ignora para alertas")
        return []

    try:
        limit = float(limit_raw)
    except (TypeError, ValueError):
        if warned_ignored is not None and channel_path not in warned_ignored:
            warned_ignored.add(channel_path)
            print(f"  ⚠ {channel_path}: límite inválido '{limit_raw}', se ignora")
        return []

    entry = _table_entry(config, channel_path)
    if entry is None:
        if warned_ignored is not None and f"__table__{channel_path}" not in warned_ignored:
            warned_ignored.add(f"__table__{channel_path}")
            print(f"  ⚠ {channel_path}: sin entrada válida en sensors_table, se omite de alertas")
        return []

    nombre, lado, tipo = entry

    if not summary_points:
        return []

    gap_ns = config.options.ma_max_gap_ns
    intervals: List[List[tuple]] = []
    current = [summary_points[0]]
    for i in range(1, len(summary_points)):
        if summary_points[i][0] - summary_points[i - 1][0] > gap_ns:
            intervals.append(current)
            current = [summary_points[i]]
        else:
            current.append(summary_points[i])
    intervals.append(current)

    out: List[Violation] = []
    for iv in intervals:
        values = [v for _, v in iv]
        pp = max(values) - min(values)
        if pp >= limit:
            out.append(
                Violation(
                    sensor_key=channel_path,
                    sensor_name=nombre,
                    sensor_side=lado,
                    sensor_type=tipo,
                    limit=limit,
                    peak_to_peak=pp,
                    t_start_ns=iv[0][0],
                )
            )
    return out