from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class Timestamped:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(Timestamped, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(40), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True, index=True)


class PatientProfile(Timestamped, Base):
    __tablename__ = "patients"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    date_of_birth: Mapped[Optional[str]] = mapped_column(String(10))
    sex: Mapped[Optional[str]] = mapped_column(String(30))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    address: Mapped[Optional[str]] = mapped_column(Text)
    emergency_contact: Mapped[dict] = mapped_column(JSON, default=dict)
    allergies: Mapped[list] = mapped_column(JSON, default=list)
    medical_history: Mapped[list] = mapped_column(JSON, default=list)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)


class Hospital(Timestamped, Base):
    __tablename__ = "hospitals"
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    address: Mapped[str] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    emergency_capacity: Mapped[int] = mapped_column(Integer, default=0)
    emergency_available: Mapped[int] = mapped_column(Integer, default=0)
    beds_available: Mapped[int] = mapped_column(Integer, default=0)
    doctors_available: Mapped[int] = mapped_column(Integer, default=0)
    services: Mapped[list] = mapped_column(JSON, default=list)
    map_place_id: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Facility(Timestamped, Base):
    __tablename__ = "facilities"
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    facility_type: Mapped[str] = mapped_column(String(80))
    services: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Doctor(Timestamped, Base):
    __tablename__ = "doctors"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    specialty: Mapped[str] = mapped_column(String(120))
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class AmbulanceDriver(Timestamped, Base):
    __tablename__ = "ambulance_drivers"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    ambulance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ambulances.id"), nullable=True)
    license_number: Mapped[Optional[str]] = mapped_column(String(80))


class Ambulance(Timestamped, Base):
    __tablename__ = "ambulances"
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    vehicle_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="AVAILABLE")
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    map_ambulance_id: Mapped[Optional[str]] = mapped_column(String(80), index=True)


class MedicalRecord(Timestamped, Base):
    __tablename__ = "medical_records"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    record_type: Mapped[str] = mapped_column(String(80), index=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    author_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class Visit(Timestamped, Base):
    __tablename__ = "visits"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    status: Mapped[str] = mapped_column(String(50), default="OPEN")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    discharge_summary: Mapped[Optional[str]] = mapped_column(Text)


class Vital(Timestamped, Base):
    __tablename__ = "vitals"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("visits.id"), nullable=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class Consultation(Timestamped, Base):
    __tablename__ = "consultations"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("visits.id"), nullable=True)
    doctor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("doctors.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(40), default="IN_PERSON")
    notes: Mapped[str] = mapped_column(Text)
    diagnosis: Mapped[Optional[str]] = mapped_column(Text)


class Prescription(Timestamped, Base):
    __tablename__ = "prescriptions"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    consultation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("consultations.id"), nullable=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    instructions: Mapped[Optional[str]] = mapped_column(Text)


class Medicine(Timestamped, Base):
    __tablename__ = "medicines"
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    generic_name: Mapped[Optional[str]] = mapped_column(String(180))


class MedicineInventory(Timestamped, Base):
    __tablename__ = "medicine_inventory"
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    medicine_id: Mapped[int] = mapped_column(ForeignKey("medicines.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=10)
    expiry_date: Mapped[Optional[str]] = mapped_column(String(10))
    __table_args__ = (UniqueConstraint("hospital_id", "medicine_id", name="uq_inventory_medicine_hospital"),)


class DiagnosticOrder(Timestamped, Base):
    __tablename__ = "diagnostic_orders"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    test_name: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(50), default="REQUESTED")
    scheduled_for: Mapped[Optional[str]] = mapped_column(String(40))


class DiagnosticService(Timestamped, Base):
    __tablename__ = "diagnostic_services"
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    test_name: Mapped[str] = mapped_column(String(180), index=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    slots_available: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("hospital_id", "test_name", name="uq_diagnostic_service_hospital"),)


class DiagnosticReport(Timestamped, Base):
    __tablename__ = "diagnostic_reports"
    order_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_orders.id"), unique=True, index=True)
    report_url: Mapped[Optional[str]] = mapped_column(Text)
    findings: Mapped[Optional[str]] = mapped_column(Text)


class Appointment(Timestamped, Base):
    __tablename__ = "appointments"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    doctor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("doctors.id"), nullable=True)
    scheduled_for: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(50), default="BOOKED")
    reason: Mapped[Optional[str]] = mapped_column(Text)


class AppointmentSlot(Timestamped, Base):
    __tablename__ = "appointment_slots"
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    doctor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("doctors.id"), nullable=True, index=True)
    starts_at: Mapped[str] = mapped_column(String(40), index=True)
    ends_at: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="AVAILABLE")
    __table_args__ = (UniqueConstraint("hospital_id", "doctor_id", "starts_at", name="uq_appointment_slot"),)


