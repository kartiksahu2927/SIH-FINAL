"""
routes.py — Flask API endpoints for teammate integration & maps service
=======================================================================
Defines REST endpoints for:
- Hospital availability updates (Bed/Doctor availability)
- Hospital emergency response (Accept/Reject)
- Multi-Ambulance Fleet GPS telemetry & status
- Real street-level route calculations (OSRM / Google Routes) & hospital search
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
import math
import os
import requests

api_bp = Blueprint('api', __name__, url_prefix='/api')

# ============================================================
# MULTI-AMBULANCE FLEET PROTOTYPE DATA
# ============================================================
AMBULANCE_FLEET = {
    "AMB-101": {
        "id": "AMB-101",
        "callsign": "Ambulance Unit 101 (ALS)",
        "plate": "DL-01-EM-1081",
        "type": "Advanced Life Support (ALS)",
        "crew": "Vikram Singh & Dr. A. Sharma",
        "status": "DISPATCHED",
        "status_color": "emergency",
        "equipment": ["Ventilator", "Defibrillator", "Multipara Monitor", "Oxygen 100%"],
        "oxygen_level": 98,
        "speed_kmh": 48.5,
        "lat": 27.646870,
        "lng": 77.551921,
        "location_name": "K.D. Medical College & Apex Trauma Hub",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    },
    "AMB-102": {
        "id": "AMB-102",
        "callsign": "Ambulance Unit 102 (ICU)",
        "plate": "DL-04-TC-2042",
        "type": "Mobile ICU Unit",
        "crew": "Rajesh Kumar & Nurse Priya",
        "status": "AVAILABLE",
        "status_color": "success",
        "equipment": ["Cardiac Monitor", "Syringe Pump", "Suction Unit", "Oxygen 95%"],
        "oxygen_level": 95,
        "speed_kmh": 0.0,
        "lat": 27.645050,
        "lng": 77.550500,
        "location_name": "KD Dental Station Outpost",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    },
    "AMB-103": {
        "id": "AMB-103",
        "callsign": "Ambulance Unit 103 (BLS)",
        "plate": "DL-07-BL-5053",
        "type": "Basic Life Support (BLS)",
        "crew": "Manoj Yadav & EMT Rohit",
        "status": "PATROL",
        "status_color": "info",
        "equipment": ["Automated AED", "Trauma Splints", "First Aid Kit", "Oxygen 88%"],
        "oxygen_level": 88,
        "speed_kmh": 32.0,
        "lat": 27.564500,
        "lng": 77.632500,
        "location_name": "Nayati Medicity Highway Corridor",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    },
    "AMB-104": {
        "id": "AMB-104",
        "callsign": "Ambulance Unit 104 (NICU/Critical)",
        "plate": "DL-09-CC-8084",
        "type": "Critical Care & Pediatric Unit",
        "crew": "Suresh Verma & Dr. Sneha",
        "status": "STANDBY",
        "status_color": "warning",
        "equipment": ["Infant Incubator", "Blood Gas Analyzer", "Emergency Ventilator"],
        "oxygen_level": 100,
        "speed_kmh": 0.0,
        "lat": 27.508500,
        "lng": 77.662000,
        "location_name": "Mathura District Combined Hospital Base",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
}

# In-memory storage for hackathon prototype
hospital_eligibility = {}

# Legacy single ambulance pointer
ambulance_location = AMBULANCE_FLEET["AMB-101"]

# ============================================================
# COMPREHENSIVE EMERGENCY HOSPITAL NETWORK WITH LIVE TELEMETRY
# ============================================================
DEFAULT_HOSPITALS = [
    # ---- Interactive Emergency Hospital (Dedicated Device 2 Endpoint) ----
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
        "blood_bank": True,
        "pediatric_er": True,
        "critical_facilities": ["Cath Lab (24x7)", "Trauma Resuscitation Bay #1 & #2", "Digital CT Scanner", "Emergency OT #1"],
        "telemetry_source": "JS Hospital Real-Time HL7 Node",
        "pediatric_beds_available": 6,
        "oxygen_level_pct": 100,
        "oxygen_hours_remaining": 96,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 8,
        "nurse_staff_count": 25,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 10, "O_pos": 25, "A_pos": 18, "B_pos": 20, "AB_pos": 10 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "required_facility_available": True,
        "specialist_available": ["trauma", "cardiology", "neurology", "critical_care"],
        "accepted": None,
        "telemetry_source": "JS Hospital Real-Time SIH HL7 Node",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    # ---- Mathura / NH-44 Corridor (KD Medical / KD Dental Sector) ----
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
        "emergency_beds_occupied": 16,
        "emergency_beds_available": 14,
        "icu_beds_total": 12,
        "icu_beds_occupied": 7,
        "icu_beds_available": 5,
        "ventilators_total": 8,
        "ventilators_available": 4,
        "pediatric_beds_available": 6,
        "oxygen_level_pct": 98,
        "oxygen_hours_remaining": 72,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 8,
        "nurse_staff_count": 24,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 6, "O_pos": 18, "A_pos": 14, "B_pos": 16, "AB_pos": 8 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "emergency_medicine", "cardiology", "orthopedics"],
        "accepted": None,
        "telemetry_source": "KD Medical Apex Emergency Gateway v4",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
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
        "emergency_beds_occupied": 10,
        "emergency_beds_available": 8,
        "icu_beds_total": 6,
        "icu_beds_occupied": 3,
        "icu_beds_available": 3,
        "ventilators_total": 4,
        "ventilators_available": 2,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 96,
        "oxygen_hours_remaining": 50,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "er_doctors_count": 5,
        "nurse_staff_count": 14,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": False,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 3, "O_pos": 10, "A_pos": 8, "B_pos": 9, "AB_pos": 4 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "maxillofacial_surgery", "general_surgery"],
        "accepted": None,
        "telemetry_source": "KD Dental Emergency Telemetry Feed",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
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
        "emergency_beds_occupied": 19,
        "emergency_beds_available": 16,
        "icu_beds_total": 16,
        "icu_beds_occupied": 9,
        "icu_beds_available": 7,
        "ventilators_total": 12,
        "ventilators_available": 6,
        "pediatric_beds_available": 8,
        "oxygen_level_pct": 99,
        "oxygen_hours_remaining": 84,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 8,
        "nurse_staff_count": 22,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 7, "O_pos": 20, "A_pos": 15, "B_pos": 18, "AB_pos": 8 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "cardiology", "neurology", "oncology"],
        "accepted": None,
        "telemetry_source": "Nayati Medicity Realtime HIS",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
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
        "emergency_beds_occupied": 16,
        "emergency_beds_available": 9,
        "icu_beds_total": 8,
        "icu_beds_occupied": 5,
        "icu_beds_available": 3,
        "ventilators_total": 6,
        "ventilators_available": 2,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 94,
        "oxygen_hours_remaining": 50,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "er_doctors_count": 6,
        "nurse_staff_count": 16,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": False,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 4, "O_pos": 14, "A_pos": 10, "B_pos": 12, "AB_pos": 5 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "general_surgery", "orthopedics"],
        "accepted": None,
        "telemetry_source": "UP State Health Emergency Network",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
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
        "emergency_beds_occupied": 14,
        "emergency_beds_available": 8,
        "icu_beds_total": 8,
        "icu_beds_occupied": 5,
        "icu_beds_available": 3,
        "ventilators_total": 6,
        "ventilators_available": 3,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 97,
        "oxygen_hours_remaining": 60,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "er_doctors_count": 6,
        "nurse_staff_count": 18,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 5, "O_pos": 15, "A_pos": 11, "B_pos": 13, "AB_pos": 6 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "internal_medicine", "general_surgery"],
        "accepted": None,
        "telemetry_source": "RKMS Vrindavan Hospital Feed",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
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
        "emergency_beds_occupied": 12,
        "emergency_beds_available": 6,
        "icu_beds_total": 6,
        "icu_beds_occupied": 4,
        "icu_beds_available": 2,
        "ventilators_total": 4,
        "ventilators_available": 2,
        "pediatric_beds_available": 3,
        "oxygen_level_pct": 95,
        "oxygen_hours_remaining": 48,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "er_doctors_count": 4,
        "nurse_staff_count": 12,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": False,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": False
        },
        "blood_bank": { "O_neg": 3, "O_pos": 8, "A_pos": 7, "B_pos": 9, "AB_pos": 3 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "industrial_burn_care", "general_medicine"],
        "accepted": None,
        "telemetry_source": "Refinery Health Corridor Stream",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    # ---- Delhi NCR Apex Emergency Hospitals ----
    {
        "place_id": "HOSP_AIIMS_DELHI",
        "name": "AIIMS New Delhi (Apex Trauma Centre)",
        "latitude": 28.5672,
        "longitude": 77.2100,
        "address": "Sri Aurobindo Marg, Ansari Nagar, New Delhi",
        "phone": "+91 11 2658 8500",
        "rating": 4.9,
        "user_ratings_total": 5240,
        "emergency_beds_total": 35,
        "emergency_beds_occupied": 21,
        "emergency_beds_available": 14,
        "icu_beds_total": 16,
        "icu_beds_occupied": 11,
        "icu_beds_available": 5,
        "ventilators_total": 12,
        "ventilators_available": 6,
        "pediatric_beds_available": 8,
        "oxygen_level_pct": 98,
        "oxygen_hours_remaining": 72,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 8,
        "nurse_staff_count": 22,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 6, "O_pos": 18, "A_pos": 14, "B_pos": 16, "AB_pos": 8 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "cardiology", "neurology", "orthopedics"],
        "accepted": None,
        "telemetry_source": "HL7 FHIR v4 Apex Emergency Gateway",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_SAFDARJUNG",
        "name": "Safdarjung Hospital & Emergency Care Block",
        "latitude": 28.5705,
        "longitude": 77.2078,
        "address": "Ring Road, Opposite AIIMS, New Delhi",
        "phone": "+91 11 2616 5060",
        "rating": 4.5,
        "user_ratings_total": 3680,
        "emergency_beds_total": 30,
        "emergency_beds_occupied": 19,
        "emergency_beds_available": 11,
        "icu_beds_total": 14,
        "icu_beds_occupied": 10,
        "icu_beds_available": 4,
        "ventilators_total": 10,
        "ventilators_available": 5,
        "pediatric_beds_available": 6,
        "oxygen_level_pct": 96,
        "oxygen_hours_remaining": 60,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "er_doctors_count": 6,
        "nurse_staff_count": 18,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 4, "O_pos": 15, "A_pos": 10, "B_pos": 12, "AB_pos": 6 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "general_surgery", "burn_care"],
        "accepted": None,
        "telemetry_source": "Safdarjung Telemetry Feed v2",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_MAX_SAKET",
        "name": "Max Super Speciality Hospital Saket",
        "latitude": 28.5284,
        "longitude": 77.2119,
        "address": "1, 2, Press Enclave Marg, Saket, New Delhi",
        "phone": "+91 11 2651 5050",
        "rating": 4.7,
        "user_ratings_total": 3120,
        "emergency_beds_total": 20,
        "emergency_beds_occupied": 13,
        "emergency_beds_available": 7,
        "icu_beds_total": 8,
        "icu_beds_occupied": 8,
        "icu_beds_available": 0,
        "ventilators_total": 6,
        "ventilators_available": 1,
        "pediatric_beds_available": 3,
        "oxygen_level_pct": 99,
        "oxygen_hours_remaining": 80,
        "divert_status": "HEAVY_LOAD",
        "er_wait_time_minutes": 5,
        "er_doctors_count": 5,
        "nurse_staff_count": 14,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": False
        },
        "blood_bank": { "O_neg": 3, "O_pos": 10, "A_pos": 9, "B_pos": 8, "AB_pos": 4 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": False,
        "specialist_available": ["cardiology", "oncology", "pulmonology"],
        "accepted": True,
        "telemetry_source": "Max Healthcare Realtime HIS",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_RML",
        "name": "Dr. Ram Manohar Lohia Hospital (RML)",
        "latitude": 28.6247,
        "longitude": 77.1998,
        "address": "Baba Kharak Singh Marg, Connaught Place, New Delhi",
        "phone": "+91 11 2336 5525",
        "rating": 4.4,
        "user_ratings_total": 2450,
        "emergency_beds_total": 26,
        "emergency_beds_occupied": 17,
        "emergency_beds_available": 9,
        "icu_beds_total": 10,
        "icu_beds_occupied": 6,
        "icu_beds_available": 4,
        "ventilators_total": 8,
        "ventilators_available": 3,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 94,
        "oxygen_hours_remaining": 54,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "er_doctors_count": 6,
        "nurse_staff_count": 16,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 5, "O_pos": 14, "A_pos": 11, "B_pos": 13, "AB_pos": 5 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["emergency_medicine", "cardiology", "neurology"],
        "accepted": None,
        "telemetry_source": "Central Govt Hospital Telemetry Gateway",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_APOLLO_IND",
        "name": "Indraprastha Apollo Hospital",
        "latitude": 28.5398,
        "longitude": 77.2832,
        "address": "Sarita Vihar, Delhi Mathura Road, New Delhi",
        "phone": "+91 11 2692 5858",
        "rating": 4.8,
        "user_ratings_total": 4120,
        "emergency_beds_total": 28,
        "emergency_beds_occupied": 16,
        "emergency_beds_available": 12,
        "icu_beds_total": 15,
        "icu_beds_occupied": 9,
        "icu_beds_available": 6,
        "ventilators_total": 12,
        "ventilators_available": 7,
        "pediatric_beds_available": 6,
        "oxygen_level_pct": 99,
        "oxygen_hours_remaining": 96,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 7,
        "nurse_staff_count": 20,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 8, "O_pos": 20, "A_pos": 16, "B_pos": 18, "AB_pos": 9 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["transplant", "cardiology", "neurology", "trauma"],
        "accepted": None,
        "telemetry_source": "Apollo Prism Telemetry Feed",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_GANGA_RAM",
        "name": "Sir Ganga Ram Hospital",
        "latitude": 28.6385,
        "longitude": 77.1895,
        "address": "Sir Ganga Ram Hospital Marg, Old Rajinder Nagar, New Delhi",
        "phone": "+91 11 2575 0000",
        "rating": 4.7,
        "user_ratings_total": 3540,
        "emergency_beds_total": 24,
        "emergency_beds_occupied": 16,
        "emergency_beds_available": 8,
        "icu_beds_total": 12,
        "icu_beds_occupied": 8,
        "icu_beds_available": 4,
        "ventilators_total": 8,
        "ventilators_available": 4,
        "pediatric_beds_available": 5,
        "oxygen_level_pct": 97,
        "oxygen_hours_remaining": 68,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "er_doctors_count": 6,
        "nurse_staff_count": 18,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 4, "O_pos": 16, "A_pos": 12, "B_pos": 14, "AB_pos": 6 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["nephrology", "cardiology", "trauma_care"],
        "accepted": None,
        "telemetry_source": "SGRH Realtime HL7 Stream",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_BLK_MAX",
        "name": "BLK-Max Super Speciality Hospital",
        "latitude": 28.6441,
        "longitude": 77.1788,
        "address": "Pusa Road, Karol Bagh, New Delhi",
        "phone": "+91 11 3040 3040",
        "rating": 4.6,
        "user_ratings_total": 2890,
        "emergency_beds_total": 22,
        "emergency_beds_occupied": 15,
        "emergency_beds_available": 7,
        "icu_beds_total": 10,
        "icu_beds_occupied": 7,
        "icu_beds_available": 3,
        "ventilators_total": 6,
        "ventilators_available": 2,
        "pediatric_beds_available": 3,
        "oxygen_level_pct": 95,
        "oxygen_hours_remaining": 50,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 2,
        "er_doctors_count": 5,
        "nurse_staff_count": 15,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 3, "O_pos": 11, "A_pos": 9, "B_pos": 10, "AB_pos": 4 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["oncology", "trauma", "cardiac_surgery"],
        "accepted": None,
        "telemetry_source": "BLK-Max Emergency Node",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_FORTIS_ESCORTS",
        "name": "Fortis Escorts Heart Institute & Multi-Care",
        "latitude": 28.5604,
        "longitude": 77.2764,
        "address": "Okhla Road, Sukhdev Vihar Metro, New Delhi",
        "phone": "+91 11 4713 5000",
        "rating": 4.8,
        "user_ratings_total": 3410,
        "emergency_beds_total": 20,
        "emergency_beds_occupied": 12,
        "emergency_beds_available": 8,
        "icu_beds_total": 12,
        "icu_beds_occupied": 7,
        "icu_beds_available": 5,
        "ventilators_total": 8,
        "ventilators_available": 4,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 98,
        "oxygen_hours_remaining": 75,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 6,
        "nurse_staff_count": 16,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": False
        },
        "blood_bank": { "O_neg": 5, "O_pos": 15, "A_pos": 12, "B_pos": 14, "AB_pos": 7 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["cardiac_emergency", "pediatric_cardiology"],
        "accepted": None,
        "telemetry_source": "Fortis Care Live Telemetry",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_LNJP",
        "name": "Lok Nayak Hospital (LNJP / MAMC)",
        "latitude": 28.6369,
        "longitude": 77.2405,
        "address": "Jawaharlal Nehru Marg, Delhi Gate, New Delhi",
        "phone": "+91 11 2323 3000",
        "rating": 4.3,
        "user_ratings_total": 2180,
        "emergency_beds_total": 32,
        "emergency_beds_occupied": 23,
        "emergency_beds_available": 9,
        "icu_beds_total": 12,
        "icu_beds_occupied": 9,
        "icu_beds_available": 3,
        "ventilators_total": 10,
        "ventilators_available": 3,
        "pediatric_beds_available": 6,
        "oxygen_level_pct": 93,
        "oxygen_hours_remaining": 45,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 3,
        "er_doctors_count": 7,
        "nurse_staff_count": 20,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 4, "O_pos": 16, "A_pos": 10, "B_pos": 14, "AB_pos": 5 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": True
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["trauma", "general_medicine", "orthopedics"],
        "accepted": None,
        "telemetry_source": "LNJP Emergency Stream",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_MOOLCHAND",
        "name": "Moolchand Medcity Hospital",
        "latitude": 28.5670,
        "longitude": 77.2345,
        "address": "Lala Lajpat Rai Marg, Near Moolchand Metro, New Delhi",
        "phone": "+91 11 4200 0000",
        "rating": 4.5,
        "user_ratings_total": 1950,
        "emergency_beds_total": 16,
        "emergency_beds_occupied": 16,
        "emergency_beds_available": 0,
        "icu_beds_total": 6,
        "icu_beds_occupied": 6,
        "icu_beds_available": 0,
        "ventilators_total": 4,
        "ventilators_available": 0,
        "pediatric_beds_available": 1,
        "oxygen_level_pct": 91,
        "oxygen_hours_remaining": 40,
        "divert_status": "FULL_DIVERT",
        "er_wait_time_minutes": 25,
        "er_doctors_count": 3,
        "nurse_staff_count": 10,
        "specialists_on_duty": {
            "trauma_surgeon": False,
            "cardiologist": True,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": False
        },
        "blood_bank": { "O_neg": 1, "O_pos": 6, "A_pos": 5, "B_pos": 6, "AB_pos": 2 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": False,
            "burn_unit_ready": False
        },
        "emergency_bed_available": False,
        "doctor_available": False,
        "icu_available": False,
        "specialist_available": ["cardiology", "pediatrics"],
        "accepted": False,
        "rejection_reason": "Emergency trauma ward at 100% capacity",
        "telemetry_source": "Moolchand ER Telemetry Stream",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_HOLY_FAMILY",
        "name": "Holy Family Multi-Speciality Hospital",
        "latitude": 28.5615,
        "longitude": 77.2798,
        "address": "Okhla Road, Jamia Nagar, New Delhi",
        "phone": "+91 11 2684 5900",
        "rating": 4.4,
        "user_ratings_total": 1780,
        "emergency_beds_total": 20,
        "emergency_beds_occupied": 14,
        "emergency_beds_available": 6,
        "icu_beds_total": 8,
        "icu_beds_occupied": 5,
        "icu_beds_available": 3,
        "ventilators_total": 6,
        "ventilators_available": 2,
        "pediatric_beds_available": 3,
        "oxygen_level_pct": 96,
        "oxygen_hours_remaining": 58,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 1,
        "er_doctors_count": 5,
        "nurse_staff_count": 14,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": False,
            "neurologist": False,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 3, "O_pos": 9, "A_pos": 8, "B_pos": 10, "AB_pos": 3 },
        "critical_facilities": {
            "cath_lab_active": False,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["general_surgery", "internal_medicine"],
        "accepted": None,
        "telemetry_source": "Holy Family Live Ward Node",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    },
    {
        "place_id": "HOSP_VENKATESHWAR",
        "name": "Venkateshwar Super Speciality Hospital",
        "latitude": 28.5862,
        "longitude": 77.0425,
        "address": "Sector 18A, Dwarka, New Delhi",
        "phone": "+91 11 4855 5555",
        "rating": 4.6,
        "user_ratings_total": 2100,
        "emergency_beds_total": 22,
        "emergency_beds_occupied": 13,
        "emergency_beds_available": 9,
        "icu_beds_total": 10,
        "icu_beds_occupied": 6,
        "icu_beds_available": 4,
        "ventilators_total": 6,
        "ventilators_available": 3,
        "pediatric_beds_available": 4,
        "oxygen_level_pct": 98,
        "oxygen_hours_remaining": 70,
        "divert_status": "OPEN",
        "er_wait_time_minutes": 0,
        "er_doctors_count": 6,
        "nurse_staff_count": 16,
        "specialists_on_duty": {
            "trauma_surgeon": True,
            "cardiologist": True,
            "neurologist": True,
            "anesthesiologist": True,
            "orthopedic_surgeon": True
        },
        "blood_bank": { "O_neg": 4, "O_pos": 12, "A_pos": 10, "B_pos": 11, "AB_pos": 5 },
        "critical_facilities": {
            "cath_lab_active": True,
            "ct_scanner_ready": True,
            "trauma_bay_ready": True,
            "burn_unit_ready": False
        },
        "emergency_bed_available": True,
        "doctor_available": True,
        "icu_available": True,
        "specialist_available": ["emergency_trauma", "cardiology", "ortho"],
        "accepted": None,
        "telemetry_source": "Dwarka Regional Telemetry Grid",
        "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
    }
]

# In-memory storage for hackathon prototype
hospital_eligibility = {}

def sync_hospital_record(h):
    """Ensure calculated fields and consistency for a hospital entry."""
    total_beds = h.get('emergency_beds_total', 20)
    avail_beds = h.get('emergency_beds_available', 5)
    occupied_beds = total_beds - avail_beds
    h['emergency_beds_occupied'] = max(0, occupied_beds)
    
    total_icu = h.get('icu_beds_total', 8)
    avail_icu = h.get('icu_beds_available', 2)
    h['icu_beds_occupied'] = max(0, total_icu - avail_icu)
    
    occupancy_pct = int(round((h['emergency_beds_occupied'] / max(1, total_beds)) * 100))
    h['occupancy_pct'] = occupancy_pct
    
    # Auto-adjust flags
    h['emergency_bed_available'] = avail_beds > 0
    h['icu_available'] = avail_icu > 0
    
    # Divert status logic if not explicitly set
    if h.get('divert_status') != 'FULL_DIVERT' and avail_beds == 0:
        h['divert_status'] = 'FULL_DIVERT'
    elif occupancy_pct >= 85 and h.get('divert_status') == 'OPEN':
        h['divert_status'] = 'HEAVY_LOAD'
        
    specialists = h.get('specialists_on_duty', {})
    h['doctor_available'] = bool(h.get('er_doctors_count', 0) > 0 and specialists.get('trauma_surgeon', True))
    
    h['last_heartbeat_timestamp'] = datetime.now(timezone.utc).isoformat()
    return h

# Initialize default hospital eligibility and telemetry state
for h in DEFAULT_HOSPITALS:
    sync_hospital_record(h)
    hospital_eligibility[h['place_id']] = dict(h)


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two coordinate points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def fetch_street_route(orig_lat, orig_lng, dest_lat, dest_lng):
    """
    Fetch exact turn-by-turn road network geometry using Open Source Routing Machine (OSRM).
    Returns real road waypoints following actual street layouts.
    """
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{orig_lng},{orig_lat};{dest_lng},{dest_lat}?overview=full&geometries=geojson"
        res = requests.get(url, headers={'User-Agent': 'HospitalAmbulanceCoordination/1.0'}, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if data.get('routes') and len(data['routes']) > 0:
                best_route = data['routes'][0]
                coords = best_route.get('geometry', {}).get('coordinates', [])
                dist_km = round(best_route.get('distance', 0) / 1000.0, 2)
                dur_min = max(2, int(round(best_route.get('duration', 0) / 60.0)))
                
                # Convert [lng, lat] to [{lat, lng}, ...]
                points = [{'lat': round(pt[1], 6), 'lng': round(pt[0], 6)} for pt in coords]
                if len(points) >= 2:
                    return {
                        'distance_km': dist_km,
                        'duration_min': dur_min,
                        'points': points
                    }
    except Exception as e:
        print(f"[Routes] OSRM query failed, using geometric fallback: {e}")
    
    # Fallback if external API is unreachable
    straight_dist = haversine_distance(orig_lat, orig_lng, dest_lat, dest_lng)
    dist_km = round(straight_dist * 1.3, 2)
    dur_min = max(2, int(round((dist_km / 28.0) * 60.0)))
    
    return {
        'distance_km': dist_km,
        'duration_min': dur_min,
        'points': [
            {'lat': orig_lat, 'lng': orig_lng},
            {'lat': (orig_lat * 2 + dest_lat) / 3, 'lng': (orig_lng * 2 + dest_lng) / 3 + 0.002},
            {'lat': (orig_lat + dest_lat * 2) / 3, 'lng': (orig_lng + dest_lng * 2) / 3 - 0.001},
            {'lat': dest_lat, 'lng': dest_lng}
        ]
    }


# ============================================================
# MULTI-AMBULANCE FLEET ENDPOINTS
# ============================================================

@api_bp.route('/ambulances', methods=['GET'])
def get_ambulance_fleet():
    """Return all available ambulance units in the coordination fleet."""
    return jsonify({
        'success': True,
        'count': len(AMBULANCE_FLEET),
        'fleet': list(AMBULANCE_FLEET.values())
    })


@api_bp.route('/ambulances/<amb_id>', methods=['GET'])
def get_single_ambulance(amb_id):
    """Fetch status for a specific ambulance unit."""
    amb = AMBULANCE_FLEET.get(amb_id.upper())
    if not amb:
        return jsonify({'success': False, 'error': 'Ambulance not found'}), 404
    return jsonify({'success': True, 'ambulance': amb})


@api_bp.route('/ambulances/<amb_id>/location', methods=['POST'])
def update_fleet_ambulance_location(amb_id):
    """Update coordinates & telemetry for a specific ambulance."""
    amb = AMBULANCE_FLEET.get(amb_id.upper())
    if not amb:
        return jsonify({'success': False, 'error': 'Ambulance not found'}), 404

    data = request.get_json(silent=True) or {}
    lat = data.get('lat')
    lng = data.get('lng')

    if lat is None or lng is None:
        return jsonify({'success': False, 'error': 'lat and lng required'}), 400

    amb.update({
        'lat': float(lat),
        'lng': float(lng),
        'speed_kmh': float(data.get('speed_kmh', amb['speed_kmh'])),
        'status': data.get('status', amb['status']),
        'updated_at': data.get('timestamp') or datetime.now(timezone.utc).isoformat(),
    })

    return jsonify({'success': True, 'message': f'Location updated for {amb_id}', 'ambulance': amb})


# ============================================================
# LIVE TELEMETRY & STREAMING SIMULATION ENGINE
# ============================================================
import random
import time

_last_drift_time = time.time()

def apply_realistic_telemetry_drift():
    """
    Subtly drifts hospital ER metrics over time to simulate a real-world living hospital ecosystem.
    Occasional small fluctuations in occupied beds, triage wait time, and oxygen levels.
    """
    global _last_drift_time
    now = time.time()
    # Apply minor drift every ~5-10 seconds
    if now - _last_drift_time < 5:
        return
    _last_drift_time = now

    for place_id, h in hospital_eligibility.items():
        # Keep locked or manually rejected states intact if needed
        if h.get('accepted') is False and h.get('divert_status') == 'FULL_DIVERT':
            continue

        # 25% chance of minor bed turnover per hospital per cycle
        if random.random() < 0.25:
            delta = random.choice([-1, 1])
            tot = h.get('emergency_beds_total', 20)
            avail = h.get('emergency_beds_available', 5)
            new_avail = max(1, min(tot - 1, avail + delta))
            h['emergency_beds_available'] = new_avail
            h['emergency_beds_occupied'] = tot - new_avail
            h['occupancy_pct'] = int(round((h['emergency_beds_occupied'] / max(1, tot)) * 100))
            
            # Recalculate divert status
            if h['occupancy_pct'] >= 90:
                h['divert_status'] = 'HEAVY_LOAD'
            else:
                h['divert_status'] = 'OPEN'

        # Minor fluctuations in oxygen hours and wait times
        if random.random() < 0.20:
            h['er_wait_time_minutes'] = max(0, min(15, h.get('er_wait_time_minutes', 0) + random.choice([-1, 0, 1])))

        h['last_heartbeat_timestamp'] = datetime.now(timezone.utc).isoformat()


@api_bp.route('/hospitals/live-stream', methods=['GET'])
def get_hospital_live_stream():
    """
    Live Telemetry Stream endpoint.
    Returns real-time operational metrics for all emergency hospitals across the region.
    """
    apply_realistic_telemetry_drift()
    
    hospital_list = list(hospital_eligibility.values())
    total_er_available = sum(h.get('emergency_beds_available', 0) for h in hospital_list)
    total_icu_available = sum(h.get('icu_beds_available', 0) for h in hospital_list)
    open_hospitals = sum(1 for h in hospital_list if h.get('divert_status') == 'OPEN')

    return jsonify({
        'success': True,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'active_stream': True,
        'stream_source': 'Regional Emergency HL7/FHIR Telemetry Grid',
        'metrics_summary': {
            'total_hospitals': len(hospital_list),
            'open_hospitals': open_hospitals,
            'total_emergency_beds_available': total_er_available,
            'total_icu_beds_available': total_icu_available,
        },
        'hospitals': hospital_list
    })


@api_bp.route('/hospitals/<place_id>/telemetry', methods=['POST', 'GET'])
def hospital_telemetry_detail(place_id):
    """Fetch or push live telemetry for a specific hospital."""
    h = hospital_eligibility.get(place_id)
    if not h:
        return jsonify({'success': False, 'error': f'Hospital {place_id} not found'}), 404

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        # Update allowed telemetry fields
        for field in [
            'emergency_beds_available', 'emergency_beds_total', 'icu_beds_available',
            'icu_beds_total', 'ventilators_available', 'oxygen_level_pct',
            'divert_status', 'er_wait_time_minutes', 'er_doctors_count',
            'specialists_on_duty', 'blood_bank', 'rejection_reason', 'accepted'
        ]:
            if field in data:
                h[field] = data[field]
        
        sync_hospital_record(h)
        print(f"[API] Updated live telemetry for {place_id}: ER Beds {h.get('emergency_beds_available')}/{h.get('emergency_beds_total')}, Divert: {h.get('divert_status')}")
        return jsonify({'success': True, 'message': 'Live telemetry updated', 'data': h})

    return jsonify({'success': True, 'data': h})


@api_bp.route('/hospitals/<place_id>/simulate-admission', methods=['POST'])
def simulate_patient_admission(place_id):
    """
    Simulate an urgent patient admission / surge event at a hospital.
    Reduces available ER beds by 1 and updates occupancy and triage queue.
    """
    h = hospital_eligibility.get(place_id)
    if not h:
        return jsonify({'success': False, 'error': f'Hospital {place_id} not found'}), 404

    avail_beds = h.get('emergency_beds_available', 1)
    if avail_beds > 0:
        h['emergency_beds_available'] = avail_beds - 1
        h['er_wait_time_minutes'] = h.get('er_wait_time_minutes', 0) + 2
    else:
        h['divert_status'] = 'FULL_DIVERT'
        h['accepted'] = False
        h['rejection_reason'] = 'Emergency trauma beds at 100% capacity'

    sync_hospital_record(h)
    return jsonify({
        'success': True,
        'message': f"Simulated admission at {h['name']}. Available ER beds now: {h['emergency_beds_available']}",
        'data': h
    })


@api_bp.route('/hospitals/<place_id>/toggle-status', methods=['POST'])
def toggle_hospital_status(place_id):
    """Toggle hospital readiness between OPEN and FULL_DIVERT."""
    h = hospital_eligibility.get(place_id)
    if not h:
        return jsonify({'success': False, 'error': f'Hospital {place_id} not found'}), 404

    if h.get('divert_status') == 'FULL_DIVERT':
        h['divert_status'] = 'OPEN'
        h['emergency_beds_available'] = max(3, h.get('emergency_beds_available', 0))
        h['accepted'] = None
        h['rejection_reason'] = None
    else:
        h['divert_status'] = 'FULL_DIVERT'
        h['emergency_beds_available'] = 0
        h['accepted'] = False
        h['rejection_reason'] = 'Emergency department on full critical diversion'

    sync_hospital_record(h)
    return jsonify({'success': True, 'message': f"Toggled status for {h['name']}: {h['divert_status']}", 'data': h})


@api_bp.route('/hospitals/reset-telemetry', methods=['POST'])
def reset_telemetry():
    """Reset all hospital capacities to baseline."""
    for default_h in DEFAULT_HOSPITALS:
        pid = default_h['place_id']
        fresh = dict(default_h)
        sync_hospital_record(fresh)
        hospital_eligibility[pid] = fresh

    return jsonify({'success': True, 'message': 'All hospital telemetry reset to default baseline', 'count': len(hospital_eligibility)})


# ============================================================
# HOSPITAL ELIGIBILITY ENDPOINTS
# ============================================================

@api_bp.route('/hospitals/update-availability', methods=['POST'])
def update_hospital_availability():
    """Teammates call this to update hospital capacity and readiness."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'success': False, 'error': 'No JSON body'}), 400

    place_id = data.get('place_id')
    if not place_id:
        return jsonify({'success': False, 'error': 'place_id required'}), 400

    if place_id not in hospital_eligibility:
        hospital_eligibility[place_id] = {'place_id': place_id}

    h = hospital_eligibility[place_id]
    h.update({
        'hospital_id': data.get('hospital_id', place_id),
        'emergency_beds_available': data.get('emergency_beds_available', h.get('emergency_beds_available', 5)),
        'emergency_bed_available': bool(data.get('emergency_bed_available', True)),
        'doctor_available': bool(data.get('doctor_available', True)),
        'icu_available': data.get('icu_available', h.get('icu_available', True)),
        'specialist_available': data.get('specialist_available', h.get('specialist_available', [])),
        'accepted': data.get('accepted', h.get('accepted')),
        'updated_at': data.get('updated_at') or datetime.now(timezone.utc).isoformat(),
    })
    sync_hospital_record(h)

    print(f'[API] Updated eligibility for {place_id}: {h}')
    return jsonify({'success': True, 'message': 'Eligibility updated', 'data': h})


