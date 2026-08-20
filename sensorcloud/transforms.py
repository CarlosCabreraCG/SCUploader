"""
Transformaciones aplicadas sobre series de tiempo (timestamp_ns, valor):

- División en intervalos continuos (para respetar huecos de datos).
- Media móvil centrada.
- "Demeaning" por intervalo (centrar cada intervalo en su punto medio).
- Ecuaciones de calibración lineales por canal.
- Ecuaciones compuestas entre variables.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

Point = Tuple[int, float]


# ---------------------------------------------------------------------------
# Intervalos continuos
# ---------------------------------------------------------------------------

def split_into_intervals(data_points: List[Point], max_gap_ns: int) -> List[List[Point]]:
    """
    Divide `data_points` en intervalos continuos.

    Dos puntos consecutivos pertenecen al mismo intervalo si la diferencia
    entre sus timestamps es <= max_gap_ns; en caso contrario se considera
    un corte y empieza un nuevo intervalo.
    """
    if not data_points:
        return []
    intervals: List[List[Point]] = []
    current = [data_points[0]]
    for i in range(1, len(data_points)):
        if data_points[i][0] - data_points[i - 1][0] <= max_gap_ns:
            current.append(data_points[i])
        else:
            intervals.append(current)
            current = [data_points[i]]
    intervals.append(current)
    return intervals


# ---------------------------------------------------------------------------
# Media móvil + demeaning
# ---------------------------------------------------------------------------

def apply_centered_moving_average(
    data_points: List[Point],
    window: int = 5,
    max_gap_ns: int = 60 * 1_000_000_000,
) -> List[Point]:
    """
    Aplica una media móvil centrada de tamaño `window` respetando discontinuidades.

    - Divide los datos en intervalos continuos (gap > max_gap_ns -> nuevo intervalo).
    - Dentro de cada intervalo calcula la media centrada; descarta los
      `window // 2` puntos de cada extremo donde no hay suficientes vecinos.
    - Intervalos con menos puntos que `window` se descartan por completo.
    """
    if not data_points:
        return []

    if window < 3:
        window = 3
    if window % 2 == 0:
        window += 1
        print(f"  ⚠ Ventana ajustada a {window} (debe ser impar)")

    half = window // 2
    intervals = split_into_intervals(data_points, max_gap_ns)

    print(
        f"  Intervalos continuos detectados: {len(intervals)} "
        f"(gap_max={max_gap_ns / 1e9:.0f}s, ventana={window})"
    )

    result: List[Point] = []
    for interval in intervals:
        n = len(interval)
        if n < window:
            print(f"  ⚠ Intervalo con {n} puntos descartado (mínimo requerido: {window})")
            continue
        for i in range(half, n - half):
            avg = sum(interval[j][1] for j in range(i - half, i + half + 1)) / window
            result.append((interval[i][0], avg))

    return result


def apply_interval_demeaning(data_points: List[Point], max_gap_ns: int = 60 * 1_000_000_000) -> List[Point]:
    """
    Centra cada intervalo continuo en el punto medio de su rango de valores
    (resta (max + min) / 2 a cada punto del intervalo).

    Los intervalos se detectan con el mismo criterio de gap que la media
    móvil, sobre los `data_points` recibidos (que típicamente ya son el
    resultado de aplicar la media móvil centrada).
    """
    if not data_points:
        return []

    intervals = split_into_intervals(data_points, max_gap_ns)
    print(f"  Demeaning: {len(intervals)} intervalo(s) detectado(s)")

    result: List[Point] = []
    for interval in intervals:
        values = [v for _, v in interval]
        center = (max(values) + min(values)) / 2
        for ts, val in interval:
            result.append((ts, val - center))

    return result


# ---------------------------------------------------------------------------
# Ecuaciones de calibración (lineales) y compuestas
# ---------------------------------------------------------------------------

def apply_linear_calibration(
    data_dict: Dict[str, List[Point]],
    channel_calibration: Dict[str, Dict[str, float]],
) -> Dict[str, List[Point]]:
    """
    Aplica, por canal, la ecuación de calibración lineal
    `(slope * valor + offset) / divisor`.

    `channel_calibration` tiene la forma:
        {"16748_ch1": {"slope": -1.7612e-3, "offset": 12452.0176, "divisor": 5}, ...}
    """
    transformed: Dict[str, List[Point]] = {}
    for channel, data_points in data_dict.items():
        calib = channel_calibration.get(channel)
        if calib is None:
            transformed[channel] = data_points
            continue
        slope = calib["slope"]
        offset = calib["offset"]
        divisor = calib.get("divisor", 1) or 1
        try:
            transformed[channel] = [
                (ts, (slope * val + offset) / divisor) for ts, val in data_points
            ]
            print(f"    Calibración aplicada a {channel}: {len(transformed[channel])} puntos")
        except Exception as e:
            print(f"    Error aplicando calibración a {channel}: {e}")
            transformed[channel] = data_points
    return transformed


def compile_composite_equation(equation: str, variables: List[str]):
    """
    Compila una ecuación compuesta (p. ej. "(a + 0.3*c) / (1 - 0.09)") en una
    función que recibe las variables como argumentos con nombre.
    """
    # Restringimos el espacio de nombres disponible al evaluar la ecuación
    # para evitar acceso accidental a builtins u otros globales.
    safe_globals = {"__builtins__": {}}
    return eval(f"lambda {','.join(variables)}: {equation}", safe_globals)


def evaluate_composite_series(
    var_to_data: Dict[str, Dict],
    variables: List[str],
    equation: str,
) -> List[Point]:
    """
    Evalúa una ecuación compuesta sobre los timestamps comunes a todas las
    variables involucradas y devuelve la serie resultante.
    """
    common_timestamps = set(var_to_data[variables[0]]["timestamps"])
    for var in variables[1:]:
        common_timestamps &= set(var_to_data[var]["timestamps"])
    common_timestamps = sorted(common_timestamps)

    if not common_timestamps:
        return []

    composite_func = compile_composite_equation(equation, variables)

    combined: List[Point] = []
    for ts in common_timestamps:
        values = {}
        valid = True
        for var in variables:
            val = var_to_data[var]["values_dict"].get(ts)
            if val is None:
                valid = False
                break
            values[var] = val
        if valid:
            try:
                combined.append((ts, composite_func(**values)))
            except Exception as e:
                print(f"  Error evaluando ecuación en timestamp {ts}: {e}")

    return combined
