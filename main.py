"""
ADIT Campus Transit Tracker — Python Backend Service (FastAPI)
================================================================

This is the Python "brain" of the ADIT College Bus Manager project.
The React frontend (hosted on Lovable) talks to THIS service over HTTP:

    Frontend  --GET /api/buses-->  this FastAPI service
              <--JSON bus list--   (live positions + Python-computed ETAs)

What this service does
----------------------
1.  Keeps an in-memory fleet of buses (A01-A05, matching the app's cloud
    database exactly), each assigned to a campus route with ordered stops.
2.  Simulates live GPS movement: a background thread advances every
    "on route" bus along the road segments between its stops.
3.  Computes the Estimated Time of Arrival (ETA) at the next stop in
    pure Python using the haversine distance formula and a speed model
    (with a traffic factor for delayed buses).
4.  Exposes the exact REST endpoints the frontend already expects:

        GET  /health                              -> service status probe
        GET  /api/buses                           -> fleet snapshot for the UI
        GET  /api/buses/{bus_number}/eta          -> Python-computed ETA details
        POST /api/buses/{bus_number}/location     -> driver-app location update

The frontend reads VITE_BUS_API_BASE_URL to find this service.
With the base URL empty (or the service unreachable), the app falls back
to its cloud database, so this service can be started/stopped at any time
without breaking the UI.

Run it with:
    pip install -r requirements.txt
    python main.py
Then open http://localhost:8000/docs for the auto-generated API docs.
"""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

HOST = "0.0.0.0"          # listen on all interfaces so the UI can reach it
PORT = int(os.environ.get("PORT", "8000"))  # hosts like Render inject PORT; local runs keep 8000

AVG_BUS_SPEED_KMPH = 30.0     # average city-bus speed
TRAFFIC_FACTOR_DELAYED = 1.8  # delayed buses effectively crawl (x1.8 slower)
TICK_SECONDS = 2.0            # how often the GPS simulation updates
ARRIVAL_RADIUS_KM = 0.05      # "we reached the stop" threshold (~50 m)

# --------------------------------------------------------------------------
# Route data (approximate coordinates around Anand / Vallabh Vidyanagar)
# --------------------------------------------------------------------------

ADIT = (22.5525, 72.9230)  # A. D. Patel Institute of Technology campus

class StopDef:
    def __init__(self, name: str, lat: float, lng: float,
                 arrival: str, departure: str):
        self.name = name
        self.lat = lat
        self.lng = lng
        self.arrival = arrival
        self.departure = departure

class RouteDef:
    def __init__(self, code: str, name: str, start: str, destination: str,
                 stops: List[StopDef]):
        self.code = code
        self.name = name
        self.start = start
        self.destination = destination
        self.stops = stops

