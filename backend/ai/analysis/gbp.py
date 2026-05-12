"""Google Business Profile verification via SerpAPI (Google Maps engine).

Fetches GBP data for a dealership by name + address hint, then compares
the dealer's website address and business hours against the GBP listing.
"""

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------------------------------------------------------- #
#  SerpAPI                                                                     #
# --------------------------------------------------------------------------- #

async def fetch_gbp_data(
    dealer_name: str,
    address_hint: str,
    api_key: str,
    website_url: str = "",
) -> Optional[dict]:
    """Query SerpAPI Google Maps to find the best-matching dealer listing.

    Returns a dict with keys: place_id, name, formatted_address, phone, hours,
    weekday_text, maps_url — or None if no match found.
    """
    if not dealer_name and not address_hint and not website_url:
        return None

    # Extract domain hint from website URL
    domain_hint = ""
    if website_url:
        try:
            domain_hint = urlparse(website_url).netloc.lstrip("www.")
        except Exception:
            pass

    full_query   = f"{dealer_name} {address_hint}".strip()
    name_only    = dealer_name.strip()
    domain_query = f"{dealer_name} {domain_hint}".strip() if domain_hint else ""

    async with httpx.AsyncClient(timeout=15.0) as client:

        async def _search(query: str) -> Optional[dict]:
            """Run one SerpAPI Google Maps search; return first usable result or None."""
            resp = await client.get(_SERPAPI_URL, params={
                "engine":   "google_maps",
                "q":        query,
                "api_key":  api_key,
            })
            data = resp.json()

            # SerpAPI returns either place_results (direct single match)
            # or local_results (list of matches)
            place = data.get("place_results")
            if place and place.get("title"):
                logger.info(f"[GBP] Direct place_results match for {query!r}: {place.get('title')}")
                return place

            local = data.get("local_results", [])
            if local:
                logger.info(f"[GBP] local_results[0] for {query!r}: {local[0].get('title')}")
                return local[0]

            return None

        result = None
        for query in [full_query, domain_query, name_only]:
            if not query:
                continue
            logger.info(f"[GBP] Trying SerpAPI query: {query!r}")
            result = await _search(query)
            if result:
                break

    if not result:
        logger.warning(f"[GBP] No SerpAPI results for any query variant of {dealer_name!r}")
        return None

    # Parse hours: SerpAPI returns [{"monday": "9 AM–7:30 PM"}, ...]
    hours_dict: dict[str, str] = {}
    raw_hours = result.get("hours", [])
    weekday_text: list[str] = []
    for entry in (raw_hours if isinstance(raw_hours, list) else []):
        for day_lower, time_str in entry.items():
            day_cap = day_lower.capitalize()
            hours_dict[day_cap] = time_str
            weekday_text.append(f"{day_cap}: {time_str}")

    maps_url = (
        result.get("link") or
        result.get("place_id_search") or
        (f"https://www.google.com/maps/place/?q=place_id:{result.get('place_id')}"
         if result.get("place_id") else "")
    )

    return {
        "place_id":          result.get("place_id", ""),
        "name":              result.get("title") or result.get("name", ""),
        "formatted_address": result.get("address", ""),
        "phone":             result.get("phone", ""),
        "hours":             hours_dict,
        "weekday_text":      weekday_text,
        "maps_url":          maps_url,
    }


# --------------------------------------------------------------------------- #
#  Address comparison                                                           #
# --------------------------------------------------------------------------- #