class Teleconsultation(Timestamped, Base):
    __tablename__ = "teleconsultations"
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    meeting_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="SCHEDULED")
    notes: Mapped[Optional[str]] = mapped_column(Text)


class QueueEntry(Timestamped, Base):
    __tablename__ = "queue_entries"
    appointment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("appointments.id"), nullable=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    token_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="WAITING")
    __table_args__ = (UniqueConstraint("hospital_id", "token_number", name="uq_queue_token_hospital"),)


class Referral(Timestamped, Base):
    __tablename__ = "referrals"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    from_hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    to_hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    required_service: Mapped[str] = mapped_column(String(160))
    clinical_summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    timeline: Mapped[list] = mapped_column(JSON, default=list)


class EmergencyRequest(Timestamped, Base):
    __tablename__ = "emergency_requests"
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True, index=True)
    ambulance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ambulances.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="REQUESTED", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    map_request_id: Mapped[Optional[str]] = mapped_column(String(120), index=True)


class LocationEvent(Timestamped, Base):
    __tablename__ = "locations"
    emergency_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emergency_requests.id"), nullable=True, index=True)
    ambulance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ambulances.id"), nullable=True, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    speed_kmh: Mapped[Optional[float]] = mapped_column(Float)


class Notification(Timestamped, Base):
    __tablename__ = "notifications"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(50))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    reference: Mapped[dict] = mapped_column(JSON, default=dict)


class NotificationPreference(Timestamped, Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    emergency_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    appointment_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    follow_up_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class FollowUp(Timestamped, Base):
    __tablename__ = "follow_ups"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    assigned_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    condition: Mapped[str] = mapped_column(String(120))
    due_on: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(50), default="SCHEDULED")
    priority: Mapped[str] = mapped_column(String(40), default="ROUTINE")


class Rating(Timestamped, Base):
    __tablename__ = "ratings"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    stars: Mapped[int] = mapped_column(Integer)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("patient_id", "visit_id", name="uq_rating_patient_visit"),)


class PatientFeedback(Timestamped, Base):
    __tablename__ = "patient_feedback"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    written_feedback: Mapped[Optional[str]] = mapped_column(Text)


class TreatmentRecord(Timestamped, Base):
    __tablename__ = "hospital_treatment_records"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    responsible_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class TreatmentComparison(Timestamped, Base):
    __tablename__ = "treatment_comparisons"
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), unique=True, index=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("patient_feedback.id"), index=True)
    treatment_record_id: Mapped[int] = mapped_column(ForeignKey("hospital_treatment_records.id"), index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(50), default="PENDING_REVIEW")


class Complaint(Timestamped, Base):
    __tablename__ = "complaints"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True, index=True)
    visit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("visits.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(60), default="SUBMITTED", index=True)
    severity: Mapped[str] = mapped_column(String(30), default="NORMAL")


class ComplaintResponse(Timestamped, Base):
    __tablename__ = "complaint_responses"
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), index=True)
    responder_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    response: Mapped[str] = mapped_column(Text)
    visible_to_patient: Mapped[bool] = mapped_column(Boolean, default=True)


class Escalation(Timestamped, Base):
    __tablename__ = "escalations"
    hospital_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hospitals.id"), nullable=True, index=True)
    complaint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("complaints.id"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(60), default="OPEN")


class QualityIndicator(Timestamped, Base):
    __tablename__ = "quality_indicators"
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    level: Mapped[str] = mapped_column(String(60))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(60), default="PENDING_REVIEW")


class PolicySetting(Timestamped, Base):
    __tablename__ = "policy_settings"
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Timestamped, Base):
    __tablename__ = "audit_logs"
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(160), index=True)
    resource: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class EmergencyAssistantSession(Timestamped, Base):
    __tablename__ = "emergency_assistant_sessions"
    emergency_request_id: Mapped[int] = mapped_column(ForeignKey("emergency_requests.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE", index=True)


class EmergencyAssistantMessage(Timestamped, Base):
    __tablename__ = "emergency_assistant_messages"
    session_id: Mapped[int] = mapped_column(ForeignKey("emergency_assistant_sessions.id"), index=True)
    sender: Mapped[str] = mapped_column(String(40))  # user, assistant, system
    message: Mapped[str] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    structured_data: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmergencyGuidance(Timestamped, Base):
    __tablename__ = "emergency_guidances"
    category: Mapped[str] = mapped_column(String(80), index=True)
    language: Mapped[str] = mapped_column(String(10), default="en", index=True)
    approved_content: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(160), default="WHO / Red Cross / Emergency Medical Protocols")
    version: Mapped[str] = mapped_column(String(40), default="1.0")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EmergencySafetyEvent(Timestamped, Base):
    __tablename__ = "emergency_safety_events"
    emergency_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emergency_requests.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(40), default="HIGH")
    action: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