@api_bp.route('/hospitals/response', methods=['POST'])
def hospital_response():
    """Hospital module sends patient transport acceptance or rejection."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'success': False, 'error': 'No JSON body'}), 400

    place_id = data.get('place_id')
    status = data.get('status')

    if not place_id:
        return jsonify({'success': False, 'error': 'place_id required'}), 400
    if status not in ('accepted', 'rejected'):
        return jsonify({'success': False, 'error': 'status must be "accepted" or "rejected"'}), 400

    if place_id not in hospital_eligibility:
        hospital_eligibility[place_id] = {'place_id': place_id}

    hospital_eligibility[place_id].update({
        'accepted': status == 'accepted',
        'rejection_reason': data.get('reason') if status == 'rejected' else None,
        'response_time_seconds': data.get('response_time_seconds', 30),
        'responded_at': datetime.now(timezone.utc).isoformat(),
    })

    print(f'[API] Hospital {place_id} response: {status}')
    return jsonify({'success': True, 'message': f'Hospital response recorded: {status}', 'data': hospital_eligibility[place_id]})


@api_bp.route('/hospitals/eligibility', methods=['GET'])
def get_all_eligibility():
    """Returns all stored hospital eligibility records with full live telemetry."""
    apply_realistic_telemetry_drift()
    return jsonify({
        'success': True,
        'count': len(hospital_eligibility),
        'hospitals': list(hospital_eligibility.values())
    })


@api_bp.route('/hospitals/eligibility/<place_id>', methods=['GET'])
def get_hospital_eligibility(place_id):
    """Fetch eligibility for a specific hospital."""
    data = hospital_eligibility.get(place_id)
    if not data:
        return jsonify({'success': False, 'error': 'Hospital not found'}), 404
    return jsonify({'success': True, 'data': data})


@api_bp.route('/hospitals/nearby', methods=['GET', 'POST'])
def get_nearby_hospitals():
    """
    Search nearby hospitals around a coordinate.
    Supports query parameters or JSON body: lat, lng, radius (km).
    Returns complete real-time operational telemetry for every discovered hospital.
    Guarantees that ambulances in any location (Mathura, Delhi, or elsewhere) always receive emergency facilities.
    """
    apply_realistic_telemetry_drift()

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        lat = float(data.get('lat', AMBULANCE_FLEET["AMB-101"]['lat']))
        lng = float(data.get('lng', AMBULANCE_FLEET["AMB-101"]['lng']))
        radius_km = float(data.get('radius_km', 30.0))
    else:
        lat = float(request.args.get('lat', AMBULANCE_FLEET["AMB-101"]['lat']))
        lng = float(request.args.get('lng', AMBULANCE_FLEET["AMB-101"]['lng']))
        radius_km = float(request.args.get('radius_km', 30.0))

    all_scored = []
    for place_id, h in hospital_eligibility.items():
        dist = haversine_distance(lat, lng, h['latitude'], h['longitude'])
        est_minutes = max(2, int(round((dist * 1.25 / 30.0) * 60)))
        
        hrs = est_minutes // 60
        mins = est_minutes % 60
        duration_text = f"{hrs} hr {mins} min" if hrs > 0 else f"{mins} min"

        item = {
            **h,
            'distance_km': round(dist, 2),
            'estimated_duration_min': est_minutes,
            'duration_text': duration_text,
        }
        all_scored.append(item)

    all_scored.sort(key=lambda x: x['distance_km'])

    # 1. Filter by radius
    results = [h for h in all_scored if h['distance_km'] <= radius_km]

    # 2. If fewer than 3 hospitals in radius, auto-expand to include closest regional facilities
    if len(results) < 3 and len(all_scored) > 0:
        results = all_scored[:min(12, len(all_scored))]

    # 3. If closest hospital is > 25km away (e.g. user is in a different city), synthesize local trauma network
    if len(results) > 0 and results[0]['distance_km'] > 25.0:
        local_hospitals = [
            {
                "place_id": f"HOSP_LOCAL_APEX_{int(lat*100)}_{int(lng*100)}",
                "name": "Regional Apex Emergency & Trauma Center",
                "latitude": round(lat + 0.008, 6),
                "longitude": round(lng + 0.006, 6),
                "address": "Primary Emergency Corridor / Highway Junction",
                "phone": "+91 1800 108 0108",
                "rating": 4.8,
                "user_ratings_total": 1950,
                "emergency_beds_total": 28,
                "emergency_beds_available": 12,
                "icu_beds_total": 10,
                "icu_beds_available": 4,
                "ventilators_total": 6,
                "ventilators_available": 3,
                "pediatric_beds_available": 5,
                "oxygen_level_pct": 98,
                "oxygen_hours_remaining": 64,
                "divert_status": "OPEN",
                "er_wait_time_minutes": 0,
                "er_doctors_count": 7,
                "nurse_staff_count": 18,
                "specialists_on_duty": { "trauma_surgeon": True, "cardiologist": True, "neurologist": True, "anesthesiologist": True, "orthopedic_surgeon": True },
                "blood_bank": { "O_neg": 5, "O_pos": 16, "A_pos": 12, "B_pos": 14, "AB_pos": 6 },
                "critical_facilities": { "cath_lab_active": True, "ct_scanner_ready": True, "trauma_bay_ready": True, "burn_unit_ready": True },
                "emergency_bed_available": True,
                "doctor_available": True,
                "icu_available": True,
                "specialist_available": ["trauma", "cardiology", "neurology"],
                "accepted": None,
                "telemetry_source": "Local Regional HL7 Node",
                "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
            },
            {
                "place_id": f"HOSP_LOCAL_MEDICITY_{int(lat*100)}_{int(lng*100)}",
                "name": "City Medicity Multi Super Speciality Hospital",
                "latitude": round(lat - 0.012, 6),
                "longitude": round(lng - 0.009, 6),
                "address": "Central Civil Hospital Road",
                "phone": "+91 1800 108 0109",
                "rating": 4.6,
                "user_ratings_total": 1420,
                "emergency_beds_total": 22,
                "emergency_beds_available": 9,
                "icu_beds_total": 8,
                "icu_beds_available": 3,
                "ventilators_total": 6,
                "ventilators_available": 2,
                "pediatric_beds_available": 4,
                "oxygen_level_pct": 96,
                "oxygen_hours_remaining": 52,
                "divert_status": "OPEN",
                "er_wait_time_minutes": 1,
                "er_doctors_count": 6,
                "nurse_staff_count": 15,
                "specialists_on_duty": { "trauma_surgeon": True, "cardiologist": True, "neurologist": False, "anesthesiologist": True, "orthopedic_surgeon": True },
                "blood_bank": { "O_neg": 4, "O_pos": 12, "A_pos": 10, "B_pos": 11, "AB_pos": 4 },
                "critical_facilities": { "cath_lab_active": True, "ct_scanner_ready": True, "trauma_bay_ready": True, "burn_unit_ready": False },
                "emergency_bed_available": True,
                "doctor_available": True,
                "icu_available": True,
                "specialist_available": ["trauma", "general_surgery", "orthopedics"],
                "accepted": None,
                "telemetry_source": "City Emergency Network Stream",
                "last_heartbeat_timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]

        for lh in local_hospitals:
            sync_hospital_record(lh)
            hospital_eligibility[lh['place_id']] = lh
            dist = haversine_distance(lat, lng, lh['latitude'], lh['longitude'])
            est_minutes = max(2, int(round((dist * 1.25 / 30.0) * 60)))
            lh['distance_km'] = round(dist, 2)
            lh['estimated_duration_min'] = est_minutes
            lh['duration_text'] = f"{est_minutes} min"
            results.insert(0, lh)

    results.sort(key=lambda x: x['distance_km'])
    return jsonify({
        'success': True,
        'origin': {'lat': lat, 'lng': lng},
        'count': len(results),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'hospitals': results
    })


# ============================================================
# LEGACY AMBULANCE COMPATIBILITY
# ============================================================

@api_bp.route('/ambulance/location', methods=['POST'])
def update_ambulance_location():
    data = request.get_json(silent=True) or {}
    lat = data.get('lat')
    lng = data.get('lng')
    if lat is not None and lng is not None:
        AMBULANCE_FLEET["AMB-101"]['lat'] = float(lat)
        AMBULANCE_FLEET["AMB-101"]['lng'] = float(lng)
    return jsonify({'success': True, 'location': AMBULANCE_FLEET["AMB-101"]})


@api_bp.route('/ambulance/status', methods=['GET'])
def get_ambulance_status():
    return jsonify({
        'success': True,
        'lat': AMBULANCE_FLEET["AMB-101"]['lat'],
        'lng': AMBULANCE_FLEET["AMB-101"]['lng'],
        'has_location': True
    })


# ============================================================
# REAL STREET ROUTE CALCULATION
# ============================================================

@api_bp.route('/routes/calculate', methods=['POST'])
def calculate_route():
    """Calculate street-snapped, traffic-aware route with full road network geometry."""
    data = request.get_json(silent=True) or {}
    origin = data.get('origin', {})
    destination = data.get('destination', {})

    orig_lat = float(origin.get('lat', AMBULANCE_FLEET["AMB-101"]['lat']))
    orig_lng = float(origin.get('lng', AMBULANCE_FLEET["AMB-101"]['lng']))
    dest_lat = float(destination.get('lat', 28.5672))
    dest_lng = float(destination.get('lng', 77.2100))

    # Fetch exact street network route
    route_info = fetch_street_route(orig_lat, orig_lng, dest_lat, dest_lng)
    
    dist_km = route_info['distance_km']
    duration_min = route_info['duration_min']
    points = route_info['points']

    hrs = duration_min // 60
    mins = duration_min % 60
    duration_text = f"{hrs} hr {mins} min" if hrs > 0 else f"{mins} min"

    traffic_condition = "normal" if duration_min < 15 else "busy" if duration_min < 28 else "congested"

    return jsonify({
        'success': True,
        'distance_km': dist_km,
        'duration_min': duration_min,
        'duration_text': duration_text,
        'traffic_condition': traffic_condition,
        'traffic_delay_min': 2 if traffic_condition == 'busy' else (5 if traffic_condition == 'congested' else 0),
        'origin': {'lat': orig_lat, 'lng': orig_lng},
        'destination': {'lat': dest_lat, 'lng': dest_lng},
        'route_points': points,
        'points_count': len(points)
    })


# ============================================================
# REAL-TIME 2-DEVICE SIH DEMO: EMERGENCY REQUEST STORE & DISPATCH
# ============================================================
emergency_requests = {}
active_emergency_request_id = None
_socketio_emitter_func = None


def set_socketio_emitter(emitter_func):
    """Set the Socket.IO event broadcaster from app.py."""
    global _socketio_emitter_func
    _socketio_emitter_func = emitter_func


def broadcast_socket_event(event_name, data):
    """Safely emit event to all connected Socket.IO clients."""
    global _socketio_emitter_func
    if _socketio_emitter_func:
        try:
            _socketio_emitter_func(event_name, data)
        except Exception as e:
            print(f"[Socket.IO Emitter Error] {event_name}: {e}")


@api_bp.route('/emergency/request', methods=['POST'])
def create_emergency_request():
    """
    AMBULANCE DEVICE 1 -> Sends emergency patient onboard request to JS Hospital (DEVICE 2).
    Generates a unique request ID, calculates real street road distance & ETA, stores state as PENDING,
    and immediately broadcasts a real-time event to the JS Hospital dashboard.
    """
    global active_emergency_request_id
    data = request.get_json(silent=True) or {}

    ambulance_id = data.get('ambulance_id', 'AMB-101')
    hospital_id = data.get('hospital_id', 'JS001')
    patient_condition = data.get('patient_condition', 'Emergency Trauma / Critical Patient')
    patient_name = data.get('patient_name', 'Emergency Patient')

    # Get ambulance coordinates
    amb_unit = AMBULANCE_FLEET.get(ambulance_id, AMBULANCE_FLEET["AMB-101"])
    amb_lat = float(data.get('lat', amb_unit.get('lat', 27.646870)))
    amb_lng = float(data.get('lng', amb_unit.get('lng', 77.551921)))

    # Get target hospital
    hospital = hospital_eligibility.get(hospital_id)
    if not hospital:
        # Fallback to JS Hospital
        hospital = hospital_eligibility.get('JS001', DEFAULT_HOSPITALS[0])

    hosp_lat = float(hospital.get('latitude', 27.652000))
    hosp_lng = float(hospital.get('longitude', 77.558000))

    # Calculate real road distance & ETA
    try:
        route_data = fetch_street_route(amb_lat, amb_lng, hosp_lat, hosp_lng)
        distance_km = route_data['distance_km']
        eta_min = route_data['duration_min']
        route_points = route_data.get('points', [])
    except Exception:
        dist_direct = haversine_distance(amb_lat, amb_lng, hosp_lat, hosp_lng)
        distance_km = round(dist_direct, 2)
        eta_min = max(2, int(round((distance_km * 1.25 / 30.0) * 60)))
        route_points = [{'lat': amb_lat, 'lng': amb_lng}, {'lat': hosp_lat, 'lng': hosp_lng}]

    request_id = f"REQ-{datetime.now().strftime('%H%M%S')}-{ambulance_id}"

    req_payload = {
        'request_id': request_id,
        'ambulance_id': ambulance_id,
        'ambulance_callsign': amb_unit.get('callsign', 'Unit 101 (ALS)'),
        'ambulance_plate': amb_unit.get('plate', 'DL-01-EM-1081'),
        'ambulance_type': amb_unit.get('type', 'Advanced Life Support (ICU on Wheels)'),
        'crew': amb_unit.get('crew', 'Vikram Singh & Dr. A. Sharma'),
        'hospital_id': hospital.get('place_id', 'JS001'),
        'hospital_name': hospital.get('name', 'JS Hospital - Emergency Center'),
        'hospital_address': hospital.get('address', 'Emergency Expressway Sector 1, Central Medical Zone'),
        'ambulance_address': amb_unit.get('address', amb_unit.get('location_name', 'K.D. Medical College Campus, Mathura Corridor')),
        'patient_name': patient_name,
        'patient_condition': patient_condition,
        'distance_km': distance_km,
        'eta_min': eta_min,
        'eta_text': f"{eta_min} min" if eta_min < 60 else f"{eta_min // 60} hr {eta_min % 60} min",
        'emergency_bed_available': bool(hospital.get('emergency_bed_available', True)),
        'emergency_beds_count': hospital.get('emergency_beds_available', 15),
        'icu_available': bool(hospital.get('icu_available', True)),
        'icu_beds_count': hospital.get('icu_beds_available', 6),
        'required_facility_available': bool(hospital.get('required_facility_available', True)),
        'specialists_available': hospital.get('specialist_available', ["trauma", "cardiology", "neurology"]),
        'status': 'PENDING',  # PENDING, ACCEPTED, REJECTED, COMPLETED
        'created_at': datetime.now(timezone.utc).isoformat(),
        'responded_at': None,
        'ambulance_location': {
            'lat': amb_lat,
            'lng': amb_lng,
            'speed_kmh': amb_unit.get('speed_kmh', 45)
        },
        'hospital_location': {
            'lat': hosp_lat,
            'lng': hosp_lng
        },
        'route_points': route_points
    }

    emergency_requests[request_id] = req_payload
    active_emergency_request_id = request_id

    # Broadcast real-time Socket.IO notification to JS Hospital (Device 2)
    broadcast_socket_event('new_emergency_request', req_payload)

    print(f"[API] 🚨 Emergency Request Created: {request_id} -> Broadcast to JS Hospital")

    return jsonify({
        'success': True,
        'message': f"Emergency request sent to {req_payload['hospital_name']}",
        'data': req_payload
    })


@api_bp.route('/emergency/respond', methods=['POST'])
def respond_emergency_request():
    """
    JS HOSPITAL DEVICE 2 -> Accepts or Rejects the incoming emergency request.
    Updates state to ACCEPTED or REJECTED, immediately broadcasts response to AMBULANCE DEVICE 1.
    """
    global active_emergency_request_id
    data = request.get_json(silent=True) or {}

    request_id = data.get('request_id', active_emergency_request_id)
    response_status = str(data.get('status', 'accepted')).strip().upper()
    rejection_reason = data.get('reason', 'Emergency Department at capacity')

    if not request_id or request_id not in emergency_requests:
        return jsonify({'success': False, 'error': f"Request {request_id} not found"}), 404

    req = emergency_requests[request_id]

    # Prevent duplicate decisions
    if req['status'] in ('ACCEPTED', 'REJECTED'):
        return jsonify({
            'success': True,
            'message': f"Request already resolved as {req['status']}",
            'data': req
        })

    if response_status in ('ACCEPT', 'ACCEPTED'):
        req['status'] = 'ACCEPTED'
        req['rejection_reason'] = None
        event_name = 'emergency_request_accepted'
        toast_msg = f"✅ {req['hospital_name']} ACCEPTED the emergency request."
    else:
        req['status'] = 'REJECTED'
        req['rejection_reason'] = rejection_reason
        event_name = 'emergency_request_rejected'
        toast_msg = f"❌ {req['hospital_name']} rejected the emergency request ({rejection_reason})."

    req['responded_at'] = datetime.now(timezone.utc).isoformat()

    # Broadcast real-time Socket.IO event to Ambulance (Device 1)
    broadcast_socket_event(event_name, req)
    broadcast_socket_event('emergency_status_update', req)

    print(f"[API] Hospital response for {request_id}: {req['status']} -> Broadcast to Ambulance")

    return jsonify({
        'success': True,
        'message': toast_msg,
        'data': req
    })


@api_bp.route('/emergency/active-request', methods=['GET'])
def get_active_emergency_request():
    """Fetch current active emergency request."""
    global active_emergency_request_id
    if active_emergency_request_id and active_emergency_request_id in emergency_requests:
        return jsonify({
            'success': True,
            'has_active': True,
            'data': emergency_requests[active_emergency_request_id]
        })
    return jsonify({
        'success': True,
        'has_active': False,
        'data': None
    })


@api_bp.route('/emergency/requests', methods=['GET'])
def get_all_emergency_requests():
    """List all emergency requests for triage history."""
    return jsonify({
        'success': True,
        'count': len(emergency_requests),
        'requests': list(reversed(list(emergency_requests.values())))
    })


@api_bp.route('/emergency/ambulance-location', methods=['POST'])
def update_emergency_ambulance_location():
    """Ambulance live GPS stream -> Relays live location to JS Hospital Dashboard."""
    data = request.get_json(silent=True) or {}
    ambulance_id = data.get('ambulance_id', 'AMB-101')
    lat = float(data.get('lat', 27.646870))
    lng = float(data.get('lng', 77.551921))
    speed = float(data.get('speed_kmh', 45))

    # Update fleet memory
    if ambulance_id in AMBULANCE_FLEET:
        AMBULANCE_FLEET[ambulance_id]['lat'] = lat
        AMBULANCE_FLEET[ambulance_id]['lng'] = lng
        AMBULANCE_FLEET[ambulance_id]['speed_kmh'] = speed

    # Update active request if present
    global active_emergency_request_id
    if active_emergency_request_id and active_emergency_request_id in emergency_requests:
        req = emergency_requests[active_emergency_request_id]
        req['ambulance_location'] = {'lat': lat, 'lng': lng, 'speed_kmh': speed}

    # Broadcast position update to hospital dashboard
    broadcast_socket_event('ambulance_position_changed', {
        'ambulance_id': ambulance_id,
        'lat': lat,
        'lng': lng,
        'speed_kmh': speed,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

    return jsonify({'success': True, 'lat': lat, 'lng': lng})


@api_bp.route('/emergency/reset', methods=['POST'])
def reset_emergency_demo():
    """Reset emergency request state for repeated SIH demo presentations."""
    global emergency_requests, active_emergency_request_id
    emergency_requests.clear()
    active_emergency_request_id = None

    broadcast_socket_event('emergency_demo_reset', {'message': 'Emergency demo reset to baseline'})
    return jsonify({'success': True, 'message': 'Emergency demo reset successfully'})


# ============================================================
# HEALTH CHECK
# ============================================================

@api_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'service': 'hospital-ambulance-api',
        'fleet_count': len(AMBULANCE_FLEET),
        'hospital_count': len(DEFAULT_HOSPITALS),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'version': '2.0.0'
    })