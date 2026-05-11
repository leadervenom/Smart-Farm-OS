from __future__ import annotations

import csv
import json
import math
import time
from urllib.parse import quote
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Tuple

from services.climate_control import ACTUATORS, IDEAL_CLIMATE, apply_plan_to_controls, build_rack_climate_plan
from services.ai_engine import create_rack_ai_plan

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
PLANTS_CSV = DATA_DIR / "plants.csv"
SENSOR_CSV = DATA_DIR / "sensor_stream.csv"
EVENT_LOG = LOG_DIR / "events.jsonl"

IDEALS = IDEAL_CLIMATE
BASE_RACK_CONTROLS = {
    "ventilation": 50,
    "lighting": 74,
    "nutrient_pump": 62,
    "circulation": 58,
    "humidifier": 30,
}


@dataclass
class Plant:
    id: str
    name: str
    species: str
    growth_stage: str
    temp_min: float
    temp_max: float
    humidity_min: float
    humidity_max: float
    ph_min: float
    ph_max: float
    ec_min: float
    ec_max: float
    days_in_stage: int
    health_score: float
    rack: str
    level: int
    slot: int

    @classmethod
    def from_row(cls, row: Dict[str, str]) -> "Plant":
        return cls(
            id=row["id"],
            name=row["name"],
            species=row["species"],
            growth_stage=row["growth_stage"],
            temp_min=float(row["temp_min"]),
            temp_max=float(row["temp_max"]),
            humidity_min=float(row["humidity_min"]),
            humidity_max=float(row["humidity_max"]),
            ph_min=float(row["ph_min"]),
            ph_max=float(row["ph_max"]),
            ec_min=float(row["ec_min"]),
            ec_max=float(row["ec_max"]),
            days_in_stage=int(row["days_in_stage"]),
            health_score=float(row["health_score"]),
            rack=row["rack"],
            level=int(row["level"]),
            slot=int(row["slot"]),
        )

    def to_row(self) -> Dict[str, Any]:
        return asdict(self)


