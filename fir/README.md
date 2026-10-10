# MediRoute

MediRoute is a working FastAPI healthcare and emergency-care platform that surrounds the existing read-only ambulance/map application with patient care, hospital operations, driver coordination, accountability, and government monitoring.

## Run locally

1. Create a virtual environment and install dependencies:

   `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`

2. Copy `.env.example` to `.env`, set a strong `JWT_SECRET`, and set `DATABASE_URL` to PostgreSQL for shared/deployed environments. The local demo falls back to `sqlite:///./mediroute.db`.
3. Run `.\.venv\Scripts\uvicorn mediroute.app:app --reload --port 8000`.
4. Open `http://127.0.0.1:8000`. Interactive API documentation is at `/docs`, OpenAPI JSON is at `/openapi.json`, and health checks are available at `/health`, `/healthz`, and `/api/health`.
5. Run the protected map system separately, unchanged, and set `MAP_BRIDGE_URL` to its Flask URL (normally `http://127.0.0.1:5000`, with its health check at `http://127.0.0.1:5000/api/health`).

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

### Real-time hospital coordination

See [EMERGENCY_COORDINATION.md](EMERGENCY_COORDINATION.md) for the 60-second
hospital response window, JS/KS demo accounts, same-position road-route
comparison, dynamic destination UI, APIs, database additions and the external
map-service destination-interface limitation. Run Uvicorn with `--env-file .env`
to load the local backend settings.

- JWT authentication, scrypt password hashing, RBAC, rate limiting, validation, audit logs, secure response headers, and access-scoped records.
- Longitudinal records, visits, vitals, consultations, prescriptions, appointments, queues, diagnostics, medicine inventory, high-risk follow-up, and closed-loop referrals.
- Emergency request/acceptance/assignment/location status flows with a read-only Flask map adapter; see [MAP_INTEGRATION.md](MAP_INTEGRATION.md).
- Patient feedback, treatment documentation and comparison, one interaction-bound overall rating, complaints/responses/escalations, and policy-driven quality indicators.
- Patient, Hospital, Ambulance Driver, Admin, and Government Authority dashboards, real-time notification WebSocket transport, six Indian-language dictionaries, responsive medical styling, FastAPI docs, seeded demo data, and end-to-end workflow tests.

## TinyFish discovery features

MediRoute uses one backend-only TinyFish client for two source-backed workflows:

- **Facility Discovery** (`ADMIN` and `GOVERNMENT_AUTHORITY`): TinyFish Search finds public facility pages and TinyFish Fetch extracts published details. Results are stored as `facility_discoveries`, remain pending review, and are compared with the trusted `hospitals` table before an authorized reviewer can approve them. Approval creates a trusted hospital with zero declared emergency capacity; it never fabricates capacity or changes the protected map service.
- **Government Healthcare Schemes**: authorized reviewers can research official government sources and review `healthcare_schemes`. Patients can browse only records marked `APPROVED` and `ACTIVE`; missing fields remain unavailable and the UI labels the information as informational rather than an eligibility decision.

### TinyFish configuration

Set the backend environment variable in local `.env` or in the Render service environment:

```bash
TINYFISH_API_KEY=
```

The current TinyFish REST contract uses `X-API-Key`, `GET https://api.search.tinyfish.ai`, and `POST https://api.fetch.tinyfish.ai`. Search and Fetch are used instead of exposing a key to the browser. Search requests are rate-limited and recent identical searches reuse stored records. Search/Fetch limits and account allowances are controlled by TinyFish; check the account wallet/allowance before enabling frequent refreshes.

The application has no Alembic migration system. Its existing startup convention calls `Base.metadata.create_all`, which creates the two new tables (`facility_discoveries` and `healthcare_schemes`) when the service starts against an existing database. Run the service once with the target `DATABASE_URL` before using the new routes.

### New API responsibilities

All routes require the existing bearer JWT. Patients may read approved schemes; only admin/government users may trigger research or review records.

- `POST /api/facility-discoveries/search`
- `GET /api/facility-discoveries`, `GET /api/facility-discoveries/{id}`
- `PATCH /api/facility-discoveries/{id}/review`
- `GET /api/healthcare-schemes` and `GET /api/healthcare-schemes/{id}`
- `POST /api/healthcare-schemes/discover` or `/api/healthcare-schemes/refresh`
- `GET /api/healthcare-schemes/discoveries`
- `PATCH /api/healthcare-schemes/{id}/review`

### Local verification

1. Copy `.env.example` to `.env`, set `JWT_SECRET`, and set `TINYFISH_API_KEY` only if live research is desired.
2. Start `uvicorn mediroute.app:app --reload --port 8000`.
3. Sign in as `government@mediroute.demo` or `admin@mediroute.demo`, open **Facility Discovery**, and submit a location. Review results before approval.
4. Open **Healthcare Schemes** as an authorized user to research/review; sign in as `patient@mediroute.demo` to browse approved records.
5. Without `TINYFISH_API_KEY`, discovery returns a clear `503` configuration error and does not return fabricated results. Ordinary approved-scheme browsing continues to work.

For Render, add `TINYFISH_API_KEY` to the FastAPI service's Environment settings, keep it out of frontend configuration, and deploy/restart once so `create_all` creates the tables. Leave the separate map service environment and deployment unchanged.

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
