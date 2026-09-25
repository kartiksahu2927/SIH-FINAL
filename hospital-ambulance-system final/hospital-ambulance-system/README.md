# 🚑 Hospital & Ambulance Coordination System

> **Smart India Hackathon 2026** — Maps & Location / Navigation Module

A real-time hospital and ambulance coordination platform designed to reduce
the time required to find a suitable hospital during medical emergencies.

---

## 🏗️ Architecture (Module Boundary)

```
┌──────────────────────────────────────────────────────────────┐
│  THIS MODULE (Maps & Location)                               │
│  • Ambulance GPS tracking                                    │
│  • Nearby hospital search (Places API)                       │
│  • Traffic-aware route calculation (Routes API)              │
│  • Hospital eligibility filtering                            │
│  • Hospital selection algorithm                              │
│  • Dynamic route updates                                     │
│                                                              │
│  API interfaces provided for teammate integration:           │
│    POST /api/hospitals/update-availability                   │
│    POST /api/hospitals/response                              │
│    GET  /api/ambulance/status                                │
│    ...  (see API_DOCS.md)                                    │
├──────────────────────────────────────────────────────────────┤
│  TEAMMATE MODULES (not in this repo)                         │
│  • Hospital availability / database                          │
│  • Emergency request management                              │
│  • Hospital dashboard / acceptance UI                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
hospital-ambulance-system/
│
├── backend/
│   ├── app.py                 # Flask application (serves frontend + APIs)
│   ├── routes.py              # API route definitions
│   ├── location_service.py    # Geolocation / GPS utilities
│   ├── maps_service.py        # Google Maps, Places, Routes API calls
│   ├── hospital_service.py    # Hospital filtering & selection logic
│   └── requirements.txt       # Python dependencies
│
├── frontend/
│   ├── index.html             # Main dashboard
│   ├── css/
│   │   └── style.css          # Dashboard styles
│   └── js/
│       ├── config.js          # API key (gitignored)
│       ├── config.example.js  # Template for config.js
│       ├── map.js             # Google Map init & helpers
│       ├── location.js        # Ambulance geolocation (Stage 2+)
│       └── dashboard.js       # UI updates, panels (later stages)
│
├── .env                       # Environment variables (gitignored)
├── .env.example               # Template for .env
├── .gitignore
└── README.md                  # This file
```

---

## 🚀 Quick Start

### 1. Clone / Download

```bash
cd hospital-ambulance-system
```

### 2. Set up the Google Maps API Key

See the **Google Maps API Key Setup** section below.

```bash
# Copy config template
cp frontend/js/config.example.js frontend/js/config.js
# Paste your real API key into frontend/js/config.js

# Copy env template
cp .env.example .env
# Paste your real API key into .env (for backend REST calls)
```

### 3. Create a Python Virtual Environment

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 4. Install Python Packages

```bash
pip install -r requirements.txt
```

### 5. Run the Backend

```bash
python app.py
```

### 6. Open in Browser

Navigate to **http://localhost:5000**

---

## 🔑 Google Maps API Key Setup

> ⚠️ **Do not skip this step** — the map will not load without a valid key.

### Step-by-step:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Sign in with your Google account.
3. Create a **new project** (name it e.g. `sih-ambulance-coordination`).
4. Navigate to **APIs & Services → Library**.
5. Search for and **enable** these APIs:
   - **Maps JavaScript API** (required for the map)
   - **Places API (New)** (required for nearby hospital search)
   - **Routes API** (required for route/ETA calculation)
6. Navigate to **APIs & Services → Credentials**.
7. Click **Create Credentials → API Key**.
8. **Copy** the generated key.

### Restrict the Key (Important!)

1. Click the newly created key to edit it.
2. Under **API restrictions**, select **Restrict key**:
   - `Maps JavaScript API`
   - `Places API (New)`
   - `Routes API`
3. Under **Application restrictions**:
   - **HTTP referrers** (for frontend key):
     - `http://localhost:5000/*`
     - `http://localhost:*/*`
   - Or **IP addresses** (for backend key).
4. Click **Save**.

### Use Two Keys (Recommended for Production):

| Key | Used By | Restriction |
|-----|---------|-------------|
| Frontend key | `frontend/js/config.js` | HTTP referrer |
| Backend key | `.env` → `GOOGLE_MAPS_API_KEY` | IP address |

For this prototype, a **single key with referrer restriction** is fine.

### Billing:

- Google Maps Platform offers a **$200/month free credit**.
- Enable **billing** on your Cloud project (required but you won't
  exceed the free tier during development).

---

## 🧪 Testing

### Quick Test (Stage 1):

1. Run `python app.py` from the `backend/` folder.
2. Open http://localhost:5000.
3. You should see a Google Map centred on New Delhi.

### Common Errors:

| Error | Fix |
|-------|-----|
| Map shows grey / won't load | Check API key in `config.js`. Ensure "Maps JavaScript API" is enabled. |
| "This page didn't load Google Maps correctly" | API key invalid or Maps JS API not enabled. Check Cloud Console. |
| `404` errors on static files | Ensure `app.py` is run from `backend/` folder. |
| `ModuleNotFoundError: flask` | Run `pip install -r requirements.txt` inside your venv. |

---

## 🔌 Integration with Teammate Modules

This Maps module provides **REST API endpoints** that your teammates
can call to:

- Send hospital availability data → `POST /api/hospitals/update-availability`
- Receive emergency requests → `GET /api/ambulance/status`
- Send hospital acceptance/rejection → `POST /api/hospitals/response`

See `API_DOCS.md` (coming in later stages) for full endpoint documentation.

---

## 📈 Build Stages

| Stage | What It Adds |
|-------|-------------|
| 1 ✅ | Google Map display |
| 2 | Ambulance GPS location |
| 3 | Ambulance marker on map |
| 4 | Nearby hospital search (Places API) |
| 5 | Hospital markers on map |
| 6 | Route calculation & ETA |
| 7 | Hospital eligibility filtering |
| 8 | Hospital acceptance / selection |
| 9 | Dynamic route updates |
| 10 | Polished emergency dashboard |

---

## 🛠️ Tech Stack

- **Frontend:** HTML5, CSS3, JavaScript (vanilla)
- **Backend:** Python 3, Flask
- **APIs:** Google Maps JavaScript API, Places API (New), Routes API
- **Browser APIs:** Geolocation API

---

## 📝 License

This project was built for **Smart India Hackathon 2026**.