_STATE_ABBR = {
    "al":"alabama","ak":"alaska","az":"arizona","ar":"arkansas","ca":"california",
    "co":"colorado","ct":"connecticut","de":"delaware","fl":"florida","ga":"georgia",
    "hi":"hawaii","id":"idaho","il":"illinois","in":"indiana","ia":"iowa",
    "ks":"kansas","ky":"kentucky","la":"louisiana","me":"maine","md":"maryland",
    "ma":"massachusetts","mi":"michigan","mn":"minnesota","ms":"mississippi",
    "mo":"missouri","mt":"montana","ne":"nebraska","nv":"nevada","nh":"new hampshire",
    "nj":"new jersey","nm":"new mexico","ny":"new york","nc":"north carolina",
    "nd":"north dakota","oh":"ohio","ok":"oklahoma","or":"oregon","pa":"pennsylvania",
    "ri":"rhode island","sc":"south carolina","sd":"south dakota","tn":"tennessee",
    "tx":"texas","ut":"utah","vt":"vermont","va":"virginia","wa":"washington",
    "wv":"west virginia","wi":"wisconsin","wy":"wyoming",
}
_STREET_ABBR = [
    ("st","street"),("ave","avenue"),("blvd","boulevard"),("dr","drive"),
    ("rd","road"),("ln","lane"),("ct","court"),("pkwy","parkway"),("hwy","highway"),
]


def compare_address(website_addr: str, gbp_addr: str) -> dict:
    """Normalize both addresses and return comparison result."""
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[^\w\s]", " ", s)
        for abbr, full in _STREET_ABBR:
            s = re.sub(rf"\b{abbr}\b", full, s)
        for abbr, full in _STATE_ABBR.items():
            s = re.sub(rf"\b{abbr}\b", full, s)
        return " ".join(s.split())

    wa, ga = norm(website_addr), norm(gbp_addr)
    if not wa or not ga:
        return {"match": None, "website": website_addr, "gbp": gbp_addr,
                "note": "Could not compare — one address is missing"}

    exact = wa == ga
    if not exact:
        wa_tok, ga_tok = set(wa.split()), set(ga.split())
        overlap = len(wa_tok & ga_tok) / max(len(wa_tok | ga_tok), 1)
        match = overlap >= 0.70
    else:
        match = True

    return {
        "match": match,
        "website": website_addr,
        "gbp":     gbp_addr,
        "note":    "" if match else "Address differs between website and Google Business Profile",
    }


# --------------------------------------------------------------------------- #
#  Hours comparison                                                             #
# --------------------------------------------------------------------------- #

def compare_hours(website_hours: dict, gbp_hours: dict) -> list[dict]:
    """Compare hours day by day. Returns list of discrepancy dicts."""
    discrepancies = []
    all_days = set(list(website_hours) + list(gbp_hours))

    for day in DAYS_ORDER:
        if day not in all_days:
            continue
        wh = website_hours.get(day, "")
        gh = gbp_hours.get(day, "")

        if _norm_hours(wh) != _norm_hours(gh):
            discrepancies.append({
                "day":     day,
                "website": wh or "Not listed",
                "gbp":     gh or "Not listed",
            })

    return discrepancies


def _time_to_minutes(t: str) -> Optional[int]:
    """Convert a time string to minutes since midnight.

    Handles 12-hour with/without colon ('9:00 AM', '9 AM', '7:30 PM')
    and 24-hour ('09:00', '20:00') formats.
    """
    t = t.strip().upper()
    # 12-hour with colon: "9:00 AM", "7:30 PM"
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)$", t)
    if m:
        h, mn, period = int(m.group(1)), int(m.group(2)), m.group(3)
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        return h * 60 + mn
    # 12-hour without colon: "9 AM", "12 PM"
    m = re.match(r"(\d{1,2})\s*(AM|PM)$", t)
    if m:
        h, period = int(m.group(1)), m.group(2)
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        return h * 60
    # 24-hour: "09:00", "20:00"
    m = re.match(r"(\d{1,2}):(\d{2})$", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _norm_hours(s: str) -> str:
    """Normalize a day's hours range to 'open_mins-close_mins' for comparison.

    Handles 12-hour (with/without colon), 24-hour, and en/em-dash separators.
    Falls back to lowercased raw string if parsing fails (e.g. 'Closed').
    """
    s = (s or "").strip()
    parts = re.split(r"\s*[-–—]\s*", s, maxsplit=1)
    if len(parts) == 2:
        open_m  = _time_to_minutes(parts[0])
        close_m = _time_to_minutes(parts[1])
        if open_m is not None and close_m is not None:
            return f"{open_m}-{close_m}"
    return s.lower().strip()
