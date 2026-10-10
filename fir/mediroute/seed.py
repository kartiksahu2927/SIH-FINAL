import os
import math

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Ambulance, AmbulanceDriver, Doctor, EmergencyGuidance, Hospital, Medicine, MedicineInventory, Notification, NotificationPreference, PatientProfile, PolicySetting, User
from .security import hash_password


DEMO_PASSWORD = os.getenv("MEDIROUTE_DEMO_PASSWORD", "MediRoute!2026")


def seed_demo_data(db: Session) -> None:
    def coordinate(name: str, default: float, limit: int) -> float:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value) or not -limit <= value <= limit:
            raise ValueError(f"{name} must be a valid configured demo coordinate")
        return value
    # Seed Emergency Guidance if not yet present
    if not db.scalar(select(EmergencyGuidance.id).limit(1)):
        from .emergency_assistant import APPROVED_PROTOCOLS
        guidance_records = []
        for cat, lang_dict in APPROVED_PROTOCOLS.items():
            for lang, content in lang_dict.items():
                guidance_records.append(
                    EmergencyGuidance(
                        category=cat,
                        language=lang,
                        approved_content=content,
                        source="WHO / Red Cross / Emergency Medical Protocols",
                        version="1.0",
                        active=True
                    )
                )
        db.add_all(guidance_records)
        db.commit()

    js_hospital = db.scalar(select(Hospital).where(Hospital.map_place_id == "JS001"))
    if not js_hospital:
        js_hospital = Hospital(
            name="JS Hospital - Emergency Center (Demo)", address="MediRoute controlled demo location, Mathura, Uttar Pradesh",
            phone="1800-108-2026", latitude=coordinate("DEMO_JS_HOSPITAL_LAT", 27.652, 90), longitude=coordinate("DEMO_JS_HOSPITAL_LNG", 77.558, 180), emergency_capacity=24,
            emergency_available=12, beds_available=28, doctors_available=7,
            services=["emergency", "trauma", "maternal", "diagnostics", "teleconsultation"], map_place_id="JS001",
            demo_facility=True, capabilities_source="DEMO_CONFIG", location_source="DEMO_CONFIG",
        )
        db.add(js_hospital)
    else:
        js_hospital.demo_facility = True
        js_hospital.capabilities_source = js_hospital.capabilities_source or "DEMO_CONFIG"
        js_hospital.location_source = js_hospital.location_source or "DEMO_CONFIG"

    if not db.scalar(select(User.id).limit(1)):
        referral_hospital = Hospital(
            name="MediRoute Specialist Centre", address="Mathura Specialist Health Campus, Uttar Pradesh",
            phone="1800-108-2027", latitude=27.5645, longitude=77.6325, emergency_capacity=30,
            emergency_available=16, beds_available=34, doctors_available=9,
            services=["emergency", "cardiology", "neurology", "trauma", "diagnostics"], map_place_id="HOSP_NAYATI_MEDICITY",
        )
        ambulance = Ambulance(code="MR-AMB-101", vehicle_type="Advanced Life Support", status="AVAILABLE", latitude=27.64687, longitude=77.551921, map_ambulance_id="AMB-101")
        db.add_all([referral_hospital, ambulance]); db.flush()

        users = [
            User(email="patient@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Asha Verma", role="PATIENT", language="en"),
            User(email="hospital@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="JS Hospital Demo Team", role="HOSPITAL", hospital_id=js_hospital.id, language="en"),
            User(email="doctor@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Dr. Rohan Mehta", role="HOSPITAL", hospital_id=js_hospital.id, language="en"),
            User(email="driver@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Arun Singh", role="AMBULANCE_DRIVER", language="en"),
            User(email="admin@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="MediRoute Administrator", role="ADMIN", language="en"),
            User(email="government@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="District Health Authority", role="GOVERNMENT_AUTHORITY", language="en"),
        ]
        db.add_all(users); db.flush()
        db.add(PatientProfile(user_id=users[0].id, phone="+91 90000 00000", address="Mathura", emergency_contact={"name": "Ravi Verma", "phone": "+91 90000 00001"}, allergies=["Penicillin"], medical_history=["Hypertension"], risk_flags=["hypertension"]))
        db.add(Doctor(user_id=users[2].id, hospital_id=js_hospital.id, specialty="Emergency Medicine", available=True))
        db.add(AmbulanceDriver(user_id=users[3].id, ambulance_id=ambulance.id, license_number="UP-MR-2026-001"))
        paracetamol = Medicine(name="Paracetamol 500 mg", generic_name="Paracetamol")
        ors = Medicine(name="Oral Rehydration Salts", generic_name="ORS")
        db.add_all([paracetamol, ors]); db.flush()
        db.add_all([
            MedicineInventory(hospital_id=js_hospital.id, medicine_id=paracetamol.id, quantity=350, low_stock_threshold=50, expiry_date="2027-12-31"),
            MedicineInventory(hospital_id=js_hospital.id, medicine_id=ors.id, quantity=90, low_stock_threshold=30, expiry_date="2027-08-31"),
        ])
        db.add(PolicySetting(key="quality_thresholds", value={"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3, "review_period_days": 30}))

    # KS Hospital is implemented by the map module's copy of the JS Hospital
    # reception dashboard. Remove the previous FastAPI-only role account if an
    # older database was initialized with it; do not create it again.
    old_ks_user = db.scalar(select(User).where(User.email == "ks-hospital@mediroute.demo"))
    if old_ks_user:
        db.execute(delete(Notification).where(Notification.user_id == old_ks_user.id))
        db.execute(delete(NotificationPreference).where(NotificationPreference.user_id == old_ks_user.id))
        db.delete(old_ks_user)
    # Existing MediRoute facilities already had configured coordinates before
    # source metadata existed. Label those values transparently; this does not
    # claim live capacity or move a facility. Unknown capacity is confirmed by
    # the hospital acceptance transaction.
    for facility in db.scalars(select(Hospital)).all():
        if facility.latitude is not None and facility.longitude is not None and not facility.location_source:
            facility.location_source = "LEGACY_CONFIG"
            facility.capabilities_source = facility.capabilities_source or "LEGACY_CONFIG"
    db.commit()
