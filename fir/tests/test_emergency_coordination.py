"""Controlled route fixtures exercise decisions; production uses real OSRM only."""
import asyncio
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mediroute.models import EmergencyHospitalResponse, EmergencyRequest, EmergencyRouteDecision, Hospital, LocationEvent, Notification, User, AmbulanceDriver
from mediroute.services import MapBridge


@pytest.fixture
def scenario(monkeypatch):
    app = importlib.import_module('mediroute.app')
    monkeypatch.setenv('MEDIROUTE_DEMO_COORDINATION', 'true')
    routes = {"JS001": (18., 15.), "KS001": (5., 4.)}
    calls, updates = [], []

    async def route(origin, destination):
        calls.append((dict(origin), dict(destination)))
        await asyncio.sleep(0.01)
        key = 'JS001' if destination['lat'] == 27.652 else 'KS001'
        if routes[key] is None:
            return None
        minutes, km = routes[key]
        return {'duration_min': minutes, 'distance_km': km, 'points': [origin, destination], 'provider': 'TEST_FIXTURE', 'available_at': app.utc_iso()}

    async def location(*args):
        return True

    async def destination(request_id, payload):
        updates.append((request_id, payload))
        return payload

    monkeypatch.setattr(app.bridge, 'calculate_route', route)
    monkeypatch.setattr(app.bridge, 'update_location', location)
    monkeypatch.setattr(app.bridge, 'update_destination', destination)
    with TestClient(app.app) as client:
        headers = {}
        for role in ('patient', 'driver', 'hospital', 'ks-hospital', 'admin'):
            response = client.post('/api/auth/login', json={'email': f'{role}@mediroute.demo', 'password': 'MediRoute!2026'})
            assert response.status_code == 200
            headers[role] = {'Authorization': f"Bearer {response.json()['access_token']}"}
        yield app, client, headers, routes, calls, updates


