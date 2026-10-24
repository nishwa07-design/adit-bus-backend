# ADIT Campus Transit Tracker — Python Backend (FastAPI)

This is the **Python part of your bus tracker project**. The React frontend
(hosted on Lovable) was already built with hooks to call a Python FastAPI
service — this code fills exactly that role, with **no changes to the
frontend, its design, or how it works**.

## What the Python code does

| File | Role |
| --- | --- |
| `main.py` | The whole service: route data, bus model, GPS simulation, ETA algorithm, REST API |
| `test_service.py` | Automated tests proving every endpoint works |
| `requirements.txt` | The two libraries needed (`fastapi`, `uvicorn`) |

Core Python concepts used (good viva points):

- **Haversine formula** (`haversine_km`) — great-circle distance between two
  GPS coordinates, the standard way to compute distance on Earth.
- **ETA algorithm** — `ETA = remaining distance / effective speed`, with a
  traffic factor (`TRAFFIC_FACTOR_DELAYED`) slowing delayed buses.
- **Background thread** — `threading` moves every active bus every 2 seconds,
  simulating live GPS movement.
- **Object-oriented model** — `Bus`, `RouteDef`, `StopDef` classes.
- **REST API with FastAPI** — decorator-based endpoints, Pydantic request
  validation, automatic API documentation.

## How to run it (2 minutes)

1. Install Python 3.10+ from [python.org](https://python.org)
   (tick "Add Python to PATH" during install).
2. In a terminal, inside this folder:
   ```bash
   pip install -r requirements.txt
   python main.py
   ```
3. Open **http://localhost:8000/docs** — FastAPI auto-generates an
   interactive API page. This is the single best thing to show in a viva:
   live Python API, clickable, with real bus data.

## The endpoints

| Endpoint | Method | What it does |
| --- | --- | --- |
| `/health` | GET | Status probe the frontend uses to check the service |
| `/api/buses` | GET | Fleet snapshot: every bus's GPS position, status, driver, seats and Python-computed ETA — the frontend polls this every 20 s |
| `/api/buses/{bus_number}/eta` | GET | Full ETA breakdown for one bus (distance, speed, minutes, wall-clock arrival time) |
| `/api/buses/{bus_number}/location` | POST | A driver app pushes a GPS fix; the service adopts it and recomputes the ETA |

## How it connects to the app

The frontend already contains the code to use this service. When
`VITE_BUS_API_BASE_URL` is set to this service's URL, the app's live bus
positions and status come **from Python**; with it empty, the app falls back
to its cloud database, so nothing breaks while the service is off.

For a demo on your laptop: run `python main.py`, and the app is pointed at
`http://localhost:8000` (browsers allow a page to call its own localhost).
Tell Lovable when you want that switch turned on — it's one setting, no
frontend code changes.

## Deploy to Render (free, gives the service a public URL)

1. Push this folder to a GitHub repository (create the repo at github.com,
   then drag-and-drop `main.py`, `requirements.txt`, `test_service.py`,
   `README.md` and `.gitignore` onto the repo page — no git commands needed).
2. Sign up at render.com with your GitHub account.
3. Click **New +** -> **Web Service** -> connect the repository.
4. Fill the form:
   - **Language / Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance type:** Free
5. Click **Create Web Service**. After 2-3 minutes Render shows a URL like
   `https://adit-bus-backend.onrender.com`.
6. Test it: open `<that-url>/docs` in a browser and run `GET /api/buses`.

The service reads Render's `PORT` environment variable automatically
(`PORT = int(os.environ.get("PORT", "8000"))` in main.py), so the same file
runs locally on port 8000 and on Render at whatever port Render assigns.

## Suggested viva walkthrough

1. Open `main.py` — show the `Bus` class and `haversine_km`.
2. Run `python main.py`, open `http://localhost:8000/docs`, hit
   **GET /api/buses** and watch coordinates and `eta_minutes` change on
   repeated calls (the simulation thread is moving the buses).
3. Show the tracker UI and point out the same buses appear there —
   "the React frontend polls this Python service every 20 seconds."
4. POST a new location for a bus and show the ETA change instantly.
