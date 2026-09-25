from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ambulance, AmbulanceDriver, Doctor, EmergencyGuidance, Hospital, Medicine, MedicineInventory, PatientProfile, PolicySetting, User
from .security import hash_password


DEMO_PASSWORD = "MediRoute!2026"


def seed_demo_data(db: Session) -> None:
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

    if db.scalar(select(User.id).limit(1)):
        return
    hospital = Hospital(
        name="MediRoute District Hospital", address="Mathura Rural Health Corridor, Uttar Pradesh",
        phone="1800-108-2026", latitude=27.652, longitude=77.558, emergency_capacity=24,
        emergency_available=12, beds_available=28, doctors_available=7,
        services=["emergency", "trauma", "maternal", "diagnostics", "teleconsultation"], map_place_id="JS001",
    )
    referral_hospital = Hospital(
        name="MediRoute Specialist Centre", address="Mathura Specialist Health Campus, Uttar Pradesh",
        phone="1800-108-2027", latitude=27.5645, longitude=77.6325, emergency_capacity=30,
        emergency_available=16, beds_available=34, doctors_available=9,
        services=["emergency", "cardiology", "neurology", "trauma", "diagnostics"], map_place_id="HOSP_NAYATI_MEDICITY",
    )
    ambulance = Ambulance(code="MR-AMB-101", vehicle_type="Advanced Life Support", status="AVAILABLE", latitude=27.64687, longitude=77.551921, map_ambulance_id="AMB-101")
    db.add_all([hospital, referral_hospital, ambulance]); db.flush()

    users = [
        User(email="patient@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Asha Verma", role="PATIENT", language="en"),
        User(email="hospital@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="MediRoute Hospital Team", role="HOSPITAL", hospital_id=hospital.id, language="en"),
        User(email="doctor@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Dr. Rohan Mehta", role="HOSPITAL", hospital_id=hospital.id, language="en"),
        User(email="driver@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="Arun Singh", role="AMBULANCE_DRIVER", language="en"),
        User(email="admin@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="MediRoute Administrator", role="ADMIN", language="en"),
        User(email="government@mediroute.demo", password_hash=hash_password(DEMO_PASSWORD), full_name="District Health Authority", role="GOVERNMENT_AUTHORITY", language="en"),
    ]
    db.add_all(users); db.flush()
    db.add(PatientProfile(user_id=users[0].id, phone="+91 90000 00000", address="Mathura", emergency_contact={"name": "Ravi Verma", "phone": "+91 90000 00001"}, allergies=["Penicillin"], medical_history=["Hypertension"], risk_flags=["hypertension"]))
    db.add(Doctor(user_id=users[2].id, hospital_id=hospital.id, specialty="Emergency Medicine", available=True))
    db.add(AmbulanceDriver(user_id=users[3].id, ambulance_id=ambulance.id, license_number="UP-MR-2026-001"))
    paracetamol = Medicine(name="Paracetamol 500 mg", generic_name="Paracetamol")
    ors = Medicine(name="Oral Rehydration Salts", generic_name="ORS")
    db.add_all([paracetamol, ors]); db.flush()
    db.add_all([
        MedicineInventory(hospital_id=hospital.id, medicine_id=paracetamol.id, quantity=350, low_stock_threshold=50, expiry_date="2027-12-31"),
        MedicineInventory(hospital_id=hospital.id, medicine_id=ors.id, quantity=90, low_stock_threshold=30, expiry_date="2027-08-31"),
    ])
    db.add(PolicySetting(key="quality_thresholds", value={"minimum_ratings": 3, "low_rating_percentage": 40, "complaint_count": 3, "review_period_days": 30}))
    db.commit()

