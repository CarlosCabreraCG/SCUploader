"""
Interfaz gráfica (Flet) para visualizar los datos ya procesados y
guardados localmente por el pipeline (ver `sensorcloud/datastore.py`).

No se conecta a la API de SensorCloud: solo lee los CSV en `data/` que
`python main.py transfer` (o `schedule`) va generando. Permite:

- Elegir, con checkboxes, qué canales/sensores graficar.
- Elegir un rango de fecha/hora de inicio y fin.
- Ver un gráfico de líneas interactivo (con tooltip) de los canales
  seleccionados en ese rango.
- Descargar los datos mostrados como un archivo Excel (.xlsx).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import flet as ft
import flet_charts as fch

from . import datastore

Point = Tuple[int, float]

PALETTE = [
    "#2563eb",  # azul
    "#dc2626",  # rojo
    "#16a34a",  # verde
    "#d97706",  # naranja
    "#7c3aed",  # violeta
    "#0891b2",  # celeste
    "#be185d",  # rosa
    "#65a30d",  # lima
]


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _global_range(channels: List[str], data_dir: str) -> Optional[Tuple[datetime, datetime]]:
    """Calcula el rango [mínimo, máximo] de timestamps entre todos los canales dados."""
    min_ts, max_ts = None, None
    
    for ch in channels:
        points = datastore.load_points(ch, data_dir=data_dir)
        if not points:
            continue
        first_ts, last_ts = points[0][0], points[-1][0]
        min_ts = first_ts if min_ts is None else min(min_ts, first_ts)
        max_ts = last_ts if max_ts is None else max(max_ts, last_ts)
    if min_ts is None:
        return None
    return datetime.fromtimestamp(min_ts / 1e9), datetime.fromtimestamp(max_ts / 1e9)


def _export_to_excel(loaded: Dict[str, List[Point]], path: str) -> None:
    """Guarda los datos cargados en un .xlsx: una hoja 'Combinado' + una hoja por canal."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)

    all_ts = sorted({ts for points in loaded.values() for ts, _ in points})
    lookups = {ch: dict(points) for ch, points in loaded.items()}

    combined = wb.create_sheet("Combinado")
    combined.append(["timestamp_iso"] + list(loaded.keys()))
    for ts in all_ts:
        row = [datetime.fromtimestamp(ts / 1e9).isoformat()]
        for ch in loaded.keys():
            row.append(lookups[ch].get(ts, ""))
        combined.append(row)

    for ch, points in loaded.items():
        sheet_name = (ch or "canal")[:31]
        ws = wb.create_sheet(sheet_name)
        ws.append(["timestamp_iso", "valor"])
        for ts, val in points:
            ws.append([datetime.fromtimestamp(ts / 1e9).isoformat(), val])

    wb.save(path)


