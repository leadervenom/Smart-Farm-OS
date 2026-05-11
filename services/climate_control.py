from __future__ import annotations

from typing import Any, Dict, List, Tuple

IDEAL_CLIMATE: Dict[str, Tuple[float, float]] = {
    "temperature": (20.0, 26.0),
    "humidity": (60.0, 78.0),
    "ph": (5.8, 6.8),
    "ec": (1.2, 2.2),
    "water_level": (30.0, 100.0),
    "flow_rate": (1.0, 2.8),
    "light_lux": (300.0, 580.0),
    "airflow": (1.1, 2.2),
}

ACTUATORS = ["ventilation", "lighting", "nutrient_pump", "circulation", "humidifier"]


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def mean(levels: List[Dict[str, Any]], metric: str) -> float:
    if not levels:
        return 0.0
    return sum(float(level[metric]) for level in levels) / len(levels)


def metric_state(value: float, metric: str) -> str:
    low, high = IDEAL_CLIMATE[metric]
    if low <= value <= high:
        return "stable"
    tolerance = {
        "temperature": 3.0,
        "humidity": 8.0,
        "ph": 0.45,
        "ec": 0.45,
        "water_level": 12.0,
        "flow_rate": 0.55,
        "light_lux": 160.0,
        "airflow": 0.35,
    }[metric]
    if value < low - tolerance or value > high + tolerance:
        return "critical"
    return "warning"


def metric_direction(value: float, metric: str) -> str:
    low, high = IDEAL_CLIMATE[metric]
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "inside"


def summarize_imbalances(levels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    imbalances: List[Dict[str, Any]] = []
    for metric in ["temperature", "humidity", "airflow", "light_lux", "ec", "ph", "flow_rate"]:
        value = round(mean(levels, metric), 2)
        low, high = IDEAL_CLIMATE[metric]
        state = metric_state(value, metric)
        direction = metric_direction(value, metric)
        if state != "stable":
            imbalances.append({
                "metric": metric,
                "value": value,
                "ideal_min": low,
                "ideal_max": high,
                "status": state,
                "direction": direction,
            })
    return imbalances


def build_rack_climate_plan(rack_id: str, levels: List[Dict[str, Any]], controls: Dict[str, Any]) -> Dict[str, Any]:
    """Rack-level multivariable controller.

    It deliberately considers cross-effects instead of correcting one metric blindly:
    lighting changes heat, ventilation changes both heat and humidity, and circulation changes
    flow/air movement. Gemini can replace the reasoning layer, but this deterministic plan
    keeps the prototype functional without an API key.
    """
    avg = {metric: round(mean(levels, metric), 2) for metric in IDEAL_CLIMATE}
    targets = {key: int(controls.get(key, 50)) for key in ACTUATORS}
    actions: List[Dict[str, Any]] = []
    conflicts: List[str] = []

    def move(control: str, delta: int, reason: str) -> None:
        before = targets[control]
        targets[control] = clamp(before + delta)
        actions.append({
            "control": control,
            "change_percent": targets[control] - before,
            "target_percent": targets[control],
            "reason": reason,
        })

    temp_dir = metric_direction(avg["temperature"], "temperature")
    hum_dir = metric_direction(avg["humidity"], "humidity")
    airflow_dir = metric_direction(avg["airflow"], "airflow")
    light_dir = metric_direction(avg["light_lux"], "light_lux")
    ec_dir = metric_direction(avg["ec"], "ec")
    flow_dir = metric_direction(avg["flow_rate"], "flow_rate")

    if temp_dir == "high":
        move("ventilation", +12, "Rack heat is above target; airflow is the safest first correction.")
        move("lighting", -8, "Light reduction lowers heat load without touching nutrient chemistry.")
    elif temp_dir == "low":
        move("ventilation", -8, "Rack is cool; reduce exhaust before increasing light intensity.")
        move("lighting", +5, "Slight light increase can recover temperature and growth energy.")

    if hum_dir == "high":
        move("ventilation", +10, "Humidity is high; airflow reduces mold pressure.")
        move("humidifier", -18, "Humidifier duty should drop until the rack returns to range.")
    elif hum_dir == "low":
        move("humidifier", +14, "Humidity is low; controlled humidification prevents leaf stress.")

    if airflow_dir == "low":
        move("circulation", +12, "Internal circulation is low; increase fan mixing inside the rack.")
    elif airflow_dir == "high" and hum_dir != "high":
        move("circulation", -8, "Air movement is above target; reduce circulation if humidity is not high.")

    if light_dir == "high" and temp_dir != "low":
        move("lighting", -10, "Light is above target; dimming prevents heat rise and saves energy.")
    elif light_dir == "low" and temp_dir != "high":
        move("lighting", +10, "Light is below target; increase only if heat is not already high.")

    if ec_dir == "low":
        move("nutrient_pump", +8, "EC is low; slightly increase nutrient pump duty.")
    elif ec_dir == "high":
        move("nutrient_pump", -10, "EC is high; reduce nutrient feed until dilution/inspection.")

    if flow_dir == "low":
        move("circulation", +8, "Flow rate is low; raise circulation to avoid stagnant nutrient zones.")
    elif flow_dir == "high":
        move("circulation", -8, "Flow rate is high; reduce circulation to avoid root stress.")

    if temp_dir == "high" and hum_dir == "low":
        conflicts.append("Ventilation cools the rack but can dry it further. Pair ventilation with humidifier support.")
    if temp_dir == "low" and hum_dir == "high":
        conflicts.append("Humidity needs airflow, but temperature is already low. Use circulation before heavy exhaust.")
    if light_dir == "low" and temp_dir == "high":
        conflicts.append("Light is low but heat is high. Do not increase light until heat is corrected.")
    if ec_dir != "inside" and flow_dir == "low":
        conflicts.append("Nutrient correction is less reliable while flow rate is low. Stabilize circulation first.")

    imbalances = summarize_imbalances(levels)
    penalty = 0
    for item in imbalances:
        penalty += 14 if item["status"] == "critical" else 7
    level_penalty = sum(5 for level in levels if level.get("status") == "imbalanced")
    score = clamp(100 - penalty - level_penalty, 0, 100)

    if score < 55:
        priority = "critical"
    elif score < 72:
        priority = "high"
    elif score < 88:
        priority = "medium"
    else:
        priority = "low"

    if not actions:
        actions.append({
            "control": "maintain",
            "change_percent": 0,
            "target_percent": None,
            "reason": "Rack is inside the target envelope. Keep current actuator settings.",
        })

    return {
        "mode": "local_multivariable",
        "rack_id": rack_id,
        "score": score,
        "priority": priority,
        "summary": f"Rack {rack_id} climate score {score}/100. {len(imbalances)} variable(s) outside target.",
        "averages": avg,
        "imbalances": imbalances,
        "target_controls": targets,
        "actions": actions[:8],
        "conflicts": conflicts[:4],
        "expected_outcome": "Rack climate should move toward target within the next control cycle if the proposed actuator values are applied.",
    }


def apply_plan_to_controls(current: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(current)
    targets = plan.get("target_controls") or {}
    for key in ACTUATORS:
        if key in targets:
            updated[key] = clamp(float(targets[key]))
    for action in plan.get("actions", []):
        control = action.get("control")
        if control not in ACTUATORS:
            continue
        if action.get("target_percent") is not None:
            updated[control] = clamp(float(action["target_percent"]))
        elif action.get("change_percent") is not None:
            updated[control] = clamp(float(updated.get(control, 50)) + float(action["change_percent"]))
    return updated
