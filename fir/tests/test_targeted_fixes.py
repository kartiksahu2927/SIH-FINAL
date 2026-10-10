import importlib

from fastapi.testclient import TestClient

from mediroute.nearby import _normalize_element


PASSWORD = "MediRoute!2026"


def login(client: TestClient, account: str) -> dict:
    result = client.post("/api/auth/login", json={"email": f"{account}@mediroute.demo", "password": PASSWORD})
    assert result.status_code == 200, result.text
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def test_fastapi_has_no_second_hospital_role_and_js_identity_remains_available():
    app_module = importlib.import_module("mediroute.app")
    with TestClient(app_module.app) as client:
        legacy_js_dashboard = client.get("/hospital")
        assert legacy_js_dashboard.status_code == 200
        assert "JS Hospital" in legacy_js_dashboard.text
        assert client.get("/ks-hospital?demo=ks").status_code == 404
        js_headers = login(client, "hospital")
        js_user = client.get("/api/auth/me", headers=js_headers).json()
        assert js_user["role"] == "HOSPITAL"
        assert "JS Hospital" in client.get(f"/api/hospitals/{js_user['hospital_id']}").json()["name"]
        assert client.get("/api/hospitals/me/emergencies", headers=js_headers).status_code == 200


def test_osm_normalizer_accepts_hospital_tag_and_rejects_unrelated_venue():
    hospital = _normalize_element(
        {"type": "node", "id": 101, "lat": 28.459, "lon": 77.5, "tags": {"name": "KCC Area Hospital", "amenity": "hospital", "addr:city": "Greater Noida"}},
        28.4595,
        77.5,
    )
    university = _normalize_element(
        {"type": "node", "id": 102, "lat": 28.459, "lon": 77.5, "tags": {"name": "Example University", "amenity": "university"}},
        28.4595,
        77.5,
    )
    assert hospital and hospital["facility_type"] == "hospital"
    assert university is None


def test_nearby_endpoint_uses_explicit_origin_and_returns_sorted_verified_records(monkeypatch):
    app_module = importlib.import_module("mediroute.app")

    async def fake_search(latitude, longitude, radius_km):
        assert (latitude, longitude) == (28.4595, 77.5)
        return [
            {"name": "Sharda Hospital", "distance_km": 2.38, "facility_type": "hospital", "source": "OpenStreetMap Overpass", "latitude": 28.45, "longitude": 77.49, "services": [], "address": "Greater Noida"},
            {"name": "Yatharth Hospital", "distance_km": 1.31, "facility_type": "hospital", "source": "OpenStreetMap Overpass", "latitude": 28.46, "longitude": 77.50, "services": [], "address": "Greater Noida"},
        ]

    monkeypatch.setattr(app_module, "discover_osm_hospitals", fake_search)
    with TestClient(app_module.app) as client:
        result = client.get("/api/hospitals/nearby?lat=28.4595&lng=77.5&radius_km=10")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["origin"] == {"lat": 28.4595, "lng": 77.5, "source": "explicit_query_coordinates"}
        assert [item["name"] for item in body["hospitals"]] == ["Yatharth Hospital", "Sharda Hospital"]
        assert all(item["facility_type"] == "hospital" for item in body["hospitals"])
