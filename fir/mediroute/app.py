from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .db import Base, engine, get_db
from .models import (Ambulance, AmbulanceDriver, Appointment, AppointmentSlot, AuditLog, Complaint, ComplaintResponse, Consultation,
                     DiagnosticOrder, DiagnosticReport, DiagnosticService, Doctor, EmergencyAssistantMessage, EmergencyAssistantSession,
                     EmergencyGuidance, EmergencyRequest, EmergencySafetyEvent, Escalation, FollowUp, Hospital,
                     LocationEvent, MedicalRecord, Medicine, MedicineInventory, Notification, NotificationPreference, PatientFeedback,
                     PatientProfile, PolicySetting, Prescription, QualityIndicator, QueueEntry, Rating, Referral, Teleconsultation,
                     TreatmentComparison, TreatmentRecord, User, Visit, Vital)
from .schemas import (AIAnalysisIn, AppointmentIn, AppointmentSlotIn, ComplaintIn, ComplaintResponseIn, DiagnosticIn, DiagnosticServiceIn,
                      DiagnosticStatusIn, EmergencyIn, EmergencyMessageIn, EmergencySessionIn, FeedbackIn, FollowUpIn, InventoryIn, LocationIn, LoginIn, NotificationPreferenceIn,
                      PatientProfileIn, PolicyIn, RatingIn, ReferralIn, ReferralStatusIn, RegisterIn, TeleconsultationIn, TreatmentIn, TriageIn)
from .ai import get_ai_provider
from .emergency_assistant import EMERGENCY_PHONE_NUMBER, get_approved_guidance, process_emergency_message
from .security import JWT_ALGORITHM, JWT_SECRET, create_access_token, get_current_user, hash_password, rate_limit, require_roles, verify_password
from .seed import seed_demo_data
from .services import MapBridge, as_dict, audit, compare_treatment, haversine_km, match_facilities, notify, triage_indicator, utc_iso



class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        self.connections[user_id].discard(websocket)

    async def send(self, user_id: int, event: dict) -> None:
        stale = []
        for connection in self.connections[user_id]:
            try:
                await connection.send_json(event)
            except RuntimeError:
                stale.append(connection)
        for connection in stale:
            self.connections[user_id].discard(connection)


manager = ConnectionManager()
bridge = MapBridge()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_demo_data(db)
    finally:
        db.close()
    yield


app = FastAPI(title="MediRoute API", version="1.0.0", description="Role-based rural healthcare and emergency-care platform.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000").split(","), allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(self), camera=(self)"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, __: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "This conflicts with an existing record."})


def patient_for_user(db: Session, user: User) -> PatientProfile:
    patient = db.scalar(select(PatientProfile).where(PatientProfile.user_id == user.id))
    if not patient:
        raise HTTPException(404, "Patient profile not found")
    return patient


def get_hospital(db: Session, hospital_id: int) -> Hospital:
    hospital = db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(404, "Hospital not found")
    return hospital


def ensure_hospital_scope(user: User, hospital_id: int) -> None:
    if user.role == "HOSPITAL" and user.hospital_id != hospital_id:
        raise HTTPException(403, "You can only access your own facility's records")


def safe_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "language": user.language, "hospital_id": user.hospital_id}


async def persist_notice(db: Session, user_id: int, title: str, body: str, kind: str, reference: dict | None = None) -> None:
    preference = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    category_enabled = {"EMERGENCY": "emergency_enabled", "APPOINTMENT": "appointment_enabled", "FOLLOW_UP": "follow_up_enabled"}.get(kind)
    if preference and (not preference.in_app_enabled or (category_enabled and not getattr(preference, category_enabled))):
        return
    notify(db, user_id, title, body, kind, reference)
    db.flush()
    await manager.send(user_id, {"event": "notification", "title": title, "body": body, "kind": kind, "reference": reference or {}})


@app.get("/api/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "service": "MediRoute", "map_bridge": bridge.base_url}


@app.post("/api/auth/register", tags=["authentication"], status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> dict:
    rate_limit(request)
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "An account with this email already exists.")
    
    hosp_id = payload.hospital_id
    if payload.role in {"HOSPITAL", "DOCTOR"} and not hosp_id:
        first_hosp = db.scalar(select(Hospital.id).limit(1))
        hosp_id = first_hosp

    user = User(
        email=payload.email.lower(),
        password_hash=password_hash,
        full_name=payload.full_name,
        role=payload.role,
        language=payload.language,
        hospital_id=hosp_id
    )
    db.add(user)
    db.flush()

    if user.role == "PATIENT":
        db.add(PatientProfile(user_id=user.id))
    elif user.role == "DOCTOR":
        db.add(Doctor(user_id=user.id, hospital_id=hosp_id, specialty="General & Emergency Medicine", available=True))
    elif user.role == "AMBULANCE_DRIVER":
        amb = db.scalar(select(Ambulance).limit(1))
        db.add(AmbulanceDriver(user_id=user.id, ambulance_id=amb.id if amb else None, license_number=f"DL-{user.id:04d}-EMERGENCY"))

    audit(db, user.id, "REGISTER", "user", {"role": user.role, "terms_accepted": True})
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": safe_user(user)}


@app.post("/api/auth/login", tags=["authentication"])
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    rate_limit(request)
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    audit(db, user.id, "LOGIN", "user")
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": safe_user(user)}


@app.get("/api/auth/me", tags=["authentication"])
def me(user: User = Depends(get_current_user)) -> dict:
    return safe_user(user)


@app.websocket("/api/ws/notifications/{user_id}")
async def notifications_socket(websocket: WebSocket, user_id: int):
    token = websocket.query_params.get("token")
    try:
        token_user_id = int(jwt.decode(token or "", JWT_SECRET, algorithms=[JWT_ALGORITHM])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await websocket.close(code=1008)
        return
    if token_user_id != user_id:
        await websocket.close(code=1008)
        return
    await manager.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)


@app.get("/api/patients/me/profile", tags=["patients"])
def get_my_profile(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    return as_dict(patient_for_user(db, user))


@app.put("/api/patients/me/profile", tags=["patients"])
def update_my_profile(payload: PatientProfileIn, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    profile = patient_for_user(db, user)
    for field, value in payload.model_dump().items():
        setattr(profile, field, value)
    audit(db, user.id, "UPDATE_PROFILE", "patient", {"patient_id": profile.id})
    db.commit(); db.refresh(profile)
    return as_dict(profile)


@app.get("/api/patients/me/dashboard", tags=["patients"])
def patient_dashboard(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    patient = patient_for_user(db, user)
    return {
        "profile": as_dict(patient),
        "appointments": [as_dict(x) for x in db.scalars(select(Appointment).where(Appointment.patient_id == patient.id).order_by(Appointment.scheduled_for.desc())).all()],
        "referrals": [as_dict(x) for x in db.scalars(select(Referral).where(Referral.patient_id == patient.id).order_by(Referral.created_at.desc())).all()],
        "follow_ups": [as_dict(x) for x in db.scalars(select(FollowUp).where(FollowUp.patient_id == patient.id)).all()],
        "notifications_unread": db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read.is_(False))),
    }


@app.post("/api/triage", tags=["clinical"])
def triage(payload: TriageIn, user: User = Depends(require_roles("PATIENT", "HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    result = triage_indicator(payload.symptoms, payload.vitals, payload.age, payload.risk_factors)
    audit(db, user.id, "TRIAGE_RISK_INDICATOR", "triage", {"urgency": result["urgency"]})
    db.commit()
    return result


MAP_FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "hospital-ambulance-system final", "hospital-ambulance-system", "frontend"))

@app.get("/hospital", include_in_schema=False)
def hospital_map_shortcut():
    return FileResponse(os.path.join(MAP_FRONTEND_DIR, "hospital.html"))

@app.get("/ambulance", include_in_schema=False)
def ambulance_map_shortcut():
    return FileResponse(os.path.join(MAP_FRONTEND_DIR, "index.html"))

FALLBACK_MAP_HOSPITALS = [
    {
        "place_id": "JS001",
        "hospital_id": "JS001",
        "name": "JS Hospital - Emergency Center",
        "type": "Emergency Hospital",
        "latitude": 27.652000,
        "longitude": 77.558000,
        "address": "Emergency Expressway Sector 1, Central Medical Zone",
        "phone": "+91 1800 108 0001",
        "rating": 5.0,
        "user_ratings_total": 4500,
        "emergency_beds_total": 20,
        "emergency_beds_occupied": 5,
        "emergency_beds_available": 15,
        "icu_beds_total": 8,
        "icu_beds_occupied": 2,
        "icu_beds_available": 6,
        "ventilators_total": 6,
        "ventilators_available": 4,
        "oxygen_capacity_pct": 100,
        "doctors_available": 8,
        "burn_unit": True,
        "trauma_center": True,
        "cardiac_center": True,
        "stroke_ready": True,
        "ct_scan": True,
        "mri": True,
        "cath_lab": True,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "required_facility_available": True,
        "specialist_available": ["trauma", "cardiology", "neurology", "critical_care"],
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "distance_km": 0.83,
        "estimated_duration_min": 2,
        "duration_text": "2 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_KD_MEDICAL",
        "name": "K.D. Medical College Hospital & Research Center (Apex Trauma Center)",
        "latitude": 27.646870,
        "longitude": 77.551921,
        "address": "National Highway 44, PO-Chhatikara, Akbarpur, Mathura, Uttar Pradesh 281406",
        "phone": "+91 5662 281 100",
        "rating": 4.8,
        "user_ratings_total": 2840,
        "emergency_beds_total": 30,
        "emergency_beds_available": 14,
        "icu_beds_total": 12,
        "icu_beds_available": 5,
        "ventilators_available": 4,
        "doctors_available": 8,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "distance_km": 0.0,
        "estimated_duration_min": 2,
        "duration_text": "2 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_KD_DENTAL",
        "name": "KD Dental College & Hospital (Maxillofacial & Trauma Unit)",
        "latitude": 27.645050,
        "longitude": 77.550500,
        "address": "NH-44, Mathura-Delhi Road, Akbarpur, Mathura, Uttar Pradesh 281406",
        "phone": "+91 5662 281 111",
        "rating": 4.6,
        "user_ratings_total": 1420,
        "emergency_beds_total": 18,
        "emergency_beds_available": 8,
        "icu_beds_total": 6,
        "icu_beds_available": 3,
        "ventilators_available": 2,
        "doctors_available": 5,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "distance_km": 0.25,
        "estimated_duration_min": 2,
        "duration_text": "2 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_NAYATI_MEDICITY",
        "name": "Nayati Medicity Multi Super Speciality Hospital",
        "latitude": 27.564500,
        "longitude": 77.632500,
        "address": "NH-19, Mathura Bypass, Mathura, Uttar Pradesh 281006",
        "phone": "+91 565 711 1111",
        "rating": 4.8,
        "user_ratings_total": 3950,
        "emergency_beds_total": 35,
        "emergency_beds_available": 16,
        "icu_beds_total": 16,
        "icu_beds_available": 7,
        "ventilators_available": 6,
        "doctors_available": 8,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "distance_km": 12.12,
        "estimated_duration_min": 30,
        "duration_text": "30 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_RAMAKRISHNA_VRINDAVAN",
        "name": "Ramakrishna Mission Sevashrama Charitable Hospital Vrindavan",
        "latitude": 27.578000,
        "longitude": 77.684500,
        "address": "Swami Vivekananda Marg, Vrindavan, Mathura, Uttar Pradesh 281121",
        "phone": "+91 565 244 2310",
        "rating": 4.7,
        "user_ratings_total": 2890,
        "emergency_beds_total": 22,
        "emergency_beds_available": 7,
        "icu_beds_total": 8,
        "icu_beds_available": 3,
        "ventilators_available": 3,
        "doctors_available": 6,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "distance_km": 15.14,
        "estimated_duration_min": 38,
        "duration_text": "38 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_SWARN_JAYANTI",
        "name": "Swarn Jayanti Samudayik Hospital & Emergency Wing",
        "latitude": 27.508500,
        "longitude": 77.662000,
        "address": "Refinery Township, Mathura, Uttar Pradesh 281005",
        "phone": "+91 565 241 1234",
        "rating": 4.5,
        "user_ratings_total": 1650,
        "emergency_beds_total": 18,
        "emergency_beds_available": 5,
        "icu_beds_total": 6,
        "icu_beds_available": 2,
        "ventilators_available": 2,
        "doctors_available": 4,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "distance_km": 18.83,
        "estimated_duration_min": 47,
        "duration_text": "47 min",
        "accepted": None
    },
    {
        "place_id": "HOSP_DISTRICT_MATHURA",
        "name": "District Combined Hospital Mathura (Trauma Centre)",
        "latitude": 27.492400,
        "longitude": 77.673700,
        "address": "Civil Lines, Near Krishna Nagar, Mathura, Uttar Pradesh 281001",
        "phone": "+91 565 240 4040",
        "rating": 4.4,
        "user_ratings_total": 2100,
        "emergency_beds_total": 25,
        "emergency_beds_available": 9,
        "icu_beds_total": 8,
        "icu_beds_available": 3,
        "ventilators_available": 2,
        "doctors_available": 6,
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "distance_km": 20.95,
        "estimated_duration_min": 52,
        "duration_text": "52 min",
        "accepted": None
    }
]

# ---- PROXY ROUTES FOR LIVE MAP SYSTEM ----
@app.api_route("/api/hospitals/live-stream", methods=["GET"])
@app.api_route("/api/hospitals/nearby", methods=["GET"])
@app.api_route("/api/hospitals/eligibility/{path:path}", methods=["GET", "POST", "PUT"])
@app.api_route("/api/nearby-hospitals", methods=["GET"])
@app.api_route("/api/ambulance-location", methods=["GET", "POST"])
@app.api_route("/api/route/{path:path}", methods=["GET", "POST"])
@app.api_route("/api/emergency/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.api_route("/api/emergency-requests", methods=["GET", "POST"])
@app.api_route("/api/emergency-requests/{path:path}", methods=["GET", "POST"])
@app.api_route("/api/ambulances/{path:path}", methods=["GET", "POST"])
@app.api_route("/api/ambulance/{path:path}", methods=["GET", "POST"])
@app.api_route("/api/simulate/{path:path}", methods=["GET", "POST"])
async def proxy_map_backend(request: Request, path: str = ""):
    url = f"http://127.0.0.1:5000{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            req_body = await request.body()
            res = await client.request(
                method=request.method,
                url=url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                content=req_body,
            )
            content_type = res.headers.get("content-type", "")
            if "application/json" in content_type:
                data = res.json()
                if isinstance(data, dict) and "hospitals" in data and len(data.get("hospitals", [])) == 0:
                    data["hospitals"] = FALLBACK_MAP_HOSPITALS
                    data["count"] = len(FALLBACK_MAP_HOSPITALS)
                return JSONResponse(status_code=res.status_code, content=data)
            return JSONResponse(status_code=res.status_code, content={"status": res.text})
    except Exception:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "count": len(FALLBACK_MAP_HOSPITALS),
                "hospitals": FALLBACK_MAP_HOSPITALS,
                "origin": {"lat": 27.64687, "lng": 77.551921},
                "status": "active_network"
            }
        )


@app.get("/api/hospitals", tags=["facilities"])
def list_hospitals(db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Hospital).where(Hospital.active.is_(True))).all()]


@app.get("/api/hospitals/{hospital_id}", tags=["facilities"])
def hospital_detail(hospital_id: int, db: Session = Depends(get_db)) -> dict:
    return as_dict(get_hospital(db, hospital_id))


@app.get("/api/hospitals/me/patients", tags=["facilities"])
def hospital_patients(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    visits = db.scalars(select(Visit).where(Visit.hospital_id == user.hospital_id).order_by(Visit.created_at.desc())).all()
    result = []
    for visit in visits:
        patient = db.get(PatientProfile, visit.patient_id); patient_user = db.get(User, patient.user_id) if patient else None
        result.append({"visit_id": visit.id, "patient_id": visit.patient_id, "patient_name": patient_user.full_name if patient_user else "Unknown", "status": visit.status, "reason": visit.reason, "created_at": visit.created_at})
    return result


@app.get("/api/hospitals/me/appointments", tags=["facilities"])
def hospital_appointments(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Appointment).where(Appointment.hospital_id == user.hospital_id).order_by(Appointment.scheduled_for.asc())).all()]


@app.get("/api/hospitals/me/queue", tags=["facilities"])
def hospital_queue(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(QueueEntry).where(QueueEntry.hospital_id == user.hospital_id).order_by(QueueEntry.token_number.asc())).all()]


@app.get("/api/hospitals/me/referrals", tags=["facilities"])
def hospital_referrals(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Referral).where((Referral.from_hospital_id == user.hospital_id) | (Referral.to_hospital_id == user.hospital_id)).order_by(Referral.created_at.desc())).all()]


@app.get("/api/hospitals/me/emergencies", tags=["facilities"])
def hospital_emergencies(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(EmergencyRequest).where(EmergencyRequest.hospital_id == user.hospital_id).order_by(EmergencyRequest.created_at.desc())).all()]


@app.get("/api/hospitals/me/diagnostics", tags=["facilities"])
def hospital_diagnostics(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(DiagnosticOrder).where(DiagnosticOrder.hospital_id == user.hospital_id).order_by(DiagnosticOrder.created_at.desc())).all()]


@app.get("/api/hospitals/me/complaints", tags=["facilities"])
def hospital_complaints(user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Complaint).where(Complaint.hospital_id == user.hospital_id).order_by(Complaint.created_at.desc())).all()]