class FarmStore:
    """Stateful simulator over CSV data. Replace SENSOR_CSV with real ESP32/MQTT data later."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.sensor_rows = self._load_sensor_rows()
        self.plants = self._load_plants()
        self.tick_index = 0
        self.history: Dict[str, List[float]] = {
            "temperature": [], "humidity": [], "ph": [], "ec": [],
            "water_level": [], "flow_rate": [], "light_lux": [], "airflow": []
        }
        rack_ids = sorted({r["rack"] for r in self.sensor_rows} | {p.rack for p in self.plants})
        self.rack_controls: Dict[str, Dict[str, Any]] = {rack: dict(BASE_RACK_CONTROLS) for rack in rack_ids}
        self.controls = {
            **dict(BASE_RACK_CONTROLS),
            "auto_mode": True,
            "lockdown": False,
        }
        self.dismissed_alerts: set[str] = set()
        self.last_predictive_alerts: List[Dict[str, Any]] = []
        self.last_prediction_scan: int | None = None
        self.last_ai_plan: Dict[str, Any] | None = None
        self.latest_rows: List[Dict[str, Any]] = []
        self.latest_racks: List[Dict[str, Any]] = []
        LOG_DIR.mkdir(exist_ok=True)

    def _load_sensor_rows(self) -> List[Dict[str, Any]]:
        with SENSOR_CSV.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        parsed = []
        for r in rows:
            parsed.append({
                "tick": int(r["tick"]), "rack": r["rack"], "level": int(r["level"]),
                "temperature": float(r["temperature"]), "humidity": float(r["humidity"]),
                "ph": float(r["ph"]), "ec": float(r["ec"]),
                "water_level": float(r["water_level"]), "flow_rate": float(r["flow_rate"]),
                "light_lux": float(r["light_lux"]), "airflow": float(r["airflow"]),
            })
        return parsed

    def _load_plants(self) -> List[Plant]:
        with PLANTS_CSV.open(newline="", encoding="utf-8") as f:
            return [Plant.from_row(r) for r in csv.DictReader(f)]

    def save_plants(self) -> None:
        fieldnames = list(self.plants[0].to_row().keys())
        with PLANTS_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in self.plants:
                writer.writerow(p.to_row())

    def _rows_for_next_tick(self) -> List[Dict[str, Any]]:
        tick = self.tick_index % 360
        rows = [r.copy() for r in self.sensor_rows if r["tick"] == tick]
        self.tick_index += 1
        return self._apply_controls(rows)

    def _apply_controls(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rack-level manual override affects the simulated environment.

        The demo also applies a controlled problem ramp. With weak settings, heat, humidity,
        light load, and reservoir risk drift upward so the optimizer has something visible to fix.
        When AI optimization raises ventilation / lowers lighting / lowers humidifier duty, the
        next telemetry ticks move back toward the target envelope.
        """
        demo_load = min(1.0, max(0.0, (self.tick_index - 2) / 16))
        for r in rows:
            controls = self.rack_controls.setdefault(r["rack"], dict(BASE_RACK_CONTROLS))
            vent = (controls["ventilation"] - 50) / 50
            light = (controls["lighting"] - 74) / 50
            pump = (controls["nutrient_pump"] - 62) / 50
            circ = (controls["circulation"] - 58) / 50
            humid = (controls["humidifier"] - 30) / 50

            weak_cooling = max(0.0, (72 - controls["ventilation"]) / 34)
            heat_from_lights = max(0.0, (controls["lighting"] - 62) / 38)
            humid_risk = max(0.0, (64 - controls["ventilation"]) / 36) + max(0.0, (controls["humidifier"] - 18) / 42)
            circulation_risk = max(0.0, (58 - controls["circulation"]) / 45)

            r["temperature"] = round(r["temperature"] - vent * 1.15 + light * 0.55 + demo_load * (1.2 * weak_cooling + 0.7 * heat_from_lights), 2)
            r["humidity"] = round(r["humidity"] - vent * 2.3 + humid * 3.2 + demo_load * (2.6 * humid_risk), 2)
            r["ec"] = round(r["ec"] + pump * 0.16, 2)
            r["flow_rate"] = round(max(0.4, r["flow_rate"] + circ * 0.35 - demo_load * 0.12 * circulation_risk), 2)
            r["airflow"] = round(max(0.4, r["airflow"] + vent * 0.45 + circ * 0.15 - demo_load * 0.18 * weak_cooling), 2)
            r["light_lux"] = round(max(50, r["light_lux"] * (controls["lighting"] / 74) + demo_load * max(0.0, controls["lighting"] - 62) * 2.1), 1)
            r["water_level"] = round(max(0.0, r["water_level"] - demo_load * (4.5 + max(0.0, controls["circulation"] - 58) / 12)), 2)
        return rows

    def _sync_global_controls(self) -> None:
        for key in ACTUATORS:
            self.controls[key] = int(sum(c[key] for c in self.rack_controls.values()) / max(1, len(self.rack_controls)))

    @staticmethod
    def _mean(rows: List[Dict[str, Any]], metric: str) -> float:
        return sum(r[metric] for r in rows) / len(rows)

    @staticmethod
    def _status(value: float, metric: str) -> str:
        low, high = IDEALS[metric]
        if low <= value <= high:
            return "stable"
        tolerance = {
            "temperature": 3.0, "humidity": 8.0, "ph": 0.45,
            "ec": 0.45, "water_level": 12.0, "flow_rate": 0.55,
            "light_lux": 160.0, "airflow": 0.35,
        }[metric]
        if value < low - tolerance or value > high + tolerance:
            return "critical"
        return "warning"

    def _build_sensors(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        labels = {
            "temperature": ("Temperature", "°C"),
            "humidity": ("Humidity", "%"),
            "ph": ("Water pH", "pH"),
            "ec": ("EC / Nutrients", "mS/cm"),
            "water_level": ("Water Level", "%"),
            "flow_rate": ("Flow Rate", "L/min"),
            "light_lux": ("Light", "lux"),
            "airflow": ("Airflow", "m/s"),
        }
        sensors = []
        for metric, (name, unit) in labels.items():
            value = self._mean(rows, metric)
            self.history[metric].append(round(value, 2))
            self.history[metric] = self.history[metric][-36:]
            low, high = IDEALS[metric]
            sensors.append({
                "id": metric,
                "name": name,
                "value": round(value, 2),
                "unit": unit,
                "ideal_min": low,
                "ideal_max": high,
                "status": self._status(value, metric),
                "history": self.history[metric],
            })
        return sensors

    def _zone_status(self, row: Dict[str, Any]) -> str:
        penalties = 0
        for metric in ["temperature", "humidity", "ph", "ec", "flow_rate", "airflow", "light_lux"]:
            penalties += {"stable": 0, "warning": 1, "critical": 2}[self._status(row[metric], metric)]
        if penalties >= 4:
            return "imbalanced"
        if penalties >= 2:
            return "slight_imbalance"
        return "balanced"

    def _plant_pressure(self, plant: Plant, row: Dict[str, Any]) -> float:
        penalty = 0.0
        checks = [
            (row["temperature"], plant.temp_min, plant.temp_max, 10),
            (row["humidity"], plant.humidity_min, plant.humidity_max, 8),
            (row["ph"], plant.ph_min, plant.ph_max, 12),
            (row["ec"], plant.ec_min, plant.ec_max, 10),
        ]
        for value, low, high, weight in checks:
            if value < low:
                penalty += min(weight, abs(low - value) * weight / max(1, abs(high - low)))
            elif value > high:
                penalty += min(weight, abs(value - high) * weight / max(1, abs(high - low)))
        return penalty

    def _build_racks(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        racks = []
        plant_lookup: Dict[Tuple[str, int], List[Plant]] = {}
        for p in self.plants:
            plant_lookup.setdefault((p.rack, p.level), []).append(p)
        for rack_id in sorted({r["rack"] for r in rows}):
            levels = []
            rack_rows = sorted([x for x in rows if x["rack"] == rack_id], key=lambda x: x["level"], reverse=True)
            for r in rack_rows:
                plants = [asdict(p) for p in sorted(plant_lookup.get((rack_id, r["level"]), []), key=lambda x: x.slot)]
                for p in plants:
                    source = next(x for x in self.plants if x.id == p["id"])
                    p["predicted_pressure"] = round(self._plant_pressure(source, r), 1)
                    p["live_health"] = max(0, min(100, round(source.health_score - p["predicted_pressure"], 1)))
                levels.append({
                    "id": f"{rack_id}{r['level']}",
                    "rack": rack_id,
                    "level": r["level"],
                    "temperature": r["temperature"],
                    "humidity": r["humidity"],
                    "ph": r["ph"],
                    "ec": r["ec"],
                    "water_level": r["water_level"],
                    "flow_rate": r["flow_rate"],
                    "light_lux": r["light_lux"],
                    "airflow": r["airflow"],
                    "status": self._zone_status(r),
                    "plants": plants,
                })
            controls = self.rack_controls.setdefault(rack_id, dict(BASE_RACK_CONTROLS))
            climate_plan = build_rack_climate_plan(rack_id, levels, controls)
            racks.append({
                "id": rack_id,
                "name": f"Rack {rack_id}",
                "levels": levels,
                "controls": dict(controls),
                "climate_plan": climate_plan,
            })
        return racks

    def _metric_penalty(self, value: float, metric: str) -> float:
        low, high = IDEALS[metric]
        span = max(0.1, high - low)
        midpoint = (low + high) / 2
        if low <= value <= high:
            edge_ratio = abs(value - midpoint) / max(0.1, span / 2)
            return 1.2 if edge_ratio > 0.82 else 0.0
        outside = low - value if value < low else value - high
        return 4.0 + min(14.0, (outside / span) * 28.0)

    def _health_score(self, sensors: List[Dict[str, Any]], racks: List[Dict[str, Any]]) -> float:
        score = 100.0
        for sensor in sensors:
            score -= self._metric_penalty(float(sensor["value"]), sensor["id"])
            if len(sensor.get("history", [])) >= 5:
                slope = (sensor["history"][-1] - sensor["history"][-5]) / 4
                low, high = IDEALS[sensor["id"]]
                if (sensor["value"] > high and slope > 0) or (sensor["value"] < low and slope < 0):
                    score -= 2.5
        for rack in racks:
            for level in rack["levels"]:
                if level["status"] == "slight_imbalance":
                    score -= 2.0
                if level["status"] == "imbalanced":
                    score -= 5.0
                for metric in ["temperature", "humidity", "ph", "ec", "flow_rate", "airflow", "light_lux"]:
                    score -= self._metric_penalty(float(level[metric]), metric) * 0.28
        return round(max(0, min(100, score)), 1)

    @staticmethod
    def plant_state(score: float) -> Dict[str, str]:
        if score >= 84:
            return {"label": "Stable", "detail": "System is inside the target hydroponic envelope."}
        if score >= 68:
            return {"label": "Drifting", "detail": "One or two variables are moving toward a bad range."}
        if score >= 46:
            return {"label": "Unstable", "detail": "Multiple variables need active correction."}
        return {"label": "Critical State", "detail": "Immediate manual inspection is required."}

    @staticmethod
    def health_verdict(score: float) -> Dict[str, str]:
        if score >= 75:
            return {"label": "Good", "level": "good", "detail": "Overall farm condition is acceptable."}
        if score >= 45:
            return {"label": "Bad", "level": "bad", "detail": "Overall farm condition is unsafe if left unchanged."}
        return {"label": "Critical", "level": "critical", "detail": "Correct the system and inspect hardware immediately."}

    def _make_alerts(self, rows: List[Dict[str, Any]], racks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
        avg = {m: self._mean(rows, m) for m in IDEALS}

        def trend(metric: str, fallback: float) -> float:
            hist = self.history[metric]
            if len(hist) < 5:
                return fallback
            return (hist[-1] - hist[-5]) / 4

        trend_rate = {
            "temperature": trend("temperature", 0.45),
            "humidity": trend("humidity", 0.9),
            "ph": trend("ph", 0.025),
            "ec": trend("ec", 0.025),
            "water_level": trend("water_level", -1.9),
            "flow_rate": trend("flow_rate", -0.03),
            "light_lux": trend("light_lux", 12.0),
            "airflow": trend("airflow", -0.04),
        }

        def future(metric: str, hours: int) -> float:
            return avg[metric] + trend_rate[metric] * hours

        temp_6h = future("temperature", 6)
        humidity_6h = future("humidity", 6)
        light_10h = future("light_lux", 10)
        water_12h = future("water_level", 12)
        airflow_6h = future("airflow", 6)
        ec_8h = future("ec", 8)
        ph_8h = future("ph", 8)

        if humidity_6h > 75 or avg["humidity"] > 76 or trend_rate["humidity"] > 0.45:
            alerts.append(self._alert(
                "humidity_disease_risk", "High humidity disease risk predicted",
                "Humidity trend is likely to create mold pressure if the current ventilation/humidifier setup continues.",
                "Run AI Optimize or increase ventilation and reduce humidifier duty cycle.",
                "critical" if humidity_6h > 82 else "warning", 4, "humidity", True,
                [{"id": "increase_ventilation", "label": "Ventilation +18%"}, {"id": "lower_humidifier", "label": "Humidifier -15%"}],
                forecast=f"{avg['humidity']:.1f}% now → {humidity_6h:.1f}% forecast in 6h",
                problem="Future high humidity may cause fungal disease and wet leaf surfaces."
            ))

        if temp_6h > 26.0 or avg["temperature"] > 26.2 or trend_rate["temperature"] > 0.28:
            alerts.append(self._alert(
                "temperature_heat_stress", "High temperature stress predicted",
                "Temperature is projected to cross the safe crop climate envelope in the next control window.",
                "Run AI Optimize or lower lighting while increasing ventilation.",
                "critical" if temp_6h > 29 else "warning", 2, "temperature", True,
                [{"id": "increase_ventilation", "label": "Ventilation +18%"}, {"id": "dim_lights", "label": "Lighting -10%"}],
                forecast=f"{avg['temperature']:.1f}°C now → {temp_6h:.1f}°C forecast in 6h",
                problem="Future heat stress may reduce growth quality and raise transpiration load."
            ))

        hours_to_refill = max(1, math.ceil(max(0.0, avg["water_level"] - 30) / max(0.1, avg["flow_rate"] * 2.8)))
        if water_12h < 65 or hours_to_refill <= 18:
            alerts.append(self._alert(
                "reservoir_refill_window", "Reservoir refill window predicted",
                f"At the current flow rate, the reservoir may approach refill range in about {hours_to_refill} hours.",
                "Notify the on-site team and reduce circulation slightly until refill is confirmed.",
                "warning", min(hours_to_refill, 12), "water_level", True,
                [{"id": "reduce_circulation", "label": "Reduce circulation 12%"}, {"id": "mark_refill", "label": "Mark refill done"}],
                forecast=f"{avg['water_level']:.1f}% now → {water_12h:.1f}% forecast in 12h",
                problem="Future low reservoir level can starve circulation and nutrient delivery."
            ))

        if light_10h > 540 or (avg["light_lux"] > 500 and temp_6h > 26):
            alerts.append(self._alert(
                "lighting_heat_load", "Lighting heat-load risk predicted",
                "Lighting is likely to add avoidable heat load while the rack is already trending warm.",
                "Run AI Optimize or dim lights for one cycle and check the climate response.",
                "info", 3, "light_lux", True,
                [{"id": "dim_lights", "label": "Lighting -10%"}],
                forecast=f"{avg['light_lux']:.0f} lux now → {light_10h:.0f} lux effective load in 10h",
                problem="Future light/heat conflict may waste energy and push temperature above target."
            ))

        if airflow_6h < 1.15:
            alerts.append(self._alert(
                "airflow_stagnation", "Airflow stagnation predicted",
                "Air movement may fall too low for consistent rack-level climate mixing.",
                "Increase circulation before making nutrient or pH changes.",
                "warning", 5, "airflow", True,
                [{"id": "increase_ventilation", "label": "Ventilation +18%"}],
                forecast=f"{avg['airflow']:.2f} m/s now → {airflow_6h:.2f} m/s forecast in 6h",
                problem="Stagnant pockets can create uneven humidity and temperature between levels."
            ))

        if ph_8h > 6.8 or ph_8h < 5.8:
            alerts.append(self._alert(
                "ph_drift_forecast", "pH drift predicted",
                "pH trend may leave the nutrient target range soon.",
                "Notify the farmer for manual dosing confirmation. Do not automate acid/base dosing blindly.",
                "critical", 6, "ph", False,
                [{"id": "log_ph_check", "label": "Log pH inspection"}],
                forecast=f"pH {avg['ph']:.2f} now → {ph_8h:.2f} forecast in 8h",
                problem="Future pH drift can reduce nutrient uptake."
            ))

        if ec_8h > 2.2:
            alerts.append(self._alert(
                "ec_concentration_forecast", "Nutrient concentration rise predicted",
                "EC is projected to move above the target range if pump settings remain unchanged.",
                "Reduce nutrient pump duty and confirm reservoir concentration manually.",
                "warning", 6, "ec", True,
                [{"id": "reduce_nutrient_pump", "label": "Nutrient pump -10%"}],
                forecast=f"EC {avg['ec']:.2f} now → {ec_8h:.2f} mS/cm forecast in 8h",
                problem="Future EC concentration may cause root stress."
            ))

        for rack in racks:
            for level in rack["levels"]:
                if level["status"] == "imbalanced":
                    alerts.append(self._alert(
                        f"zone_{level['id']}_imbalance", f"Rack {level['rack']} Level {level['level']} imbalance predicted",
                        "Local rack conditions are uneven enough to create a future weak zone.",
                        "Run AI Optimize so the rack-level multivariable controller balances ventilation, light, and circulation together.",
                        "warning", 1, "microclimate", True,
                        [{"id": "balance_zone", "label": "Balance affected racks"}],
                        forecast=f"Rack {level['rack']} L{level['level']}: {level['temperature']}°C, {level['humidity']}% RH, airflow {level['airflow']} m/s",
                        problem="Future uneven microclimate may create one weak rack level while the average still looks acceptable."
                    ))

        if not alerts:
            alerts.append(self._alert(
                "stable_forecast", "No near-term issue predicted",
                "Current trends do not show a strong failure pattern for the next control window.",
                "Keep monitoring. Run another prediction scan after changing rack controls.",
                "info", 0, "system", False, [],
                forecast="All projected values remain inside the control envelope.",
                problem="No strong future problem detected."
            ))
        return [a for a in alerts if a["id"] not in self.dismissed_alerts]

    @staticmethod
    def _alert(alert_id: str, title: str, message: str, recommendation: str, severity: str,
               hours_until: int, metric: str, automatable: bool, actions: List[Dict[str, str]],
               forecast: str | None = None, problem: str | None = None) -> Dict[str, Any]:
        return {
            "id": alert_id,
            "title": title,
            "message": message,
            "recommendation": recommendation,
            "severity": severity,
            "hours_until": hours_until,
            "metric": metric,
            "automatable": automatable,
            "actions": actions,
            "forecast": forecast,
            "problem": problem,
        }

    def next_state(self) -> Dict[str, Any]:
        with self.lock:
            rows = self._rows_for_next_tick()
            sensors = self._build_sensors(rows)
            racks = self._build_racks(rows)
            score = self._health_score(sensors, racks)
            self.latest_rows = rows
            self.latest_racks = racks
            self._sync_global_controls()
            return {
                "timestamp": int(time.time()),
                "tick": self.tick_index,
                "sensors": sensors,
                "racks": racks,
                "plants": [asdict(p) for p in self.plants],
                "health_score": score,
                "plant_state": self.plant_state(score),
                "health_verdict": self.health_verdict(score),
                "alerts": self.last_predictive_alerts,
                "last_prediction_scan": self.last_prediction_scan,
                "controls": self.controls,
                "last_ai_plan": self.last_ai_plan,
                "event_log": self.read_events(limit=12),
            }

    def current_snapshot(self) -> Dict[str, Any]:
        return self.next_state()

    def run_predictive_scan(self) -> Dict[str, Any]:
        with self.lock:
            rows = self._rows_for_next_tick()
            sensors = self._build_sensors(rows)
            racks = self._build_racks(rows)
            self.latest_rows = rows
            self.latest_racks = racks
            self.last_predictive_alerts = self._make_alerts(rows, racks)
            self.last_prediction_scan = int(time.time())
            self.log_event("predictive_scan", {"alerts": len(self.last_predictive_alerts)})
            return {"alerts": self.last_predictive_alerts, "last_prediction_scan": self.last_prediction_scan}

    def log_event(self, event_type: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        item = {"ts": int(time.time()), "type": event_type, "detail": detail}
        with EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        return item

    def read_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not EVENT_LOG.exists():
            return []
        lines = EVENT_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]

    def manual_override(self, target: str, value: Any) -> Dict[str, Any]:
        with self.lock:
            if target not in self.controls:
                raise ValueError(f"Unknown control target: {target}")
            if isinstance(self.controls[target], bool):
                self.controls[target] = bool(value)
            else:
                value = int(max(0, min(100, int(value))))
                self.controls[target] = value
                if target in ACTUATORS:
                    for rack_controls in self.rack_controls.values():
                        rack_controls[target] = value
            event = self.log_event("manual_override", {"target": target, "value": self.controls[target], "scope": "all_racks"})
            return {"controls": self.controls, "rack_controls": self.rack_controls, "event": event}

    def rack_manual_override(self, rack_id: str, target: str, value: Any) -> Dict[str, Any]:
        with self.lock:
            if rack_id not in self.rack_controls:
                raise ValueError(f"Unknown rack: {rack_id}")
            if target not in ACTUATORS:
                raise ValueError(f"Unknown rack control target: {target}")
            self.rack_controls[rack_id][target] = int(max(0, min(100, int(value))))
            self._sync_global_controls()
            event = self.log_event("rack_manual_override", {"rack": rack_id, "target": target, "value": self.rack_controls[rack_id][target]})
            return {"rack": rack_id, "controls": dict(self.rack_controls[rack_id]), "event": event}

    def optimize_rack(self, rack_id: str, apply: bool = True) -> Dict[str, Any]:
        with self.lock:
            if rack_id not in self.rack_controls:
                raise ValueError(f"Unknown rack: {rack_id}")
            racks = self.latest_racks or self._build_racks(self._rows_for_next_tick())
            rack = next((r for r in racks if r["id"] == rack_id), None)
            if not rack:
                raise ValueError(f"Rack not found: {rack_id}")
            fallback = build_rack_climate_plan(rack_id, rack["levels"], self.rack_controls[rack_id])
            rack_snapshot = {
                "id": rack_id,
                "levels": rack["levels"],
                "controls": self.rack_controls[rack_id],
                "fallback_plan": fallback,
            }
            plan = create_rack_ai_plan(rack_snapshot, fallback)
            if apply:
                self.rack_controls[rack_id] = apply_plan_to_controls(self.rack_controls[rack_id], plan)
                self._sync_global_controls()
            event = self.log_event("rack_optimized", {"rack": rack_id, "mode": plan.get("mode"), "applied": apply, "summary": plan.get("summary")})
            return {"rack": rack_id, "controls": dict(self.rack_controls[rack_id]), "plan": plan, "event": event}

    def dismiss_alert(self, alert_id: str) -> Dict[str, Any]:
        self.dismissed_alerts.add(alert_id)
        self.last_predictive_alerts = [a for a in self.last_predictive_alerts if a["id"] != alert_id]
        event = self.log_event("alert_dismissed", {"alert_id": alert_id})
        return {"dismissed": sorted(self.dismissed_alerts), "alerts": self.last_predictive_alerts, "event": event}

    def _apply_action_to_all_racks(self, control: str, delta: int) -> None:
        for rack_controls in self.rack_controls.values():
            rack_controls[control] = int(max(0, min(100, rack_controls[control] + delta)))
        self._sync_global_controls()

    def apply_action(self, action_id: str, alert_id: str | None = None) -> Dict[str, Any]:
        with self.lock:
            if action_id == "ai_optimize":
                return self.optimize_all_racks(alert_id=alert_id, source="alert_action")
            elif action_id == "increase_ventilation":
                self._apply_action_to_all_racks("ventilation", 18)
            elif action_id == "lower_humidifier":
                self._apply_action_to_all_racks("humidifier", -15)
            elif action_id == "dim_lights":
                self._apply_action_to_all_racks("lighting", -10)
            elif action_id == "reduce_circulation":
                self._apply_action_to_all_racks("circulation", -12)
            elif action_id == "reduce_nutrient_pump":
                self._apply_action_to_all_racks("nutrient_pump", -10)
            elif action_id == "balance_zone":
                for rack_id in list(self.rack_controls):
                    self.optimize_rack(rack_id, apply=True)
            elif action_id == "mark_refill":
                self.log_event("reservoir_refill", {"source": "operator"})
            elif action_id == "log_ph_check":
                self.log_event("ph_inspection_required", {"source": "operator"})
            else:
                raise ValueError(f"Unknown action_id: {action_id}")
            if alert_id:
                self.dismissed_alerts.add(alert_id)
                self.last_predictive_alerts = [a for a in self.last_predictive_alerts if a["id"] != alert_id]
            event = self.log_event("action_applied", {"action_id": action_id, "alert_id": alert_id, "controls": self.controls})
            return {"controls": self.controls, "rack_controls": self.rack_controls, "alerts": self.last_predictive_alerts, "event": event}

    def optimize_all_racks(self, alert_id: str | None = None, source: str = "dashboard") -> Dict[str, Any]:
        with self.lock:
            if not self.latest_racks:
                rows = self._rows_for_next_tick()
                self.latest_rows = rows
                self.latest_racks = self._build_racks(rows)
            actions: List[Dict[str, Any]] = []
            rack_results: List[Dict[str, Any]] = []
            priority_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            worst_priority = "low"
            used_gemini = False

            for rack in self.latest_racks:
                rack_id = rack["id"]
                fallback = build_rack_climate_plan(rack_id, rack["levels"], self.rack_controls[rack_id])
                rack_snapshot = {
                    "id": rack_id,
                    "levels": rack["levels"],
                    "controls": self.rack_controls[rack_id],
                    "fallback_plan": fallback,
                    "triggered_by_alert": alert_id,
                }
                plan = create_rack_ai_plan(rack_snapshot, fallback)
                active_metrics = {a.get("metric") for a in self.last_predictive_alerts}
                if plan.get("actions") and all(a.get("control") == "maintain" for a in plan.get("actions", [])) and active_metrics:
                    targets = dict(plan.get("target_controls") or self.rack_controls[rack_id])
                    preventive_actions: List[Dict[str, Any]] = []
                    if "temperature" in active_metrics or "humidity" in active_metrics or "light_lux" in active_metrics:
                        targets["ventilation"] = max(int(targets.get("ventilation", 50)), 72)
                        preventive_actions.append({"control": "ventilation", "target_percent": targets["ventilation"], "reason": "Predictive scan shows future heat/humidity risk; increase exhaust before the rack leaves range."})
                    if "temperature" in active_metrics or "light_lux" in active_metrics:
                        targets["lighting"] = min(int(targets.get("lighting", 74)), 62)
                        preventive_actions.append({"control": "lighting", "target_percent": targets["lighting"], "reason": "Predictive scan shows future heat-load risk; dim lighting for one control cycle."})
                    if "humidity" in active_metrics:
                        targets["humidifier"] = min(int(targets.get("humidifier", 30)), 14)
                        preventive_actions.append({"control": "humidifier", "target_percent": targets["humidifier"], "reason": "Predictive scan shows future disease pressure; reduce humidifier duty."})
                    if "water_level" in active_metrics:
                        targets["circulation"] = min(int(targets.get("circulation", 58)), 50)
                        preventive_actions.append({"control": "circulation", "target_percent": targets["circulation"], "reason": "Predictive scan shows reservoir refill window; reduce circulation until refill is confirmed."})
                    if preventive_actions:
                        plan = dict(plan)
                        plan["priority"] = "medium"
                        plan["target_controls"] = targets
                        plan["actions"] = preventive_actions
                        plan["summary"] = f"Rack {rack_id} was inside range now, but predictive alerts require preventive control changes."
                        plan["expected_outcome"] = "Prevent the forecasted heat, humidity, and reservoir risks before they become current faults."

                used_gemini = used_gemini or plan.get("mode") == "gemini"
                self.rack_controls[rack_id] = apply_plan_to_controls(self.rack_controls[rack_id], plan)
                if priority_rank.get(plan.get("priority", "low"), 0) > priority_rank.get(worst_priority, 0):
                    worst_priority = plan.get("priority", "low")
                for action in plan.get("actions", []):
                    if action.get("control") == "maintain":
                        continue
                    item = dict(action)
                    item["rack"] = rack_id
                    actions.append(item)
                rack_results.append({"rack": rack_id, "plan": plan, "controls": dict(self.rack_controls[rack_id])})

            self._sync_global_controls()
            if alert_id:
                self.dismissed_alerts.add(alert_id)
                self.last_predictive_alerts = [a for a in self.last_predictive_alerts if a["id"] != alert_id]

            self.last_ai_plan = {
                "mode": "gemini" if used_gemini else "local_multivariable",
                "summary": f"Applied multivariable optimization to {len(rack_results)} rack(s). Ventilation, lighting, humidifier, circulation, and nutrient pump targets were updated.",
                "priority": worst_priority,
                "actions": actions[:12] or [{"control": "maintain", "change_percent": 0, "reason": "All racks were already inside the control envelope."}],
                "expected_outcome": "The next telemetry ticks should show lower heat/humidity pressure and a recovering health score if the simulated conditions continue.",
                "operator_note": "This is applied to the simulator immediately. In real hardware, route these values through a supervised actuator layer.",
            }
            event = self.log_event("ai_optimization_applied", {
                "source": source, "alert_id": alert_id, "mode": self.last_ai_plan["mode"],
                "racks": [r["rack"] for r in rack_results], "controls": self.controls,
            })
            return {"plan": self.last_ai_plan, "rack_results": rack_results, "controls": self.controls, "rack_controls": self.rack_controls, "alerts": self.last_predictive_alerts, "event": event}

    def notify_alert(self, alert_id: str, recipient: str | None = None) -> Dict[str, Any]:
        with self.lock:
            recipient = recipient or "onsite-team@farm.local"
            alert = next((a for a in self.last_predictive_alerts if a["id"] == alert_id), None)
            if not alert:
                raise ValueError("Alert not found. Run a prediction scan first or choose an active alert.")
            subject = f"Smart Farm Alert: {alert['title']}"
            body = (
                f"Predictive alert: {alert['title']}\n"
                f"Severity: {alert['severity']}\n"
                f"Problem: {alert.get('problem') or alert['message']}\n"
                f"Forecast: {alert.get('forecast') or 'No forecast details'}\n"
                f"Recommendation: {alert['recommendation']}"
            )
            mailto_url = f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(body)}"
            event = self.log_event("email_notification_prepared", {"alert_id": alert_id, "recipient": recipient, "subject": subject})
            return {"sent": True, "simulated": True, "recipient": recipient, "subject": subject, "body": body, "mailto_url": mailto_url, "event": event}

    def store_ai_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.last_ai_plan = plan
            event = self.log_event("ai_plan_created", {"summary": plan.get("summary"), "mode": plan.get("mode")})
            return {"plan": plan, "event": event}

    def update_plant(self, plant_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"growth_stage", "days_in_stage", "health_score", "rack", "level", "slot"}
        with self.lock:
            plant = next((p for p in self.plants if p.id == plant_id), None)
            if not plant:
                raise ValueError("Plant not found")
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key in {"days_in_stage", "level", "slot"}:
                    value = int(value)
                if key == "health_score":
                    value = float(max(0, min(100, float(value))))
                setattr(plant, key, value)
            self.save_plants()
            event = self.log_event("plant_updated", {"plant_id": plant_id, "fields": fields})
            return {"plant": asdict(plant), "event": event}
