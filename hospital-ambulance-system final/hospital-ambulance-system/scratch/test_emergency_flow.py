import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def test_flow():
    # 1. Health check
    r_health = requests.get(f"{BASE_URL}/api/health")
    print("Health Check:", r_health.status_code, r_health.json())

    # 2. Check JS Hospital in nearby list
    r_nearby = requests.get(f"{BASE_URL}/api/hospitals/nearby?lat=27.646870&lng=77.551921&radius_km=15")
    hospitals = r_nearby.json().get('hospitals', [])
    js_hosp = next((h for h in hospitals if h.get('place_id') == 'JS001' or h.get('hospital_id') == 'JS001'), None)
    print("JS Hospital found in nearby list:", js_hosp is not None)
    if js_hosp:
        print(f" - Name: {js_hosp['name']} | Distance: {js_hosp.get('distance_km')} km | ER Beds: {js_hosp.get('emergency_beds_available')} | ICU Beds: {js_hosp.get('icu_beds_available')}")

    # 3. Ambulance sends emergency request
    payload = {
        "ambulance_id": "AMB-101",
        "hospital_id": "JS001",
        "patient_name": "Emergency Patient (Severe Trauma)",
        "lat": 27.646870,
        "lng": 77.551921
    }
    r_req = requests.post(f"{BASE_URL}/api/emergency/request", json=payload)
    print("Create Emergency Request:", r_req.status_code, r_req.json().get('message'))
    req_data = r_req.json().get('data', {})
    req_id = req_data.get('request_id')
    print("Request ID:", req_id, "Status:", req_data.get('status'))

    # 4. Check active request
    r_active = requests.get(f"{BASE_URL}/api/emergency/active-request")
    print("Active Request status:", r_active.json().get('data', {}).get('status'))

    # 5. JS Hospital accepts the request
    r_resp = requests.post(f"{BASE_URL}/api/emergency/respond", json={"request_id": req_id, "status": "ACCEPTED"})
    print("Hospital Decision (ACCEPT):", r_resp.status_code, r_resp.json().get('message'))
    print("Updated Status:", r_resp.json().get('data', {}).get('status'))

    # 6. Verify duplicate prevention
    r_dup = requests.post(f"{BASE_URL}/api/emergency/respond", json={"request_id": req_id, "status": "REJECTED"})
    print("Duplicate Response handling:", r_dup.json().get('message'))

if __name__ == "__main__":
    test_flow()