def build_app(page: ft.Page, data_dir: str = datastore.DEFAULT_DATA_DIR) -> None:
    page.title = "SensorCloud Sync - Visor"
    page.padding = 20
    page.window.width = 1150
    page.window.height = 720
    page.theme_mode = ft.ThemeMode.LIGHT

    status_text = ft.Text("", color=ft.Colors.RED_700)
    state: Dict = {"start": None, "end": None, "last_loaded": {}}
    zoom_state = {"zoom_level": 1.0, "pan_x": 0, "pan_y": 0}

    # ------------------------------------------------------------------
    # Chart
    # ------------------------------------------------------------------
    chart = fch.LineChart(
        expand=True,
        interactive=True,
        tooltip_bgcolor=ft.Colors.with_opacity(0.9, ft.Colors.BLUE_GREY_900),
        border=ft.border.all(1, ft.Colors.OUTLINE),
        left_axis=ft.ChartAxis(labels_size=50),
        bottom_axis=ft.ChartAxis(labels_size=32),
        # NUEVAS PROPIEDADES PARA ZOOM:
        enable_interaction=True,  # Habilita interacción completa
        zoom=1.0,
        min_zoom=0.5,
        max_zoom=5.0,
    )

    # ------------------------------------------------------------------
    # Selección de canales
    # ------------------------------------------------------------------
    checkboxes: Dict[str, ft.Checkbox] = {}
    channels_column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=220)

    def rebuild_channel_list() -> List[str]:
        channels = datastore.list_channels(data_dir)
        checkboxes.clear()
        channels_column.controls.clear()
        for i, ch in enumerate(channels):
            color = PALETTE[i % len(PALETTE)]
            cb = ft.Checkbox(label=ch, value=True, label_style=ft.TextStyle(color=color, weight=ft.FontWeight.BOLD))
            checkboxes[ch] = cb
            channels_column.controls.append(cb)
        return channels

    channels = rebuild_channel_list()

    # ------------------------------------------------------------------
    # Selección de rango de tiempo
    # ------------------------------------------------------------------
    default_range = _global_range(channels, data_dir)
    now = datetime.now()
    state["start"] = default_range[0] if default_range else now - timedelta(days=1)
    state["end"] = default_range[1] if default_range else now

    start_label = ft.Text(f"Inicio: {_fmt(state['start'])}")
    end_label = ft.Text(f"Fin: {_fmt(state['end'])}")

    def make_pickers(key: str, label: ft.Text):
        def on_date_change(e: ft.ControlEvent):
            d = date_picker.value
            if d:
                state[key] = state[key].replace(year=d.year, month=d.month, day=d.day)
                label.value = f"{'Inicio' if key == 'start' else 'Fin'}: {_fmt(state[key])}"
                page.update()

        def on_time_change(e: ft.ControlEvent):
            t = time_picker.value
            if t:
                state[key] = state[key].replace(hour=t.hour, minute=t.minute)
                label.value = f"{'Inicio' if key == 'start' else 'Fin'}: {_fmt(state[key])}"
                page.update()

        date_picker = ft.DatePicker(value=state[key], on_change=on_date_change)
        time_picker = ft.TimePicker(value=state[key].time(), on_change=on_time_change)
        page.overlay.extend([date_picker, time_picker])
        return date_picker, time_picker

    start_date_picker, start_time_picker = make_pickers("start", start_label)
    end_date_picker, end_time_picker = make_pickers("end", end_label)

    # ------------------------------------------------------------------
    # Graficar
    # ------------------------------------------------------------------
    def build_chart(e: Optional[ft.ControlEvent] = None) -> None:
        status_text.value = ""
        selected = [ch for ch, cb in checkboxes.items() if cb.value]
        if not selected:
            status_text.value = "Selecciona al menos un canal."
            page.update()
            return

        start_ns = int(state["start"].timestamp() * 1_000_000_000)
        end_ns = int(state["end"].timestamp() * 1_000_000_000)
        if start_ns >= end_ns:
            status_text.value = "El inicio debe ser anterior al fin."
            page.update()
            return

        loaded: Dict[str, List[Point]] = {}
        data_series = []
        for ch in selected:
            points = datastore.load_points(ch, start_ns, end_ns, data_dir=data_dir)
            if not points:
                continue
            loaded[ch] = points
            color = PALETTE[channels.index(ch) % len(PALETTE)] if ch in channels else PALETTE[0]
            data_points = [
                fch.LineChartDataPoint(
                    x=(ts - start_ns) / 1e9 / 60.0,
                    y=val,
                    tooltip=f"{ch}\n{datetime.fromtimestamp(ts / 1e9).strftime('%Y-%m-%d %H:%M:%S')}\n{val:.4f}",
                )
                for ts, val in points
            ]
            data_series.append(
                fch.LineChartData(
                    data_points=data_points, 
                    color=color, 
                    stroke_width=2, 
                    curved=False, 
                    point=False
                )
            )

        if not data_series:
            status_text.value = "No hay datos guardados en ese rango para los canales seleccionados."
            chart.data_series = []
            page.update()
            return

        state["last_loaded"] = loaded

        total_minutes = max((end_ns - start_ns) / 1e9 / 60.0, 1e-6)
        bottom_labels = []
        for i in range(5):
            pos = total_minutes * i / 4
            label_dt = datetime.fromtimestamp(start_ns / 1e9 + pos * 60)
            bottom_labels.append(ft.ChartAxisLabel(value=pos, label=ft.Text(label_dt.strftime("%m-%d %H:%M"), size=10)))

        chart.data_series = data_series
        chart.min_x = 0
        chart.max_x = total_minutes
        chart.bottom_axis = ft.ChartAxis(labels=bottom_labels, labels_size=32)

        # Restaurar el estado del zoom después de actualizar los datos
        chart.zoom = zoom_state["zoom_level"]
        # Nota: pan_x y pan_y no son propiedades directas del LineChart en Flet
        # Se manejan internamente por la interacción del usuario

        total_points = sum(len(p) for p in loaded.values())
        status_text.color = ft.Colors.GREEN_800
        status_text.value = f"Mostrando {len(data_series)} canal(es), {total_points} puntos en total."
        page.update()

    # Agregar estas funciones después de build_chart:

    def on_scroll_zoom(e: ft.ControlEvent) -> None:
        """Maneja el zoom con la rueda del mouse"""
        if hasattr(e, 'delta_y') and e.delta_y != 0:
            # Ajustar nivel de zoom
            zoom_delta = -e.delta_y * 0.01  # Ajusta la sensibilidad
            new_zoom = max(0.5, min(5.0, chart.zoom + zoom_delta))
            chart.zoom = new_zoom
            zoom_state["zoom_level"] = new_zoom
            page.update()

    def on_pan_start(e: ft.ControlEvent) -> None:
        """Inicia el panning del gráfico"""
        # Guardar la posición inicial del mouse
        if hasattr(e, 'local_x') and hasattr(e, 'local_y'):
            zoom_state["pan_start_x"] = e.local_x
            zoom_state["pan_start_y"] = e.local_y
            zoom_state["is_panning"] = True

    def on_pan_update(e: ft.ControlEvent) -> None:
        """Actualiza el panning del gráfico"""
        if zoom_state.get("is_panning", False):
            # El panning se maneja automáticamente por Flet cuando el chart es interactive
            # y tiene enable_interaction=True
            pass

    def on_pan_end(e: ft.ControlEvent) -> None:
        """Termina el panning del gráfico"""
        zoom_state["is_panning"] = False
    # ------------------------------------------------------------------
    # Descargar Excel
    # ------------------------------------------------------------------
    def on_save_result(e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        path = e.path if e.path.lower().endswith(".xlsx") else e.path + ".xlsx"
        try:
            _export_to_excel(state["last_loaded"], path)
            status_text.color = ft.Colors.GREEN_800
            status_text.value = f"Excel guardado en: {path}"
        except Exception as exc:
            status_text.color = ft.Colors.RED_700
            status_text.value = f"Error guardando Excel: {exc}"
        page.update()

    file_picker = ft.FilePicker(on_result=on_save_result)
    page.overlay.append(file_picker)

    def on_download_click(e: ft.ControlEvent) -> None:
        if not state["last_loaded"]:
            status_text.color = ft.Colors.RED_700
            status_text.value = "Primero genera el gráfico (botón 'Graficar')."
            page.update()
            return
        file_picker.save_file(
            dialog_title="Guardar datos como Excel",
            file_name="sensorcloud_datos.xlsx",
            allowed_extensions=["xlsx"],
        )

    def on_refresh_click(e: ft.ControlEvent) -> None:
        nonlocal channels
        channels = rebuild_channel_list()
        status_text.color = ft.Colors.BLUE_800
        status_text.value = f"Lista de canales actualizada ({len(channels)} encontrados)."
        page.update()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    left_panel = ft.Container(
        width=300,
        content=ft.Column(
            [
                ft.Row(
                    [ft.Text("Canales", weight=ft.FontWeight.BOLD, size=16), ft.IconButton(ft.Icons.REFRESH, tooltip="Recargar canales", on_click=on_refresh_click)],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                channels_column,
                ft.Divider(),
                ft.Text("Rango de tiempo", weight=ft.FontWeight.BOLD, size=16),
                start_label,
                ft.Row(
                    [
                        ft.OutlinedButton("📅 Fecha", on_click=lambda e: page.open(start_date_picker)),
                        ft.OutlinedButton("🕐 Hora", on_click=lambda e: page.open(start_time_picker)),
                    ]
                ),
                end_label,
                ft.Row(
                    [
                        ft.OutlinedButton("📅 Fecha", on_click=lambda e: page.open(end_date_picker)),
                        ft.OutlinedButton("🕐 Hora", on_click=lambda e: page.open(end_time_picker)),
                    ]
                ),
                ft.Divider(),
                ft.ElevatedButton("Graficar", icon=ft.Icons.SHOW_CHART, on_click=build_chart, width=260),
                ft.ElevatedButton("Descargar Excel", icon=ft.Icons.DOWNLOAD, on_click=on_download_click, width=260),
                ft.Container(status_text, padding=ft.padding.only(top=10)),
            ],
            spacing=8,
        ),
    )

    right_panel = ft.Container(
        expand=True,
        padding=10,
        content=chart if channels else ft.Column(
            [
                ft.Text("No hay datos guardados todavía.", size=16, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Ejecuta primero: python main.py transfer\n"
                    "(o python main.py schedule) para generar datos en la carpeta 'data/'.",
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        ),
    )

    page.add(ft.Row([left_panel, ft.VerticalDivider(), right_panel], expand=True))

    if channels:
        build_chart()