def create(scenario, **kwargs):
    app, client, headers, *_ = scenario
    response = client.post('/api/emergencies', headers=headers['driver'], json={'latitude': 27.61, 'longitude': 77.6, 'condition': 'Controlled trauma demo', 'required_service': 'trauma', 'demo': True, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()['id']


def gps(scenario, eid, latitude=27.61, longitude=77.6, **kwargs):
    _, client, headers, *_ = scenario
    result = client.post(f'/api/emergencies/{eid}/location', headers=headers['driver'], json={'latitude': latitude, 'longitude': longitude, 'observed_at': datetime.now(timezone.utc).isoformat(), 'source': 'DEMO', **kwargs})
    return result


def respond(scenario, eid, role='hospital', status='ACCEPTED'):
    _, client, headers, *_ = scenario
    return client.patch(f'/api/emergencies/{eid}/hospital-response', headers=headers[role], json={'status': status})


def state(scenario, eid):
    _, client, headers, *_ = scenario
    r = client.get(f'/api/emergencies/{eid}/coordination', headers=headers['driver'])
    assert r.status_code == 200, r.text
    return r.json()


def expire(scenario, eid):
    app = scenario[0]
    past = datetime.now(timezone.utc) - timedelta(seconds=2)
    with Session(app.engine) as db:
        e = db.get(EmergencyRequest, eid)
        e.details = {**e.details, 'response_deadline': past.isoformat()}
        for response in db.scalars(select(EmergencyHospitalResponse).where(EmergencyHospitalResponse.emergency_id == eid)):
            response.response_deadline = past
        db.commit()


def transit(scenario, eid):
    _, client, h, *_ = scenario
    result = client.patch(f'/api/emergencies/{eid}/status', headers=h['driver'], json={'status': 'IN_TRANSIT'})
    assert result.status_code == 200, result.text


def test_nearby_current_gps_includes_js_and_ks_without_moving_them(scenario):
    eid = create(scenario)
    assert gps(scenario, eid).status_code == 200
    app, client, h, *_ = scenario
    result = client.get(f'/api/emergencies/{eid}/nearby', headers=h['driver']).json()
    assert result['origin']['latitude'] == 27.61
    ids = {m['map_place_id'] for m in result['matches']}
    assert {'JS001', 'KS001'} <= ids
    assert all(m['demo_facility'] for m in result['matches'] if m['map_place_id'] in {'JS001', 'KS001'})
    for hospital in result['matches']:
        assert hospital['distance_km'] == round(app.haversine_km(27.61, 77.6, hospital['latitude'], hospital['longitude']), 2)
    assert gps(scenario, eid, 28.61, 77.21).status_code == 200
    assert client.get(f'/api/emergencies/{eid}/nearby', headers=h['driver']).json()['matches'] == []


def test_notification_delivery_both_hospitals_idempotent_dispatch(scenario):
    app, client, h, *_ = scenario
    eid = create(scenario)
    for role in ['hospital', 'ks-hospital']:
        queue = client.get('/api/hospitals/me/emergencies', headers=h[role]).json()
        assert queue[0]['id'] == eid and len(queue[0]['responses']) == 1
        assert 'patient_id' not in queue[0]['emergency']
        notices = client.get('/api/notifications', headers=h[role]).json()
        assert any(n['title'] == 'Incoming emergency request' and n['reference']['emergency_id'] == eid for n in notices)
    with Session(app.engine) as db:
        count = db.scalar(select(func.count(Notification.id)))
    for _ in range(2):
        assert client.post(f'/api/emergencies/{eid}/dispatch', headers=h['driver'], json={'origin':'PICKUP'}).status_code == 200
    with Session(app.engine) as db:
        assert db.scalar(select(func.count(Notification.id))) == count
        assert db.scalar(select(func.count(EmergencyHospitalResponse.id))) >= 2


def test_accept_decline_and_existing_js_status_api(scenario):
    app, client, h, *_ = scenario
    eid = create(scenario)
    assert respond(scenario, eid, 'ks-hospital', 'DECLINED').status_code == 200
    accepted = client.patch(f'/api/emergencies/{eid}/status', headers=h['hospital'], json={'status':'ACCEPTED'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['hospital_id'] == 1
    version = state(scenario, eid)['state_version']
    assert respond(scenario, eid).json()['idempotent'] is True
    assert state(scenario, eid)['state_version'] == version
    assert respond(scenario, eid, 'ks-hospital').status_code == 409


def test_server_timeout_without_browser_and_no_late_first_acceptance(scenario):
    app, client, h, *_ = scenario
    eid = create(scenario)
    e = state(scenario, eid)['details']
    assert (datetime.fromisoformat(e['response_deadline']) - datetime.fromisoformat(e['dispatch_at'])).total_seconds() == 60
    expire(scenario, eid)
    # Only the server maintenance loop runs; no endpoint poll expires this.
    for _ in range(30):
        with Session(app.engine) as db:
            if db.get(EmergencyRequest, eid).status == 'NO_ACCEPTANCE':
                break
        time.sleep(.1)
    with Session(app.engine) as db:
        assert db.get(EmergencyRequest, eid).status == 'NO_ACCEPTANCE'
        assert all(r.status == 'TIMED_OUT' for r in db.scalars(select(EmergencyHospitalResponse)))
    assert respond(scenario, eid).status_code == 409
    assert state(scenario, eid)['details']['escalation']


def test_reroute_en_route_same_current_position_and_map_update(scenario):
    _, _, _, _, calls, updates = scenario
    eid = create(scenario)
    assert gps(scenario, eid).status_code == 200
    assert respond(scenario, eid).status_code == 200
    transit(scenario, eid)
    assert gps(scenario, eid, 27.615, 77.601).status_code == 200
    calls.clear()
    assert respond(scenario, eid, 'ks-hospital').status_code == 200
    result = state(scenario, eid)
    assert result['details']['assignment_version'] == 2
    assert result['hospital_id'] != 1
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == {'lat':27.615, 'lng':77.601}
    assert result['details']['last_route_comparison']['time_saving_minutes'] == 13
    assert result['route_history'][-1]['previous_hospital_id'] == 1
    assert updates[-1][1]['hospital_id'] == 'KS001'
    assert updates[-1][1]['assignment_version'] == 2


@pytest.mark.parametrize('route', [(15.,14.), (2.,1.)])
def test_retain_when_insignificant_or_near_destination(scenario, route):
    routes = scenario[3]
    eid = create(scenario)
    gps(scenario, eid)
    respond(scenario, eid)
    if route[0] == 2:
        routes['JS001'] = (2., 1.)
        routes['KS001'] = (.5, .2)
    else:
        routes['KS001'] = route
    assert respond(scenario, eid, 'ks-hospital').status_code == 200
    assert state(scenario, eid)['hospital_id'] == 1


def test_late_second_acceptance_only_during_travel(scenario):
    eid = create(scenario)
    gps(scenario, eid)
    respond(scenario, eid)
    expire(scenario, eid)
    assert respond(scenario, eid, 'ks-hospital').status_code == 409
    transit(scenario, eid)
    assert respond(scenario, eid, 'ks-hospital').status_code == 200
    assert state(scenario, eid)['hospital_id'] != 1


def test_required_care_and_clinical_destination_lock(scenario):
    app, client, h, *_ = scenario
    eid = create(scenario, required_service='neurosurgery')
    assert state(scenario, eid)['responses'] == []
    assert respond(scenario, eid).status_code == 403
    eid = create(scenario)
    gps(scenario, eid)
    respond(scenario, eid)
    with Session(app.engine) as db:
        e = db.get(EmergencyRequest, eid)
        e.details = {**e.details, 'clinical_destination_locked':True, 'clinical_hospital_id':1}
        db.commit()
    assert respond(scenario, eid, 'ks-hospital').status_code == 200
    assert state(scenario, eid)['hospital_id'] == 1


@pytest.mark.parametrize('terminal', ['ARRIVED', 'COMPLETED', 'CANCELLED'])
def test_no_rerouting_after_arrival_or_closure(scenario, terminal):
    app, client, h, *_ = scenario
    eid = create(scenario)
    gps(scenario, eid)
    respond(scenario, eid)
    transit(scenario, eid)
    if terminal != 'CANCELLED':
        assert client.patch(f'/api/emergencies/{eid}/status', headers=h['driver'], json={'status':'ARRIVED'}).status_code == 200
    if terminal != 'ARRIVED':
        assert client.patch(f'/api/emergencies/{eid}/status', headers=h['driver'], json={'status':terminal}).status_code == 200
    assert respond(scenario, eid, 'ks-hospital').status_code == 409
    assert gps(scenario, eid).status_code == 409
    assert state(scenario, eid)['hospital_id'] == 1


def test_stale_gps_and_route_failure_never_fabricate_eta(scenario):
    app, client, h, routes, *_ = scenario
    eid = create(scenario)
    assert gps(scenario, eid, observed_at=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()).status_code == 422
    assert client.get(f'/api/emergencies/{eid}/nearby', headers=h['driver']).status_code == 422
    assert respond(scenario, eid).status_code == 200
    assert state(scenario, eid)['details']['current_route']['available'] is False
    routes['JS001'] = None
    assert gps(scenario, eid).status_code == 200
    assert respond(scenario, eid, 'ks-hospital').status_code == 200
    e = state(scenario, eid)
    assert e['hospital_id'] == 1
    assert e['details']['current_route'].get('duration_min') is None


def test_withdrawal_and_capacity_loss_select_accepted_replacement(scenario):
    _, client, h, routes, *_ = scenario
    eid = create(scenario)
    gps(scenario, eid)
    respond(scenario, eid)
    routes['KS001'] = (17.,14.)
    respond(scenario, eid, 'ks-hospital')
    assert state(scenario, eid)['hospital_id'] == 1
    assert respond(scenario, eid, status='WITHDRAWN').status_code == 200
    replacement = state(scenario, eid)['hospital_id']
    assert replacement != 1
    assert client.patch(f'/api/hospitals/{replacement}/capacity', headers=h['ks-hospital'], json={'emergency_available':0}).status_code == 200
    assert state(scenario, eid)['hospital_id'] is None
    assert state(scenario, eid)['details']['escalation']


def test_authorization_and_hospital_identity_cannot_be_spoofed(scenario):
    app, client, h, *_ = scenario
    eid = create(scenario)
    assert client.get(f'/api/emergencies/{eid}/coordination').status_code == 401
    assert client.patch(f'/api/emergencies/{eid}/hospital-response', headers=h['driver'], json={'status':'ACCEPTED'}).status_code == 403
    assert client.patch(f'/api/emergencies/{eid}/hospital-response', headers=h['hospital'], json={'status':'ACCEPTED','hospital_id':3}).status_code == 422
    assert client.patch(f'/api/emergencies/{eid}/status', headers=h['driver'], json={'status':'ACCEPTED'}).status_code == 409
    with Session(app.engine) as db:
        driver = db.scalar(select(AmbulanceDriver))
        e = db.get(EmergencyRequest, eid)
        e.ambulance_id = None
        db.commit()
    assert client.patch(f'/api/emergencies/{eid}/status', headers=h['driver'], json={'status':'DRIVER_ASSIGNED'}).status_code == 403


def test_simultaneous_acceptances_and_stale_route_decision(scenario, monkeypatch):
    app, client, h, routes, calls, updates = scenario
    eid = create(scenario)
    gps(scenario, eid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda role: respond(scenario, eid, role), ['hospital','ks-hospital']))
    for role, response in zip(['hospital','ks-hospital'], responses):
        assert response.status_code in (200,409), response.text
        if response.status_code == 409:
            assert respond(scenario, eid, role).status_code == 200
    final = state(scenario, eid)
    accepted_ids = {r['hospital_id'] for r in final['responses'] if r['status'] == 'ACCEPTED'}
    assert {1, 3} <= accepted_ids
    assert final['hospital_id'] in {r['hospital_id'] for r in final['responses']}
    old = final['details']['assignment_version']
    async def superseded(origin, destination):
        with Session(app.engine) as db:
            e = db.get(EmergencyRequest, eid)
            e.state_version += 1
            e.status = 'ARRIVED'
            db.commit()
        return {'duration_min': 1, 'distance_km':1, 'points':[], 'available_at': app.utc_iso()}
    monkeypatch.setattr(app.bridge, 'calculate_route', superseded)
    response = client.post(f'/api/emergencies/{eid}/reassess', headers=h['driver'])
    assert response.status_code == 200
    assert response.json()['decision']['stale'] is True
    assert state(scenario, eid)['details']['assignment_version'] == old


def test_cooldown_prevents_route_oscillation(scenario):
    _, client, h, routes, *_ = scenario
    eid = create(scenario)
    gps(scenario, eid); respond(scenario, eid); respond(scenario, eid, 'ks-hospital')
    assigned = state(scenario, eid)['hospital_id']
    routes['JS001'], routes['KS001'] = (1.,1.), (20.,20.)
    client.post(f'/api/emergencies/{eid}/reassess', headers=h['driver'])
    assert state(scenario, eid)['hospital_id'] == assigned


def test_original_js_identity_seed_idempotency_and_demo_isolation(scenario):
    app, client, h, *_ = scenario
    with Session(app.engine) as db:
        js = db.scalar(select(Hospital).where(Hospital.map_place_id=='JS001'))
        identity = js.id, js.name, js.latitude, js.longitude
        app.seed_demo_data(db)
        assert (js.id, js.name, js.latitude, js.longitude) == identity
        assert db.scalar(select(func.count(Hospital.id)).where(Hospital.map_place_id=='KS001')) == 1
    eid = create(scenario, demo=False)
    assert all(response['hospital_map_place_id'] not in {'JS001', 'KS001'} for response in state(scenario, eid)['responses'])
    assert client.get('/api/hospitals/1', headers=h['hospital']).status_code == 200


def test_existing_websocket_notifies_authenticated_hospital(scenario):
    _, client, h, *_ = scenario
    user = client.get('/api/auth/me', headers=h['ks-hospital']).json()
    token = h['ks-hospital']['Authorization'].split()[1]
    with client.websocket_connect(f"/api/ws/notifications/{user['id']}?token={token}") as ws:
        eid = create(scenario)
        notice = ws.receive_json()
        assert notice['kind'] == 'EMERGENCY'
        assert notice['reference']['emergency_id'] == eid


def test_map_renderer_and_osrm_provider_contract(scenario, monkeypatch):
    _, client, h, *_ = scenario
    assert client.get('/coordination-map.html').status_code == 200
    script = client.get('/coordination-map/library.js')
    assert script.status_code == 200 and 'drawRouteOnMap' in script.text
    original = httpx.AsyncClient
    def handler(request):
        assert '/route/v1/driving/77.6,27.61;77.558,27.652' in str(request.url)
        return httpx.Response(200,json={'code':'Ok','routes':[{'distance':5000,'duration':600,'geometry':{'coordinates':[[77.6,27.61],[77.558,27.652]]}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    bridge = MapBridge()
    result = asyncio.run(bridge.calculate_route({'lat':27.61,'lng':77.6},{'lat':27.652,'lng':77.558}))
    assert result['distance_km'] == 5 and result['duration_min'] == 10
    assert result['traffic_available'] is False
    def failed(request):
        return httpx.Response(503)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(failed), **kwargs))
    assert asyncio.run(bridge.calculate_route({'lat':27.61,'lng':77.6},{'lat':27.652,'lng':77.558})) is None
