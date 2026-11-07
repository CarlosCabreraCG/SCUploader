# sensorcloud/transforms.py
from __future__ import annotations

from typing import Dict, List, Tuple

Point = Tuple[int, float]

PROCESS_TOLERANCE_NS = 5 * 1_000_000_000  # ±5 s para casar tStart entre variables


# ---------------------------------------------------------------------------
# Intervalos continuos
# ---------------------------------------------------------------------------

def split_into_intervals(data_points: List[Point], max_gap_ns: int) -> List[List[Point]]:
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
# Media móvil + demeaning (composite)
# ---------------------------------------------------------------------------

def apply_centered_moving_average(
    data_points: List[Point],
    window: int = 5,
    max_gap_ns: int = 60 * 1_000_000_000,
) -> List[Point]:
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
# Calibración y composite
# ---------------------------------------------------------------------------

def apply_linear_calibration(
    data_dict: Dict[str, List[Point]],
    channel_calibration: Dict[str, Dict[str, float]],
) -> Dict[str, List[Point]]:
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
    safe_globals = {"__builtins__": {}}
    return eval(f"lambda {','.join(variables)}: {equation}", safe_globals)


def evaluate_composite_series(
    var_to_data: Dict[str, Dict],
    variables: List[str],
    equation: str,
) -> List[Point]:
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


# ---------------------------------------------------------------------------
# process_equations
# ---------------------------------------------------------------------------

def compute_interval_metrics_by_var(
    var_to_data: Dict[str, Dict],
    max_gap_ns: int,
) -> Dict[str, List[Dict]]:
    """Por cada var, devuelve una lista de métricas por intervalo continuo.

    Métricas: tStart, tEnd, max, min, pp, prom, start, end (ns).
    """
    out: Dict[str, List[Dict]] = {}
    for var, payload in var_to_data.items():
        pts = payload["data_points"]
        intervals = split_into_intervals(pts, max_gap_ns)
        metrics_list: List[Dict] = []
        for iv in intervals:
            vals = [v for _, v in iv]
            mx = max(vals)
            mn = min(vals)
            metrics_list.append(
                {
                    "tStart": iv[0][0],
                    "tEnd": iv[-1][0],
                    "max": mx,
                    "min": mn,
                    "pp": mx - mn,
                    "prom": (mx + mn) / 2,
                    "start": iv[0][0],
                    "end": iv[-1][0],
                }
            )
        out[var] = metrics_list
    return out


def _find_interval_by_tstart(metrics: List[Dict], ts: int, tol: int) -> Dict | None:
    """Búsqueda binaria del intervalo con tStart más cercano a ts dentro de tol."""
    if not metrics:
        return None
    lo, hi = 0, len(metrics) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        d = metrics[mid]["tStart"] - ts
        if best is None or abs(d) < abs(best[0]):
            best = (d, metrics[mid])
        if d < 0:
            lo = mid + 1
        elif d > 0:
            hi = mid - 1
        else:
            break
    if best is None or abs(best[0]) > tol:
        return None
    return best[1]


def _extract_metric_tokens(equation: str) -> List[str]:
    import re
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", equation)


def evaluate_process_series(
    group_name: str,
    equation: str,
    principal_var: str,
    metrics_by_var: Dict[str, List[Dict]],
    tolerance_ns: int = PROCESS_TOLERANCE_NS,
) -> List[Point]:
    """Evalúa una ecuación de process_equations y devuelve un punto por intervalo.

    - El principal es `principal_var`. Se recorren SUS intervalos.
    - Para cada intervalo del principal, se buscan los intervalos de las
      demás vars por tStart más cercano dentro de tolerancia.
    - Si alguna var no tiene intervalo que case, se descarta ese intervalo.
    - El timestamp del punto resultante es el tStart del principal.
    """
    principal_metrics = metrics_by_var.get(principal_var, [])
    if not principal_metrics:
        return []

    tokens = _extract_metric_tokens(equation)
    used_vars = sorted({tok.rsplit("_", 1)[0] for tok in tokens})

    # Compilar una función que reciba las métricas por nombre de token.
    safe_globals = {"__builtins__": {}}
    func = eval(f"lambda {','.join(tokens)}: {equation}", safe_globals)

    out: List[Point] = []
    for iv in principal_metrics:
        t_start = iv["tStart"]
        kwargs = {}
        ok = True
        for var in used_vars:
            other = iv if var == principal_var else _find_interval_by_tstart(
                metrics_by_var.get(var, []), t_start, tolerance_ns
            )
            if other is None:
                ok = False
                break
            for metric in ("PROM", "PP", "MAX", "MIN", "START", "END"):
                key = f"{var}_{metric}"
                if key in tokens:
                    kwargs[key] = other[metric.lower()]
        if not ok:
            continue
        try:
            value = func(**kwargs)
            out.append((t_start, float(value)))
        except Exception as e:
            print(f"  Error evaluando process_equations['{group_name}'] en tStart={t_start}: {e}")

    return out

def summarize_intervals(data_points: List[Point], max_gap_ns: int) -> List[Point]:
    """
    Reduce `data_points` a, por cada intervalo continuo, los puntos
    relevantes para un dashboard: primer punto, máximo, mínimo y último.

    - Los intervalos se detectan con el mismo criterio que la media móvil
      (gap > max_gap_ns -> nuevo intervalo).
    - Se preserva el timestamp original de cada punto elegido.
    - Si varios roles caen sobre el mismo timestamp, se emite una sola vez.
    - El resultado está ordenado por timestamp.
    """
    if not data_points:
        return []

    intervals = split_into_intervals(data_points, max_gap_ns)
    summary: List[Point] = []

    for interval in intervals:
        first = interval[0]
        last = interval[-1]
        max_pt = max(interval, key=lambda p: p[1])
        min_pt = min(interval, key=lambda p: p[1])

        picked = {first[0]: first, max_pt[0]: max_pt, min_pt[0]: min_pt, last[0]: last}
        summary.extend(sorted(picked.values(), key=lambda p: p[0]))

    return summary