import uuid
import pytest
from fastapi.testclient import TestClient

from mediroute.app import app


PASSWORD = "MediRoute!2026"


def login(client: TestClient, account: str) -> dict:
    response = client.post("/api/auth/login", json={"email": f"{account}@mediroute.demo", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_auth_and_registration():
    with TestClient(app) as client:
        # Invalid credentials
        bad_login = client.post("/api/auth/login", json={"email": "patient@mediroute.demo", "password": "WrongPassword!"})
        assert bad_login.status_code == 401

        # Public register patient
        unique_email = f"patient_{uuid.uuid4().hex[:8]}@mediroute.demo"
        reg_res = client.post("/api/auth/register", json={
            "email": unique_email,
            "password": "SecurePassword!123",
            "full_name": "New Patient",
            "role": "PATIENT",
            "language": "hi"
        })
        assert reg_res.status_code == 201
        assert "access_token" in reg_res.json()
        assert reg_res.json()["user"]["role"] == "PATIENT"

        # Register hospital role
        hosp_email = f"hospital_{uuid.uuid4().hex[:8]}@mediroute.demo"
        hosp_res = client.post("/api/auth/register", json={
            "email": hosp_email,
            "password": "SecurePassword!123",
            "full_name": "City Emergency Hospital",
            "role": "HOSPITAL"
        })
        assert hosp_res.status_code == 201
        assert hosp_res.json()["user"]["role"] == "HOSPITAL"

        # Duplicate registration fails with 409
        dup_reg = client.post("/api/auth/register", json={
            "email": unique_email,
            "password": "SecurePassword!123",
            "full_name": "Duplicate User",
            "role": "PATIENT"
        })
        assert dup_reg.status_code == 409


def test_end_to_end_healthcare_workflow():
    with TestClient(app) as client:
        # Verify all health check endpoints and DB status
        for route in ("/health", "/healthz", "/api/health"):
            res = client.get(route)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["database"] == "healthy"
            assert data["service"] == "MediRoute"

        patient_headers = login(client, "patient")
        hospital_headers = login(client, "hospital")
        driver_headers = login(client, "driver")
        admin_headers = login(client, "admin")
        government_headers = login(client, "government")

        # Patient Profile
        profile_res = client.get("/api/patients/me/profile", headers=patient_headers)
        assert profile_res.status_code == 200
        profile = profile_res.json()

        update_prof = client.put("/api/patients/me/profile", headers=patient_headers, json={
            "phone": "+91 98765 43210",
            "address": "Village Kalyanpur, Mathura",
            "allergies": ["Aspirin", "Sulfa"],
            "medical_history": ["Hypertension"],
            "emergency_contact": {"name": "Suresh", "phone": "+91 98765 00000"}
        })
        assert update_prof.status_code == 200
        assert update_prof.json()["phone"] == "+91 98765 43210"

        # Triage Decision Support
        triage_high = client.post("/api/triage", headers=patient_headers, json={"symptoms": ["chest pain", "shortness of breath"], "vitals": {"spo2": 89, "systolic": 185}, "age": 68, "risk_factors": ["hypertension", "diabetes"]})
        assert triage_high.status_code == 200
        assert triage_high.json()["urgency"] == "HIGH"
        assert triage_high.json()["emergency_escalation_recommended"] is True

        triage_low = client.post("/api/triage", headers=patient_headers, json={"symptoms": ["mild headache"], "vitals": {"spo2": 99, "systolic": 120}, "age": 30, "risk_factors": []})
        assert triage_low.status_code == 200
        assert triage_low.json()["urgency"] == "LOW"

        # Hospital Capacity Management
        cap_res = client.patch("/api/hospitals/1/capacity", headers=hospital_headers, json={
            "emergency_available": 10,
            "beds_available": 25,
            "doctors_available": 6
        })
        assert cap_res.status_code == 200
        assert cap_res.json()["beds_available"] == 25

        # Appointment Slots and Booking
        slot_res = client.post("/api/hospitals/1/appointment-slots", headers=hospital_headers, json={
            "starts_at": "2026-10-15T09:00:00Z",
            "ends_at": "2026-10-15T09:30:00Z"
        })
        assert slot_res.status_code == 201

        slots = client.get("/api/hospitals/1/appointment-slots")
        assert slots.status_code == 200
        assert len(slots.json()) >= 1

        appointment = client.post("/api/appointments", headers=patient_headers, json={"hospital_id": 1, "scheduled_for": "2026-10-15T09:00:00Z", "reason": "General checkup"})
        assert appointment.status_code == 201
        app_id = appointment.json()["id"]

        # Queue Entry
        queue = client.post(f"/api/appointments/{app_id}/queue", headers=patient_headers)
        assert queue.status_code == 201
        assert queue.json()["token_number"] >= 1
        assert "estimated_wait_minutes" in queue.json()

        my_queues = client.get("/api/queues/me", headers=patient_headers)
        assert my_queues.status_code == 200
        assert len(my_queues.json()) >= 1

        # Teleconsultation
        tele = client.post("/api/teleconsultations", headers=patient_headers, json={
            "appointment_id": app_id,
            "provider": "Secure Telehealth Gateway",
            "meeting_url": "https://telehealth.mediroute.demo/room/123"
        })
        assert tele.status_code == 201
        assert tele.json()["status"] == "SCHEDULED"

        # Diagnostic Services and Inventory
        diag_svc = client.put("/api/hospitals/1/diagnostic-services", headers=hospital_headers, json={
            "test_name": "Complete Blood Count",
            "available": True,
            "slots_available": 15
        })
        assert diag_svc.status_code == 200

        diag_search = client.get("/api/diagnostic-services?test_name=Blood")
        assert diag_search.status_code == 200
        assert len(diag_search.json()) >= 1

        diag_order = client.post("/api/diagnostics", headers=hospital_headers, json={
            "patient_id": profile["id"],
            "hospital_id": 1,
            "test_name": "Complete Blood Count",
            "scheduled_for": "2026-10-16T10:00:00Z"
        })
        assert diag_order.status_code == 201
        order_id = diag_order.json()["id"]

        diag_update = client.patch(f"/api/diagnostics/{order_id}", headers=hospital_headers, json={
            "status": "COMPLETED",
            "findings": "Hemoglobin 13.5 g/dL, normal range.",
            "report_url": "https://storage.mediroute.demo/reports/cbc-101.pdf"
        })
        assert diag_update.status_code == 200

        my_diags = client.get("/api/diagnostics/me", headers=patient_headers)
        assert my_diags.status_code == 200
        assert len(my_diags.json()) >= 1

        # Medicine Inventory
        inv_res = client.put("/api/hospitals/1/inventory", headers=hospital_headers, json={
            "medicine_name": "Amoxicillin 500mg",
            "generic_name": "Amoxicillin",
            "quantity": 200,
            "low_stock_threshold": 20,
            "expiry_date": "2027-10-30"
        })
        assert inv_res.status_code == 200

        med_avail = client.get("/api/medicines/availability?query=Amoxicillin")
        assert med_avail.status_code == 200
        assert len(med_avail.json()) >= 1

        # Maternal and Child Records
        mat_rec = client.post("/api/maternal-records", headers=hospital_headers, json={
            "patient_id": profile["id"],
            "pregnancy_history": [{"gravida": 1, "para": 0}],
            "anc_visits": [{"date": "2026-09-01", "trimester": 1}],
            "vitals": {"bp": "120/80", "weight_kg": 58},
            "risk_indicators": ["normal"]
        })
        assert mat_rec.status_code == 201

        child_rec = client.post("/api/child-records", headers=hospital_headers, json={
            "patient_id": profile["id"],
            "growth_vitals": {"height_cm": 75, "weight_kg": 9.5},
            "vaccinations": ["BCG", "OPV-1", "DPT-1"]
        })
        assert child_rec.status_code == 201

        # Follow-Up & Monitoring
        follow_up = client.post("/api/follow-ups", headers=hospital_headers, json={
            "patient_id": profile["id"],
            "condition": "Hypertension Monitoring",
            "due_on": "2026-09-10T00:00:00Z",
            "priority": "HIGH"
        })
        assert follow_up.status_code == 201

        monitor = client.post("/api/follow-ups/monitor", headers=admin_headers)
        assert monitor.status_code == 200
        assert monitor.json()["count"] >= 1

        # Clinical Visit & Discrepancy Flow
        visit = client.post("/api/visits", headers=hospital_headers, json={"patient_id": profile["id"], "hospital_id": 1, "reason": "Emergency assessment"})
        assert visit.status_code == 201
        visit_id = visit.json()["id"]

        treatment = client.post("/api/treatment-records", headers=hospital_headers, json={
            "patient_id": profile["id"],
            "visit_id": visit_id,
            "medicines": ["Paracetamol 500mg"],
            "tests": ["ECG"],
            "procedures": ["IV drip"],
            "treatment": "Monitored and stabilized",
            "discharge": True
        })
        assert treatment.status_code == 201

        assert client.patch(f"/api/visits/{visit_id}/discharge", headers=hospital_headers, json={"summary": "Stable; follow-up planned"}).status_code == 200

        feedback = client.post("/api/patient-feedback", headers=patient_headers, json={
            "visit_id": visit_id,
            "treatment_received": True,
            "medicines_received": False,  # Discrepancy: hospital says medicines provided, patient says not received
            "tests_performed": True,
            "procedures_received": True,
            "discharged": True,
            "written_feedback": "Did not receive the prescribed tablets at pharmacy."
        })
        assert feedback.status_code == 201

        comparison = client.post(f"/api/visits/{visit_id}/compare-treatment", headers=hospital_headers)
        assert comparison.status_code == 200
        findings = comparison.json()["result"]["findings"]
        assert len(findings) >= 1
        assert findings[0]["field"] == "medicines"

        # Rating
        assert client.post("/api/ratings", headers=patient_headers, json={"visit_id": visit_id, "stars": 4, "feedback": "Good nursing staff."}).status_code == 201

        # Closed-Loop Referral
        referral = client.post("/api/referrals", headers=hospital_headers, json={"patient_id": profile["id"], "to_hospital_id": 2, "required_service": "cardiology", "clinical_summary": "Requires specialist cardiology evaluation"})
        assert referral.status_code == 201
        ref_id = referral.json()["id"]

        # Step through closed-loop statuses
        for next_status in ["ACCEPTED", "SCHEDULED", "IN_TRANSIT", "ARRIVED", "UNDER_TREATMENT", "DISCHARGED", "COMPLETED"]:
            res = client.patch(f"/api/referrals/{ref_id}/status", headers=hospital_headers, json={"status": next_status, "note": f"Transitioned to {next_status}"})
            assert res.status_code == 200
            assert res.json()["status"] == next_status

        # Facility Suitability Matching
        matches = client.get("/api/facility-matches?latitude=27.65&longitude=77.55&required_service=cardiology")
        assert matches.status_code == 200
        assert len(matches.json()["matches"]) >= 1

        # Emergency & Driver Tracking
        emergency = client.post("/api/emergencies", headers=patient_headers, json={"latitude": 27.64, "longitude": 77.55, "condition": "Severe trauma from road collision", "required_service": "trauma"})
        assert emergency.status_code == 201
        em_id = emergency.json()["id"]

        # Driver updates location
        loc_update = client.post(f"/api/emergencies/{em_id}/location", headers=driver_headers, json={"latitude": 27.642, "longitude": 77.553, "speed_kmh": 45.0})
        assert loc_update.status_code == 200

        driver_trips = client.get("/api/driver/assignments", headers=driver_headers)
        assert driver_trips.status_code == 200

        # Complaints & Escalations
        complaint = client.post("/api/complaints", headers=patient_headers, json={"hospital_id": 1, "visit_id": visit_id, "category": "MEDICINE_NOT_PROVIDED", "description": "Medicine documented on record but pharmacy refused to dispense.", "severity": "SERIOUS"})
        assert complaint.status_code == 201 and complaint.json()["status"] == "ESCALATED"
        comp_id = complaint.json()["id"]

        response = client.post(f"/api/complaints/{comp_id}/responses", headers=hospital_headers, json={"response": "We have checked the dispensary log and contacted the patient to provide medication."})
        assert response.status_code == 201

        # Notification Preferences & Read State
        pref_get = client.get("/api/notification-preferences", headers=patient_headers)
        assert pref_get.status_code == 200
        pref_put = client.put("/api/notification-preferences", headers=patient_headers, json={"in_app_enabled": True, "emergency_enabled": True, "appointment_enabled": True, "follow_up_enabled": True})
        assert pref_put.status_code == 200

        notifs = client.get("/api/notifications", headers=patient_headers)
        assert notifs.status_code == 200
        if notifs.json():
            notif_id = notifs.json()[0]["id"]
            client.patch(f"/api/notifications/{notif_id}/read", headers=patient_headers)

        # AI Decision Support Endpoint
        ai_res = client.post("/api/ai/analyse", headers=patient_headers, json={"task": "classify_complaint", "text": "The hospital had an unnecessary delay in emergency room."})
        assert ai_res.status_code == 200
        assert "safety_note" in ai_res.json()

        # Interoperability Preview (FHIR shaped)
        interop = client.get(f"/api/patients/{profile['id']}/interoperability-preview", headers=patient_headers)
        assert interop.status_code == 200
        assert interop.json()["compliance"] == "Not FHIR compliant"

        # Admin & Government Dashboards
        assert client.put("/api/admin/policy", headers=admin_headers, json={"minimum_ratings": 1, "low_rating_percentage": 20, "complaint_count": 1, "review_period_days": 30}).status_code == 200
        assert client.post("/api/admin/quality/recalculate", headers=admin_headers).status_code == 200
        users_list = client.get("/api/admin/users", headers=admin_headers)
        assert users_list.status_code == 200
        logs = client.get("/api/admin/audit-logs", headers=admin_headers)
        assert logs.status_code == 200

        government = client.get("/api/government/dashboard", headers=government_headers)
        assert government.status_code == 200 and "privacy_note" in government.json()


def test_role_boundaries_and_translations():
    with TestClient(app) as client:
        patient_headers = login(client, "patient")
        # Role violation: patient attempting to access admin users
        assert client.get("/api/admin/users", headers=patient_headers).status_code == 403
        # Role violation: patient attempting to access hospital-specific endpoints
        assert client.get("/api/hospitals/me/patients", headers=patient_headers).status_code == 403
        # Role violation: patient attempting to update facility capacity
        assert client.patch("/api/hospitals/1/capacity", headers=patient_headers, json={"emergency_available": 5}).status_code == 403

        # Translations for all 6 Indian languages
        for lang in ["en", "hi", "bn", "mr", "ta", "te"]:
            res = client.get(f"/api/i18n/{lang}")
            assert res.status_code == 200
            assert "emergency" in res.json()["strings"]

