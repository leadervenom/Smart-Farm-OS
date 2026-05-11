from __future__ import annotations

import json
import os
from typing import Any, Dict, List

try:
    from google import genai
except Exception:  # Package may not be installed until requirements.txt is installed.
    genai = None

from services.climate_control import ACTUATORS


def _compact_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "health_score": snapshot.get("health_score"),
        "plant_state": snapshot.get("plant_state"),
        "sensors": [
            {"id": s["id"], "value": s["value"], "unit": s["unit"], "status": s["status"],
             "ideal_min": s["ideal_min"], "ideal_max": s["ideal_max"]}
            for s in snapshot.get("sensors", [])
        ],
        "racks": [
            {"id": r["id"], "climate_plan": r.get("climate_plan"), "controls": r.get("controls"), "levels": [
                {"id": l["id"], "level": l["level"], "status": l["status"],
                 "temperature": l["temperature"], "humidity": l["humidity"], "ph": l["ph"],
                 "ec": l["ec"], "airflow": l["airflow"], "light_lux": l["light_lux"],
                 "plants": [{"name": p["name"], "stage": p["growth_stage"], "live_health": p.get("live_health")} for p in l.get("plants", [])]}
                for l in r.get("levels", [])
            ]}
            for r in snapshot.get("racks", [])
        ],
        "alerts": snapshot.get("alerts", []),
        "controls": snapshot.get("controls", {}),
    }


