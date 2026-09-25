from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditLog, Complaint, DiagnosticService, Hospital, Notification, PatientFeedback, QualityIndicator, Rating, TreatmentRecord


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_dict(obj: Any, fields: Iterable[str] | None = None) -> dict:
    if fields is None:
        fields = (column.name for column in obj.__table__.columns)
    return {field: getattr(obj, field) for field in fields}


def audit(db: Session, user_id: int | None, action: str, resource: str, detail: dict | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, resource=resource, detail=detail or {}))


def notify(db: Session, user_id: int, title: str, body: str, kind: str, reference: dict | None = None) -> None:
    db.add(Notification(user_id=user_id, title=title, body=body, kind=kind, reference=reference or {}))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lng / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def match_facilities(db: Session, lat: float | None, lng: float | None, required_service: str | None) -> list[dict]:
    """Transparent, non-AI suitability calculation; it never makes a clinical decision."""
    hospitals = db.scalars(select(Hospital).where(Hospital.active.is_(True))).all()
    matches = []
    wanted = (required_service or "").strip().lower()
    for hospital in hospitals:
        services = [str(s).lower() for s in (hospital.services or [])]
        diagnostic = db.scalar(select(DiagnosticService).where(DiagnosticService.hospital_id == hospital.id, DiagnosticService.available.is_(True), DiagnosticService.test_name.ilike(f"%{wanted}%"))) if wanted else None
        service_match = not wanted or wanted in services or diagnostic is not None
        distance = None
        if None not in (lat, lng, hospital.latitude, hospital.longitude):
            distance = round(haversine_km(lat, lng, hospital.latitude, hospital.longitude), 2)
        estimated_travel_minutes = max(2, round((distance or 12) / 35 * 60))
        capacity_score = min(hospital.emergency_available, 20) * 3 + min(hospital.beds_available, 30)
        doctor_score = min(hospital.doctors_available, 10) * 5
        travel_penalty = (distance or 15) * 2
        score = round((30 if service_match else -30) + capacity_score + doctor_score - travel_penalty, 1)
        reasons = []
        if service_match:
            reasons.append("required service listed")
        if hospital.emergency_available:
            reasons.append(f"{hospital.emergency_available} emergency beds available")
        if hospital.doctors_available:
            reasons.append(f"{hospital.doctors_available} doctors available")
        matches.append({
            "hospital_id": hospital.id, "name": hospital.name, "distance_km": distance, "estimated_travel_minutes": estimated_travel_minutes,
            "suitability_score": score, "reasons": reasons or ["capacity requires review"],
            "emergency_available": hospital.emergency_available, "beds_available": hospital.beds_available,
            "doctors_available": hospital.doctors_available, "services": hospital.services, "diagnostic_available": bool(diagnostic),
        })
    return sorted(matches, key=lambda item: item["suitability_score"], reverse=True)


def triage_indicator(symptoms: list[str], vitals: dict, age: int | None, risk_factors: list[str]) -> dict:
    """Named rule-based calculation, deliberately not represented as AI diagnosis."""
    symptom_text = " ".join(symptoms).lower()
    risks = {str(item).lower() for item in risk_factors}
    points, reasons = 0, []
    emergency_words = ("chest pain", "unconscious", "seizure", "severe bleeding", "breathing", "stroke")
    if any(word in symptom_text for word in emergency_words):
        points += 6; reasons.append("emergency symptom pattern reported")
    systolic = float(vitals.get("systolic", 120) or 120)
    oxygen = float(vitals.get("spo2", 98) or 98)
    temperature = float(vitals.get("temperature_c", 37) or 37)
    if systolic < 90 or systolic > 180: points += 3; reasons.append("blood pressure needs urgent review")
    if oxygen < 92: points += 4; reasons.append("low oxygen reading")
    if temperature >= 39.5: points += 2; reasons.append("high temperature")
    if age is not None and (age < 2 or age >= 65): points += 1; reasons.append("age-related vulnerability")
    if risks.intersection({"pregnancy", "diabetes", "hypertension", "tb", "elderly", "heart disease"}):
        points += 1; reasons.append("reported risk factor")
    if points >= 6: level, escalation = "HIGH", True
    elif points >= 3: level, escalation = "MODERATE", False
    else: level, escalation = "LOW", False
    return {
        "method": "rule-based risk indicator", "urgency": level, "emergency_escalation_recommended": escalation,
        "score": points, "reasons": reasons or ["no high-risk indicator detected from submitted data"],
        "disclaimer": "Decision support only—not a diagnosis. A qualified healthcare professional must make clinical decisions.",
    }