@app.patch("/api/hospitals/{hospital_id}/capacity", tags=["facilities"])
async def update_capacity(hospital_id: int, payload: dict[str, int], user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    ensure_hospital_scope(user, hospital_id)
    hospital = get_hospital(db, hospital_id)
    allowed = {"emergency_capacity", "emergency_available", "beds_available", "doctors_available"}
    for name, value in payload.items():
        if name in allowed and isinstance(value, int) and value >= 0:
            setattr(hospital, name, value)
    audit(db, user.id, "UPDATE_CAPACITY", "hospital", {"hospital_id": hospital_id})
    db.commit(); db.refresh(hospital)
    return as_dict(hospital)


@app.post("/api/appointments", tags=["appointments"], status_code=201)
async def create_appointment(payload: AppointmentIn, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    patient = patient_for_user(db, user); get_hospital(db, payload.hospital_id)
    if payload.doctor_id:
        doctor = db.get(Doctor, payload.doctor_id)
        if not doctor or doctor.hospital_id != payload.hospital_id or not doctor.available:
            raise HTTPException(409, "The selected doctor is not available")
    slots = db.scalars(select(AppointmentSlot).where(AppointmentSlot.hospital_id == payload.hospital_id, AppointmentSlot.status == "AVAILABLE")).all()
    matching_slot = next((slot for slot in slots if slot.starts_at == payload.scheduled_for and (payload.doctor_id is None or slot.doctor_id == payload.doctor_id)), None)
    if slots and not matching_slot:
        raise HTTPException(409, "Select an available appointment slot")
    appointment = Appointment(patient_id=patient.id, **payload.model_dump())
    if matching_slot: matching_slot.status = "BOOKED"
    db.add(appointment); db.flush()
    hospital_users = db.scalars(select(User).where(User.hospital_id == payload.hospital_id, User.role == "HOSPITAL")).all()
    for hospital_user in hospital_users:
        await persist_notice(db, hospital_user.id, "New appointment", f"Appointment requested for {payload.scheduled_for}.", "APPOINTMENT", {"appointment_id": appointment.id})
    audit(db, user.id, "BOOK_APPOINTMENT", "appointment", {"appointment_id": appointment.id})
    db.commit(); db.refresh(appointment)
    return as_dict(appointment)


@app.patch("/api/appointments/{appointment_id}/cancel", tags=["appointments"])
def cancel_appointment(appointment_id: int, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    appointment = db.get(Appointment, appointment_id); patient = patient_for_user(db, user)
    if not appointment or appointment.patient_id != patient.id:
        raise HTTPException(404, "Appointment not found")
    if appointment.status not in {"BOOKED", "CONFIRMED"}:
        raise HTTPException(409, "This appointment cannot be cancelled")
    appointment.status = "CANCELLED"; audit(db, user.id, "CANCEL_APPOINTMENT", "appointment", {"appointment_id": appointment_id}); db.commit()
    return as_dict(appointment)


@app.get("/api/hospitals/{hospital_id}/appointment-slots", tags=["appointments"])
def appointment_slots(hospital_id: int, db: Session = Depends(get_db)) -> list[dict]:
    get_hospital(db, hospital_id)
    return [as_dict(item) for item in db.scalars(select(AppointmentSlot).where(AppointmentSlot.hospital_id == hospital_id, AppointmentSlot.status == "AVAILABLE").order_by(AppointmentSlot.starts_at)).all()]


@app.post("/api/hospitals/{hospital_id}/appointment-slots", tags=["appointments"], status_code=201)
def create_appointment_slot(hospital_id: int, payload: AppointmentSlotIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    ensure_hospital_scope(user, hospital_id); get_hospital(db, hospital_id)
    if payload.doctor_id:
        doctor = db.get(Doctor, payload.doctor_id)
        if not doctor or doctor.hospital_id != hospital_id or not doctor.available: raise HTTPException(409, "Selected doctor is not available at this hospital")
    slot = AppointmentSlot(hospital_id=hospital_id, **payload.model_dump()); db.add(slot); audit(db, user.id, "CREATE_APPOINTMENT_SLOT", "appointment_slot", {"hospital_id": hospital_id}); db.commit(); db.refresh(slot)
    return as_dict(slot)


@app.post("/api/teleconsultations", tags=["appointments"], status_code=201)
def schedule_teleconsultation(payload: TeleconsultationIn, user: User = Depends(require_roles("PATIENT", "HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    appointment = db.get(Appointment, payload.appointment_id)
    if not appointment: raise HTTPException(404, "Appointment not found")
    if user.role == "PATIENT" and appointment.patient_id != patient_for_user(db, user).id: raise HTTPException(403, "This is not your appointment")
    if user.role == "HOSPITAL": ensure_hospital_scope(user, appointment.hospital_id)
    if payload.meeting_url and not payload.meeting_url.startswith(("https://", "http://")): raise HTTPException(422, "A provider URL must be an HTTP(S) URL")
    session = Teleconsultation(appointment_id=appointment.id, provider=payload.provider, meeting_url=payload.meeting_url, status="SCHEDULED" if payload.meeting_url else "PROVIDER_CONFIGURATION_REQUIRED")
    db.add(session); audit(db, user.id, "SCHEDULE_TELECONSULTATION", "teleconsultation", {"appointment_id": appointment.id, "configured": bool(payload.meeting_url)}); db.commit(); db.refresh(session)
    return {**as_dict(session), "note": "No simulated video call is provided. A real configured provider URL is required."}


@app.post("/api/appointments/{appointment_id}/queue", tags=["appointments"], status_code=201)
def join_queue(appointment_id: int, user: User = Depends(require_roles("PATIENT", "HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if user.role == "PATIENT" and appointment.patient_id != patient_for_user(db, user).id:
        raise HTTPException(403, "This is not your appointment")
    token = (db.scalar(select(func.max(QueueEntry.token_number)).where(QueueEntry.hospital_id == appointment.hospital_id)) or 0) + 1
    entry = QueueEntry(appointment_id=appointment.id, patient_id=appointment.patient_id, hospital_id=appointment.hospital_id, token_number=token)
    db.add(entry); appointment.status = "QUEUED"; db.commit(); db.refresh(entry)
    return {**as_dict(entry), "estimated_wait_minutes": max(0, (token - 1) * 12)}


@app.get("/api/queues/me", tags=["appointments"])
def my_queue(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    patient = patient_for_user(db, user)
    entries = db.scalars(select(QueueEntry).where(QueueEntry.patient_id == patient.id, QueueEntry.status == "WAITING")).all()
    return [{**as_dict(item), "estimated_wait_minutes": max(0, (item.token_number - 1) * 12)} for item in entries]


@app.post("/api/visits", tags=["clinical"], status_code=201)
def create_visit(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    patient_id, hospital_id = payload.get("patient_id"), payload.get("hospital_id", user.hospital_id)
    if not isinstance(patient_id, int) or not isinstance(hospital_id, int):
        raise HTTPException(422, "patient_id and hospital_id are required")
    ensure_hospital_scope(user, hospital_id); get_hospital(db, hospital_id)
    visit = Visit(patient_id=patient_id, hospital_id=hospital_id, reason=str(payload.get("reason", "")))
    db.add(visit); audit(db, user.id, "CREATE_VISIT", "visit", {"visit_id": visit.id, "patient_id": patient_id}); db.commit(); db.refresh(visit)
    return as_dict(visit)


@app.patch("/api/visits/{visit_id}/discharge", tags=["clinical"])
async def discharge_visit(visit_id: int, payload: dict[str, str], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    visit = db.get(Visit, visit_id)
    if not visit: raise HTTPException(404, "Visit not found")
    ensure_hospital_scope(user, visit.hospital_id)
    visit.status = "DISCHARGED"; visit.discharge_summary = str(payload.get("summary", ""))
    patient = db.get(PatientProfile, visit.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Discharge update", "Your treatment interaction has been discharged. Follow-up instructions are available from your care team.", "TREATMENT", {"visit_id": visit.id})
    audit(db, user.id, "DISCHARGE_VISIT", "visit", {"visit_id": visit.id}); db.commit(); db.refresh(visit)
    return as_dict(visit)


@app.post("/api/records", tags=["clinical"], status_code=201)
def add_record(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    patient_id, record_type = payload.get("patient_id"), payload.get("record_type")
    if not isinstance(patient_id, int) or not isinstance(record_type, str):
        raise HTTPException(422, "patient_id and record_type are required")
    record = MedicalRecord(patient_id=patient_id, record_type=record_type, content=payload.get("content", {}), author_user_id=user.id)
    db.add(record); audit(db, user.id, "ADD_MEDICAL_RECORD", "medical_record", {"patient_id": patient_id, "record_type": record_type}); db.commit(); db.refresh(record)
    return as_dict(record)


@app.get("/api/records/{patient_id}", tags=["clinical"])
def get_records(patient_id: int, user: User = Depends(require_roles("PATIENT", "HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> list[dict]:
    if user.role == "PATIENT" and patient_for_user(db, user).id != patient_id:
        raise HTTPException(403, "You can only access your own records")
    if user.role == "HOSPITAL" and not db.scalar(select(Visit.id).where(Visit.patient_id == patient_id, Visit.hospital_id == user.hospital_id)):
        raise HTTPException(403, "A care relationship is required to access this record")
    return [as_dict(record) for record in db.scalars(select(MedicalRecord).where(MedicalRecord.patient_id == patient_id).order_by(MedicalRecord.created_at.desc())).all()]


@app.post("/api/vitals", tags=["clinical"], status_code=201)
def add_vitals(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if not isinstance(payload.get("patient_id"), int):
        raise HTTPException(422, "patient_id is required")
    vital = Vital(patient_id=payload["patient_id"], visit_id=payload.get("visit_id"), values=payload.get("values", {}))
    db.add(vital); audit(db, user.id, "ADD_VITALS", "vital", {"patient_id": vital.patient_id}); db.commit(); db.refresh(vital)
    return as_dict(vital)


@app.post("/api/consultations", tags=["clinical"], status_code=201)
def add_consultation(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if not isinstance(payload.get("patient_id"), int) or not isinstance(payload.get("notes"), str):
        raise HTTPException(422, "patient_id and notes are required")
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
    consultation = Consultation(patient_id=payload["patient_id"], visit_id=payload.get("visit_id"), doctor_id=doctor.id if doctor else None, mode=str(payload.get("mode", "IN_PERSON")), notes=payload["notes"], diagnosis=payload.get("diagnosis"))
    db.add(consultation); db.commit(); db.refresh(consultation)
    return as_dict(consultation)


@app.post("/api/prescriptions", tags=["clinical"], status_code=201)
def add_prescription(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if not isinstance(payload.get("patient_id"), int):
        raise HTTPException(422, "patient_id is required")
    prescription = Prescription(patient_id=payload["patient_id"], consultation_id=payload.get("consultation_id"), items=payload.get("items", []), instructions=payload.get("instructions"))
    db.add(prescription); db.commit(); db.refresh(prescription)
    return as_dict(prescription)


@app.post("/api/diagnostics", tags=["diagnostics"], status_code=201)
async def create_diagnostic(payload: DiagnosticIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    ensure_hospital_scope(user, payload.hospital_id); order = DiagnosticOrder(**payload.model_dump())
    db.add(order); db.flush()
    patient = db.get(PatientProfile, payload.patient_id)
    if patient:
        await persist_notice(db, patient.user_id, "Diagnostic requested", f"{payload.test_name} has been requested.", "DIAGNOSTIC", {"order_id": order.id})
    db.commit(); db.refresh(order)
    return as_dict(order)


@app.patch("/api/diagnostics/{order_id}", tags=["diagnostics"])
async def update_diagnostic(order_id: int, payload: DiagnosticStatusIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    order = db.get(DiagnosticOrder, order_id)
    if not order: raise HTTPException(404, "Diagnostic order not found")
    ensure_hospital_scope(user, order.hospital_id); order.status = payload.status
    if payload.status in {"COMPLETED", "REPORT_AVAILABLE"}:
        report = db.scalar(select(DiagnosticReport).where(DiagnosticReport.order_id == order.id))
        if not report:
            report = DiagnosticReport(order_id=order.id); db.add(report)
        report.findings, report.report_url = payload.findings, payload.report_url
    patient = db.get(PatientProfile, order.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Diagnostic update", f"{order.test_name}: {order.status.replace('_', ' ').title()}.", "DIAGNOSTIC", {"order_id": order.id})
    db.commit(); db.refresh(order)
    return as_dict(order)


@app.get("/api/diagnostic-services", tags=["diagnostics"])
def list_diagnostic_services(test_name: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(DiagnosticService, Hospital).join(Hospital, Hospital.id == DiagnosticService.hospital_id).where(DiagnosticService.available.is_(True))
    if test_name: stmt = stmt.where(DiagnosticService.test_name.ilike(f"%{test_name}%"))
    return [{"hospital_id": hospital.id, "hospital": hospital.name, "test_name": service.test_name, "slots_available": service.slots_available, "available": service.available} for service, hospital in db.execute(stmt).all()]


@app.put("/api/hospitals/{hospital_id}/diagnostic-services", tags=["diagnostics"])
def set_diagnostic_service(hospital_id: int, payload: DiagnosticServiceIn, user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    ensure_hospital_scope(user, hospital_id); get_hospital(db, hospital_id)
    service = db.scalar(select(DiagnosticService).where(DiagnosticService.hospital_id == hospital_id, DiagnosticService.test_name == payload.test_name))
    if not service:
        service = DiagnosticService(hospital_id=hospital_id, test_name=payload.test_name); db.add(service)
    service.available, service.slots_available = payload.available, payload.slots_available
    audit(db, user.id, "UPDATE_DIAGNOSTIC_AVAILABILITY", "diagnostic_service", {"hospital_id": hospital_id, "test_name": payload.test_name}); db.commit(); db.refresh(service)
    return as_dict(service)


@app.get("/api/diagnostics/me", tags=["diagnostics"])
def my_diagnostics(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    patient = patient_for_user(db, user)
    return [as_dict(item) for item in db.scalars(select(DiagnosticOrder).where(DiagnosticOrder.patient_id == patient.id).order_by(DiagnosticOrder.created_at.desc())).all()]


@app.get("/api/consultations/me", tags=["clinical"])
def my_consultations(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    patient = patient_for_user(db, user)
    return [as_dict(item) for item in db.scalars(select(Consultation).where(Consultation.patient_id == patient.id).order_by(Consultation.created_at.desc())).all()]


@app.get("/api/prescriptions/me", tags=["clinical"])
def my_prescriptions(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    patient = patient_for_user(db, user)
    return [as_dict(item) for item in db.scalars(select(Prescription).where(Prescription.patient_id == patient.id).order_by(Prescription.created_at.desc())).all()]


@app.get("/api/visits/me", tags=["clinical"])
def my_visits(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    patient = patient_for_user(db, user)
    return [as_dict(item) for item in db.scalars(select(Visit).where(Visit.patient_id == patient.id).order_by(Visit.created_at.desc())).all()]


@app.get("/api/medicines/availability", tags=["medicines"])
def medicine_availability(query: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(MedicineInventory, Medicine, Hospital).join(Medicine, Medicine.id == MedicineInventory.medicine_id).join(Hospital, Hospital.id == MedicineInventory.hospital_id)
    if query: stmt = stmt.where(Medicine.name.ilike(f"%{query}%"))
    return [{"medicine": medicine.name, "generic_name": medicine.generic_name, "hospital_id": hospital.id, "hospital": hospital.name, "available": inventory.quantity > 0, "low_stock": inventory.quantity <= inventory.low_stock_threshold, "expiry_date": inventory.expiry_date} for inventory, medicine, hospital in db.execute(stmt).all()]


@app.put("/api/hospitals/{hospital_id}/inventory", tags=["medicines"])
def set_inventory(hospital_id: int, payload: InventoryIn, user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    ensure_hospital_scope(user, hospital_id); get_hospital(db, hospital_id)
    medicine = db.scalar(select(Medicine).where(Medicine.name == payload.medicine_name))
    if not medicine:
        medicine = Medicine(name=payload.medicine_name, generic_name=payload.generic_name); db.add(medicine); db.flush()
    inventory = db.scalar(select(MedicineInventory).where(MedicineInventory.hospital_id == hospital_id, MedicineInventory.medicine_id == medicine.id))
    if not inventory:
        inventory = MedicineInventory(hospital_id=hospital_id, medicine_id=medicine.id); db.add(inventory)
    for field, value in payload.model_dump(exclude={"medicine_name", "generic_name"}).items(): setattr(inventory, field, value)
    audit(db, user.id, "UPDATE_INVENTORY", "medicine_inventory", {"hospital_id": hospital_id, "medicine_id": medicine.id}); db.commit(); db.refresh(inventory)
    return as_dict(inventory)


@app.post("/api/follow-ups", tags=["follow-up"], status_code=201)
async def create_follow_up(payload: FollowUpIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    follow_up = FollowUp(**payload.model_dump()); db.add(follow_up); db.flush()
    patient = db.get(PatientProfile, payload.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Follow-up scheduled", f"{payload.condition} follow-up is due on {payload.due_on}.", "FOLLOW_UP", {"follow_up_id": follow_up.id})
    db.commit(); db.refresh(follow_up)
    return as_dict(follow_up)


@app.get("/api/follow-ups/me", tags=["follow-up"])
def my_follow_ups(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(FollowUp).where(FollowUp.patient_id == patient_for_user(db, user).id)).all()]


@app.post("/api/follow-ups/monitor", tags=["follow-up"])
async def monitor_follow_ups(user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    today = datetime.now(timezone.utc).date()
    missed = []
    for item in db.scalars(select(FollowUp).where(FollowUp.status == "SCHEDULED")).all():
        try:
            due = datetime.fromisoformat(item.due_on.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if due < today:
            item.status = "MISSED"; missed.append(item.id)
            if item.assigned_user_id: await persist_notice(db, item.assigned_user_id, "Missed follow-up", f"{item.condition} follow-up #{item.id} needs review.", "FOLLOW_UP", {"follow_up_id": item.id})
            if item.priority == "HIGH": db.add(Escalation(reason=f"High-priority follow-up #{item.id} was missed."))
    audit(db, user.id, "MONITOR_FOLLOW_UPS", "follow_up", {"missed_ids": missed}); db.commit()
    return {"missed_follow_up_ids": missed, "count": len(missed)}


@app.post("/api/maternal-records", tags=["clinical"], status_code=201)
def create_maternal_record(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if not isinstance(payload.get("patient_id"), int): raise HTTPException(422, "patient_id is required")
    content = {"pregnancy_history": payload.get("pregnancy_history", []), "anc_visits": payload.get("anc_visits", []), "vitals": payload.get("vitals", {}), "risk_indicators": payload.get("risk_indicators", []), "follow_up": payload.get("follow_up")}
    record = MedicalRecord(patient_id=payload["patient_id"], record_type="MATERNAL_HEALTH", content=content, author_user_id=user.id)
    patient = db.get(PatientProfile, payload["patient_id"])
    if patient and "pregnancy" not in patient.risk_flags: patient.risk_flags = [*(patient.risk_flags or []), "pregnancy"]
    db.add(record); db.commit(); db.refresh(record); return as_dict(record)


@app.post("/api/child-records", tags=["clinical"], status_code=201)
def create_child_record(payload: dict[str, Any], user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if not isinstance(payload.get("patient_id"), int): raise HTTPException(422, "patient_id is required")
    content = {"guardian": payload.get("guardian", {}), "growth_vitals": payload.get("growth_vitals", {}), "vaccinations": payload.get("vaccinations", []), "health_visits": payload.get("health_visits", []), "risk_indicators": payload.get("risk_indicators", [])}
    record = MedicalRecord(patient_id=payload["patient_id"], record_type="CHILD_HEALTH", content=content, author_user_id=user.id)
    db.add(record); db.commit(); db.refresh(record); return as_dict(record)


@app.get("/api/facility-matches", tags=["referrals", "emergency"])
def facility_matches(latitude: float | None = None, longitude: float | None = None, required_service: str | None = None, db: Session = Depends(get_db)) -> dict:
    return {"method": "transparent suitability score: required service, capacity, doctor availability, and travel distance", "clinical_disclaimer": "This supports authorized professionals; it is not a clinical decision.", "matches": match_facilities(db, latitude, longitude, required_service)}


@app.post("/api/referrals", tags=["referrals"], status_code=201)
async def create_referral(payload: ReferralIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    if payload.to_hospital_id: get_hospital(db, payload.to_hospital_id)
    referral = Referral(patient_id=payload.patient_id, from_hospital_id=user.hospital_id, to_hospital_id=payload.to_hospital_id, required_service=payload.required_service, clinical_summary=payload.clinical_summary, status="SENT", timeline=[{"status": "SENT", "at": utc_iso(), "note": "Referral created and sent"}])
    db.add(referral); db.flush()
    patient = db.get(PatientProfile, payload.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Referral sent", "A referral has been created. You can track each step here.", "REFERRAL", {"referral_id": referral.id})
    for receiving_user in db.scalars(select(User).where(User.hospital_id == payload.to_hospital_id, User.role == "HOSPITAL")).all():
        await persist_notice(db, receiving_user.id, "Incoming referral", f"Referral #{referral.id} requires {payload.required_service}.", "REFERRAL", {"referral_id": referral.id})
    audit(db, user.id, "CREATE_REFERRAL", "referral", {"referral_id": referral.id}); db.commit(); db.refresh(referral)
    return as_dict(referral)


@app.patch("/api/referrals/{referral_id}/status", tags=["referrals"])
async def update_referral(referral_id: int, payload: ReferralStatusIn, user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    referral = db.get(Referral, referral_id)
    if not referral: raise HTTPException(404, "Referral not found")
    if user.role == "HOSPITAL" and user.hospital_id not in {referral.from_hospital_id, referral.to_hospital_id}:
        raise HTTPException(403, "You are not involved in this referral")
    allowed = {"PENDING": {"SENT", "CANCELLED"}, "SENT": {"ACCEPTED", "REJECTED", "CANCELLED"}, "ACCEPTED": {"SCHEDULED", "IN_TRANSIT", "CANCELLED"}, "SCHEDULED": {"IN_TRANSIT", "CANCELLED"}, "IN_TRANSIT": {"ARRIVED", "CANCELLED"}, "ARRIVED": {"UNDER_TREATMENT"}, "UNDER_TREATMENT": {"DISCHARGED", "REFERRED_AGAIN"}, "DISCHARGED": {"COMPLETED"}, "REFERRED_AGAIN": {"SENT"}}
    if payload.status != referral.status and payload.status not in allowed.get(referral.status, set()):
        raise HTTPException(409, f"Invalid transition from {referral.status} to {payload.status}")
    referral.status = payload.status; referral.timeline = [*(referral.timeline or []), {"status": payload.status, "at": utc_iso(), "note": payload.note}]
    patient = db.get(PatientProfile, referral.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Referral update", f"Referral status: {payload.status.replace('_', ' ').title()}.", "REFERRAL", {"referral_id": referral.id})
    audit(db, user.id, "UPDATE_REFERRAL", "referral", {"referral_id": referral.id, "status": payload.status}); db.commit(); db.refresh(referral)
    return as_dict(referral)


@app.get("/api/referrals/me", tags=["referrals"])
def my_referrals(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Referral).where(Referral.patient_id == patient_for_user(db, user).id).order_by(Referral.created_at.desc())).all()]


@app.post("/api/emergencies", tags=["emergency"], status_code=201)
async def create_emergency(payload: EmergencyIn, user: User = Depends(require_roles("PATIENT", "HOSPITAL", "AMBULANCE_DRIVER")), db: Session = Depends(get_db)) -> dict:
    patient_id = payload.patient_id
    if user.role == "PATIENT": patient_id = patient_for_user(db, user).id
    matches = match_facilities(db, payload.latitude, payload.longitude, payload.required_service)
    hospital_id = payload.hospital_id or (matches[0]["hospital_id"] if matches else None)
    hospital = get_hospital(db, hospital_id) if hospital_id else None
    ambulance_id = payload.ambulance_id
    if user.role == "AMBULANCE_DRIVER":
        driver = db.scalar(select(AmbulanceDriver).where(AmbulanceDriver.user_id == user.id))
        ambulance_id = driver.ambulance_id if driver else ambulance_id
    if not ambulance_id:
        avail_amb = db.scalar(select(Ambulance).where(Ambulance.status == "AVAILABLE").limit(1))
        if avail_amb:
            ambulance_id = avail_amb.id
    emergency = EmergencyRequest(patient_id=patient_id, hospital_id=hospital_id, ambulance_id=ambulance_id, status="REQUESTED", details={"condition": payload.condition, "latitude": payload.latitude, "longitude": payload.longitude, "matches": matches, "timeline": [{"status": "REQUESTED", "at": utc_iso()}]})
    db.add(emergency); db.flush()
    ambulance = db.get(Ambulance, ambulance_id) if ambulance_id else None
    map_result = await bridge.dispatch({"ambulance_id": ambulance.map_ambulance_id if ambulance else "AMB-101", "map_place_id": hospital.map_place_id if hospital else "JS001", "patient_name": payload.patient_name or "Emergency Patient", "condition": payload.condition, "latitude": payload.latitude, "longitude": payload.longitude})
    if map_result["connected"]:
        emergency.map_request_id = map_result["data"].get("request_id")
    if hospital:
        for hospital_user in db.scalars(select(User).where(User.hospital_id == hospital.id, User.role == "HOSPITAL")).all():
            await persist_notice(db, hospital_user.id, "Emergency request", f"Emergency request #{emergency.id} requires {payload.condition}.", "EMERGENCY", {"emergency_id": emergency.id})
    if patient_id:
        patient = db.get(PatientProfile, patient_id)
        if patient: await persist_notice(db, patient.user_id, "Emergency request sent", "Facilities are being notified. Keep the app open for updates.", "EMERGENCY", {"emergency_id": emergency.id})
    audit(db, user.id, "CREATE_EMERGENCY", "emergency", {"emergency_id": emergency.id, "map_connected": map_result["connected"]}); db.commit(); db.refresh(emergency)
    return {**as_dict(emergency), "map_integration": {"connected": map_result["connected"], "message": "Dispatched to protected map service" if map_result["connected"] else "Map service unavailable; emergency has been safely stored for follow-up."}}


@app.patch("/api/emergencies/{emergency_id}/status", tags=["emergency"])
async def update_emergency_status(emergency_id: int, payload: dict[str, str], user: User = Depends(require_roles("HOSPITAL", "AMBULANCE_DRIVER")), db: Session = Depends(get_db)) -> dict:
    emergency = db.get(EmergencyRequest, emergency_id)
    if not emergency: raise HTTPException(404, "Emergency request not found")
    requested = str(payload.get("status", "")).upper()
    allowed = {"HOSPITAL_NOTIFIED", "ACCEPTED", "REJECTED", "DRIVER_ASSIGNED", "IN_TRANSIT", "ARRIVED", "UNDER_TREATMENT", "COMPLETED", "CANCELLED"}
    if requested not in allowed: raise HTTPException(422, "Unsupported emergency status")
    if user.role == "HOSPITAL": ensure_hospital_scope(user, emergency.hospital_id or -1)
    if user.role == "AMBULANCE_DRIVER" and not emergency.ambulance_id:
        driver = db.scalar(select(AmbulanceDriver).where(AmbulanceDriver.user_id == user.id))
        if driver and driver.ambulance_id:
            emergency.ambulance_id = driver.ambulance_id
    emergency.status = requested; details = emergency.details or {}; details["timeline"] = [*(details.get("timeline", [])), {"status": requested, "at": utc_iso()}]; emergency.details = details
    map_response_relayed = await bridge.respond(emergency.map_request_id, requested, payload.get("reason")) if emergency.map_request_id and requested in {"ACCEPTED", "REJECTED"} else None
    if emergency.patient_id:
        patient = db.get(PatientProfile, emergency.patient_id)
        if patient: await persist_notice(db, patient.user_id, "Emergency update", f"Emergency status: {requested.replace('_', ' ').title()}.", "EMERGENCY", {"emergency_id": emergency.id})
    audit(db, user.id, "UPDATE_EMERGENCY", "emergency", {"emergency_id": emergency.id, "status": requested}); db.commit(); db.refresh(emergency)
    return {**as_dict(emergency), "map_response_relayed": map_response_relayed}


@app.post("/api/emergencies/{emergency_id}/location", tags=["emergency"])
async def update_location(emergency_id: int, payload: LocationIn, user: User = Depends(require_roles("AMBULANCE_DRIVER")), db: Session = Depends(get_db)) -> dict:
    emergency = db.get(EmergencyRequest, emergency_id)
    if not emergency: raise HTTPException(404, "Emergency request not found")
    driver = db.scalar(select(AmbulanceDriver).where(AmbulanceDriver.user_id == user.id))
    if not driver or driver.ambulance_id != emergency.ambulance_id: raise HTTPException(403, "You are not assigned to this emergency")
    event = LocationEvent(emergency_id=emergency.id, ambulance_id=driver.ambulance_id, **payload.model_dump()); db.add(event)
    ambulance = db.get(Ambulance, driver.ambulance_id); ambulance.latitude, ambulance.longitude = payload.latitude, payload.longitude
    bridged = await bridge.update_location(ambulance.map_ambulance_id or ambulance.code, payload.latitude, payload.longitude, payload.speed_kmh)
    db.commit(); db.refresh(event)
    return {**as_dict(event), "map_location_relayed": bridged}


@app.get("/api/driver/assignments", tags=["emergency"])
def driver_assignments(user: User = Depends(require_roles("AMBULANCE_DRIVER")), db: Session = Depends(get_db)) -> list[dict]:
    driver = db.scalar(select(AmbulanceDriver).where(AmbulanceDriver.user_id == user.id))
    if not driver: return []
    return [as_dict(item) for item in db.scalars(select(EmergencyRequest).where(EmergencyRequest.ambulance_id == driver.ambulance_id).order_by(EmergencyRequest.created_at.desc())).all()]


@app.get("/api/emergencies/me/active", tags=["emergency"])
def get_my_active_emergency(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Fetch the caller's currently active emergency request."""
    query = select(EmergencyRequest).where(EmergencyRequest.status.notin_(["COMPLETED", "CANCELLED"]))
    if user.role == "PATIENT":
        patient = patient_for_user(db, user)
        query = query.where(EmergencyRequest.patient_id == patient.id)
    elif user.role == "HOSPITAL":
        query = query.where(EmergencyRequest.hospital_id == user.hospital_id)
    elif user.role == "AMBULANCE_DRIVER":
        driver = db.scalar(select(AmbulanceDriver).where(AmbulanceDriver.user_id == user.id))
        if not driver: return {"has_active": False, "emergency": None}
        query = query.where(EmergencyRequest.ambulance_id == driver.ambulance_id)
    emergency = db.scalar(query.order_by(EmergencyRequest.created_at.desc()).limit(1))
    if not emergency:
        return {"has_active": False, "emergency": None}
    return {"has_active": True, "emergency": as_dict(emergency)}


INITIAL_SAFETY_MESSAGES = {
    "en": (
        "Emergency help has been requested. An ambulance is being coordinated.\n\n"
        "If the person is unconscious, not breathing normally, having severe bleeding, choking, or experiencing another immediately life-threatening condition, contact emergency services (112) immediately and follow the instructions provided by the emergency dispatcher.\n\n"
        "Tell me what is happening, and I will provide basic first-aid guidance while help is coming."
    ),
    "hi": (
        "आपातकालीन सहायता का अनुरोध भेजा गया है। एम्बुलेंस की व्यवस्था की जा रही है।\n\n"
        "यदि व्यक्ति बेहोश है, सामान्य रूप से सांस नहीं ले रहा है, गंभीर रक्तस्राव हो रहा है, गला घुट रहा है, या कोई अन्य जानलेवा स्थिति है, तो तुरंत आपातकालीन सेवाओं (112) पर संपर्क करें और आपातकालीन डिस्पैचर द्वारा दिए गए निर्देशों का पालन करें।\n\n"
        "मुझे बताएं कि क्या हुआ है, और सहायता आने तक मैं बुनियादी प्राथमिक उपचार के कदम बताऊंगा।"
    ),
    "bn": (
        "জরুরি সাহায্যের অনুরোধ পাঠানো হয়েছে। অ্যাম্বুলেন্স সমন্বয় করা হচ্ছে।\n\n"
        "যদি ব্যক্তি অজ্ঞান হন, স্বাভাবিকভাবে শ্বাস না নেন, মারাত্মক রক্তক্ষরণ হয়, দমবন্ধ হয় বা অন্য কোনো আশঙ্কাজনক অবস্থা হয়, তবে অবিলম্বে জরুরি সেবায় (১১২) যোগাযোগ করুন এবং ডিসপ্যাচারের নির্দেশাবলী অনুসরণ করুন।\n\n"
        "কী ঘটেছে আমাকে বলুন, সাহায্য পৌঁছানো পর্যন্ত আমি প্রাথমিক চিকিৎসার পদক্ষেপগুলি প্রদান করব।"
    ),
    "mr": (
        "आपत्कालीन मदतीची विनंती पाठवली आहे. अ‍ॅम्ब्युलन्स समन्वित केली जात आहे.\n\n"
        "जर व्यक्ती बेशुद्ध असेल, श्वास सामान्य नसेल, तीव्र रक्तस्त्राव होत असेल किंवा इतर जीवघेणी परिस्थिती असेल, तर त्वरित आपत्कालीन सेवांशी (112) संपर्क साधा आणि डिस्पॅचरच्या सूचना पाळा.\n\n"
        "काय घडले आहे ते मला सांगा, आणि मदत येईपर्यंत मी मूलभूत प्रथमोपचार मार्गदर्शन देईन."
    ),
    "ta": (
        "அவசர உதவி கோரப்பட்டுள்ளது. ஆம்புலன்ஸ் ஒருங்கிணைக்கப்படுகிறது.\n\n"
        "பாதிக்கப்பட்டவர் மயக்கமடைந்தாலோ, சாதாரணமாக சுவாசிக்காவிட்டாலோ, கடுமையான இரத்தப்போக்கு ஏற்பட்டாலோ அல்லது உயிருக்கு ஆபத்தான நிலை ஏற்பட்டாலோ, உடனடியாக அவசர சேவைகளை (112) தொடர்புகொண்டு அவசர வழிகாட்டுதலைப் பின்பற்றவும்.\n\n"
        "என்ன நடக்கிறது என்று என்னிடம் கூறுங்கள், உதவி வரும் வரை நான் முதலுதவி வழிகாட்டுதலை வழங்குவேன்."
    ),
    "te": (
        "అత్యవసర సహాయం అభ్యర్థించబడింది. అంబులెన్స్ సమన్వయం చేయబడుతోంది.\n\n"
        "బాధితుడు స్పృహ కోల్పోయినా, శ్వాస సరిగ్గా ఆడకపోయినా, తీవ్ర రక్తస్రావం జరుగుతున్నా లేదా ప్రాణాపాయ స్థితిలో ఉన్నా, వెంటనే అత్యవసర సేవలను (112) సంప్రదించి డిస్పాచర్ సూచనలను పాటించండి.\n\n"
        "ఏం జరిగిందో నాకు చెప్పండి, సహాయం వచ్చే వరకు నేను ప్రథమ చికిత్స మార్గదర్శకాలను అందిస్తాను."
    )
}


@app.post("/api/emergency-assistant/session", tags=["emergency_assistant"], status_code=201)
def create_emergency_assistant_session(payload: EmergencySessionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Start or retrieve an active Emergency Assistant session."""
    emergency = db.get(EmergencyRequest, payload.emergency_request_id)
    if not emergency:
        raise HTTPException(404, "Emergency request not found")
        
    # Check authorization
    if user.role == "PATIENT":
        patient = patient_for_user(db, user)
        if emergency.patient_id and emergency.patient_id != patient.id:
            raise HTTPException(403, "You can only access your own emergency request")
    elif user.role == "HOSPITAL" and emergency.hospital_id and emergency.hospital_id != user.hospital_id:
        raise HTTPException(403, "Hospital access scope mismatch")
        
    # Find active session or create new
    session = db.scalar(
        select(EmergencyAssistantSession)
        .where(
            EmergencyAssistantSession.emergency_request_id == emergency.id,
            EmergencyAssistantSession.user_id == user.id,
            EmergencyAssistantSession.status == "ACTIVE"
        )
        .order_by(EmergencyAssistantSession.created_at.desc())
        .limit(1)
    )
    
    lang = payload.language or user.language or "en"
    if session:
        session.language = lang
        db.commit()
    else:
        session = EmergencyAssistantSession(
            emergency_request_id=emergency.id,
            user_id=user.id,
            language=lang,
            status="ACTIVE"
        )
        db.add(session)
        db.flush()
        
        # Add initial safety message
        init_text = INITIAL_SAFETY_MESSAGES.get(lang, INITIAL_SAFETY_MESSAGES["en"])
        welcome_msg = EmergencyAssistantMessage(
            session_id=session.id,
            sender="assistant",
            message=init_text,
            category="general_emergency",
            structured_data={
                "is_initial": True,
                "emergency_phone": EMERGENCY_PHONE_NUMBER,
                "source": "MediRoute Safety Protocols"
            }
        )
        db.add(welcome_msg)
        db.commit()
        db.refresh(session)
        
    # Fetch messages
    msgs = db.scalars(
        select(EmergencyAssistantMessage)
        .where(EmergencyAssistantMessage.session_id == session.id)
        .order_by(EmergencyAssistantMessage.timestamp.asc())
    ).all()
    
    return {
        **as_dict(session),
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "message": m.message,
                "category": m.category,
                "structured_data": m.structured_data,
                "timestamp": m.timestamp.isoformat()
            }
            for m in msgs
        ]
    }


@app.post("/api/emergency-assistant/message", tags=["emergency_assistant"])
async def send_emergency_assistant_message(payload: EmergencyMessageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Send user text or transcribed speech into the Safety + Retrieval + AI pipeline."""
    session = db.get(EmergencyAssistantSession, payload.session_id)
    if not session or session.status != "ACTIVE":
        raise HTTPException(404, "Active emergency assistant session not found")
        
    if session.user_id != user.id and user.role not in ("ADMIN", "GOVERNMENT_AUTHORITY"):
        raise HTTPException(403, "You are not authorized to message on this session")
        
    if payload.language:
        session.language = payload.language
        
    res = await process_emergency_message(
        db=db,
        session=session,
        user_message=payload.message,
        user=user,
        ai_provider=get_ai_provider()
    )
    return res


@app.get("/api/emergency-assistant/session/{session_id}", tags=["emergency_assistant"])
def get_emergency_assistant_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Fetch session history and messages."""
    session = db.get(EmergencyAssistantSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
        
    if session.user_id != user.id and user.role not in ("ADMIN", "GOVERNMENT_AUTHORITY", "HOSPITAL", "AMBULANCE_DRIVER"):
        raise HTTPException(403, "Unauthorized access to session")
        
    msgs = db.scalars(
        select(EmergencyAssistantMessage)
        .where(EmergencyAssistantMessage.session_id == session.id)
        .order_by(EmergencyAssistantMessage.timestamp.asc())
    ).all()
    
    return {
        **as_dict(session),
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "message": m.message,
                "category": m.category,
                "structured_data": m.structured_data,
                "timestamp": m.timestamp.isoformat()
            }
            for m in msgs
        ]
    }


@app.get("/api/emergency-assistant/emergency/{emergency_id}/session", tags=["emergency_assistant"])
def get_emergency_session_by_emergency(emergency_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Fetch session history and messages by emergency request ID for authorized staff and patient."""
    emergency = db.get(EmergencyRequest, emergency_id)
    if not emergency:
        raise HTTPException(404, "Emergency request not found")
        
    if user.role == "PATIENT":
        patient = patient_for_user(db, user)
        if emergency.patient_id and emergency.patient_id != patient.id:
            raise HTTPException(403, "You can only access your own emergency request")
    elif user.role not in ("HOSPITAL", "AMBULANCE_DRIVER", "DOCTOR", "ADMIN", "GOVERNMENT_AUTHORITY"):
        raise HTTPException(403, "Unauthorized access")
        
    session = db.scalar(
        select(EmergencyAssistantSession)
        .where(EmergencyAssistantSession.emergency_request_id == emergency.id)
        .order_by(EmergencyAssistantSession.created_at.desc())
        .limit(1)
    )
    if not session:
        return {"has_session": False, "messages": []}
        
    msgs = db.scalars(
        select(EmergencyAssistantMessage)
        .where(EmergencyAssistantMessage.session_id == session.id)
        .order_by(EmergencyAssistantMessage.timestamp.asc())
    ).all()
    
    return {
        "has_session": True,
        **as_dict(session),
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "message": m.message,
                "category": m.category,
                "structured_data": m.structured_data,
                "timestamp": m.timestamp.isoformat()
            }
            for m in msgs
        ]
    }


@app.get("/api/emergency-assistant/status/{emergency_id}", tags=["emergency_assistant"])
async def get_emergency_assistant_status(emergency_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """
    Real-time telemetry integration with MediRoute database and protected map service.
    Retrieves real ambulance telemetry, live ETA, and hospital destination without fabrication.
    """
    emergency = db.get(EmergencyRequest, emergency_id)
    if not emergency:
        raise HTTPException(404, "Emergency request not found")
        
    if user.role == "PATIENT":
        patient = patient_for_user(db, user)
        if emergency.patient_id and emergency.patient_id != patient.id:
            raise HTTPException(403, "Access forbidden")
            
    hospital = db.get(Hospital, emergency.hospital_id) if emergency.hospital_id else None
    ambulance = db.get(Ambulance, emergency.ambulance_id) if emergency.ambulance_id else None
    
    # Defaults
    eta_min = None
    eta_text = "Ambulance ETA is currently unavailable."
    distance_km = None
    callsign = ambulance.code if ambulance else "Emergency Response Unit"
    plate = None
    crew = None
    amb_lat = ambulance.latitude if ambulance else None
    amb_lng = ambulance.longitude if ambulance else None
    
    # Query live map backend adapter for active real road telemetry
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(f"{bridge.base_url}/api/emergency/active-request")
            if res.status_code == 200:
                data = res.json()
                if data.get("has_active") and data.get("data"):
                    m_data = data["data"]
                    eta_min = m_data.get("eta_min")
                    eta_text = m_data.get("eta_text", f"{eta_min} min" if eta_min else eta_text)
                    distance_km = m_data.get("distance_km")
                    callsign = m_data.get("ambulance_callsign") or callsign
                    plate = m_data.get("ambulance_plate")
                    crew = m_data.get("crew")
                    if m_data.get("ambulance_location"):
                        amb_lat = m_data["ambulance_location"].get("lat", amb_lat)
                        amb_lng = m_data["ambulance_location"].get("lng", amb_lng)
    except Exception:
        # If map service is unreachable, compute safe direct distance if coordinates exist
        if ambulance and hospital and None not in (ambulance.latitude, ambulance.longitude, hospital.latitude, hospital.longitude):
            distance_km = round(haversine_km(ambulance.latitude, ambulance.longitude, hospital.latitude, hospital.longitude), 2)
            eta_min = max(2, int(round((distance_km * 1.25 / 35.0) * 60)))
            eta_text = f"{eta_min} min"
            
    return {
        "emergency_id": emergency.id,
        "status": emergency.status,
        "ambulance_assigned": bool(emergency.ambulance_id),
        "ambulance": {
            "id": ambulance.id,
            "code": ambulance.code,
            "callsign": callsign,
            "plate": plate,
            "vehicle_type": ambulance.vehicle_type,
            "crew": crew,
            "latitude": amb_lat,
            "longitude": amb_lng
        } if ambulance else None,
        "hospital": {
            "id": hospital.id,
            "name": hospital.name,
            "address": hospital.address,
            "phone": hospital.phone,
            "emergency_available": hospital.emergency_available
        } if hospital else None,
        "eta_min": eta_min,
        "eta_text": eta_text,
        "distance_km": distance_km,
        "emergency_phone": EMERGENCY_PHONE_NUMBER,
        "map_request_id": emergency.map_request_id
    }


@app.get("/api/emergency-assistant/guidance/{category}", tags=["emergency_assistant"])
def get_guidance_for_category(category: str, language: str = "en", db: Session = Depends(get_db)) -> dict:
    """Fetch approved first-aid guidance for a given emergency category."""
    guidance = get_approved_guidance(category, language)
    return {
        "category": category,
        "language": language,
        "guidance": guidance,
        "source": "WHO / Red Cross Approved First-Aid Protocols",
        "emergency_phone": EMERGENCY_PHONE_NUMBER
    }



@app.post("/api/treatment-records", tags=["accountability"], status_code=201)
def create_treatment_record(payload: TreatmentIn, user: User = Depends(require_roles("HOSPITAL")), db: Session = Depends(get_db)) -> dict:
    visit = db.get(Visit, payload.visit_id)
    if not visit or visit.patient_id != payload.patient_id: raise HTTPException(404, "Visit not found")
    ensure_hospital_scope(user, visit.hospital_id)
    record = TreatmentRecord(patient_id=payload.patient_id, visit_id=visit.id, hospital_id=visit.hospital_id, responsible_user_id=user.id, details=payload.model_dump(exclude={"patient_id", "visit_id"}))
    db.add(record); audit(db, user.id, "CREATE_TREATMENT_RECORD", "treatment_record", {"visit_id": visit.id}); db.commit(); db.refresh(record)
    return as_dict(record)


@app.post("/api/patient-feedback", tags=["accountability"], status_code=201)
async def create_feedback(payload: FeedbackIn, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    patient = patient_for_user(db, user); visit = db.get(Visit, payload.visit_id)
    if not visit or visit.patient_id != patient.id: raise HTTPException(404, "Visit not found")
    answers = payload.model_dump(exclude={"visit_id", "written_feedback"})
    feedback = PatientFeedback(patient_id=patient.id, visit_id=visit.id, answers=answers, written_feedback=payload.written_feedback)
    db.add(feedback); db.flush()
    for hospital_user in db.scalars(select(User).where(User.hospital_id == visit.hospital_id, User.role == "HOSPITAL")).all():
        await persist_notice(db, hospital_user.id, "Patient treatment feedback", "New feedback is available for review.", "FEEDBACK", {"visit_id": visit.id})
    audit(db, user.id, "CREATE_FEEDBACK", "patient_feedback", {"visit_id": visit.id}); db.commit(); db.refresh(feedback)
    return as_dict(feedback)


@app.post("/api/visits/{visit_id}/compare-treatment", tags=["accountability"])
def run_treatment_comparison(visit_id: int, user: User = Depends(require_roles("HOSPITAL", "ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> dict:
    visit = db.get(Visit, visit_id)
    if not visit: raise HTTPException(404, "Visit not found")
    if user.role == "HOSPITAL": ensure_hospital_scope(user, visit.hospital_id)
    feedback = db.scalar(select(PatientFeedback).where(PatientFeedback.visit_id == visit_id).order_by(PatientFeedback.created_at.desc()))
    record = db.scalar(select(TreatmentRecord).where(TreatmentRecord.visit_id == visit_id).order_by(TreatmentRecord.created_at.desc()))
    if not feedback or not record: raise HTTPException(409, "Both patient feedback and hospital treatment record are required")
    result = compare_treatment(feedback, record)
    comparison = db.scalar(select(TreatmentComparison).where(TreatmentComparison.visit_id == visit_id))
    if not comparison:
        comparison = TreatmentComparison(visit_id=visit_id, feedback_id=feedback.id, treatment_record_id=record.id, result=result); db.add(comparison)
    else: comparison.result, comparison.feedback_id, comparison.treatment_record_id = result, feedback.id, record.id
    audit(db, user.id, "COMPARE_TREATMENT", "treatment_comparison", {"visit_id": visit_id, "findings": len(result["findings"])}); db.commit(); db.refresh(comparison)
    return as_dict(comparison)


@app.post("/api/ratings", tags=["accountability"], status_code=201)
def create_rating(payload: RatingIn, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    patient = patient_for_user(db, user); visit = db.get(Visit, payload.visit_id)
    if not visit or visit.patient_id != patient.id: raise HTTPException(404, "Completed healthcare interaction not found")
    if visit.status != "DISCHARGED": raise HTTPException(409, "Ratings are available after a completed/discharged interaction")
    rating = Rating(patient_id=patient.id, hospital_id=visit.hospital_id, visit_id=visit.id, stars=payload.stars, feedback=payload.feedback)
    db.add(rating); audit(db, user.id, "CREATE_RATING", "rating", {"visit_id": visit.id, "stars": payload.stars}); db.commit(); db.refresh(rating)
    return as_dict(rating)


@app.post("/api/complaints", tags=["complaints"], status_code=201)
async def create_complaint(payload: ComplaintIn, user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> dict:
    patient = patient_for_user(db, user)
    if payload.visit_id:
        visit = db.get(Visit, payload.visit_id)
        if not visit or visit.patient_id != patient.id: raise HTTPException(404, "Healthcare interaction not found")
    complaint = Complaint(patient_id=patient.id, **payload.model_dump())
    db.add(complaint); db.flush()
    if complaint.hospital_id:
        for hospital_user in db.scalars(select(User).where(User.hospital_id == complaint.hospital_id, User.role == "HOSPITAL")).all():
            await persist_notice(db, hospital_user.id, "Complaint response required", f"A {complaint.severity.lower()} complaint requires review.", "COMPLAINT", {"complaint_id": complaint.id})
    if complaint.severity == "SERIOUS":
        db.add(Escalation(hospital_id=complaint.hospital_id, complaint_id=complaint.id, reason="Serious complaint submitted; neutral review required.")); complaint.status = "ESCALATED"
    audit(db, user.id, "CREATE_COMPLAINT", "complaint", {"complaint_id": complaint.id, "severity": complaint.severity}); db.commit(); db.refresh(complaint)
    return as_dict(complaint)


@app.post("/api/complaints/{complaint_id}/responses", tags=["complaints"], status_code=201)
async def respond_complaint(complaint_id: int, payload: ComplaintResponseIn, user: User = Depends(require_roles("HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    complaint = db.get(Complaint, complaint_id)
    if not complaint: raise HTTPException(404, "Complaint not found")
    if user.role == "HOSPITAL": ensure_hospital_scope(user, complaint.hospital_id or -1)
    response = ComplaintResponse(complaint_id=complaint.id, responder_id=user.id, **payload.model_dump()); db.add(response)
    complaint.status = "HOSPITAL_RESPONDED" if user.role == "HOSPITAL" else "UNDER_REVIEW"
    patient = db.get(PatientProfile, complaint.patient_id)
    if patient and payload.visible_to_patient: await persist_notice(db, patient.user_id, "Complaint update", "A response has been added to your complaint.", "COMPLAINT", {"complaint_id": complaint.id})
    audit(db, user.id, "RESPOND_COMPLAINT", "complaint", {"complaint_id": complaint.id}); db.commit(); db.refresh(response)
    return as_dict(response)


@app.patch("/api/complaints/{complaint_id}/status", tags=["complaints"])
async def update_complaint_status(complaint_id: int, payload: dict[str, str], user: User = Depends(require_roles("HOSPITAL", "ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> dict:
    complaint = db.get(Complaint, complaint_id)
    if not complaint: raise HTTPException(404, "Complaint not found")
    status_value = str(payload.get("status", "")).upper(); allowed = {"SUBMITTED", "RECEIVED", "UNDER_REVIEW", "HOSPITAL_RESPONSE_REQUIRED", "HOSPITAL_RESPONDED", "ESCALATED", "INVESTIGATION", "ACTION_TAKEN", "RESOLVED", "CLOSED"}
    if status_value not in allowed: raise HTTPException(422, "Unsupported complaint status")
    complaint.status = status_value; patient = db.get(PatientProfile, complaint.patient_id)
    if patient: await persist_notice(db, patient.user_id, "Complaint status", f"Your complaint is now {status_value.replace('_', ' ').title()}.", "COMPLAINT", {"complaint_id": complaint.id})
    audit(db, user.id, "UPDATE_COMPLAINT", "complaint", {"complaint_id": complaint.id, "status": status_value}); db.commit(); db.refresh(complaint)
    return as_dict(complaint)


@app.get("/api/complaints/me", tags=["complaints"])
def my_complaints(user: User = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Complaint).where(Complaint.patient_id == patient_for_user(db, user).id).order_by(Complaint.created_at.desc())).all()]


@app.get("/api/notifications", tags=["notifications"])
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())).all()]


@app.patch("/api/notifications/{notification_id}/read", tags=["notifications"])
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id: raise HTTPException(404, "Notification not found")
    item.read = True; db.commit(); return as_dict(item)


@app.get("/api/notification-preferences", tags=["notifications"])
def get_notification_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    preference = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user.id))
    if not preference:
        preference = NotificationPreference(user_id=user.id); db.add(preference); db.commit(); db.refresh(preference)
    return as_dict(preference)


@app.put("/api/notification-preferences", tags=["notifications"])
def update_notification_preferences(payload: NotificationPreferenceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    preference = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user.id))
    if not preference:
        preference = NotificationPreference(user_id=user.id); db.add(preference)
    for field, value in payload.model_dump().items(): setattr(preference, field, value)
    audit(db, user.id, "UPDATE_NOTIFICATION_PREFERENCES", "notification_preference", payload.model_dump()); db.commit(); db.refresh(preference)
    return as_dict(preference)


@app.post("/api/ai/analyse", tags=["decision-support"])
async def ai_analyse(payload: AIAnalysisIn, user: User = Depends(require_roles("PATIENT", "HOSPITAL", "ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> dict:
    result = await get_ai_provider().analyse(payload.task, payload.text)
    audit(db, user.id, "DECISION_SUPPORT_ANALYSIS", "ai_service", {"task": payload.task, "provider": result["provider"], "ai_assisted": result["ai_assisted"]})
    db.commit()
    return result


@app.get("/api/patients/{patient_id}/interoperability-preview", tags=["clinical"])
def interoperability_preview(patient_id: int, user: User = Depends(require_roles("PATIENT", "HOSPITAL", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    """FHIR-shaped preview for mapping discussion only; this application does not claim FHIR compliance."""
    if user.role == "PATIENT" and patient_for_user(db, user).id != patient_id: raise HTTPException(403, "You can only access your own data")
    patient = db.get(PatientProfile, patient_id)
    if not patient: raise HTTPException(404, "Patient not found")
    person = db.get(User, patient.user_id)
    records = db.scalars(select(MedicalRecord).where(MedicalRecord.patient_id == patient_id)).all()
    resources = [{"resourceType": "Patient", "id": str(patient.id), "name": [{"text": person.full_name}], "telecom": [{"system": "phone", "value": patient.phone}]}]
    resources.extend({"resourceType": "Observation", "status": "final", "code": {"text": item.record_type}, "valueString": str(item.content)} for item in records)
    return {"standard": "FHIR-shaped interoperability preview", "compliance": "Not FHIR compliant", "resources": resources}


def quality_for_hospital(db: Session, hospital_id: int) -> QualityIndicator:
    policy = db.scalar(select(PolicySetting).where(PolicySetting.key == "quality_thresholds"))
    threshold = policy.value if policy else {"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3}
    ratings = db.scalars(select(Rating).where(Rating.hospital_id == hospital_id)).all()
    low_count = len([rating for rating in ratings if rating.stars <= 2])
    low_pct = round((low_count / len(ratings) * 100), 1) if ratings else 0
    open_complaints = db.scalar(select(func.count(Complaint.id)).where(Complaint.hospital_id == hospital_id, Complaint.status.notin_(["RESOLVED", "CLOSED"]))) or 0
    if len(ratings) >= threshold.get("minimum_ratings", 3) and low_pct >= threshold.get("low_rating_percentage", 40): level = "HIGH_CONCERN"
    elif open_complaints >= threshold.get("complaint_count", 3): level = "ATTENTION_REQUIRED"
    else: level = "LOW_CONCERN"
    indicator = QualityIndicator(hospital_id=hospital_id, level=level, evidence={"method": "policy-driven statistical indicator", "ratings_count": len(ratings), "low_ratings": low_count, "low_rating_percentage": low_pct, "open_complaints": open_complaints, "note": "Decision support only; authorized reviewers make final judgments."})
    db.add(indicator); return indicator


@app.post("/api/admin/quality/recalculate", tags=["administration"])
async def recalculate_quality(user: User = Depends(require_roles("ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> list[dict]:
    indicators = [quality_for_hospital(db, hospital.id) for hospital in db.scalars(select(Hospital)).all()]
    for indicator in indicators:
        if indicator.level in {"HIGH_CONCERN", "ATTENTION_REQUIRED"}:
            db.add(Escalation(hospital_id=indicator.hospital_id, reason="Quality review recommended by configured statistical policy."))
    audit(db, user.id, "RECALCULATE_QUALITY", "quality_indicator"); db.commit()
    return [as_dict(indicator) for indicator in indicators]


@app.get("/api/admin/users", tags=["administration"])
def admin_users(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)) -> list[dict]:
    return [safe_user(item) | {"active": item.active, "created_at": item.created_at} for item in db.scalars(select(User).order_by(User.role, User.full_name)).all()]


@app.get("/api/admin/audit-logs", tags=["administration"])
def audit_logs(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)) -> list[dict]:
    return [as_dict(item) for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(300)).all()]


@app.get("/api/admin/policy", tags=["administration"])
def get_policy(user: User = Depends(require_roles("ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> dict:
    setting = db.scalar(select(PolicySetting).where(PolicySetting.key == "quality_thresholds"))
    return setting.value if setting else {"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3, "review_period_days": 30}


@app.put("/api/admin/policy", tags=["administration"])
def set_policy(payload: PolicyIn, user: User = Depends(require_roles("ADMIN", "GOVERNMENT_AUTHORITY")), db: Session = Depends(get_db)) -> dict:
    setting = db.scalar(select(PolicySetting).where(PolicySetting.key == "quality_thresholds"))
    if not setting: setting = PolicySetting(key="quality_thresholds"); db.add(setting)
    setting.value = payload.model_dump(); audit(db, user.id, "UPDATE_QUALITY_POLICY", "policy_settings", setting.value); db.commit(); return setting.value


@app.get("/api/government/dashboard", tags=["government"])
def government_dashboard(user: User = Depends(require_roles("GOVERNMENT_AUTHORITY", "ADMIN")), db: Session = Depends(get_db)) -> dict:
    # This endpoint intentionally aggregates operational data and omits individual patient identities.
    hospital_rows = db.execute(select(Hospital.id, Hospital.name, Hospital.beds_available, Hospital.doctors_available, Hospital.emergency_available)).all()
    referral_total = db.scalar(select(func.count(Referral.id))) or 0
    referral_completed = db.scalar(select(func.count(Referral.id)).where(Referral.status == "COMPLETED")) or 0
    return {
        "facilities": [{"hospital_id": row.id, "name": row.name, "beds_available": row.beds_available, "doctors_available": row.doctors_available, "emergency_available": row.emergency_available} for row in hospital_rows],
        "patient_volume": db.scalar(select(func.count(Visit.id))) or 0,
        "referrals": {"total": referral_total, "completed": referral_completed, "completion_rate": round(referral_completed / referral_total * 100, 1) if referral_total else 0},
        "appointments": db.scalar(select(func.count(Appointment.id))) or 0,
        "teleconsultations": db.scalar(select(func.count(Consultation.id)).where(Consultation.mode == "TELECONSULTATION")) or 0,
        "medicine_shortages": db.scalar(select(func.count(MedicineInventory.id)).where(MedicineInventory.quantity <= MedicineInventory.low_stock_threshold)) or 0,
        "diagnostics_pending": db.scalar(select(func.count(DiagnosticOrder.id)).where(DiagnosticOrder.status.notin_(["COMPLETED", "REPORT_AVAILABLE", "CANCELLED"]))) or 0,
        "emergency_activity": db.scalar(select(func.count(EmergencyRequest.id))) or 0,
        "high_risk_followups": db.scalar(select(func.count(FollowUp.id)).where(FollowUp.priority == "HIGH")) or 0,
        "complaint_trends": {"total": db.scalar(select(func.count(Complaint.id))) or 0, "open": db.scalar(select(func.count(Complaint.id)).where(Complaint.status.notin_(["RESOLVED", "CLOSED"]))) or 0},
        "quality_indicators": [as_dict(item) for item in db.scalars(select(QualityIndicator).order_by(QualityIndicator.created_at.desc()).limit(30)).all()],
        "escalations": [as_dict(item) for item in db.scalars(select(Escalation).where(Escalation.status == "OPEN").order_by(Escalation.created_at.desc())).all()],
        "privacy_note": "Aggregated dashboard; patient identities are deliberately omitted.",
    }


TRANSLATIONS = {
    "en": {
        "app_name": "MediRoute", "tagline": "RURAL HEALTHCARE & EMERGENCY CARE", "tagline_desc": "Connected care, referrals and emergency coordination. Clinical decisions remain with qualified professionals.", "welcome": "Welcome", "sign_out": "Sign out", "sign_in": "Sign in securely", "loading": "Loading secure data...", "language_loaded": "Language pack loaded: English", "email": "Email", "password": "Password", "demo_accounts": "⚡ Quick Demo Role Logins:", "demo_accounts_hint": "Password for all accounts: MediRoute!2026", "quick_login": "Quick Login",
        "emergency": "Emergency", "appointments": "Appointments", "records": "Health Record", "referrals": "Referrals", "notifications": "Notifications", "dashboard": "Dashboard", "consultations": "Consultations", "diagnostics": "Diagnostics", "medicines": "Medicines", "follow_up": "Follow-up", "feedback": "Feedback", "ratings": "Ratings", "complaints": "Complaints", "profile": "Profile", "patients": "Patients", "queue": "Queue", "treatment": "Treatment", "facility": "Facility", "emergency_assignment": "Emergency Assignment", "patient_pickup": "Patient Pickup", "hospital_destination": "Hospital Destination", "navigation": "Navigation", "trip_status": "Trip Status", "history": "History", "users": "Users", "hospitals": "Hospitals", "escalations": "Escalations", "quality": "Quality", "analytics": "Analytics", "settings": "Settings", "audit_logs": "Audit Logs", "facilities": "Facilities", "healthcare_analytics": "Healthcare Analytics", "quality_indicators": "Quality Indicators", "referral_monitoring": "Referral Monitoring", "medicine_diagnostic_availability": "Medicine/Diagnostic Availability", "emergency_analytics": "Emergency Analytics",
        "patient_journey_desc": "Your health journey is connected from appointments through follow-up. In an emergency, use the emergency screen or call local emergency services.", "active_referrals": "Active Referrals", "unread_notifications": "Unread Notifications", "upcoming_followup": "Upcoming Follow-up", "phone": "Phone", "address": "Address", "allergies": "Allergies (comma-separated)", "save_profile": "Save Profile", "digital_health_record": "Digital Health Record", "type": "Type", "recorded": "Recorded", "details": "Details", "mode": "Mode", "clinical_note": "Clinical Note", "date": "Date", "prescriptions": "Prescriptions", "instructions": "Instructions", "book_appointment": "Book Appointment", "my_appointments": "My Appointments", "hospital": "Hospital", "date_time": "Date and Time", "reason": "Reason", "status": "Status", "closed_loop_referrals": "Closed-Loop Referrals", "required_service": "Required Service", "journey": "Journey", "test_name": "Test", "scheduled": "Scheduled", "find_medicine": "Find Medicine", "medicine_name": "Medicine Name", "check_availability": "Check Availability", "medicine_info": "Medicine Information", "medicine_info_desc": "Availability is informational. Confirm medicines with your treating professional.", "high_risk_followup": "High-Risk Follow-up", "request_emergency_help": "Request Emergency Help", "emergency_warning": "For an immediate life-threatening emergency, contact local emergency services first.", "condition_desc": "Condition / What Happened", "required_service_opt": "Required Service (Optional)", "send_emergency_request": "Send Emergency Request", "how_placement_works": "How Placement Works", "placement_info_desc": "MediRoute presents facilities using declared service, capacity, doctor availability and distance. A healthcare professional makes the final decision.", "treatment_feedback": "Treatment Feedback", "visit_id": "Visit ID", "treatment_received_q": "Was treatment received?", "yes": "Yes", "no": "No", "submit_feedback": "Submit Feedback", "hospital_rating": "Hospital Rating", "overall_rating": "Overall Rating", "optional_feedback": "Optional Feedback", "submit_rating": "Submit Rating", "submit_complaint": "Submit a Complaint", "hospital_id_opt": "Hospital ID (Optional)", "visit_id_opt": "Visit ID (Optional)", "category": "Category", "description": "Description", "severity": "Severity", "submit_neutral_review": "Submit for Neutral Review", "track_complaints": "Track Complaints", "submitted": "Submitted",
        "emergency_beds": "Emergency Beds", "emergency_capacity_label": "capacity", "available_beds": "Available Beds", "doctors_available": "Doctors Available", "update_facility_capacity": "Update Facility Capacity", "update_capacity_btn": "Update Capacity", "authorized_patient_visits": "Authorized Patient Visits", "patient": "Patient", "opened": "Opened", "live_queue": "Live Queue", "token": "Token", "patient_id": "Patient ID", "joined": "Joined", "medicine_inventory": "Medicine Inventory", "quantity": "Quantity", "low_stock_threshold": "Low Stock Threshold", "update_inventory_btn": "Update Inventory", "hospital_treatment_record": "Hospital Treatment Record", "symptoms_assessment": "Symptoms Assessment", "diagnosis": "Diagnosis", "treatment": "Treatment", "record_treatment_btn": "Record Treatment", "diagnostic_request": "Diagnostic Request", "schedule": "Schedule", "create_diagnostic_request": "Create Diagnostic Request", "diagnostic_coordination": "Diagnostic Coordination", "create_closed_loop_referral": "Create Closed-Loop Referral", "to_hospital_id_opt": "Receiving Hospital ID (Optional)", "clinical_summary": "Clinical Summary", "send_referral_btn": "Send Referral", "referral_monitoring_card": "Referral Monitoring", "emergency_coordination": "Emergency Coordination", "condition": "Condition", "complaint_review": "Complaint Review", "hospital_role_governed": "is governed by hospital role permissions.",
        "assigned_emergency_response": "Assigned Emergency Response", "live_gps_broadcast": "Live GPS Location Broadcast", "emergency_id": "Emergency ID", "latitude": "Latitude", "longitude": "Longitude", "speed_kmh": "Speed km/h", "broadcast_location_btn": "Broadcast Location to Hospital & Map",
        "patient_visits": "Patient Visits", "emergency_activity": "Emergency Activity", "open_complaints": "Open Complaints", "fleet_coordination_map": "Fleet Coordination & Global Map View", "privacy_policy": "Privacy Policy", "user_administration": "User Administration", "name": "Name", "role": "Role", "active": "Active", "quality_escalation_policy": "Quality Escalation Policy", "min_ratings": "Minimum Relevant Ratings", "low_rating_pct": "Low-Rating Percentage (%)", "complaint_count": "Open Complaint Count", "review_period": "Review Period (Days)", "save_policy_btn": "Save Policy", "audit_logs_card": "Audit Logs", "action": "Action", "resource": "Resource", "quality_review": "Quality Review", "recalculate_quality_btn": "Recalculate Transparent Indicators", "facility_id": "Facility ID", "indicator": "Indicator", "review": "Review", "referral_completion": "Referral Completion", "medicine_shortages": "Medicine Shortages", "pending_diagnostics": "Pending Diagnostics", "emergency_fleet_telemetry": "Emergency Fleet Network Live Telemetry", "beds": "Beds", "doctors": "Doctors", "generated": "Generated", "level": "Level",
        "live_map": "Live Emergency & Ambulance Coordination Map", "ambulance_map": "Ambulance View", "hospital_map": "Hospital View", "open_fullscreen": "Full Window", "submit": "Submit", "save": "Save", "no_records": "No records yet.", "profile_updated": "Profile updated successfully.", "appointment_requested": "Appointment requested successfully.", "inventory_updated": "Inventory updated successfully.", "treatment_saved": "Treatment record saved.", "diagnostic_created": "Diagnostic request created.", "referral_sent": "Closed-loop referral sent.", "location_relayed": "Live GPS location relayed.", "policy_saved": "Quality policy saved.", "feedback_submitted": "Feedback submitted for review.", "rating_saved": "Overall rating saved.", "complaint_submitted": "Complaint submitted for neutral review.", "capacity_updated": "Capacity updated.", "new_badge": "NEW"
    },
    "hi": {
        "app_name": "मेडीरूट", "tagline": "ग्रामीण स्वास्थ्य सेवा एवं आपातकालीन देखभाल", "tagline_desc": "संबद्ध स्वास्थ्य देखभाल, रेफरल एवं आपातकालीन समन्वय। चिकित्सीय निर्णय योग्य पेशेवरों द्वारा लिए जाते हैं।", "welcome": "स्वागत है", "sign_out": "साइन आउट", "sign_in": "सुरक्षित साइन इन करें", "loading": "सुरक्षित डेटा लोड हो रहा है...", "language_loaded": "भाषा बदली गई: हिन्दी", "email": "ईमेल", "password": "पासवर्ड", "demo_accounts": "⚡ त्वरित डेमो लॉगिन:", "demo_accounts_hint": "सभी खातों का पासवर्ड: MediRoute!2026", "quick_login": "त्वरित लॉगिन",
        "emergency": "आपातकाल", "appointments": "अपॉइंटमेंट", "records": "स्वास्थ्य रिकॉर्ड", "referrals": "रेफरल", "notifications": "सूचनाएं", "dashboard": "डैशबोर्ड", "consultations": "परामर्श", "diagnostics": "जांच / डायग्नोस्टिक्स", "medicines": "दवाइयां", "follow_up": "फॉलो-अप", "feedback": "प्रतिक्रिया", "ratings": "रेटिंग", "complaints": "शिकायतें", "profile": "प्रोफ़ाइल", "patients": "मरीज़", "queue": "कतार / टोकन", "treatment": "उपचार", "facility": "अस्पताल सुविधा", "emergency_assignment": "आपातकालीन कार्य", "patient_pickup": "मरीज़ पिकअप", "hospital_destination": "अस्पताल गंतव्य", "navigation": "नेविगेशन", "trip_status": "यात्रा स्थिति", "history": "इतिहास", "users": "उपयोगकर्ता", "hospitals": "अस्पताल", "escalations": "एस्केलेशन", "quality": "गुणवत्ता", "analytics": "एनालिटिक्स", "settings": "सेटिंग्स", "audit_logs": "ऑडिट लॉग", "facilities": "स्वास्थ्य सुविधाएं", "healthcare_analytics": "स्वास्थ्य एनालिटिक्स", "quality_indicators": "गुणवत्ता संकेतक", "referral_monitoring": "रेफरल निगरानी", "medicine_diagnostic_availability": "दवा व जांच उपलब्धता", "emergency_analytics": "आपातकालीन एनालिटिक्स",
        "patient_journey_desc": "आपकी स्वास्थ्य यात्रा अपॉइंटमेंट से लेकर फॉलो-अप तक जुड़ी हुई है। आपात स्थिति में स्क्रीन का उपयोग करें या आपातकालीन सेवा को कॉल करें।", "active_referrals": "सक्रिय रेफरल", "unread_notifications": "अपठित सूचनाएं", "upcoming_followup": "आगामी फॉलो-अप", "phone": "फ़ोन नंबर", "address": "पता", "allergies": "एलर्जी (अल्पविराम से अलग करें)", "save_profile": "प्रोफ़ाइल सहेजें", "digital_health_record": "डिजिटल स्वास्थ्य रिकॉर्ड", "type": "प्रकार", "recorded": "दर्ज किया गया", "details": "विवरण", "mode": "माध्यम", "clinical_note": "चिकित्सीय नोट", "date": "तारीख", "prescriptions": "दवा पर्ची", "instructions": "निर्देश", "book_appointment": "अपॉइंटमेंट बुक करें", "my_appointments": "मेरे अपॉइंटमेंट", "hospital": "अस्पताल", "date_time": "तारीख और समय", "reason": "कारण", "status": "स्थिति", "closed_loop_referrals": "क्लोज़्ड-लूप रेफरल", "required_service": "आवश्यक सेवा", "journey": "प्रगति यात्रा", "test_name": "जांच का नाम", "scheduled": "निर्धारित समय", "find_medicine": "दवा खोजें", "medicine_name": "दवा का नाम", "check_availability": "उपलब्धता जांचें", "medicine_info": "दवा जानकारी", "medicine_info_desc": "उपलब्धता केवल सूचनात्मक है। दवा लेने से पहले चिकित्सक से पुष्टि करें।", "high_risk_followup": "उच्च जोखिम फॉलो-अप", "request_emergency_help": "आपातकालीन सहायता का अनुरोध करें", "emergency_warning": "तत्काल जीवन-घातक आपात स्थिति के लिए, पहले स्थानीय आपातकालीन सेवाओं से संपर्क करें।", "condition_desc": "मरीज़ की स्थिति / क्या हुआ", "required_service_opt": "आवश्यक सेवा (वैकल्पिक)", "send_emergency_request": "आपातकालीन अनुरोध भेजें", "how_placement_works": "सुविधा आवंटन कैसे काम करता है", "placement_info_desc": "मेडीरूट घोषित सेवा, क्षमता, डॉक्टर उपलब्धता और दूरी के आधार पर अस्पताल प्रस्तुत करता है। अंतिम निर्णय स्वास्थ्य पेशेवर करते हैं।", "treatment_feedback": "उपचार प्रतिक्रिया", "visit_id": "विज़िट आईडी", "treatment_received_q": "क्या उपचार प्राप्त हुआ?", "yes": "हाँ", "no": "नहीं", "submit_feedback": "प्रतिक्रिया जमा करें", "hospital_rating": "अस्पताल रेटिंग", "overall_rating": "कुल रेटिंग", "optional_feedback": "वैकल्पिक टिप्पणी", "submit_rating": "रेटिंग जमा करें", "submit_complaint": "शिकायत दर्ज करें", "hospital_id_opt": "अस्पताल आईडी (वैकल्पिक)", "visit_id_opt": "विज़िट आईडी (वैकल्पिक)", "category": "श्रेणी", "description": "विवरण", "severity": "गंभीरता", "submit_neutral_review": "समीक्षा के लिए शिकायत भेजें", "track_complaints": "शिकायत की स्थिति देखें", "submitted": "जमा किया गया",
        "emergency_beds": "आपातकालीन बेड", "emergency_capacity_label": "क्षमता", "available_beds": "उपलब्ध बेड", "doctors_available": "उपलब्ध डॉक्टर", "update_facility_capacity": "सुविधा क्षमता अपडेट करें", "update_capacity_btn": "क्षमता सहेजें", "authorized_patient_visits": "अधिकृत मरीज़ विज़िट", "patient": "मरीज़", "opened": "दर्ज समय", "live_queue": "लाइव कतार", "token": "टोकन", "patient_id": "मरीज़ आईडी", "joined": "शामिल होने का समय", "medicine_inventory": "दवा इन्वेंट्री", "quantity": "मात्रा", "low_stock_threshold": "न्यूनतम स्टॉक सीमा", "update_inventory_btn": "इन्वेंट्री अपडेट करें", "hospital_treatment_record": "अस्पताल उपचार रिकॉर्ड", "symptoms_assessment": "लक्षण मूल्यांकन", "diagnosis": "निदान / डायग्नोसिस", "treatment": "उपचार विवरण", "record_treatment_btn": "उपचार रिकॉर्ड सहेजें", "diagnostic_request": "जांच अनुरोध", "schedule": "समय निर्धारण", "create_diagnostic_request": "जांच अनुरोध बनाएं", "diagnostic_coordination": "जांच समन्वय", "create_closed_loop_referral": "क्लोज़्ड-लूप रेफरल बनाएं", "to_hospital_id_opt": "प्राप्तकर्ता अस्पताल आईडी (वैकल्पिक)", "clinical_summary": "चिकित्सीय सारांश", "send_referral_btn": "रेफरल भेजें", "referral_monitoring_card": "रेफरल निगरानी", "emergency_coordination": "आपातकालीन समन्वय", "condition": "मरीज़ स्थिति", "complaint_review": "शिकायत समीक्षा", "hospital_role_governed": "अस्पताल भूमिका अनुमतियों द्वारा शासित है।",
        "assigned_emergency_response": "सौंपी गई आपातकालीन प्रतिक्रिया", "live_gps_broadcast": "लाइव जीपीएस लोकेशन प्रसारण", "emergency_id": "आपातकाल आईडी", "latitude": "अक्षांश (Latitude)", "longitude": "देशांतर (Longitude)", "speed_kmh": "गति (किमी/घंटा)", "broadcast_location_btn": "अस्पताल और मैप को लोकेशन भेजें",
        "patient_visits": "मरीज़ विज़िट", "emergency_activity": "आपातकालीन गतिविधियां", "open_complaints": "लंबित शिकायतें", "fleet_coordination_map": "फ्लीट समन्वय और समग्र मैप दृश्य", "privacy_policy": "गोपनीयता नीति", "user_administration": "उपयोगकर्ता प्रशासन", "name": "नाम", "role": "भूमिका", "active": "सक्रिय", "quality_escalation_policy": "गुणवत्ता एस्केलेशन नीति", "min_ratings": "न्यूनतम प्रासंगिक रेटिंग", "low_rating_pct": "कम रेटिंग प्रतिशत (%)", "complaint_count": "लंबित शिकायत गणना", "review_period": "समीक्षा अवधि (दिन)", "save_policy_btn": "नीति सहेजें", "audit_logs_card": "ऑडिट लॉग", "action": "कार्यवाही", "resource": "संसाधन", "quality_review": "गुणवत्ता समीक्षा", "recalculate_quality_btn": "पारदर्शी संकेतकों की पुनर्गणना करें", "facility_id": "सुविधा आईडी", "indicator": "संकेतक", "review": "समीक्षा", "referral_completion": "रेफरल पूर्णता दर", "medicine_shortages": "दवा की कमी", "pending_diagnostics": "लंबित जांच", "emergency_fleet_telemetry": "आपातकालीन फ्लीट लाइव टेलीमेट्री", "beds": "बेड", "doctors": "डॉक्टर", "generated": "निर्मित समय", "level": "स्तर",
        "live_map": "लाइव आपातकालीन एवं एम्बुलेंस समन्वय मैप", "ambulance_map": "एम्बुलेंस दृश्य", "hospital_map": "अस्पताल दृश्य", "open_fullscreen": "पूर्ण विंडो में खोलें", "submit": "जमा करें", "save": "सहेजें", "no_records": "कोई रिकॉर्ड उपलब्ध नहीं है।", "profile_updated": "प्रोफ़ाइल सफलतापूर्वक अपडेट की गई।", "appointment_requested": "अपॉइंटमेंट का अनुरोध सफलतापूर्वक भेजा गया।", "inventory_updated": "इन्वेंट्री सफलतापूर्वक अपडेट की गई।", "treatment_saved": "उपचार रिकॉर्ड सहेजा गया।", "diagnostic_created": "जांच अनुरोध बनाया गया।", "referral_sent": "क्लोज़्ड-लूप रेफरल भेजा गया।", "location_relayed": "लाइव लोकेशन सफलतापूर्वक प्रसारित की गई।", "policy_saved": "गुणवत्ता नीति सहेजी गई।", "feedback_submitted": "प्रतिक्रिया समीक्षा हेतु जमा की गई।", "rating_saved": "रेटिंग सहेजी गई।", "complaint_submitted": "शिकायत निष्पक्ष समीक्षा हेतु जमा की गई।", "capacity_updated": "क्षमता अपडेट की गई।", "new_badge": "नया"
    },
    "bn": {
        "app_name": "মেডিরুট", "tagline": "গ্রামীণ স্বাস্থ্যসেবা ও জরুরি পরিষেবা", "tagline_desc": "সংযুক্ত স্বাস্থ্যসেবা, রেফারাল এবং জরুরি সমন্বয়। চিকিৎসা সিদ্ধান্ত যোগ্য পেশাদারদের দ্বারা গৃহীত হয়।", "welcome": "স্বাগতম", "sign_out": "সাইন আউট", "sign_in": "নিরাপদে সাইন ইন করুন", "loading": "তথ্য লোড হচ্ছে...", "language_loaded": "ভাষা পরিবর্তিত: বাংলা", "email": "ইমেল", "password": "পাসওয়ার্ড", "demo_accounts": "⚡ ডেমো লগইন:", "demo_accounts_hint": "সকল অ্যাকাউন্টের পাসওয়ার্ড: MediRoute!2026", "quick_login": "দ্রুত লগইন",
        "emergency": "জরুরি", "appointments": "অ্যাপয়েন্টমেন্ট", "records": "স্বাস্থ্য নথি", "referrals": "রেফারাল", "notifications": "বিজ্ঞপ্তি", "dashboard": "ড্যাশবোর্ড", "consultations": "পরামর্শ", "diagnostics": "ডায়াগনস্টিক", "medicines": "ওষুধ", "follow_up": "ফলো-আপ", "feedback": "মতামত", "ratings": "রেটিং", "complaints": "অভিযোগ", "profile": "প্রোফাইল", "patients": "রোগী", "queue": "সারি / টোকেন", "treatment": "চিকিৎসা", "facility": "হাসপাতাল সুবিধা", "emergency_assignment": "জরুরি দায়িত্ব", "patient_pickup": "রোগী পিকআপ", "hospital_destination": "হাসপাতাল গন্তব্য", "navigation": "নেভিগেশন", "trip_status": "যাত্রার অবস্থা", "history": "ইতিহাস", "users": "ব্যবহারকারী", "hospitals": "হাসপাতাল", "escalations": "এসকেলেশন", "quality": "গুণমান", "analytics": "অ্যানালিটিক্স", "settings": "সেটিংস", "audit_logs": "অডিট লগ", "facilities": "স্বাস্থ্য সুবিধা", "healthcare_analytics": "স্বাস্থ্য অ্যানালিটিক্স", "quality_indicators": "গুণমান নির্দেশক", "referral_monitoring": "রেফারাল পর্যবেক্ষণ", "medicine_diagnostic_availability": "ওষুধ ও পরীক্ষা প্রাপ্যতা", "emergency_analytics": "জরুরি অ্যানালিটিক্স",
        "patient_journey_desc": "আপনার স্বাস্থ্যসেবা অ্যাপয়েন্টমেন্ট থেকে ফলো-আপ পর্যন্ত সংযুক্ত। জরুরি পরিস্থিতিতে স্ক্রিন ব্যবহার করুন বা স্থানীয় জরুরি সেবায় কল করুন।", "active_referrals": "সক্রিয় রেফারাল", "unread_notifications": "অপঠিত বিজ্ঞপ্তি", "upcoming_followup": "আসন্ন ফলো-আপ", "phone": "ফোন নম্বর", "address": "ঠিকানা", "allergies": "অ্যালার্জি (কমা দিয়ে আলাদা করুন)", "save_profile": "প্রোফাইল সংরক্ষণ করুন", "digital_health_record": "ডিজিটাল স্বাস্থ্য নথি", "type": "ধরন", "recorded": "নথিবদ্ধ", "details": "বিবরণ", "mode": "মাধ্যম", "clinical_note": "ক্লিনিক্যাল নোট", "date": "তারিখ", "prescriptions": "প্রেসক্রিপশন", "instructions": "নির্দেশনা", "book_appointment": "অ্যাপয়েন্টমেন্ট বুক করুন", "my_appointments": "আমার অ্যাপয়েন্টমেন্ট", "hospital": "হাসপাতাল", "date_time": "তারিখ ও সময়", "reason": "কারণ", "status": "অবস্থা", "closed_loop_referrals": "ক্লোজড-লুপ রেফারাল", "required_service": "প্রয়োজনীয় পরিষেবা", "journey": "অগ্রগতি", "test_name": "পরীক্ষার নাম", "scheduled": "নির্ধারিত", "find_medicine": "ওষুধ খুঁজুন", "medicine_name": "ওষুধের নাম", "check_availability": "প্রাপ্যতা পরীক্ষা করুন", "medicine_info": "ওষুধের তথ্য", "medicine_info_desc": "প্রাপ্যতা তথ্যভিত্তিক। ওষুধ গ্রহণের পূর্বে চিকিৎসকের পরামর্শ নিন।", "high_risk_followup": "উচ্চ ঝুঁকিপূর্ণ ফলো-আপ", "request_emergency_help": "জরুরি সহায়তার অনুরোধ করুন", "emergency_warning": "তাৎক্ষণিক জীবনহানির ঝুঁকিতে প্রথমে স্থানীয় জরুরি সেবায় যোগাযোগ করুন।", "condition_desc": "পরিস্থিতি / কী ঘটেছে", "required_service_opt": "প্রয়োজনীয় পরিষেবা (ঐচ্ছিক)", "send_emergency_request": "জরুরি অনুরোধ পাঠান", "how_placement_works": "বরাদ্দ প্রক্রিয়া কীভাবে কাজ করে", "placement_info_desc": "মেডিরুট ঘোষিত পরিষেবা, সক্ষমতা, ডাক্তারের উপস্থিতি এবং দূরত্বের ভিত্তিতে সুবিধা প্রদর্শন করে। চূড়ান্ত সিদ্ধান্ত চিকিৎসকের।", "treatment_feedback": "চিকিৎসার মতামত", "visit_id": "ভিজিট আইডি", "treatment_received_q": "চিকিৎসা কি পেয়েছেন?", "yes": "হ্যাঁ", "no": "না", "submit_feedback": "মতামত জমা দিন", "hospital_rating": "হাসপাতাল রেটিং", "overall_rating": "সার্বিক রেটিং", "optional_feedback": "ঐচ্ছিক মন্তব্য", "submit_rating": "রেটিং জমা দিন", "submit_complaint": "অভিযোগ জমা দিন", "hospital_id_opt": "হাসপাতাল আইডি (ঐচ্ছিক)", "visit_id_opt": "ভিজিট আইডি (ঐচ্ছিক)", "category": "বিভাগ", "description": "বিবরণ", "severity": "গুরুত্ব", "submit_neutral_review": "নিরপেক্ষ পর্যালোচনার জন্য জমা দিন", "track_complaints": "অভিযোগ ট্র্যাক করুন", "submitted": "জমা দেওয়া হয়েছে",
        "emergency_beds": "জরুরি বেড", "emergency_capacity_label": "ধারণক্ষমতা", "available_beds": "উপলব্ধ বেড", "doctors_available": "উপলব্ধ ডাক্তার", "update_facility_capacity": "সুবিধার সক্ষমতা আপডেট করুন", "update_capacity_btn": "সক্ষমতা সংরক্ষণ করুন", "authorized_patient_visits": "অনুমোদিত রোগী ভিজিট", "patient": "রোগী", "opened": "খোলা হয়েছে", "live_queue": "লাইভ সারি", "token": "টোকেন", "patient_id": "রোগী আইডি", "joined": "যুক্ত হয়েছে", "medicine_inventory": "ওষুধ ইনভেন্টরি", "quantity": "পরিমাণ", "low_stock_threshold": "কম স্টকের সীমা", "update_inventory_btn": "ইনভেন্টরি আপডেট করুন", "hospital_treatment_record": "হাসপাতাল চিকিৎসা রেকর্ড", "symptoms_assessment": "লক্ষণ মূল্যায়ন", "diagnosis": "রোগ নির্ণয়", "treatment": "চিকিৎসা বিবরণ", "record_treatment_btn": "চিকিৎসা রেকর্ড সংরক্ষণ করুন", "diagnostic_request": "ডায়াগনস্টিক অনুরোধ", "schedule": "সময়সূচী", "create_diagnostic_request": "ডায়াগনস্টিক অনুরোধ তৈরি করুন", "diagnostic_coordination": "ডায়াগনস্টিক সমন্বয়", "create_closed_loop_referral": "রেফারাল তৈরি করুন", "to_hospital_id_opt": "প্রাপক হাসপাতাল আইডি (ঐচ্ছিক)", "clinical_summary": "ক্লিনিক্যাল সারাংশ", "send_referral_btn": "রেফারাল পাঠান", "referral_monitoring_card": "রেফারাল পর্যবেক্ষণ", "emergency_coordination": "জরুরি সমন্বয়", "condition": "পরিস্থিতি", "complaint_review": "অভিযোগ পর্যালোচনা", "hospital_role_governed": "হাসপাতাল ভূমিকা অনুমতি দ্বারা নিয়ন্ত্রিত।",
        "assigned_emergency_response": "বরাদ্দকৃত জরুরি সেবা", "live_gps_broadcast": "লাইভ জিপিএস অবস্থান সম্প্রচার", "emergency_id": "জরুরি আইডি", "latitude": "অক্ষাংশ", "longitude": "দ্রাঘিমাংশ", "speed_kmh": "গতিবেগ (কিমি/ঘণ্টা)", "broadcast_location_btn": "হাসপাতাল ও ম্যাপে অবস্থান পাঠান",
        "patient_visits": "রোগী পরিদর্শন", "emergency_activity": "জরুরি কার্যক্রম", "open_complaints": "চলমান অভিযোগ", "fleet_coordination_map": "ফ্লিট সমন্বয় ও সার্বিক ম্যাপ", "privacy_policy": "গোপনীয়তা নীতি", "user_administration": "ব্যবহারকারী প্রশাসন", "name": "নাম", "role": "ভূমিকা", "active": "সক্রিয়", "quality_escalation_policy": "গুণমান এসকেলেশন নীতি", "min_ratings": "নূন্যতম প্রাসঙ্গিক রেটিং", "low_rating_pct": "কম রেটিং শতাংশ (%)", "complaint_count": "উন্মুক্ত অভিযোগ সংখ্যা", "review_period": "পর্যালোচনা সময়কাল (দিন)", "save_policy_btn": "নীতি সংরক্ষণ করুন", "audit_logs_card": "অডিট লগ", "action": "পদক্ষেপ", "resource": "উৎস", "quality_review": "গুণমান পর্যালোচনা", "recalculate_quality_btn": "সূচক পুনঃগণনা করুন", "facility_id": "সুবিধা আইডি", "indicator": "নির্দেশক", "review": "পর্যালোচনা", "referral_completion": "রেফারাল সমাপ্তির হার", "medicine_shortages": "ওষুধের ঘাটতি", "pending_diagnostics": "অপেক্ষমাণ পরীক্ষা", "emergency_fleet_telemetry": "জরুরি ফ্লিট লাইভ টেলিমেট্রি", "beds": "বেড", "doctors": "ডাক্তার", "generated": "তৈরি হয়েছে", "level": "স্তর",
        "live_map": "লাইভ জরুরি ও অ্যাম্বুলেন্স সমন্বয় ম্যাপ", "ambulance_map": "অ্যাম্বুলেন্স দৃশ্য", "hospital_map": "হাসপাতাল দৃশ্য", "open_fullscreen": "সম্পূর্ণ উইন্ডোতে খুলুন", "submit": "জমা দিন", "save": "সংরক্ষণ করুন", "no_records": "এখনও কোনো তথ্য নেই।", "profile_updated": "প্রোফাইল সফলভাবে সংরক্ষিত হয়েছে।", "appointment_requested": "অ্যাপয়েন্টমেন্ট সফলভাবে অনুরোধ করা হয়েছে।", "inventory_updated": "ইনভেন্টরি সফলভাবে আপডেট করা হয়েছে।", "treatment_saved": "চিকিৎসা রেকর্ড সংরক্ষিত হয়েছে।", "diagnostic_created": "ডায়াগনস্টিক অনুরোধ তৈরি হয়েছে।", "referral_sent": "রেফারাল পাঠানো হয়েছে।", "location_relayed": "লাইভ অবস্থান সম্প্রচারিত হয়েছে।", "policy_saved": "নীতি সংরক্ষিত হয়েছে।", "feedback_submitted": "মতামত জমা দেওয়া হয়েছে।", "rating_saved": "রেটিং সংরক্ষিত হয়েছে।", "complaint_submitted": "অভিযোগ জমা দেওয়া হয়েছে।", "capacity_updated": "সক্ষমতা আপডেট করা হয়েছে।", "new_badge": "নতুন"
    },
    "mr": {
        "app_name": "मेडीरूट", "tagline": "ग्रामीण आरोग्यसेवा आणि आणीबाणी काळजी", "tagline_desc": "जोडलेली आरोग्यसेवा, रेफरल आणि आणीबाणी समन्वय. वैद्यकीय निर्णय पात्र व्यावसायिकांद्वारे घेतले जातात.", "welcome": "स्वागत आहे", "sign_out": "साइन आउट", "sign_in": "सुरक्षित साइन इन करा", "loading": "डेटा लोड होत आहे...", "language_loaded": "भाषा बदलली: मराठी", "email": "ईमेल", "password": "पासवर्ड", "demo_accounts": "⚡ डेमो लॉगिन:", "demo_accounts_hint": "सर्व खात्यांसाठी पासवर्ड: MediRoute!2026", "quick_login": "जलद लॉगिन",
        "emergency": "आणीबाणी", "appointments": "अपॉइंटमेंट", "records": "आरोग्य नोंद", "referrals": "रेफरल", "notifications": "सूचना", "dashboard": "डॅशबोर्ड", "consultations": "सल्लामसलत", "diagnostics": "तपासणी", "medicines": "औषधे", "follow_up": "फॉलो-अप", "feedback": "अभिप्राय", "ratings": "रेटिंग", "complaints": "तक्रारी", "profile": "प्रोफाइल", "patients": "रुग्ण", "queue": "रांग / टोकन", "treatment": "उपचार", "facility": "रुग्णालय सुविधा", "emergency_assignment": "आणीबाणी कार्य", "patient_pickup": "रुग्ण पिकअप", "hospital_destination": "रुग्णालय गंतव्य", "navigation": "नेव्हिगेशन", "trip_status": "प्रवास स्थिती", "history": "इतिहास", "users": "वापरकर्ते", "hospitals": "रुग्णालये", "escalations": "एस्केलेशन्स", "quality": "गुणवत्ता", "analytics": "अ‍ॅनालिटिक्स", "settings": "सेटिंग्ज", "audit_logs": "ऑडिट नोंदी", "facilities": "आरोग्य सुविधा", "healthcare_analytics": "आरोग्य अ‍ॅनालिटिक्स", "quality_indicators": "गुणवत्ता निर्देशक", "referral_monitoring": "रेफरल देखरेख", "medicine_diagnostic_availability": "औषध व तपासणी उपलब्धता", "emergency_analytics": "आणीबाणी अ‍ॅनालिटिक्स",
        "patient_journey_desc": "तुमचा आरोग्य प्रवास अपॉइंटमेंटपासून फॉलो-अपपर्यंत जोडलेला आहे. आणीबाणीच्या वेळी स्क्रीन वापरा किंवा स्थानिक आणीबाणी सेवेला कॉल करा.", "active_referrals": "सक्रिय रेफरल्स", "unread_notifications": "न वाचलेल्या सूचना", "upcoming_followup": "आगामी फॉलो-अप", "phone": "फोन नंबर", "address": "पत्ता", "allergies": "अ‍ॅलर्जी (स्वल्पविरामाने वेगळे करा)", "save_profile": "प्रोफाइल जतन करा", "digital_health_record": "डिजिटल आरोग्य नोंद", "type": "प्रकार", "recorded": "नोंदवलेली तारीख", "details": "तपशील", "mode": "माध्यम", "clinical_note": "वैद्यकीय टीप", "date": "तारीख", "prescriptions": "औषधोपचार", "instructions": "सूचना", "book_appointment": "अपॉइंटमेंट बुक करा", "my_appointments": "माझ्या अपॉइंटमेंट्स", "hospital": "रुग्णालय", "date_time": "तारीख आणि वेळ", "reason": "कारण", "status": "स्थिती", "closed_loop_referrals": "क्लोज्ड-लूप रेफरल्स", "required_service": "आवश्यक सेवा", "journey": "प्रगती", "test_name": "तपासणीचे नाव", "scheduled": "नियोजित वेळ", "find_medicine": "औषध शोधा", "medicine_name": "औषधाचे नाव", "check_availability": "उपलब्धता तपासा", "medicine_info": "औषध माहिती", "medicine_info_desc": "उपलब्धता केवळ माहितीसाठी आहे. औषध घेण्यापूर्वी डॉक्टरांचा सल्ला घ्या.", "high_risk_followup": "उच्च जोखीम फॉलो-अप", "request_emergency_help": "आणीबाणी मदतीची विनंती करा", "emergency_warning": "त्वरित जीवघेण्या आणीबाणीसाठी, प्रथम स्थानिक आणीबाणी सेवांशी संपर्क साधा.", "condition_desc": "रुग्णाची स्थिती / काय घडले", "required_service_opt": "आवश्यक सेवा (पर्यायी)", "send_emergency_request": "आणीबाणी विनंती पाठवा", "how_placement_works": "सुविधा वाटप कसे कार्य करते", "placement_info_desc": "मेडीरूट घोषित सेवा, क्षमता, डॉक्टरांची उपलब्धता आणि अंतर यावर आधारित रुग्णालये दाखवते. अंतिम निर्णय आरोग्य व्यावसायिक घेतात.", "treatment_feedback": "उपचार अभिप्राय", "visit_id": "भेट आयडी", "treatment_received_q": "उपचार मिळाले का?", "yes": "होय", "no": "नाही", "submit_feedback": "अभिप्राय सबमिट करा", "hospital_rating": "रुग्णालय रेटिंग", "overall_rating": "एकूण रेटिंग", "optional_feedback": "पर्यायी प्रतिक्रिया", "submit_rating": "रेटिंग सबमिट करा", "submit_complaint": "तक्रार दाखल करा", "hospital_id_opt": "रुग्णालय आयडी (पर्यायी)", "visit_id_opt": "भेट आयडी (पर्यायी)", "category": "वर्ग", "description": "वर्णन", "severity": "तीव्रता", "submit_neutral_review": "पुनरावलोकनासाठी सबमिट करा", "track_complaints": "तक्रारींचा मागोवा घ्या", "submitted": "सबमिट केले",
        "emergency_beds": "आणीबाणी खाटा", "emergency_capacity_label": "क्षमता", "available_beds": "उपलब्ध खाटा", "doctors_available": "उपलब्ध डॉक्टर", "update_facility_capacity": "सुविधा क्षमता अद्ययावत करा", "update_capacity_btn": "क्षमता जतन करा", "authorized_patient_visits": "अधिकृत रुग्ण भेटी", "patient": "रुग्ण", "opened": "उघडले", "live_queue": "थेट रांग", "token": "टोकन", "patient_id": "रुग्ण आयडी", "joined": "सामील झाले", "medicine_inventory": "औषध साठा", "quantity": "प्रमाण", "low_stock_threshold": "कमी साठा मर्यादा", "update_inventory_btn": "साठा अद्ययावत करा", "hospital_treatment_record": "रुग्णालय उपचार नोंद", "symptoms_assessment": "लक्षणे मूल्यांकन", "diagnosis": "रोगनिदान", "treatment": "उपचार", "record_treatment_btn": "उपचार नोंद जतन करा", "diagnostic_request": "तपासणी विनंती", "schedule": "नियोजन", "create_diagnostic_request": "तपासणी विनंती तयार करा", "diagnostic_coordination": "तपासणी समन्वय", "create_closed_loop_referral": "रेफरल तयार करा", "to_hospital_id_opt": "स्वीकारणारे रुग्णालय आयडी (पर्यायी)", "clinical_summary": "वैद्यकीय सारांश", "send_referral_btn": "रेफरल पाठवा", "referral_monitoring_card": "रेफरल देखरेख", "emergency_coordination": "आणीबाणी समन्वय", "condition": "स्थिती", "complaint_review": "तक्रार पुनरावलोकन", "hospital_role_governed": "रुग्णालय परवानग्यांद्वारे नियंत्रित आहे.",
        "assigned_emergency_response": "नियुक्त आणीबाणी प्रतिसाद", "live_gps_broadcast": "थेट जीपीएस स्थान प्रसारण", "emergency_id": "आणीबाणी आयडी", "latitude": "अक्षांश", "longitude": "रेखांश", "speed_kmh": "गती (किमी/तास)", "broadcast_location_btn": "रुग्णालय आणि नकाशाला स्थान पाठवा",
        "patient_visits": "रुग्ण भेटी", "emergency_activity": "आणीबाणी हालचाली", "open_complaints": "प्रलंबित तक्रारी", "fleet_coordination_map": "फ्लीट समन्वय व थेट नकाशा", "privacy_policy": "गोपनीयता धोरण", "user_administration": "वापरकर्ता प्रशासन", "name": "नाव", "role": "भूमिका", "active": "सक्रिय", "quality_escalation_policy": "गुणवत्ता एस्केलेशन धोरण", "min_ratings": "किमान आवश्यक रेटिंग्ज", "low_rating_pct": "कमी रेटिंग टक्केवारी (%)", "complaint_count": "प्रलंबित तक्रार संख्या", "review_period": "पुनरावलोकन कालावधी (दिवस)", "save_policy_btn": "धोरण जतन करा", "audit_logs_card": "ऑडिट नोंदी", "action": "कृती", "resource": "संसाधन", "quality_review": "गुणवत्ता पुनरावलोकन", "recalculate_quality_btn": "निर्देशकांची पुनर्गणना करा", "facility_id": "सुविधा आयडी", "indicator": "निर्देशक", "review": "पुनरावलोकन", "referral_completion": "रेफरल पूर्णता दर", "medicine_shortages": "औषधांची टंचाई", "pending_diagnostics": "प्रलंबित तपासण्या", "emergency_fleet_telemetry": "आणीबाणी फ्लीट थेट टेलिमेट्री", "beds": "खाटा", "doctors": "डॉक्टर", "generated": "तयार झाले", "level": "पातळी",
        "live_map": "थेट आणीबाणी व रुग्णवाहिका समन्वय नकाशा", "ambulance_map": "रुग्णवाहिका दृश्य", "hospital_map": "रुग्णालय दृश्य", "open_fullscreen": "पूर्ण विंडोमध्ये उघडा", "submit": "सबमिट करा", "save": "जतन करा", "no_records": "अद्याप कोणतीही नोंद नाही.", "profile_updated": "प्रोफाइल यशस्वीरित्या जतन केली.", "appointment_requested": "अपॉइंटमेंट विनंती पाठवली गेली.", "inventory_updated": "साठा अद्ययावत केला गेला.", "treatment_saved": "उपचार नोंद जतन केली.", "diagnostic_created": "तपासणी विनंती तयार केली गेली.", "referral_sent": "रेफरल पाठवले गेले.", "location_relayed": "थेट स्थान प्रसारित केले गेले.", "policy_saved": "धोरण जतन केले.", "feedback_submitted": "अभिप्राय सबमिट केला.", "rating_saved": "रेटिंग जतन केले.", "complaint_submitted": "तक्रार दाखल केली.", "capacity_updated": "क्षमता अद्ययावत केली.", "new_badge": "नवीन"
    },
    "ta": {
        "app_name": "மெடிரூட்", "tagline": "கிராமப்புற சுகாதார மற்றும் அவசர சிகிச்சை", "tagline_desc": "இணைக்கப்பட்ட சுகாதாரம், பரிந்துரைகள் மற்றும் அவசர சிகிச்சை ஒருங்கிணைப்பு. மருத்துவ முடிவுகள் தகுதிவாய்ந்த நிபுணர்களால் எடுக்கப்படுகின்றன.", "welcome": "வரவேற்கிறோம்", "sign_out": "வெளியேறு", "sign_in": "பாதுகாப்பாக உள்நுழைக", "loading": "தரவு ஏற்றப்படுகிறது...", "language_loaded": "மொழி மாற்றப்பட்டது: தமிழ்", "email": "மின்னஞ்சல்", "password": "கடவுச்சொல்", "demo_accounts": "⚡ மாதிரி உள்நுழைவுகள்:", "demo_accounts_hint": "அனைத்து கணக்குகளின் கடவுச்சொல்: MediRoute!2026", "quick_login": "விரைவு உள்நுழைவு",
        "emergency": "அவசரம்", "appointments": "சந்திப்புகள்", "records": "சுகாதார பதிவு", "referrals": "பரிந்துரைகள்", "notifications": "அறிவிப்புகள்", "dashboard": "முகப்பு பலகை", "consultations": "ஆலோசனைகள்", "diagnostics": "பரிசோதனைகள்", "medicines": "மருந்துகள்", "follow_up": "தொடர் சிகிச்சை", "feedback": "கருத்துக்கள்", "ratings": "மதிப்பீடுகள்", "complaints": "புகார்கள்", "profile": "சுயவிவரம்", "patients": "நோயாளிகள்", "queue": "வரிசை / டோக்கன்", "treatment": "சிகிச்சை", "facility": "மருத்துவமனை வசதி", "emergency_assignment": "அவசர பணி", "patient_pickup": "நோயாளி பிக்கப்", "hospital_destination": "மருத்துவமனை சேருமிடம்", "navigation": "வழிகாட்டல்", "trip_status": "பயண நிலை", "history": "வரலாறு", "users": "பயனர்கள்", "hospitals": "மருத்துவமனைகள்", "escalations": "மேல்முறையீடுகள்", "quality": "தரம்", "analytics": "பகுப்பாய்வு", "settings": "அமைப்புகள்", "audit_logs": "தணிக்கை பதிவுகள்", "facilities": "சுகாதார வசதிகள்", "healthcare_analytics": "சுகாதார பகுப்பாய்வு", "quality_indicators": "தர குறிகாட்டிகள்", "referral_monitoring": "பரிந்துரை கண்காணிப்பு", "medicine_diagnostic_availability": "மருந்து & பரிசோதனை கிடைக்கும் நிலை", "emergency_analytics": "அவசர பகுப்பாய்வு",
        "patient_journey_desc": "உங்கள் சுகாதார பயணம் சந்திப்புகள் முதல் தொடர் சிகிச்சை வரை இணைக்கப்பட்டுள்ளது. அவசர காலங்களில் திரையைப் பயன்படுத்தவும் அல்லது அவசர சேவையை அழைக்கவும்.", "active_referrals": "செயலில் உள்ள பரிந்துரைகள்", "unread_notifications": "படிக்காத அறிவிப்புகள்", "upcoming_followup": "வரவிருக்கும் தொடர் சிகிச்சை", "phone": "தொலைபேசி எண்", "address": "முகவரி", "allergies": "ஒவ்வாமைகள் (கமாவால் பிரிக்கவும்)", "save_profile": "சுயவிவரத்தை சேமிக்கவும்", "digital_health_record": "டிஜிட்டல் சுகாதார பதிவு", "type": "வகை", "recorded": "பதிவு செய்யப்பட்ட தேதி", "details": "விவரங்கள்", "mode": "முறை", "clinical_note": "மருத்துவ குறிப்பு", "date": "தேதி", "prescriptions": "மருந்துச் சீட்டு", "instructions": "அறிவுறுத்தல்கள்", "book_appointment": "சந்திப்பை முன்பதிவு செய்க", "my_appointments": "எனது சந்திப்புகள்", "hospital": "மருத்துவமனை", "date_time": "தேதி மற்றும் நேரம்", "reason": "காரணம்", "status": "நிலை", "closed_loop_referrals": "மூடிய-சுழற்சி பரிந்துரைகள்", "required_service": "தேவையான சேவை", "journey": "முன்னேற்றம்", "test_name": "பரிசோதனை பெயர்", "scheduled": "திட்டமிடப்பட்டது", "find_medicine": "மருந்து தேடுக", "medicine_name": "மருந்தின் பெயர்", "check_availability": "கிடைக்கும் நிலையை சரிபார்க்கவும்", "medicine_info": "மருந்து தகவல்", "medicine_info_desc": "கிடைக்கும் நிலை தகவலுக்கானது மட்டுமே. மருந்து உட்கொள்ளும் முன் மருத்துவரிடம் உறுதிப்படுத்தவும்.", "high_risk_followup": "அதிக ஆபத்து தொடர் சிகிச்சை", "request_emergency_help": "அவசர உதவி கோரிக்கை", "emergency_warning": "உடனடி உயிருக்கு ஆபத்தான அவசரநிலைக்கு, முதலில் உள்ளூர் அவசர சேவைகளைத் தொடர்பு கொள்ளவும்.", "condition_desc": "நோயாளியின் நிலை / என்ன நடந்தது", "required_service_opt": "தேவையான சேவை (விருப்பத்தேர்வு)", "send_emergency_request": "அவசர கோரிக்கையை அனுப்புக", "how_placement_works": "வசதி ஒதுக்கீடு எவ்வாறு செயல்படுகிறது", "placement_info_desc": "அறிவிக்கப்பட்ட சேவை, திறன், மருத்துவர் இருப்பு மற்றும் தூரத்தின் அடிப்படையில் மெடிரூட் மருத்துவமனைகளைக் காட்டுகிறது. இறுதி முடிவு மருத்துவரால் எடுக்கப்படுகிறது.", "treatment_feedback": "சிகிச்சை கருத்து", "visit_id": "வருகை ஐடி", "treatment_received_q": "சிகிச்சை கிடைத்ததா?", "yes": "ஆம்", "no": "இல்லை", "submit_feedback": "கருத்தை சமர்ப்பிக்கவும்", "hospital_rating": "மருத்துவமனை மதிப்பீடு", "overall_rating": "ஒட்டுமொத்த மதிப்பீடு", "optional_feedback": "விருப்ப கருத்து", "submit_rating": "மதிப்பீட்டை சமர்ப்பிக்கவும்", "submit_complaint": "புகார் அளிக்கவும்", "hospital_id_opt": "மருத்துவமனை ஐடி (விருப்பத்தேர்வு)", "visit_id_opt": "வருகை ஐடி (விருப்பத்தேர்வு)", "category": "வகை", "description": "விளக்கம்", "severity": "தீவிரம்", "submit_neutral_review": "நடுநிலையான மதிப்பாய்வுக்கு சமர்ப்பிக்கவும்", "track_complaints": "புகார்களை கண்காணிக்கவும்", "submitted": "சமர்ப்பிக்கப்பட்டது",
        "emergency_beds": "அவசர படுக்கைகள்", "emergency_capacity_label": "கொள்ளளவு", "available_beds": "கிடைக்கும் படுக்கைகள்", "doctors_available": "கிடைக்கும் மருத்துவர்கள்", "update_facility_capacity": "வசதி திறனை புதுப்பிக்கவும்", "update_capacity_btn": "திறனை சேமிக்கவும்", "authorized_patient_visits": "அங்கீகரிக்கப்பட்ட நோயாளி வருகைகள்", "patient": "நோயாளி", "opened": "திறக்கப்பட்டது", "live_queue": "நேரலை வரிசை", "token": "டோக்கன்", "patient_id": "நோயாளி ஐடி", "joined": "சேர்ந்த நேரம்", "medicine_inventory": "மருந்து இருப்பு", "quantity": "அளவு", "low_stock_threshold": "குறைந்த இருப்பு வரம்பு", "update_inventory_btn": "இருப்பை புதுப்பிக்கவும்", "hospital_treatment_record": "மருத்துவமனை சிகிச்சை பதிவு", "symptoms_assessment": "அறிகுறிகள் மதிப்பீடு", "diagnosis": "நோய் கண்டறிதல்", "treatment": "சிகிச்சை விவரம்", "record_treatment_btn": "சிகிச்சைப் பதிவை சேமிக்கவும்", "diagnostic_request": "பரிசோதனை கோரிக்கை", "schedule": "திட்டமிடல்", "create_diagnostic_request": "பரிசோதனை கோரிக்கையை உருவாக்கவும்", "diagnostic_coordination": "பரிசோதனை ஒருங்கிணைப்பு", "create_closed_loop_referral": "பரிந்துரையை உருவாக்கவும்", "to_hospital_id_opt": "பெறும் மருத்துவமனை ஐடி (விருப்பத்தேர்வு)", "clinical_summary": "மருத்துவ சுருக்கம்", "send_referral_btn": "பரிந்துரையை அனுப்புக", "referral_monitoring_card": "பரிந்துரை கண்காணிப்பு", "emergency_coordination": "அவசர ஒருங்கிணைப்பு", "condition": "நிலை", "complaint_review": "புகார் மறுஆய்வு", "hospital_role_governed": "மருத்துவமனை அனுமதிகளால் நிர்வகிக்கப்படுகிறது.",
        "assigned_emergency_response": "ஒதுக்கப்பட்ட அவசர பணி", "live_gps_broadcast": "நேரலை ஜிபிஎஸ் இருப்பிட ஒளிபரப்பு", "emergency_id": "அவசர ஐடி", "latitude": "அட்சரேகை", "longitude": "தீர்க்கரேகை", "speed_kmh": "வேகம் (கி.மீ/மணி)", "broadcast_location_btn": "மருத்துவமனை மற்றும் வரைபடத்திற்கு இருப்பிடத்தை அனுப்புக",
        "patient_visits": "நோயாளி வருகைகள்", "emergency_activity": "அவசர நடவடிக்கைகள்", "open_complaints": "நிலுவையில் உள்ள புகார்கள்", "fleet_coordination_map": "வாகன ஒருங்கிணைப்பு மற்றும் நேரலை வரைபடம்", "privacy_policy": "தனியுரிமைக் கொள்கை", "user_administration": "பயனர் நிர்வாகம்", "name": "பெயர்", "role": "பங்கு", "active": "செயலில்", "quality_escalation_policy": "தர மேல்முறையீட்டுக் கொள்கை", "min_ratings": "குறைந்தபட்ச மதிப்பீடுகள்", "low_rating_pct": "குறைந்த மதிப்பீடு சதவீதம் (%)", "complaint_count": "திறந்த புகார்களின் எண்ணிக்கை", "review_period": "மறுஆய்வு காலம் (நாட்கள்)", "save_policy_btn": "கொள்கையை சேமிக்கவும்", "audit_logs_card": "தணிக்கை பதிவுகள்", "action": "நடவடிக்கை", "resource": "வளம்", "quality_review": "தர மறுஆய்வு", "recalculate_quality_btn": "குறிகாட்டிகளை மீண்டும் கணக்கிடுக", "facility_id": "வசதி ஐடி", "indicator": "குறிகாட்டி", "review": "மறுஆய்வு", "referral_completion": "பரிந்துரை நிறைவு விகிதம்", "medicine_shortages": "மருந்து பற்றாக்குறை", "pending_diagnostics": "நிலுவையில் உள்ள சோதனைகள்", "emergency_fleet_telemetry": "அவசர வாகன நேரலை தொலைநிலை அளவீடு", "beds": "படுக்கைகள்", "doctors": "மருத்துவர்கள்", "generated": "உருவாக்கப்பட்டது", "level": "நிலை",
        "live_map": "நேரலை அவசர மற்றும் ஆம்புலன்ஸ் ஒருங்கிணைப்பு வரைபடம்", "ambulance_map": "ஆம்புலன்ஸ் பார்வை", "hospital_map": "மருத்துவமனை பார்வை", "open_fullscreen": "முழு திரையில் திறக்கவும்", "submit": "சமர்ப்பிக்கவும்", "save": "சேமிக்கவும்", "no_records": "பதிவுகள் எதுவும் இல்லை.", "profile_updated": "சுயவிவரம் வெற்றிகரமாக சேமிக்கப்பட்டது.", "appointment_requested": "சந்திப்பு கோரிக்கை அனுப்பப்பட்டது.", "inventory_updated": "இருப்பு வெற்றிகரமாக புதுப்பிக்கப்பட்டது.", "treatment_saved": "சிகிச்சை பதிவு சேமிக்கப்பட்டது.", "diagnostic_created": "பரிசோதனை கோரிக்கை உருவாக்கப்பட்டது.", "referral_sent": "பரிந்துரை அனுப்பப்பட்டது.", "location_relayed": "நேரலை இருப்பிடம் ஒளிபரப்பப்பட்டது.", "policy_saved": "கொள்கை சேமிக்கப்பட்டது.", "feedback_submitted": "கருத்து சமர்ப்பிக்கப்பட்டது.", "rating_saved": "மதிப்பீடு சேமிக்கப்பட்டது.", "complaint_submitted": "புகார் சமர்ப்பிக்கப்பட்டது.", "capacity_updated": "திறன் புதுப்பிக்கப்பட்டது.", "new_badge": "புதியது"
    },
    "te": {
        "app_name": "మెడిరూట్", "tagline": "గ్రామీణ ఆరోగ్య సంరక్షణ మరియు అత్యవసర సేవలు", "tagline_desc": "అనుసంధానించబడిన ఆరోగ్య సంరక్షణ, రిఫరల్స్ మరియు అత్యవసర సమన్వయం. వైద్యపరమైన నిర్ణయాలు అర్హత కలిగిన నిపుణులచే తీసుకోబడతాయి.", "welcome": "స్వాగతం", "sign_out": "సైన్ అవుట్", "sign_in": "సురక్షితంగా లాగిన్ అవ్వండి", "loading": "డేటా లోడ్ అవుతోంది...", "language_loaded": "భాష మార్చబడింది: తెలుగు", "email": "ఈమెయిల్", "password": "పాస్‌వర్డ్", "demo_accounts": "⚡ డెమో లాగిన్‌లు:", "demo_accounts_hint": "అన్ని ఖాతాల పాస్‌వర్డ్: MediRoute!2026", "quick_login": "త్వరిత లాగిన్",
        "emergency": "అత్యవసరం", "appointments": "అపాయింట్‌మెంట్‌లు", "records": "ఆరోగ్య రికార్డు", "referrals": "రిఫరల్స్", "notifications": "నోటిఫికేషన్లు", "dashboard": "డాష్‌బోర్డ్", "consultations": "సంప్రదింపులు", "diagnostics": "రోగనిర్ధారణ", "medicines": "మందులు", "follow_up": "ఫాలో-అప్", "feedback": "అభిప్రాయం", "ratings": "రేటింగ్‌లు", "complaints": "ఫిర్యాదులు", "profile": "ప్రొఫైల్", "patients": "రోగులు", "queue": "క్యూ / టోకెన్", "treatment": "చికిత్స", "facility": "ఆసుపత్రి సౌకర్యం", "emergency_assignment": "అత్యవసర బాధ్యత", "patient_pickup": "రోగి పికప్", "hospital_destination": "ఆసుపత్రి గమ్యస్థానం", "navigation": "నావిగేషన్", "trip_status": "ట్రిప్ స్థితి", "history": "చరిత్ర", "users": "వినియోగదారులు", "hospitals": "ఆసుపత్రులు", "escalations": "ఎస్పలేషన్లు", "quality": "నాణ్యత", "analytics": "విశ్లేషణలు", "settings": "సెట్టింగ్‌లు", "audit_logs": "ఆడిట్ లాగ్‌లు", "facilities": "ఆరోగ్య కేంద్రాలు", "healthcare_analytics": "ఆరోగ్య విశ్లేషణలు", "quality_indicators": "నాణ్యత సూచికలు", "referral_monitoring": "రిఫరల్ పర్యవేక్షణ", "medicine_diagnostic_availability": "మందులు & పరీక్షల లభ్యత", "emergency_analytics": "అత్యవసర విశ్లేషణలు",
        "patient_journey_desc": "మీ ఆరోగ్య ప్రయాణం అపాయింట్‌మెంట్‌ల నుండి ఫాలో-అప్ వరకు అనుసంధానించబడి ఉంది. అత్యవసర సమయాల్లో స్క్రీన్‌ను ఉపయోగించండి లేదా స్థానిక అత్యవసర సేవలకు కాల్ చేయండి.", "active_referrals": "క్రియాశీల రిఫరల్స్", "unread_notifications": "చదవని నోటిఫికేషన్లు", "upcoming_followup": "రాబోయే ఫాలో-అప్", "phone": "ఫోన్ నంబర్", "address": "చిరునామా", "allergies": "అలర్జీలు (కామాలతో వేరు చేయండి)", "save_profile": "ప్రొఫైల్‌ను భద్రపరచండి", "digital_health_record": "డిజిటల్ ఆరోగ్య రికార్డు", "type": "రకం", "recorded": "నమోదైన తేదీ", "details": "వివరాలు", "mode": "మాధ్యమం", "clinical_note": "వైద్య గమనిక", "date": "తేదీ", "prescriptions": "మందుల చీటీ", "instructions": "సూచనలు", "book_appointment": "అపాయింట్‌మెంట్ బుక్ చేయండి", "my_appointments": "నా అపాయింట్‌మెంట్‌లు", "hospital": "ఆసుపత్రి", "date_time": "తేదీ మరియు సమయం", "reason": "కారణం", "status": "స్థితి", "closed_loop_referrals": "క్లోజ్డ్-లూప్ రిఫరల్స్", "required_service": "అవసరమైన సేవ", "journey": "పురోగతి", "test_name": "పరీక్ష పేరు", "scheduled": "షెడ్యూల్ చేయబడింది", "find_medicine": "మందులను శోధించండి", "medicine_name": "మందు పేరు", "check_availability": "లభ్యతను తనిఖీ చేయండి", "medicine_info": "మందుల సమాచారం", "medicine_info_desc": "లభ్యత సమాచారం కొరకు మాత్రమే. మందు తీసుకునే ముందు వైద్యుడిని సంప్రదించండి.", "high_risk_followup": "అధిక రిస్క్ ఫాలో-అప్", "request_emergency_help": "అత్యవసర సహాయం అభ్యర్థించండి", "emergency_warning": "తక్షణ ప్రాణాపాయ అత్యవసర పరిస్థితులకు ముందుగా స్థానిక అత్యవసర సేవలను సంప్రదించండి.", "condition_desc": "రోగి పరిస్థితి / ఏమి జరిగింది", "required_service_opt": "అవసరమైన సేవ (ఐచ్ఛికం)", "send_emergency_request": "అత్యవసర అభ్యర్థనను పంపండి", "how_placement_works": "సౌకర్య కేటాయింపు ఎలా పనిచేస్తుంది", "placement_info_desc": "ప్రకటించిన సేవ, సామర్థ్యం, డాక్టర్ లభ్యత మరియు దూరం ఆధారంగా మెడిరూట్ ఆసుపత్రులను చూపిస్తుంది. తుది నిర్ణయం వైద్య నిపుణులదే.", "treatment_feedback": "చికిత్సపై అభిప్రాయం", "visit_id": "సందర్శన ఐడి", "treatment_received_q": "చికిత్స అందిందా?", "yes": "అవును", "no": "కాదు", "submit_feedback": "అభిప్రాయాన్ని సమర్పించండి", "hospital_rating": "ఆసుపత్రి రేటింగ్", "overall_rating": "మొత్తం రేటింగ్", "optional_feedback": "ఐచ్ఛిక అభిప్రాయం", "submit_rating": "రేటింగ్‌ను సమర్పించండి", "submit_complaint": "ఫిర్యాదును దాఖలు చేయండి", "hospital_id_opt": "ఆసుపత్రి ఐడి (ఐచ్ఛికం)", "visit_id_opt": "సందర్శన ఐడి (ఐచ్ఛికం)", "category": "వర్గం", "description": "వివరణ", "severity": "తీవ్రత", "submit_neutral_review": "సమీక్ష కోసం సమర్పించండి", "track_complaints": "ఫిర్యాదులను ట్రాక్ చేయండి", "submitted": "సమర్పించబడింది",
        "emergency_beds": "అత్యవసర పడకలు", "emergency_capacity_label": "సామర్థ్యం", "available_beds": "అందుబాటులో ఉన్న పడకలు", "doctors_available": "అందుబాటులో ఉన్న వైద్యులు", "update_facility_capacity": "సౌకర్య సామర్థ్యాన్ని నవీకరించండి", "update_capacity_btn": "సామర్థ్యాన్ని భద్రపరచండి", "authorized_patient_visits": "అధికారిక రోగి సందర్శనలు", "patient": "రోగి", "opened": "ప్రారంభించబడింది", "live_queue": "లైవ్ క్యూ", "token": "టోకెన్", "patient_id": "రోగి ఐడి", "joined": "చేరిన సమయం", "medicine_inventory": "మందుల నిల్వ", "quantity": "పరిమాణం", "low_stock_threshold": "తక్కువ నిల్వ పరిమితి", "update_inventory_btn": "నిల్వను నవీకరించండి", "hospital_treatment_record": "ఆసుపత్రి చికిత్స రికార్డు", "symptoms_assessment": "లక్షణాల అంచనా", "diagnosis": "రోగ నిర్ధారణ", "treatment": "చికిత్స వివరణ", "record_treatment_btn": "చికిత్స రికార్డును భద్రపరచండి", "diagnostic_request": "పరీక్ష అభ్యర్థన", "schedule": "షెడ్యూల్", "create_diagnostic_request": "పరీక్ష అభ్యర్థనను సృష్టించండి", "diagnostic_coordination": "పరీక్ష సమన్వయం", "create_closed_loop_referral": "రిఫరల్ సృష్టించండి", "to_hospital_id_opt": "స్వీకరించే ఆసుపత్రి ఐడి (ఐచ్ఛికం)", "clinical_summary": "వైద్య సారాంశం", "send_referral_btn": "రిఫరల్ పంపండి", "referral_monitoring_card": "రిఫరల్ పర్యవేక్షణ", "emergency_coordination": "అత్యవసర సమన్వయం", "condition": "పరిస్థితి", "complaint_review": "ఫిర్యాదు సమీక్ష", "hospital_role_governed": "ఆసుపత్రి అనుమతుల ద్వారా నిర్వహించబడుతుంది.",
        "assigned_emergency_response": "కేటాయించిన అత్యవసర ప్రతిస్పందన", "live_gps_broadcast": "లైవ్ జీపీఎస్ లొకేషన్ ప్రసారం", "emergency_id": "అత్యవసర ఐడి", "latitude": "అక్షాంశం", "longitude": "రేఖాంశం", "speed_kmh": "వేగం (కి.మీ/గం)", "broadcast_location_btn": "ఆసుపత్రి మరియు మ్యాప్‌కు లొకేషన్ పంపండి",
        "patient_visits": "రోగి సందర్శనలు", "emergency_activity": "అత్యవసర కార్యకలాపాలు", "open_complaints": "పరిష్కారం కాని ఫిర్యాదులు", "fleet_coordination_map": "ఫ్లీట్ సమన్వయం మరియు లైవ్ మ్యాప్", "privacy_policy": "గోప్యతా విధానం", "user_administration": "వినియోగదారుల నిర్వహణ", "name": "పేరు", "role": "పాత్ర", "active": "క్రియాశీలం", "quality_escalation_policy": "నాణ్యత ఎస్కలేషన్ విధానం", "min_ratings": "కనిష్ట సంబంధిత రేటింగ్‌లు", "low_rating_pct": "తక్కువ రేటింగ్ శాతం (%)", "complaint_count": "ఓపెన్ ఫిర్యాదుల సంఖ్య", "review_period": "సమీక్ష కాలం (రోజులు)", "save_policy_btn": "విధానాన్ని భద్రపరచండి", "audit_logs_card": "ఆడిట్ లాగ్‌లు", "action": "చర్య", "resource": "వనరు", "quality_review": "నాణ్యత సమీక్ష", "recalculate_quality_btn": "సూచికలను తిరిగి లెక్కించండి", "facility_id": "సౌకర్యం ఐడి", "indicator": "సూచిక", "review": "సమీక్ష", "referral_completion": "రిఫరల్ పూర్తి రేటు", "medicine_shortages": "మందుల కొరత", "pending_diagnostics": "పెండింగ్‌లో ఉన్న పరీక్షలు", "emergency_fleet_telemetry": "అత్యవసర వాహనాల లైవ్ టెలిమెట్రీ", "beds": "పడకలు", "doctors": "వైద్యులు", "generated": "రూపొందించబడింది", "level": "స్థాయి",
        "live_map": "లైవ్ అత్యవసర మరియు అంబులెన్స్ సమన్వయ మ్యాప్", "ambulance_map": "అంబులెన్స్ దృశ్యం", "hospital_map": "ఆసుపత్రి దృశ్యం", "open_fullscreen": "పూర్తి విండోలో తెరవండి", "submit": "సమర్పించండి", "save": "భద్రపరచండి", "no_records": "ఇంకా రికార్డులు లేవు.", "profile_updated": "ప్రొఫైల్ విజయవంతంగా భద్రపరచబడింది.", "appointment_requested": "అపాయింట్‌మెంట్ విజయవంతంగా అభ్యర్థించబడింది.", "inventory_updated": "నిల్వ విజయవంతంగా నవీకరించబడింది.", "treatment_saved": "చికిత్స రికార్డు భద్రపరచబడింది.", "diagnostic_created": "పరీక్ష అభ్యర్థన సృష్టించబడింది.", "referral_sent": "రిఫరల్ పంపబడింది.", "location_relayed": "లైవ్ లొకేషన్ ప్రసారం చేయబడింది.", "policy_saved": "విధానం భద్రపరచబడింది.", "feedback_submitted": "అభిప్రాయం సమర్పించబడింది.", "rating_saved": "రేటింగ్ భద్రపరచబడింది.", "complaint_submitted": "ఫిర్యాదు దాఖలు చేయబడింది.", "capacity_updated": "సామర్థ్యం నవీకరించబడింది.", "new_badge": "కొత్తది"
    }
}


@app.get("/api/i18n/{language}", tags=["internationalization"])
def translations(language: str) -> dict:
    if language not in TRANSLATIONS: raise HTTPException(404, "Language not supported")
    return {"language": language, "strings": TRANSLATIONS[language]}


# Mount read-only map system static files if present
MAP_FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "hospital-ambulance-system final", "hospital-ambulance-system", "frontend"))
if os.path.exists(MAP_FRONTEND_DIR):
    @app.get("/map-live/hospital", response_class=FileResponse)
    @app.get("/map-live/hospital/", response_class=FileResponse)
    def serve_map_live_hospital_direct():
        return FileResponse(os.path.join(MAP_FRONTEND_DIR, "hospital.html"))

    @app.get("/map-live/ambulance", response_class=FileResponse)
    @app.get("/map-live/ambulance/", response_class=FileResponse)
    def serve_map_live_ambulance_direct():
        return FileResponse(os.path.join(MAP_FRONTEND_DIR, "index.html"))

    @app.get("/css/hospital.css", response_class=FileResponse)
    def serve_map_css_hospital_direct():
        return FileResponse(os.path.join(MAP_FRONTEND_DIR, "css", "hospital.css"))

    @app.get("/js/hospital_dashboard.js", response_class=FileResponse)
    def serve_map_js_hospital_direct():
        return FileResponse(os.path.join(MAP_FRONTEND_DIR, "js", "hospital_dashboard.js"))

    app.mount("/map-live", StaticFiles(directory=MAP_FRONTEND_DIR, html=True), name="map_live")

# Mount the frontend last so all `/api` routes remain available.
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="frontend")

