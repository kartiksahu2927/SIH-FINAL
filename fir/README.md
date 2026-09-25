# MediRoute

MediRoute is a working FastAPI healthcare and emergency-care platform that surrounds the existing read-only ambulance/map application with patient care, hospital operations, driver coordination, accountability, and government monitoring.

## Run locally

1. Create a virtual environment and install dependencies:

   `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`

2. Copy `.env.example` to `.env`, set a strong `JWT_SECRET`, and set `DATABASE_URL` to PostgreSQL for shared/deployed environments. The local demo falls back to `sqlite:///./mediroute.db`.
3. Run `.\.venv\Scripts\uvicorn mediroute.app:app --reload --port 8000`.
4. Open `http://127.0.0.1:8000`. Interactive API documentation is at `/docs` and OpenAPI JSON is at `/openapi.json`.
5. Run the protected map system separately, unchanged, and set `MAP_BRIDGE_URL` to its Flask URL (normally `http://127.0.0.1:5000`).

## Demo credentials

All accounts use `MediRoute!2026`:

| Role | Email |
| --- | --- |
| Patient | `patient@mediroute.demo` |
| Hospital | `hospital@mediroute.demo` |
| Doctor | `doctor@mediroute.demo` |
| Ambulance driver | `driver@mediroute.demo` |
| Admin | `admin@mediroute.demo` |
| Government authority | `government@mediroute.demo` |

## Core capabilities

- JWT authentication, scrypt password hashing, RBAC, rate limiting, validation, audit logs, secure response headers, and access-scoped records.
- Longitudinal records, visits, vitals, consultations, prescriptions, appointments, queues, diagnostics, medicine inventory, high-risk follow-up, and closed-loop referrals.
- Emergency request/acceptance/assignment/location status flows with a read-only Flask map adapter; see [MAP_INTEGRATION.md](MAP_INTEGRATION.md).
- Patient feedback, treatment documentation and comparison, one interaction-bound overall rating, complaints/responses/escalations, and policy-driven quality indicators.
- Patient, Hospital, Ambulance Driver, Admin, and Government Authority dashboards, real-time notification WebSocket transport, six Indian-language dictionaries, responsive medical styling, FastAPI docs, seeded demo data, and end-to-end workflow tests.

## AI safeguards

The digital triage endpoint is labelled a **rule-based risk indicator**, not AI and not a diagnosis. Treatment comparisons and quality indicators are transparent, neutral decision support. `POST /api/ai/analyse` uses an explicitly labelled rule-based fallback by default; setting `AI_PROVIDER=ollama` plus `OLLAMA_MODEL` uses a real local Ollama model through the same provider interface. It is never presented as a clinical decision.

## MediRoute Emergency Assistant

MediRoute includes a dedicated emergency first-aid assistant designed to guide patients and bystanders through critical life-saving steps after an emergency request is submitted and while the ambulance is en route.

### Architecture & Safety Rules:
- **Automatic Contextual Activation:** Only appears after an emergency request is created; does not operate as an ungrounded general-purpose chatbot.
- **Authoritative Protocol Grounding:** Instructions are derived from safety-reviewed WHO and Red Cross first-aid protocols across 15 categories (Bleeding, CPR/Unconsciousness, Choking, Cardiac, Burns, Accidents, Stroke, Pregnancy, Pediatrics, Poisoning, Seizures, etc.).
- **Clinical Protocol Structure:**
  - 🚨 **DO THIS NOW:** Immediate non-harming physical actions.
  - ⏳ **NEXT ACTION:** Monitoring steps while awaiting arrival.
  - ⚠️ **WHAT NOT TO DO:** Critical contraindications (no food/water to unconscious individuals, no moving suspected spinal trauma, no unverified home remedies).
  - 📞 **WHEN TO ESCALATE:** Red-flag symptoms instructing immediate dispatcher contact.
- **Strict Clinical Boundaries:** Never claims to diagnose, never prescribes medication or dosages, and never advises cancelling emergency services.
- **Configurable Emergency Number:** Configured via `EMERGENCY_PHONE_NUMBER` environment variable (defaults to `112` for India) with a clickable dialer button (`tel:112`).
- **Multilingual Support:** Fully translated across 6 Indian languages: English, Hindi (`hi`), Bengali (`bn`), Marathi (`mr`), Tamil (`ta`), and Telugu (`te`).
- **Hands-Free Voice Support:**
  - **Speech-to-Text (`🎤`):** Web Speech Recognition with automatic language detection.
  - **Text-to-Speech (`🔊 Listen` / `⏹ Stop`):** Web Speech Synthesis read-aloud for hands-free first-aid delivery.
- **Prompt Injection Defense:** Rejects attempts to bypass medical safety rules or extract system prompts, automatically falling back to verified clinical guidelines.
- **Clinical First-Aid Log Modal (`📋 First-Aid Log`):** Allows admitting hospital triage staff and ambulance crews to inspect what first-aid actions were provided prior to arrival.

