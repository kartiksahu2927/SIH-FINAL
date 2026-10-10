from datetime import datetime, timedelta, timezone

import routes
from app import app


def setup_function():
    routes.emergency_requests.clear()
    routes.active_emergency_request_id = None
    routes.fetch_street_route = lambda *args, **kwargs: {"distance_km": 1.0, "duration_min": 2, "points": []}


def test_dispatches_to_both_receptions_and_records_independent_responses():
    client = app.test_client()
    created = client.post("/api/emergency/request", json={"ambulance_id": "AMB-101", "hospital_id": "JS001", "lat": 27.64687, "lng": 77.551921})
    assert created.status_code == 200
    request = created.json["data"]
    assert {target["hospital_id"] for target in request["hospital_targets"]} == {"JS001", "KS001"}
    assert request["response_window_seconds"] == 60

    js = client.post("/api/emergency/respond", json={"request_id": request["request_id"], "hospital_id": "JS001", "status": "ACCEPTED"})
    ks = client.post("/api/emergency/respond", json={"request_id": request["request_id"], "hospital_id": "KS001", "status": "REJECTED"})
    assert js.status_code == ks.status_code == 200
    state = client.get("/api/emergency/active-request").json["data"]
    assert state["hospital_responses"]["JS001"]["status"] == "ACCEPTED"
    assert state["hospital_responses"]["KS001"]["status"] == "REJECTED"


def test_response_is_rejected_after_server_deadline():
    client = app.test_client()
    created = client.post("/api/emergency/request", json={"ambulance_id": "AMB-101", "lat": 27.64687, "lng": 77.551921})
    request = created.json["data"]
    routes.emergency_requests[request["request_id"]]["response_deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    response = client.post("/api/emergency/respond", json={"request_id": request["request_id"], "hospital_id": "JS001", "status": "ACCEPTED"})
    assert response.status_code == 409
    assert all(item["status"] == "TIMED_OUT" for item in response.json["data"]["hospital_responses"].values())
