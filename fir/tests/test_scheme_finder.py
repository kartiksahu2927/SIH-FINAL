import importlib
import uuid

from fastapi.testclient import TestClient

from mediroute.tinyfish import TinyFishError


PASSWORD = "MediRoute!2026"


def login(client: TestClient, account: str) -> dict:
    response = client.post("/api/auth/login", json={"email": f"{account}@mediroute.demo", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_scheme_discovery_review_and_filters(monkeypatch):
    app_module = importlib.import_module("mediroute.app")
    suffix = uuid.uuid4().hex[:8]

    async def fake_discovery(keyword, state, category, beneficiary):
        return [
            {
                "name": "Finder Maternity Scheme",
                "normalized_name": "finder maternity scheme",
                "description": "Published maternity support information.",
                "government_authority": "State Health Department",
                "classification": "STATE",
                "geographic_coverage": "Uttar Pradesh",
                "benefits": ["maternity care"],
                "eligibility": ["Residents of the state"],
                "beneficiary_categories": ["women"],
                "income_conditions": None,
                "required_documents": ["Published identity document"],
                "application_process": "Apply through the official portal.",
                "official_application_url": f"https://schemes.gov.in/finder/{suffix}/apply",
                "official_information_url": f"https://schemes.gov.in/finder/{suffix}/maternity",
                "helpline": None,
                "source_urls": [f"https://schemes.gov.in/finder/{suffix}/maternity"],
                "source_evidence": [],
                "search_query": f"finder maternity {suffix}",
            },
            {
                "name": "Finder Insurance Scheme",
                "normalized_name": "finder insurance scheme",
                "description": "Published insurance support information.",
                "government_authority": "Central Health Department",
                "classification": "CENTRAL",
                "geographic_coverage": "Bihar",
                "benefits": ["health insurance"],
                "eligibility": ["Published family eligibility"],
                "beneficiary_categories": ["families"],
                "income_conditions": None,
                "required_documents": [],
                "application_process": "Apply through the official portal.",
                "official_application_url": f"https://schemes.gov.in/finder/{suffix}/insurance-apply",
                "official_information_url": f"https://schemes.gov.in/finder/{suffix}/insurance",
                "helpline": None,
                "source_urls": [f"https://schemes.gov.in/finder/{suffix}/insurance"],
                "source_evidence": [],
                "search_query": f"finder insurance {suffix}",
            },
            {
                "name": "Finder Pending Scheme",
                "normalized_name": "finder pending scheme",
                "description": "Pending record must not appear to patients.",
                "government_authority": "Government Health Department",
                "classification": "CENTRAL",
                "geographic_coverage": "India",
                "benefits": ["pending support"],
                "eligibility": [],
                "beneficiary_categories": [],
                "income_conditions": None,
                "required_documents": [],
                "application_process": None,
                "official_application_url": None,
                "official_information_url": f"https://schemes.gov.in/finder/{suffix}/pending",
                "helpline": None,
                "source_urls": [f"https://schemes.gov.in/finder/{suffix}/pending"],
                "source_evidence": [],
                "search_query": f"finder pending {suffix}",
            },
        ]

    monkeypatch.setattr(app_module.tinyfish_client, "discover_schemes", fake_discovery)
    with TestClient(app_module.app) as client:
        government_headers = login(client, "government")
        patient_headers = login(client, "patient")

        discovered = client.post("/api/healthcare-schemes/discover", headers=government_headers, json={"keyword": f"finder-{suffix}"})
        assert discovered.status_code == 200, discovered.text
        assert discovered.json()["count"] == 3
        assert all(item["verification_status"] == "PENDING_REVIEW" for item in discovered.json()["items"])

        for item in discovered.json()["items"][:2]:
            reviewed = client.patch(
                f"/api/healthcare-schemes/{item['id']}/review",
                headers=government_headers,
                json={"status": "APPROVED", "active_status": "ACTIVE"},
            )
            assert reviewed.status_code == 200, reviewed.text

        def search(query: str):
            response = client.get(f"/api/healthcare-schemes?{query}", headers=patient_headers)
            assert response.status_code == 200, response.text
            return response.json()

        assert search("keyword=maternity care")["total"] == 1
        assert search("state=Uttar%20Pradesh")["total"] >= 1
        assert search("category=maternity care")["total"] == 1
        assert search("beneficiary=women")["total"] == 1
        assert search("category=maternity care&beneficiary=women")["total"] == 1
        assert search("category=maternity care&beneficiary=families")["total"] == 0
        assert search("keyword=pending support")["total"] == 0


def test_scheme_discovery_provider_failure_is_not_empty_success(monkeypatch):
    app_module = importlib.import_module("mediroute.app")

    async def fail_discovery(*args, **kwargs):
        raise TinyFishError("provider unavailable", "UNAVAILABLE", True)

    monkeypatch.setattr(app_module.tinyfish_client, "discover_schemes", fail_discovery)
    with TestClient(app_module.app) as client:
        government_headers = login(client, "government")
        response = client.post(
            "/api/healthcare-schemes/discover",
            headers=government_headers,
            json={"keyword": f"provider-failure-{uuid.uuid4().hex[:8]}"},
        )
        assert response.status_code == 502
        assert "provider unavailable" in response.json()["detail"]
