from fastapi.testclient import TestClient
import main, time

client = TestClient(main.app)
time.sleep(3)  # let the simulation tick a couple of times

r = client.get("/health"); assert r.status_code == 200, r.text
print("health:", r.json())

r = client.get("/api/buses"); assert r.status_code == 200, r.text
buses = r.json()
assert len(buses) == 5, buses
b = buses[0]
for k in ["bus_number","driver","driver_phone","latitude","longitude","status","capacity","seats_available"]:
    assert k in b, k
print("buses[0]:", b)

r = client.get("/api/buses/A01/eta"); assert r.status_code == 200, r.text
print("eta:", r.json())
assert r.json()["eta_minutes"] is not None

r = client.post("/api/buses/A01/location",
                json={"latitude": 22.5525, "longitude": 72.9230})
assert r.status_code == 200, r.text
print("location update:", r.json())

r = client.get("/api/buses/NOPE/eta"); assert r.status_code == 404
print("404 ok")
print("ALL TESTS PASSED")