def _local_optimization(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic fallback. Not random. Gemini will replace this if GEMINI_API_KEY is set."""
    sensors = {s["id"]: s for s in snapshot.get("sensors", [])}
    actions: List[Dict[str, Any]] = []
    reasons: List[str] = []

    def add(control: str, change: int, reason: str) -> None:
        actions.append({"control": control, "change_percent": change, "reason": reason})
        reasons.append(reason)

    if sensors.get("humidity", {}).get("status") in {"warning", "critical"}:
        add("ventilation", +18, "Humidity is outside the hydroponic target range; airflow reduces mold pressure.")
        add("humidifier", -15, "Humidifier reduction prevents further disease-risk buildup.")
    if sensors.get("temperature", {}).get("status") in {"warning", "critical"}:
        add("ventilation", +12, "Temperature drift is best corrected with ventilation before nutrient changes.")
        add("lighting", -8, "Light dimming reduces heat load while preserving growth range.")
    if sensors.get("ph", {}).get("status") == "critical":
        actions.append({"control": "operator_check", "change_percent": 0, "reason": "pH dosing requires manual confirmation; do not automate chemical dosing blindly."})
    if sensors.get("light_lux", {}).get("value", 0) > 560:
        add("lighting", -10, "Current light level can be reduced to save energy without leaving target range.")
    if not actions:
        actions.append({"control": "maintain", "change_percent": 0, "reason": "No high-risk drift detected. Continue monitoring and preserve stable controls."})

    return {
        "mode": "local_fallback",
        "summary": "Local optimizer produced a safe control plan. Add GEMINI_API_KEY to use Gemini reasoning.",
        "priority": "medium" if len(actions) > 1 else "low",
        "actions": actions[:6],
        "expected_outcome": "Stabilize climate envelope over the next 2–6 hours and reduce unnecessary energy use.",
        "operator_note": "Fallback plan is rule-based and deterministic. It is not a replacement for AI reasoning.",
    }


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def create_ai_plan(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not api_key or genai is None:
        return _local_optimization(snapshot)

    system_task = """
You are the optimization engine for Smart Farm OS, a hydroponic vertical farming digital twin.
Return only valid JSON with this schema:
{
  "mode": "gemini",
  "summary": "one sentence",
  "priority": "low|medium|high|critical",
  "actions": [
    {"control": "ventilation|lighting|nutrient_pump|circulation|humidifier|operator_check", "change_percent": number, "reason": "short reason"}
  ],
  "expected_outcome": "prediction for the next 2-6 hours",
  "operator_note": "anything the human must verify"
}
Rules:
- Optimize rack conditions and future risk, not cosmetic UI.
- Keep chemical dosing manual unless data is strong.
- Favor reversible actions first: ventilation, airflow, lighting, circulation.
- Mention rack/level if a zone is the cause.
""".strip()
    payload = _compact_snapshot(snapshot)
    prompt = f"{system_task}\n\nCurrent farm snapshot:\n{json.dumps(payload, indent=2)}"

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=model, contents=prompt)
        plan = json.loads(_strip_json_fence(response.text))
        plan["mode"] = "gemini"
        return plan
    except Exception as exc:
        fallback = _local_optimization(snapshot)
        fallback["operator_note"] = f"Gemini call failed, using local fallback: {exc}"
        return fallback


def _normalize_rack_ai_plan(plan: Dict[str, Any], fallback_plan: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(fallback_plan)
    normalized.update({k: v for k, v in plan.items() if k not in {"actions", "target_controls"}})
    normalized["mode"] = plan.get("mode", "gemini")
    normalized["summary"] = plan.get("summary") or fallback_plan.get("summary")
    normalized["expected_outcome"] = plan.get("expected_outcome") or fallback_plan.get("expected_outcome")
    normalized["operator_note"] = plan.get("operator_note", "Gemini generated this rack-level plan.")

    target_controls = dict(fallback_plan.get("target_controls") or {})
    for key, value in (plan.get("target_controls") or {}).items():
        if key in ACTUATORS:
            target_controls[key] = int(max(0, min(100, round(float(value)))))

    actions: List[Dict[str, Any]] = []
    for item in plan.get("actions", []):
        control = item.get("control")
        if control not in ACTUATORS and control != "operator_check":
            continue
        action = {
            "control": control,
            "reason": item.get("reason", "Gemini rack optimization action."),
        }
        if item.get("target_percent") is not None and control in ACTUATORS:
            target = int(max(0, min(100, round(float(item["target_percent"])))) )
            action["target_percent"] = target
            target_controls[control] = target
        elif item.get("change_percent") is not None and control in ACTUATORS:
            action["change_percent"] = int(round(float(item["change_percent"])))
        else:
            action["change_percent"] = 0
        actions.append(action)

    normalized["target_controls"] = target_controls
    normalized["actions"] = actions or fallback_plan.get("actions", [])
    return normalized


def create_rack_ai_plan(rack_snapshot: Dict[str, Any], fallback_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Generate one rack optimization plan. Uses Gemini if configured, otherwise deterministic fallback."""
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not api_key or genai is None:
        plan = dict(fallback_plan)
        plan["mode"] = "local_multivariable"
        plan["operator_note"] = "No GEMINI_API_KEY detected, so the rack optimizer used the deterministic multivariable controller."
        return plan

    system_task = """
You are Smart Farm OS rack-level climate optimizer.
Return only valid JSON using this schema:
{
  "mode": "gemini",
  "summary": "one sentence explaining the rack decision",
  "priority": "low|medium|high|critical",
  "target_controls": {
    "ventilation": 0-100,
    "lighting": 0-100,
    "nutrient_pump": 0-100,
    "circulation": 0-100,
    "humidifier": 0-100
  },
  "actions": [
    {"control": "ventilation|lighting|nutrient_pump|circulation|humidifier|operator_check", "target_percent": number, "reason": "short reason"}
  ],
  "expected_outcome": "what should happen in the next 2-6 hours",
  "operator_note": "what the farmer must verify"
}
Rules:
- Optimize this specific rack only.
- Consider multivariable conflicts: ventilation affects heat and humidity, lighting affects heat, circulation affects stagnant zones.
- Do not automate acid/base dosing. Use operator_check for pH chemical changes.
- Prefer reversible changes first.
""".strip()
    prompt = f"{system_task}\n\nRack snapshot:\n{json.dumps(rack_snapshot, indent=2)}\n\nDeterministic fallback plan for reference:\n{json.dumps(fallback_plan, indent=2)}"

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=model, contents=prompt)
        raw_plan = json.loads(_strip_json_fence(response.text))
        raw_plan["mode"] = "gemini"
        return _normalize_rack_ai_plan(raw_plan, fallback_plan)
    except Exception as exc:
        plan = dict(fallback_plan)
        plan["mode"] = "local_multivariable"
        plan["operator_note"] = f"Gemini rack optimization failed, using deterministic multivariable plan: {exc}"
        return plan
