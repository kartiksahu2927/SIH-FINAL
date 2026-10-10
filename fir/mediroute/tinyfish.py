"""Small, backend-only TinyFish REST client and source normalizers.

The client intentionally uses TinyFish's documented Search and Fetch APIs rather
than putting credentials or web requests in the browser.  The normalizers only
copy information that appears in a search result or fetched page; unavailable
fields stay ``None``/empty.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

import httpx

from .scheme_quality import sanitize_scheme_candidate, scheme_name_issue, scheme_quality_issue


SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"
FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
OFFICIAL_SCHEME_DOMAINS = "gov.in,nic.in,myscheme.gov.in,pmjay.gov.in,nha.gov.in,benefits.gov.in"
PRIMARY_SCHEME_DOMAINS = "myscheme.gov.in,pmjay.gov.in,nha.gov.in,benefits.gov.in"
GOVERNMENT_SCHEME_DOMAINS = "gov.in,nic.in"
DEFAULT_RESULT_LIMIT = 5


def _int_env(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


def _float_env(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


class TinyFishError(RuntimeError):
    def __init__(self, message: str, code: str = "TINYFISH_ERROR", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_official_source(value: str | None) -> bool:
    if not valid_http_url(value):
        return False
    hostname = (urlparse(value).hostname or "").lower().rstrip(".")
    return (
        hostname.endswith(".gov.in")
        or hostname.endswith(".nic.in")
        or hostname in {"myscheme.gov.in", "pmjay.gov.in", "nha.gov.in"}
    )


def _bounded(value: Any, limit: int = 1200) -> str | None:
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result[:limit] or None


def _label_value(text: str, labels: tuple[str, ...], limit: int = 500) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:^|\n)\s*(?:{label_pattern})\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    return _bounded(match.group(1), limit) if match else None


def _section_lines(text: str, labels: tuple[str, ...], limit: int = 8) -> list[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:^|\n)\s*(?:{label_pattern})\s*:?\s*\n?([\s\S]{{0,2500}})", text, re.IGNORECASE)
    if not match:
        return []
    section = match.group(1)
    section = re.split(r"\n\s*(?:#+|[A-Z][A-Za-z /&-]{2,40})\s*:?\s*\n", section, maxsplit=1)[0]
    values: list[str] = []
    for raw in section.splitlines():
        line = re.sub(r"^\s*[-*•\d.)]+\s*", "", raw).strip()
        if line and len(line) >= 3:
            values.append(line[:500])
        if len(values) >= limit:
            break
    return values


def _phone(text: str) -> str | None:
    mobile_pattern = r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}"
    label_match = re.search(r"(?:phone|telephone|tel|helpline|contact)\s*(?:number|no\.)?\s*[:\-]?\s*(.{0,100})", text, re.IGNORECASE)
    target = label_match.group(1) if label_match else text
    mobile = re.search(mobile_pattern, target)
    if mobile:
        return _bounded(mobile.group(0), 80)
    mobile = re.search(mobile_pattern, text)
    if mobile:
        return _bounded(mobile.group(0), 80)
    landline = re.search(r"(?:0\d{2,4}[\s-]?)?\d{7,8}", target)
    return _bounded(landline.group(0), 80) if landline else None


def _postal_code(text: str) -> str | None:
    match = re.search(r"(?<!\d)\d{6}(?!\d)", text)
    return match.group(0) if match else None


def _coordinates(text: str) -> tuple[float | None, float | None]:
    match = re.search(r"(?:lat(?:itude)?\s*[:=]\s*)(-?\d{1,3}(?:\.\d+)?)\D+(?:lon(?:gitude)?|lng)\s*[:=]\s*(-?\d{1,3}(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return None, None
    try:
        latitude, longitude = float(match.group(1)), float(match.group(2))
    except ValueError:
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    return latitude, longitude


def _known_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _generic_scheme_title(value: str) -> bool:
    normalized = normalize_text(value)
    if not normalized or len(normalized) < 4:
        return True
    generic_titles = {
        "home",
        "homepage",
        "official website",
        "government schemes",
        "healthcare schemes",
        "my scheme",
        "myscheme",
        "scheme details",
        "ministry of health and family welfare",
    }
    return (
        normalized in generic_titles
        or normalized.startswith("home ")
        or normalized.endswith(" home")
        or normalized.endswith(" official website")
    )


def _source_evidence(name: str, source_url: str, snippet: str | None, fields: tuple[str, ...]) -> list[dict[str, str]]:
    excerpt = _bounded(snippet, 700)
    if not excerpt:
        return []
    return [{"field": field, "source_url": source_url, "excerpt": excerpt} for field in fields]


class TinyFishClient:
    """One reusable, timeout-bounded client for both discovery workflows."""

    def __init__(self) -> None:
        self.max_results = _int_env("TINYFISH_MAX_RESULTS", DEFAULT_RESULT_LIMIT, 1, 10)
        self.search_timeout = _float_env("TINYFISH_SEARCH_TIMEOUT_SECONDS", 35, 5, 120)
        self.fetch_timeout = _float_env("TINYFISH_FETCH_TIMEOUT_SECONDS", 150, 10, 180)
        self.fetch_page_timeout_ms = _int_env("TINYFISH_FETCH_PAGE_TIMEOUT_MS", 45000, 1000, 110000)

    @property
    def configured(self) -> bool:
        return bool(os.getenv("TINYFISH_API_KEY", "").strip())

    def _key(self) -> str:
        key = os.getenv("TINYFISH_API_KEY", "").strip()
        if not key:
            raise TinyFishError("TinyFish is not configured. Set TINYFISH_API_KEY on the backend.", "NOT_CONFIGURED")
        return key

    @staticmethod
    def _provider_error(response: httpx.Response, fallback: str) -> tuple[str, str]:
        code = f"HTTP_{response.status_code}"
        message = fallback
        try:
            data = response.json()
            error = data.get("error", data) if isinstance(data, dict) else {}
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                candidate = error.get("message")
                if isinstance(candidate, str) and candidate.strip():
                    message = candidate.strip()
        except (ValueError, TypeError):
            pass
        if code in {"MISSING_API_KEY", "INVALID_API_KEY"}:
            message = "TinyFish rejected the backend API key. Check TINYFISH_API_KEY."
        elif code in {"INSUFFICIENT_CREDITS", "FREE_ALLOWANCE_EXHAUSTED"}:
            message = "TinyFish usage is currently unavailable because the account allowance or wallet is exhausted."
        return code, message[:500]

    async def _search(self, query: str, include_domains: str | None = None, purpose: str | None = None) -> list[dict[str, Any]]:
        key = self._key()
        params: dict[str, Any] = {"query": query, "location": "IN", "language": "en", "page": 0}
        if include_domains:
            params["include_domains"] = include_domains
        if purpose:
            params["purpose"] = purpose[:2000]
        try:
            async with httpx.AsyncClient(timeout=self.search_timeout) as client:
                response = await client.get(SEARCH_ENDPOINT, params=params, headers={"X-API-Key": key})
                if response.status_code >= 400:
                    code, message = self._provider_error(response, "TinyFish search failed.")
                    raise TinyFishError(message, code, response.status_code in {429, 500, 502, 503})
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise TinyFishError("TinyFish search timed out. Try again later.", "TIMEOUT", True) from exc
        except httpx.RequestError as exc:
            raise TinyFishError("TinyFish search could not be reached.", "UNAVAILABLE", True) from exc
        except ValueError as exc:
            raise TinyFishError("TinyFish returned an invalid search response.", "INVALID_RESPONSE") from exc
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise TinyFishError("TinyFish returned an invalid search response.", "INVALID_RESPONSE")
        results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict) or not valid_http_url(item.get("url")):
                continue
            results.append({
                "title": _bounded(item.get("title"), 255) or "Untitled source",
                "snippet": _bounded(item.get("snippet"), 1200),
                "url": item["url"],
                "site_name": _bounded(item.get("site_name"), 150),
            })
        return results

    async def _fetch(self, urls: list[str], purpose: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not urls:
            return [], []
        key = self._key()
        body = {
            "urls": urls[:10],
            "format": "markdown",
            "links": True,
            "ttl": 0,
            "per_url_timeout_ms": self.fetch_page_timeout_ms,
            "purpose": purpose[:2000],
        }
        try:
            async with httpx.AsyncClient(timeout=self.fetch_timeout) as client:
                response = await client.post(FETCH_ENDPOINT, json=body, headers={"X-API-Key": key})
                if response.status_code >= 400:
                    code, message = self._provider_error(response, "TinyFish fetch failed.")
                    raise TinyFishError(message, code, response.status_code in {429, 500, 502, 503})
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise TinyFishError("TinyFish page extraction timed out. Try again later.", "TIMEOUT", True) from exc
        except httpx.RequestError as exc:
            raise TinyFishError("TinyFish page extraction could not be reached.", "UNAVAILABLE", True) from exc
        except ValueError as exc:
            raise TinyFishError("TinyFish returned an invalid fetch response.", "INVALID_RESPONSE") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise TinyFishError("TinyFish returned an invalid fetch response.", "INVALID_RESPONSE")
        pages = [page for page in payload["results"] if isinstance(page, dict) and valid_http_url(page.get("url"))]
        errors = [item for item in payload.get("errors", []) if isinstance(item, dict)]
        if not pages and errors:
            codes = ", ".join(str(item.get("error", "unknown")) for item in errors[:3])
            raise TinyFishError(f"TinyFish could not extract the requested sources ({codes}).", "FETCH_FAILED", True)
        return pages, errors

    async def discover_facilities(self, location: str, facility_type: str | None) -> list[dict[str, Any]]:
        type_phrase = f" {facility_type}" if facility_type else " hospital healthcare facility"
        query = f"{type_phrase} in {location} official address phone services"
        results = (await self._search(query))[: self.max_results]
        pages, _ = await self._fetch([item["url"] for item in results], f"Extract publicly published healthcare facility details for {location}.")
        by_url = {page.get("url"): page for page in pages}
        by_url.update({page.get("final_url"): page for page in pages if valid_http_url(page.get("final_url"))})
        candidates: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for result in results:
            page = by_url.get(result["url"], {})
            text = "\n".join(str(value or "") for value in (page.get("title"), page.get("text"), result.get("title"), result.get("snippet")))
            source_url = page.get("final_url") if valid_http_url(page.get("final_url")) else result["url"]
            name = _bounded(page.get("title") or result.get("title"), 255)
            if not name:
                continue
            name = re.split(r"\s+[|–—]\s+", name, maxsplit=1)[0].strip()
            key = normalize_text(name)
            if not key or key in candidates:
                continue
            latitude, longitude = _coordinates(text)
            detected_type = next((term for term in ("hospital", "community health centre", "primary health centre", "clinic", "diagnostic centre") if term in text.lower()), None)
            address = _label_value(text, ("address", "location", "full address"), 700)
            district = _label_value(text, ("district",), 120)
            state = _label_value(text, ("state",), 120)
            specialties = _known_terms(text, ("cardiology", "neurology", "orthopaedics", "obstetrics", "paediatrics", "general medicine", "emergency medicine", "trauma"))
            services = _known_terms(text, ("emergency", "diagnostics", "laboratory", "pharmacy", "maternity", "icu", "telemedicine", "ambulance", "blood bank"))
            candidates[key] = {
                "search_location": location,
                "requested_facility_type": facility_type,
                "name": name,
                "facility_type": detected_type,
                "address": address,
                "district": district,
                "state": state,
                "postal_code": _postal_code(text),
                "phone": _phone(text),
                "website": source_url,
                "specialties": specialties,
                "services": services,
                "latitude": latitude,
                "longitude": longitude,
                "source_url": source_url,
                "source_urls": [source_url],
                "source_evidence": _source_evidence(name, source_url, page.get("text") or result.get("snippet"), ("name", "address", "phone")),
                "search_query": query,
            }
        return list(candidates.values())

    async def discover_schemes(self, keyword: str | None, state: str | None, category: str | None, beneficiary: str | None) -> list[dict[str, Any]]:
        parts = ["government healthcare scheme", keyword or "health benefits", state or "India", category or "", beneficiary or "", "eligibility benefits application"]
        query = " ".join(part for part in parts if part).strip()
        purpose = "Find current Indian government healthcare schemes and official eligibility/application pages."
        search_specs = [
            (query, PRIMARY_SCHEME_DOMAINS),
            (f"official {query}", GOVERNMENT_SCHEME_DOMAINS),
        ]
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for search_query, domains in search_specs:
            for item in await self._search(search_query, include_domains=domains, purpose=purpose):
                url = item.get("url")
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
        official_results = [item for item in results if is_official_source(item.get("url"))][: min(10, self.max_results * 2)]
        if not official_results:
            return []
        pages, _ = await self._fetch([item["url"] for item in official_results], "Extract government healthcare scheme information from official public sources only.")
        by_url = {page.get("url"): page for page in pages}
        by_url.update({page.get("final_url"): page for page in pages if valid_http_url(page.get("final_url"))})
        candidates: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for result in official_results:
            page = by_url.get(result["url"], {})
            text = "\n".join(str(value or "") for value in (page.get("title"), page.get("description"), page.get("text"), result.get("title"), result.get("snippet")))
            source_url = page.get("final_url") if valid_http_url(page.get("final_url")) else result["url"]
            if not is_official_source(source_url):
                source_url = result["url"]
            scheme_name = _label_value(text, ("scheme name", "programme name", "program name"), 255)
            candidate_titles = [scheme_name, page.get("title"), result.get("title")]
            name = next((
                _bounded(title, 255)
                for title in candidate_titles
                if _bounded(title, 255)
                and not _generic_scheme_title(str(title))
                and not scheme_name_issue(_bounded(title, 255))
            ), None)
            reliable_name = bool(name)
            # Keep an unidentifiable source in the reviewer queue, but never
            # turn its URL, slug, or generated result key into a displayed name.
            name = name or "Unnamed scheme"
            name = re.split(r"\s+[|–—]\s+", name, maxsplit=1)[0].strip()
            key = normalize_text(name) if reliable_name else f"unnamed source {normalize_text(source_url)}"
            if not key or key in candidates:
                continue
            links = [link for link in (page.get("links") or []) if valid_http_url(link) and is_official_source(link)]
            application_url = next((link for link in links if re.search(r"apply|application|register|enrol|portal", link, re.IGNORECASE)), None)
            authority = _label_value(text, ("issuing authority", "government authority", "ministry", "department"), 255)
            classification = _label_value(text, ("scheme type", "classification", "level of government"), 80)
            if not classification:
                lowered = text.lower()
                if "central government" in lowered or "government of india" in lowered:
                    classification = "CENTRAL"
                elif "state government" in lowered:
                    classification = "STATE"
            coverage = _label_value(text, ("coverage", "geographical coverage", "available in", "applicable states"), 255)
            beneficiaries = _known_terms(text, ("women", "children", "senior citizens", "pregnant women", "persons with disabilities", "low-income families", "farmers", "unorganised workers", "families below poverty line"))
            benefits = _section_lines(text, ("benefits", "benefit package", "what you get"))
            benefits.extend(_known_terms(text, ("health insurance", "cashless treatment", "hospitalisation", "maternity care", "outpatient care", "diagnostic services", "financial assistance")))
            unique_benefits: list[str] = []
            seen_benefits: set[str] = set()
            for benefit in benefits:
                normalized_benefit = str(benefit).casefold()
                if normalized_benefit not in seen_benefits:
                    seen_benefits.add(normalized_benefit)
                    unique_benefits.append(str(benefit))
            candidate = {
                "name": name,
                "normalized_name": key,
                "description": _bounded(page.get("description") or result.get("snippet"), 1600),
                "government_authority": authority,
                "classification": classification,
                "geographic_coverage": coverage,
                "benefits": unique_benefits,
                "eligibility": _section_lines(text, ("eligibility", "eligible")),
                "beneficiary_categories": beneficiaries,
                "income_conditions": _label_value(text, ("income limit", "income criteria", "annual income"), 700),
                "required_documents": _section_lines(text, ("documents required", "required documents", "documents")),
                "application_process": _bounded(" ".join(_section_lines(text, ("how to apply", "application process", "apply"), 6)), 1600),
                "official_application_url": application_url,
                "official_information_url": source_url,
                "helpline": _label_value(text, ("helpline", "toll free", "contact"), 255) or _phone(text),
                "source_urls": [source_url],
                "source_evidence": _source_evidence(name, source_url, page.get("text") or result.get("snippet"), ("name", "description", "eligibility", "benefits")),
                "search_query": query,
            }
            candidates[key] = sanitize_scheme_candidate(candidate)
        for candidate in candidates.values():
            if scheme_quality_issue(candidate):
                candidate["verification_status"] = "REVIEW_REQUIRED"
                candidate["active_status"] = "REVIEW_REQUIRED"
        return list(candidates.values())


tinyfish_client = TinyFishClient()