ROUTES: List[RouteDef] = [
    RouteDef("R1", "Anand City Route", "Anand Railway Station", "ADIT Campus", [
        StopDef("Anand Railway Station", 22.5645, 72.9490, "07:00 AM", "07:02 AM"),
        StopDef("Anand Bus Stand", 22.5633, 72.9410, "07:10 AM", "07:12 AM"),
        StopDef("Town Hall Area", 22.5638, 72.9360, "07:20 AM", "07:22 AM"),
        StopDef("Vallabh Vidyanagar", 22.5490, 72.9270, "07:35 AM", "07:37 AM"),
        StopDef("New Vallabh Vidyanagar", 22.5545, 72.9250, "07:45 AM", "07:47 AM"),
        StopDef("ADIT Campus", ADIT[0], ADIT[1], "08:00 AM", "08:00 AM"),
    ]),
    RouteDef("R2", "Vallabh Vidyanagar Route", "Vallabh Vidyanagar", "ADIT Campus", [
        StopDef("Vallabh Vidyanagar", 22.5490, 72.9270, "07:10 AM", "07:12 AM"),
        StopDef("Sardar Patel University Area", 22.5505, 72.9300, "07:20 AM", "07:22 AM"),
        StopDef("Bakrol", 22.5595, 72.9365, "07:35 AM", "07:37 AM"),
        StopDef("New Vallabh Vidyanagar", 22.5545, 72.9250, "07:50 AM", "07:52 AM"),
        StopDef("ADIT Campus", ADIT[0], ADIT[1], "08:05 AM", "08:05 AM"),
    ]),
    RouteDef("R3", "Karamsad / Anand Side Route", "Anand City", "ADIT Campus", [
        StopDef("Anand City", 22.5638, 72.9360, "06:55 AM", "06:57 AM"),
        StopDef("Karamsad", 22.5460, 72.9110, "07:10 AM", "07:12 AM"),
        StopDef("Vallabh Vidyanagar", 22.5490, 72.9270, "07:30 AM", "07:32 AM"),
        StopDef("New Vallabh Vidyanagar", 22.5545, 72.9250, "07:45 AM", "07:47 AM"),
        StopDef("ADIT Campus", ADIT[0], ADIT[1], "08:00 AM", "08:00 AM"),
    ]),
    RouteDef("R4", "Borsad Side Route", "Borsad", "ADIT Campus", [
        StopDef("Borsad", 22.4060, 72.8980, "06:45 AM", "06:47 AM"),
        StopDef("Anand Road", 22.4700, 72.9050, "07:10 AM", "07:12 AM"),
        StopDef("Anand City", 22.5638, 72.9360, "07:25 AM", "07:27 AM"),
        StopDef("Vallabh Vidyanagar", 22.5490, 72.9270, "07:45 AM", "07:47 AM"),
        StopDef("ADIT Campus", ADIT[0], ADIT[1], "08:05 AM", "08:05 AM"),
    ]),
    RouteDef("R5", "Petlad Side Route", "Petlad", "ADIT Campus", [
        StopDef("Petlad", 22.4850, 72.8200, "06:40 AM", "06:42 AM"),
        StopDef("Anand Road", 22.5200, 72.8700, "07:05 AM", "07:07 AM"),
        StopDef("Anand City", 22.5600, 72.9400, "07:20 AM", "07:22 AM"),
        StopDef("New Vallabh Vidyanagar", 22.5545, 72.9250, "07:45 AM", "07:47 AM"),
        StopDef("ADIT Campus", ADIT[0], ADIT[1], "08:00 AM", "08:00 AM"),
    ]),
]


# --------------------------------------------------------------------------
# Fleet model
# --------------------------------------------------------------------------

