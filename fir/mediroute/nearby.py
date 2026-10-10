"""Real nearby healthcare discovery through OpenStreetMap Overpass.

Only OSM objects explicitly tagged as hospitals are returned.  The adapter does
not turn schools, universities, businesses, or arbitrary named map markers into
healthcare facilities, and it never invents capacity or travel times.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from .services import haversine_km


OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


class NearbySearchError(RuntimeError):
    pass


def _endpoint_list() -> tuple[str, ...]:
    configured = os.getenv("OSM_OVERPASS_ENDPOINTS", "").strip()
    return tuple(item.strip() for item in configured.split(",") if item.strip()) or OVERPASS_ENDPOINTS


def _bounded_radius(value: float) -> float:
    return min(max(float(value), 1.0), 100.0)


def _overpass_query(latitude: float, longitude: float, radius_km: float) -> str:
    radius_m = int(round(_bounded_radius(radius_km) * 1000))
    # amenity=hospital is the broad, established OSM hospital tag.  The
    # healthcare=hospital branch covers records using the newer healthcare tag.
    return f"""[out:json][timeout:25];
(
  nwr[amenity=hospital](around:{radius_m},{latitude},{longitude});
  nwr[healthcare=hospital](around:{radius_m},{latitude},{longitude});
);
out center tags;"""


def _element_point(element: dict[str, Any]) -> tuple[float, float] | None:
    point = element.get("center") or element
    try:
        latitude, longitude = float(point["lat"]), float(point["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _address(tags: dict[str, Any]) -> str | None:
    if tags.get("addr:full"):
        return str(tags["addr:full"]).strip()
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city") or tags.get("addr:town"),
        tags.get("addr:state"),
        tags.get("addr:postcode"),
    ]
    value = ", ".join(str(item).strip() for item in parts if item and str(item).strip())
    return value or None


def _services(tags: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("healthcare:speciality", "medical_specialty", "emergency"):
        value = tags.get(key)
        if value:
            values.extend(str(value).replace(";", ",").split(","))
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def _normalize_element(element: dict[str, Any], latitude: float, longitude: float) -> dict[str, Any] | None:
    tags = element.get("tags") or {}
    point = _element_point(element)
    name = str(tags.get("name") or tags.get("name:en") or "").strip()
    if not point or not name:
        return None
    # The tag filter is authoritative, but keep this defensive check so a
    # malformed provider response cannot classify an educational venue.
    if str(tags.get("amenity") or "").lower() != "hospital" and str(tags.get("healthcare") or "").lower() != "hospital":
        return None
    osm_type = str(element.get("type") or "unknown")
    osm_id = element.get("id")
    source_url = f"https://www.openstreetmap.org/{quote(osm_type)}/{quote(str(osm_id))}"
    return {
        "place_id": f"osm-{osm_type}-{osm_id}",
        "hospital_id": f"osm-{osm_type}-{osm_id}",
        "name": name,
        "facility_type": "hospital",
        "address": _address(tags),
        "latitude": point[0],
        "longitude": point[1],
        "distance_km": round(haversine_km(latitude, longitude, point[0], point[1]), 2),
        "services": _services(tags),
        "emergency_service_published": str(tags.get("emergency") or "").lower() in {"yes", "24/7", "emergency"},
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "website": tags.get("website") or tags.get("contact:website"),
        "availability_source": "UNKNOWN",
        "availability_status": "Not published by the nearby-search source",
        "emergency_bed_available": None,
        "doctor_available": None,
        "accepted": None,
        "source": "OpenStreetMap Overpass",
        "source_url": source_url,
    }


async def discover_osm_hospitals(latitude: float, longitude: float, radius_km: float) -> list[dict[str, Any]]:
    query = _overpass_query(latitude, longitude, radius_km)
    errors: list[str] = []
    for endpoint in _endpoint_list():
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                response = await client.post(endpoint, content=query, headers={"User-Agent": "MediRoute/2.0 nearby hospital discovery"})
                response.raise_for_status()
                payload = response.json()
            elements = payload.get("elements") if isinstance(payload, dict) else None
            if not isinstance(elements, list):
                raise NearbySearchError("The nearby-search provider returned an invalid response")
            records: dict[str, dict[str, Any]] = {}
            for element in elements:
                if not isinstance(element, dict):
                    continue
                record = _normalize_element(element, latitude, longitude)
                if record:
                    records[record["place_id"]] = record
            return sorted(records.values(), key=lambda item: item["distance_km"])
        except (httpx.HTTPError, ValueError, TypeError, NearbySearchError) as exc:
            errors.append(f"{endpoint}: {exc}")
    raise NearbySearchError("Nearby hospital discovery is temporarily unavailable. " + " | ".join(errors[:2]))
