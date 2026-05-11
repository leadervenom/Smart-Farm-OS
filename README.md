# Smart Farm OS — Python Digital Twin App

A mobile-sized Python app for a hydroponic vertical farming system.

It implements the flow:

**Monitoring → Analysing Information → Deciding Solution → Automating Action → Documentation**

## What is included

- Python backend using the standard library HTTP server
- Simulated CSV telemetry from `data/sensor_stream.csv`
- Mobile phone-sized UI
- 2D rack digital twin with clickable rack levels
- Expanded rack inspection view with level telemetry only
- Climate Twin page with rack-level multivariable control
- Rack-specific manual override sliders
- Rack-specific optimization button for each rack
- Predictive Alerts page that runs only when you press `Run Prediction Scan`
- Alert cards include AI Optimize and Notify On-site Team buttons
- Gemini API backend placeholder using `.env`; local deterministic optimizer works without it
- Local deterministic fallback optimizer when Gemini key is not added
- Documentation/event log for prediction scans, rack overrides, optimization plans, and actions

## Folder structure

```text
smart_farm_os_python_climate_v2/
├─ app.py
├─ requirements.txt
├─ .env.example
├─ data/
│  ├─ plants.csv
│  └─ sensor_stream.csv
├─ services/
│  ├─ ai_engine.py
│  ├─ climate_control.py
│  └─ data_store.py
├─ templates/
│  └─ index.html
├─ static/
│  ├─ css/style.css
│  ├─ js/
│  │  ├─ app.js
│  │  ├─ core/
│  │  │  ├─ navigation.js
│  │  │  └─ state.js
│  │  └─ features/
│  │     ├─ alerts.js
│  │     ├─ climate.js
│  │     ├─ dashboard.js
│  │     ├─ docs.js
│  │     └─ twin.js
│  ├─ img/rack_art.svg
│  └─ manifest.json
└─ logs/
```

## Run on Windows / VS Code

```bash
cd smart_farm_os_python_climate_v2
python -m venv .venv
.venv\Scripts\activate
copy .env.example .env
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

In Chrome DevTools, use device toolbar and choose a medium phone size.

## Gemini API key placement

The API key is backend-only.

Put it in `.env`:

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
```

Do not put your API key inside `static/js`, `index.html`, or any frontend file.

The backend reads it inside:

```text
services/ai_engine.py
```

## What changed in this version

Latest patch from this handoff:

- Removed the Plants bottom-nav column and removed the visible plant database page.
- Dashboard now shows a Good / Bad / Critical overall system verdict beside the health score.
- Dashboard `AI Optimize System` now applies multivariable rack settings immediately.
- Predictive Alerts now show forecasted future problems such as high humidity, high temperature, reservoir refill window, and lighting heat-load risk.
- Alerts include `AI Optimize` and `Notify On-site Team` buttons. The email button opens a prepared mailto draft and logs the notification event.
- Simulated telemetry now drifts under weak controls and recovers after optimization, so the prototype is easier to demonstrate.

Previous climate patch:

- The previous manual override block was removed from the Alerts page.
- Alerts now run as a one-time prediction scan, not a continuously recalculated loop.
- The Climate page now contains the multivariable rack digital twin.
- Each rack has visible actuator values: ventilation, lighting, nutrient pump, circulation, and humidifier.
- Manual override is now rack-specific and visually attached to the selected rack.
- Each rack has an Optimize Rack button. The Dashboard and Alerts optimizer now applies rack control values, not only text recommendations.
- The simulator intentionally ramps heat/humidity/light/reservoir pressure under weak controls so the demo visibly shows degradation and recovery.
- Frontend files are separated by feature so the app is easier to maintain.

## Replace the rack drawing with your own image

The default rack drawing is:

```text
static/img/rack_art.svg
```

Replace that file with your own rack art. Keep the filename the same if you do not want to edit the HTML.

Use a vertical drawing with roughly this ratio:

```text
760 x 980
```

The dashboard expanded twin overlays clickable level hotspots on top of the image.

## Convert to real hardware later

Replace `data/sensor_stream.csv` with one of these later:

- ESP32 HTTP push endpoint
- MQTT subscriber
- Firebase realtime data
- Serial input from sensors

Main replacement point:

```text
services/data_store.py -> FarmStore._rows_for_next_tick()
```

Optional Gemini setup:

```bash
pip install -r requirements.txt
```