class Bus:
    """One bus: position, status and its assigned route."""

    def __init__(self, bus_number: str, name: str, route: RouteDef,
                 driver: str, driver_phone: str, status: str = "on_route",
                 capacity: int = 45, seats_taken: int = 0, seed_fraction: float = 0.0):
        self.bus_number = bus_number
        self.name = name
        self.route = route
        self.driver = driver
        self.driver_phone = driver_phone
        self.status = status
        self.capacity = capacity
        self.seats_taken = seats_taken

        # Start the bus partway along its route so the fleet looks alive.
        self.stop_index = 0                                # last stop passed
        self.lat, self.lng = route.stops[0].lat, route.stops[0].lng
        self._advance_position(seed_fraction)

    # -- position helpers ---------------------------------------------------

    def current_target(self) -> Optional[StopDef]:
        """The stop the bus is heading towards (None after the last stop)."""
        if self.stop_index + 1 < len(self.route.stops):
            return self.route.stops[self.stop_index + 1]
        return None

    def next_stop_name(self) -> Optional[str]:
        target = self.current_target()
        return target.name if target else None

    def _segment_progress(self) -> float:
        """0.0..1.0 fraction travelled between the last stop and the target."""
        if self.stop_index + 1 >= len(self.route.stops):
            return 1.0
        a = self.route.stops[self.stop_index]
        b = self.route.stops[self.stop_index + 1]
        total = haversine_km(a.lat, a.lng, b.lat, b.lng)
        if total <= 0:
            return 1.0
        travelled = haversine_km(a.lat, a.lng, self.lat, self.lng)
        return max(0.0, min(1.0, travelled / total))

    def _advance_position(self, fraction: float) -> None:
        """Move the bus to `fraction` of the way to its next stop."""
        if self.stop_index + 1 >= len(self.route.stops):
            return
        a = self.route.stops[self.stop_index]
        b = self.route.stops[self.stop_index + 1]
        self.lat = a.lat + (b.lat - a.lat) * fraction
        self.lng = a.lng + (b.lng - a.lng) * fraction

    def tick(self, dt: float) -> None:
        """Advance the simulated GPS position by `dt` seconds."""
        if self.status not in ("on_route", "delayed"):
            return

        speed_kmph = AVG_BUS_SPEED_KMPH
        if self.status == "delayed":
            speed_kmph /= TRAFFIC_FACTOR_DELAYED  # traffic slows the bus down

        distance = speed_kmph * (dt / 3600.0)  # km travelled this tick
        target = self.current_target()
        if target is None:
            return

        remaining = haversine_km(self.lat, self.lng, target.lat, target.lng)
        if remaining <= ARRIVAL_RADIUS_KM:
            # Reached the stop: advance the stop index; at the end of the
            # route mark the bus as arrived at ADIT campus.
            self.stop_index += 1
            if self.stop_index + 1 >= len(self.route.stops):
                # Reached ADIT campus: restart the round trip so the demo
                # fleet keeps moving all day (positions are simulated).
                self.stop_index = 0
                self.lat = self.route.stops[0].lat
                self.lng = self.route.stops[0].lng
                return
            a = self.route.stops[self.stop_index]
            self.lat, self.lng = a.lat, a.lng
            return

        # Move `distance` km along the straight segment towards the target.
        total = haversine_km(self.route.stops[self.stop_index].lat,
                             self.route.stops[self.stop_index].lng,
                             target.lat, target.lng)
        step = min(1.0, distance / total) if total > 0 else 1.0
        new_progress = min(1.0, self._segment_progress() + step)
        self._advance_position(new_progress)

    # -- ETA computation (the core Python algorithm) ------------------------

    def eta_minutes(self) -> Optional[float]:
        """Minutes until the bus reaches its next stop.

        ETA = remaining distance / effective speed, with a traffic factor
        applied for delayed buses. This is the number the UI displays.
        """
        if self.status in ("offline", "arrived"):
            return None
        target = self.current_target()
        if target is None:
            return None
        remaining_km = haversine_km(self.lat, self.lng, target.lat, target.lng)
        speed_kmph = AVG_BUS_SPEED_KMPH
        if self.status == "delayed":
            speed_kmph /= TRAFFIC_FACTOR_DELAYED
        if speed_kmph <= 0:
            return None
        return round((remaining_km / speed_kmph) * 60.0, 1)

    def eta_clock_time(self) -> Optional[str]:
        """ETA as a wall-clock time, e.g. '08:14 AM'."""
        mins = self.eta_minutes()
        if mins is None:
            return None
        return (datetime.now() + timedelta(minutes=mins)).strftime("%I:%M %p")

    # -- serialisation for the frontend -------------------------------------

    def to_api_dict(self) -> dict:
        """Shape expected by the React app's `ApiBus` type."""
        return {
            "bus_number": self.bus_number,
            "bus_name": self.name,
            "driver": self.driver,
            "driver_phone": self.driver_phone,
            "latitude": round(self.lat, 6),
            "longitude": round(self.lng, 6),
            "status": self.status,
            "capacity": self.capacity,
            "seats_available": max(0, self.capacity - self.seats_taken),
            "next_stop": self.next_stop_name(),
            "eta_minutes": self.eta_minutes(),
            "eta_time": self.eta_clock_time(),
        }

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two GPS points, in kilometres."""
    R = 6371.0088  # Earth's mean radius, km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

# --------------------------------------------------------------------------
# Seed fleet — bus numbers and names match the app's cloud database exactly,
# so live GPS data from this service merges onto each bus's existing route,
# stops and timetable in the frontend.
# --------------------------------------------------------------------------

FLEET: Dict[str, Bus] = {
    b.bus_number: b
    for b in [
        Bus("A01", "Anand City Route", ROUTES[0], "Rajesh Patel",
            "+919876543210", "on_route", 45, 30, seed_fraction=0.35),
        Bus("A02", "Vallabh Vidyanagar Route", ROUTES[1], "Mahesh Solanki",
            "+919876543211", "delayed", 40, 38, seed_fraction=0.20),
        Bus("A03", "Karamsad / Anand Side Route", ROUTES[2], "Bharat Chauhan",
            "+919876543212", "on_route", 45, 12, seed_fraction=0.55),
        Bus("A04", "Borsad Side Route", ROUTES[3], "Dinesh Parmar",
            "+919876543213", "on_route", 50, 25, seed_fraction=0.15),
        Bus("A05", "Petlad Side Route", ROUTES[4], "Sanjay Rathod",
            "+919876543214", "offline", 45, 5, seed_fraction=0.10),
    ]
}

def simulate() -> None:
    """Background thread: move every active bus every TICK_SECONDS."""
    while True:
        for bus in FLEET.values():
            bus.tick(TICK_SECONDS)
        time.sleep(TICK_SECONDS)

threading.Thread(target=simulate, daemon=True).start()

# --------------------------------------------------------------------------
# REST API
# --------------------------------------------------------------------------

app = FastAPI(
    title="ADIT Campus Transit Tracker API",
    description="Python (FastAPI) backend providing live bus data and ETAs "
                "for the ADIT College Bus Manager frontend.",
    version="1.2.0",
)

# Allow the website (any origin) to call this service from the browser.
# Without this, browsers block the frontend's requests to localhost.
# allow_private_network=True answers Chrome's Private Network Access check
# (newer Starlette versions); older ones raise TypeError, handled below.
try:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],        # demo-friendly: the published site + localhost
        allow_methods=["*"],
        allow_headers=["*"],
        allow_private_network=True,
    )
except TypeError:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Chrome's Private Network Access check: a public https site calling
# http://localhost must get a 200 preflight answer carrying
# "Access-Control-Allow-Private-Network: true", otherwise Chrome blocks the
# request with "Permission was denied for this request to access the
# `loopback` address space". We answer that preflight here so it works on any
# Starlette version, and stamp every other response with the header too.
@app.middleware("http")
async def allow_private_network(request, call_next):
    if (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-private-network", "").lower() == "true"
    ):
        origin = request.headers.get("origin", "*")
        requested = request.headers.get("access-control-request-headers", "")
        headers = {
            "Access-Control-Allow-Origin": origin or "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": requested or "*",
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin, Access-Control-Request-Method, "
            "Access-Control-Request-Headers, Access-Control-Request-Private-Network",
        }
        return Response(status_code=200, headers=headers)
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

class LocationUpdate(BaseModel):
    """Body of the driver-app location update."""
    latitude: float
    longitude: float
    status: Optional[str] = None

@app.get("/health")
def health():
    """Simple status probe used by the frontend's health check."""
    return {"status": "ok", "buses": len(FLEET),
            "time": datetime.now().isoformat()}

