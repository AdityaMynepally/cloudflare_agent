"""Structured-data (JSON-LD) audit — business-record contradictions and
Vehicle/Product schema completeness on VDP pages.

Parses JSON-LD blocks captured per page by the extension, flattens any
@graph containers, and evaluates the MOST SPECIFIC AND MOST COMPLETE
candidate record of a given kind — never just the first one found.
Empty string/array/object values are treated as absent, not present.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Specificity ranking for identity/business-type records — a narrower
# type ranks higher than a generic one when several candidates exist.
_BUSINESS_TYPE_RANK = {
    "autodealer": 3,
    "automotive business": 3,
    "localbusiness": 2,
    "organization": 1,
}

_BUSINESS_FIELDS = ["name", "telephone", "address", "url"]

# Fields every VDP's Vehicle/Product schema should carry regardless of
# condition. mileageFromOdometer is deliberately NOT here — a new vehicle
# legitimately has none, so it is only checked for used/CPO vehicles (see
# audit_vehicle_structured_data's `is_used` argument). Severity here is
# template- (and condition-) dependent, the same way an absent field can
# be noise on one page and a real gap on another.
_VEHICLE_FIELDS_ALWAYS = ["name", "vehicleIdentificationNumber", "brand", "model"]
_MILEAGE_FIELD = "mileageFromOdometer"


def _is_present(value) -> bool:
    """Empty string, empty array and empty object all count as absent."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _types_of(obj: dict) -> list[str]:
    t = obj.get("@type")
    if t is None:
        return []
    if isinstance(t, list):
        return [str(x).lower() for x in t]
    return [str(t).lower()]


def parse_json_ld_blocks(raw_blocks: list[str]) -> list[dict]:
    """Parse raw JSON-LD text blocks, flattening @graph containers.

    A malformed block is skipped, not raised — one bad script tag must
    never take down the rest of a page's structured data.
    """
    objects: list[dict] = []
    for raw in raw_blocks or []:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                objects.extend(o for o in graph if isinstance(o, dict))
            else:
                objects.append(item)
    return objects


def _specificity(obj: dict) -> int:
    types = _types_of(obj)
    return max((_BUSINESS_TYPE_RANK.get(t, 0) for t in types), default=0)


def _completeness(obj: dict, fields: list[str]) -> int:
    return sum(1 for f in fields if _is_present(obj.get(f)))


def select_primary_business_record(records: list[dict]) -> Optional[dict]:
    """Most specific, then most complete record — never just the first found."""
    if not records:
        return None
    return max(records, key=lambda o: (_specificity(o), _completeness(o, _BUSINESS_FIELDS)))


def _normalize_name(name) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _group_by_identity(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by normalized business name.

    A multi-rooftop dealer group's site can legitimately serve several
    distinct business identities from one domain (e.g. two separate
    franchise names at the same address) — each with its own, internally
    consistent phone/address. Comparing fields ACROSS different identities
    would call that "a contradiction" when it is really just two
    different businesses; grouping by name first keeps the contradiction
    check scoped to records that actually claim to be the SAME business.
    """
    groups: dict[str, list[dict]] = {}
    for r in records:
        key = _normalize_name(r.get("name")) or "(unnamed)"
        groups.setdefault(key, []).append(r)
    return groups


def find_business_contradictions(records: list[dict]) -> list[dict]:
    """Detect records sharing one business name that disagree on telephone/address.

    A contradiction is a more serious finding than a missing field: the
    search engine has to choose which record to believe. Scoped to one
    name-group at a time — see `_group_by_identity`.
    """
    contradictions = []
    for name_key, group in _group_by_identity(records).items():
        if len(group) < 2:
            continue
        for field in ("telephone", "address"):
            values: set[str] = set()
            for r in group:
                v = r.get(field)
                if not _is_present(v):
                    continue
                key = json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v).strip().lower()
                values.add(key)
            if len(values) > 1:
                contradictions.append({
                    "field": field,
                    "business_name": group[0].get("name"),
                    "distinct_values": list(values)[:5],
                    "record_count": len(group),
                })
    return contradictions


def audit_business_structured_data(pages: list[dict]) -> dict:
    """pages: [{source_page, objects}] — JSON-LD objects already parsed per page.

    Aggregates business-identity records across ALL audited pages (the
    record can legitimately live on a contact page or in footer-only
    schema, not only the homepage), selects the primary record, and
    reports contradictions between duplicate records of the SAME business.
    A site serving more than one distinct business name (a multi-rooftop
    dealer group) is reported as `distinct_business_count`, not as a
    contradiction.
    """
    all_records = []
    for page in pages or []:
        for obj in page.get("objects") or []:
            if _specificity(obj) > 0:
                all_records.append({**obj, "_source_page": page.get("source_page")})

    if not all_records:
        return {"status": "unknown", "note": "No business-identity structured data found on any audited page"}

    primary = select_primary_business_record(all_records)
    contradictions = find_business_contradictions(all_records)
    distinct_business_count = len(_group_by_identity(all_records))

    return {
        "status": "measured",
        "record_count": len(all_records),
        "distinct_business_count": distinct_business_count,
        "primary_record": {
            "types": _types_of(primary),
            "name": primary.get("name"),
            "telephone": primary.get("telephone"),
            "source_page": primary.get("_source_page"),
            "completeness": _completeness(primary, _BUSINESS_FIELDS),
        },
        "contradictions": contradictions,
    }


def audit_vehicle_structured_data(objects: list[dict], is_used: bool = False) -> dict:
    """Vehicle/Product schema completeness check for a single VDP page.

    Only call this against a page already confirmed to be a VDP — a
    template-specific finding must never be generalised from a page that
    isn't actually one. `is_used` gates whether `mileageFromOdometer` is
    expected at all: a new vehicle legitimately has none, so it is only
    checked for used/CPO inventory.
    """
    vehicle_objs = [o for o in objects if any(t in ("vehicle", "car", "product") for t in _types_of(o))]
    if not vehicle_objs:
        return {"status": "unknown", "note": "No Vehicle/Product structured data found on this VDP"}

    fields_to_check = list(_VEHICLE_FIELDS_ALWAYS)
    if is_used:
        fields_to_check.append(_MILEAGE_FIELD)

    vehicle = max(vehicle_objs, key=lambda o: _completeness(o, fields_to_check))

    offers = vehicle.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    has_price = isinstance(offers, dict) and _is_present(offers.get("price") or offers.get("priceSpecification"))
    has_availability = isinstance(offers, dict) and _is_present(offers.get("availability"))

    missing = [f for f in fields_to_check if not _is_present(vehicle.get(f))]
    if not has_price:
        missing.append("offers.price")
    if not has_availability:
        missing.append("offers.availability")

    return {
        "status": "measured",
        "types": _types_of(vehicle),
        "missing_fields": missing,
        "complete": len(missing) == 0,
    }