def compare_treatment(feedback: PatientFeedback, record: TreatmentRecord) -> dict:
    findings = []
    patient = feedback.answers or {}
    hospital = record.details or {}
    pairs = {"medicines": "medicines_received", "tests": "tests_performed", "procedures": "procedures_received", "discharge": "discharged"}
    for hospital_key, patient_key in pairs.items():
        hospital_value, patient_value = hospital.get(hospital_key), patient.get(patient_key)
        if hospital_value and patient_value is False:
            findings.append({
                "field": hospital_key, "message": "Potential discrepancy detected", "evidence": {"hospital_record": hospital_value, "patient_feedback": patient_value},
                "strength": "MEDIUM",
            })
    return {
        "method": "transparent record comparison", "findings": findings,
        "summary": "Potential discrepancies need human review; this result does not determine fault.",
        "generated_at": utc_iso(),
    }


def recalculate_quality(db: Session, hospital_id: int) -> QualityIndicator:
    min_ratings = db.get_policy("quality_thresholds", {"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3}) if hasattr(db, "get_policy") else {"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3}
    ratings = db.scalars(select(Rating).where(Rating.hospital_id == hospital_id)).all()
    complaints = db.scalars(select(Complaint).where(Complaint.hospital_id == hospital_id)).all()
    low = sum(1 for rating in ratings if rating.stars <= 2)
    low_pct = round((low / len(ratings) * 100), 1) if ratings else 0
    open_complaints = sum(1 for complaint in complaints if complaint.status not in {"RESOLVED", "CLOSED"})
    if len(ratings) >= min_ratings["minimum_ratings"] and low_pct >= min_ratings["low_rating_percentage"]:
        level = "HIGH_CONCERN"
    elif open_complaints >= min_ratings["complaint_count"]:
        level = "ATTENTION_REQUIRED"
    else:
        level = "LOW_CONCERN"
    indicator = QualityIndicator(hospital_id=hospital_id, level=level, evidence={
        "method": "policy-driven statistical indicator", "ratings_count": len(ratings), "low_ratings": low,
        "low_rating_percentage": low_pct, "open_complaints": open_complaints,
        "note": "Decision support only. Authorized reviewers make final judgments.",
    })
    db.add(indicator)
    return indicator


class MapBridge:
    """Adapter for the protected Flask map module; it contains no copied or modified map code."""
    def __init__(self) -> None:
        self.base_url = os.getenv("MAP_BRIDGE_URL", "http://127.0.0.1:5000").rstrip("/")

    async def dispatch(self, payload: dict) -> dict:
        map_payload = {
            "ambulance_id": payload.get("ambulance_id", "AMB-101"),
            "hospital_id": payload.get("map_place_id", "JS001"),
            "patient_name": payload.get("patient_name", "Emergency Patient"),
            "patient_condition": payload.get("condition", "Emergency care required"),
            "lat": payload.get("latitude"), "lng": payload.get("longitude"),
        }
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.post(f"{self.base_url}/api/emergency/request", json=map_payload)
                response.raise_for_status()
                data = response.json()
                return {"connected": True, "data": data.get("data", data)}
        except httpx.HTTPError as exc:
            return {"connected": False, "error": str(exc), "data": None}

    async def update_location(self, ambulance_id: str, latitude: float, longitude: float, speed_kmh: float | None) -> bool:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.post(f"{self.base_url}/api/emergency/ambulance-location", json={
                    "ambulance_id": ambulance_id, "lat": latitude, "lng": longitude, "speed_kmh": speed_kmh or 0,
                })
                response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    async def respond(self, map_request_id: str, status: str, reason: str | None = None) -> bool:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.post(f"{self.base_url}/api/emergency/respond", json={"request_id": map_request_id, "status": status, "reason": reason})
                response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False