@app.get("/api/buses")
def list_buses():
    """Fleet snapshot: every bus with its live GPS position and ETA.

    This is the endpoint the React app polls every 20 seconds.
    """
    return [bus.to_api_dict() for bus in FLEET.values()]

@app.get("/api/buses/{bus_number}/eta")
def bus_eta(bus_number: str):
    """Detailed Python-computed ETA for one bus (great for the viva demo)."""
    bus = FLEET.get(bus_number.upper())
    if bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    target = bus.current_target()
    return {
        "bus_number": bus.bus_number,
        "status": bus.status,
        "next_stop": bus.next_stop_name(),
        "distance_km": round(haversine_km(bus.lat, bus.lng,
                                          target.lat, target.lng), 3)
                      if target else None,
        "speed_kmph": round(AVG_BUS_SPEED_KMPH / (TRAFFIC_FACTOR_DELAYED
                            if bus.status == "delayed" else 1), 1),
        "eta_minutes": bus.eta_minutes(),
        "eta_time": bus.eta_clock_time(),
        "computed_by": "haversine distance / speed model (Python)",
    }

@app.post("/api/buses/{bus_number}/location")
def update_location(bus_number: str, body: LocationUpdate):
    """Driver app pushes a real GPS fix; the service adopts it and
    recomputes the ETA from the new position."""
    bus = FLEET.get(bus_number.upper())
    if bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    bus.lat, bus.lng = body.latitude, body.longitude
    if body.status:
        allowed = {"on_route", "delayed", "offline", "arrived"}
        s = body.status.lower().replace(" ", "_").replace("-", "_")
        if s not in allowed:
            raise HTTPException(status_code=400,
                                detail=f"status must be one of {sorted(allowed)}")
        bus.status = s
    return {"ok": True, "bus_number": bus.bus_number,
            "eta_minutes": bus.eta_minutes()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
