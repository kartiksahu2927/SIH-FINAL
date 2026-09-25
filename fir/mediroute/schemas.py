from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


Role = Literal["PATIENT", "HOSPITAL", "DOCTOR", "AMBULANCE_DRIVER", "ADMIN", "GOVERNMENT_AUTHORITY"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str = Field(min_length=2, max_length=150)
    role: Role = "PATIENT"
    language: str = Field(default="en", max_length=10)
    hospital_id: int | None = None
    terms_accepted: bool = Field(default=True)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PatientProfileIn(BaseModel):
    date_of_birth: str | None = None
    sex: str | None = None
    phone: str | None = None
    address: str | None = None
    emergency_contact: dict[str, Any] = Field(default_factory=dict)
    allergies: list[str] = Field(default_factory=list)
    medical_history: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class TriageIn(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    vitals: dict[str, Any] = Field(default_factory=dict)
    age: int | None = Field(default=None, ge=0, le=130)
    risk_factors: list[str] = Field(default_factory=list)


class AppointmentIn(BaseModel):
    hospital_id: int
    doctor_id: int | None = None
    scheduled_for: str = Field(min_length=10, max_length=40)
    reason: str | None = Field(default=None, max_length=2000)


class AppointmentSlotIn(BaseModel):
    doctor_id: int | None = None
    starts_at: str = Field(min_length=10, max_length=40)
    ends_at: str = Field(min_length=10, max_length=40)


class TeleconsultationIn(BaseModel):
    appointment_id: int
    provider: str = Field(default="external_provider", max_length=80)
    meeting_url: str | None = Field(default=None, max_length=2000)


class ReferralIn(BaseModel):
    patient_id: int
    to_hospital_id: int | None = None
    required_service: str = Field(min_length=2, max_length=160)
    clinical_summary: str = Field(min_length=2, max_length=5000)


class ReferralStatusIn(BaseModel):
    status: Literal["PENDING", "SENT", "ACCEPTED", "REJECTED", "SCHEDULED", "IN_TRANSIT", "ARRIVED", "UNDER_TREATMENT", "REFERRED_AGAIN", "DISCHARGED", "COMPLETED", "CANCELLED"]
    note: str | None = Field(default=None, max_length=1000)


class EmergencyIn(BaseModel):
    patient_id: int | None = None
    hospital_id: int | None = None
    ambulance_id: int | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    condition: str = Field(min_length=2, max_length=1000)
    patient_name: str | None = Field(default=None, max_length=150)
    required_service: str | None = Field(default=None, max_length=160)


class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    speed_kmh: float | None = Field(default=None, ge=0, le=300)


class DiagnosticIn(BaseModel):
    patient_id: int
    hospital_id: int
    test_name: str = Field(min_length=2, max_length=180)
    scheduled_for: str | None = None


class DiagnosticStatusIn(BaseModel):
    status: Literal["REQUESTED", "SCHEDULED", "SAMPLE_COLLECTED", "PROCESSING", "COMPLETED", "REPORT_AVAILABLE", "CANCELLED"]
    findings: str | None = Field(default=None, max_length=5000)
    report_url: str | None = Field(default=None, max_length=2000)


class DiagnosticServiceIn(BaseModel):
    test_name: str = Field(min_length=2, max_length=180)
    available: bool = True
    slots_available: int = Field(default=0, ge=0)


class InventoryIn(BaseModel):
    medicine_name: str = Field(min_length=2, max_length=180)
    generic_name: str | None = Field(default=None, max_length=180)
    quantity: int = Field(ge=0)
    low_stock_threshold: int = Field(default=10, ge=0)
    expiry_date: str | None = None


class FollowUpIn(BaseModel):
    patient_id: int
    assigned_user_id: int | None = None
    condition: str = Field(min_length=2, max_length=120)
    due_on: str = Field(min_length=10, max_length=40)
    priority: Literal["ROUTINE", "PRIORITY", "HIGH"] = "ROUTINE"


class TreatmentIn(BaseModel):
    patient_id: int
    visit_id: int
    symptoms_assessment: str | None = None
    diagnosis: str | None = None
    medicines: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    treatment: str | None = None
    discharge: bool | None = None
    referral_status: str | None = None
    follow_up_instructions: str | None = None


class FeedbackIn(BaseModel):
    visit_id: int
    treatment_received: bool | None = None
    medicines_received: bool | None = None
    tests_performed: bool | None = None
    procedures_received: bool | None = None
    issue_addressed: bool | None = None
    discharged: bool | None = None
    referred: bool | None = None
    written_feedback: str | None = Field(default=None, max_length=5000)


class RatingIn(BaseModel):
    visit_id: int
    stars: int = Field(ge=1, le=5)
    feedback: str | None = Field(default=None, max_length=2000)


class ComplaintIn(BaseModel):
    hospital_id: int | None = None
    visit_id: int | None = None
    category: Literal["TREATMENT_NOT_PROVIDED", "MEDICINE_NOT_PROVIDED", "DIAGNOSTIC_SERVICE_ISSUE", "UNNECESSARY_DELAY", "DISCHARGE_ISSUE", "REFERRAL_NOT_PROVIDED", "COMMUNICATION_ISSUE", "OTHER_HEALTHCARE_SERVICE_ISSUE"]
    description: str = Field(min_length=10, max_length=5000)
    severity: Literal["NORMAL", "SERIOUS"] = "NORMAL"


class ComplaintResponseIn(BaseModel):
    response: str = Field(min_length=2, max_length=5000)
    visible_to_patient: bool = True


class PolicyIn(BaseModel):
    minimum_ratings: int = Field(default=3, ge=1)
    low_rating_percentage: float = Field(default=40, ge=0, le=100)
    complaint_count: int = Field(default=3, ge=1)
    review_period_days: int = Field(default=30, ge=1, le=365)


class NotificationPreferenceIn(BaseModel):
    in_app_enabled: bool = True
    emergency_enabled: bool = True
    appointment_enabled: bool = True
    follow_up_enabled: bool = True


class AIAnalysisIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    task: Literal["summarize_record", "classify_complaint", "prioritize_follow_up"]


class EmergencySessionIn(BaseModel):
    emergency_request_id: int
    language: str = Field(default="en", max_length=10)


class EmergencyMessageIn(BaseModel):
    session_id: int
    message: str = Field(min_length=1, max_length=2000)
    language: str | None = Field(default=None, max_length=10)

