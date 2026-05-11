"""Google Business Profile verification via Places API.

Fetches GBP data for a dealership by name + address hint, then compares
the dealer's website address and business hours against the GBP listing.
"""

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FIND_URL    = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_TEXT_URL    = "https://maps.googleapis.com/maps/api/place/textsearch/json"

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DOW_INDEX = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# --------------------------------------------------------------------------- #
#  Places API                                                                  #
# --------------------------------------------------------------------------- #

async def fetch_gbp_data(dealer_name: str, address_hint: str, api_key: str) -> Optional[dict]:
    """Query Google Places to find the best-matching dealer listing.

    Returns a dict with keys: place_id, name, formatted_address, phone, hours,
    weekday_text, maps_url — or None if no match found.
    """
    if not dealer_name and not address_hint:
        return None

    query = f"{dealer_name} {address_hint}".strip()
    logger.info(f"[GBP] Searching Places for: {query!r}")

    async with httpx.AsyncClient(timeout=12.0) as client:
        # Step 1: Text search → get place_id
        search_resp = await client.get(_TEXT_URL, params={
            "query": query,
            "key": api_key,
            "type": "car_dealer",
        })
        search_data = search_resp.json()

        results = search_data.get("results", [])
        if not results:
            # Retry without type constraint
            search_resp = await client.get(_TEXT_URL, params={"query": query, "key": api_key})
            results = search_resp.json().get("results", [])

        if not results:
            logger.warning(f"[GBP] No Places results for {query!r}")
            return None

        place_id = results[0].get("place_id")
        if not place_id:
            return None

        # Step 2: Place Details
        det_resp = await client.get(_DETAILS_URL, params={
            "place_id": place_id,
            "fields": "name,formatted_address,opening_hours,formatted_phone_number,url",
            "key": api_key,
        })
        result = det_resp.json().get("result", {})

    # Parse periods → {Monday: "9:00 AM-5:00 PM", ...}
    hours_dict: dict[str, str] = {}
    oh = result.get("opening_hours", {})
    for period in oh.get("periods", []):
        open_info  = period.get("open",  {})
        close_info = period.get("close", {})
        day_idx    = open_info.get("day")
        if day_idx is None:
            continue
        day_name   = _DOW_INDEX[day_idx]
        open_t     = _fmt_hhmm(open_info.get("time", ""))
        close_t    = _fmt_hhmm(close_info.get("time", "")) if close_info else "Closed"
        hours_dict[day_name] = f"{open_t}-{close_t}"

    return {
        "place_id": place_id,
        "name": result.get("name"),
        "formatted_address": result.get("formatted_address"),
        "phone": result.get("formatted_phone_number"),
        "hours": hours_dict,
        "weekday_text": oh.get("weekday_text", []),
        "maps_url": result.get("url"),
    }


def _fmt_hhmm(hhmm: str) -> str:
    """Convert '0900' → '9:00 AM', '1730' → '5:30 PM'."""
    if not hhmm or len(hhmm) != 4:
        return hhmm
    h, m = int(hhmm[:2]), int(hhmm[2:])
    period = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {period}"


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
        match = overlap >= 0.70   # 70% token overlap accounts for minor formatting diffs
    else:
        match = True

    return {
        "match": match,
        "website": website_addr,
        "gbp": gbp_addr,
        "note": "" if match else "Address differs between website and Google Business Profile",
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
                "day": day,
                "website": wh or "Not listed",
                "gbp":     gh or "Not listed",
            })

    return discrepancies


def _norm_hours(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s*[-–]\s*", "-", s)
    s = re.sub(r"\s+", "", s)
    # Normalize am/pm spacing: "9:00am" == "9:00 am"
    s = re.sub(r"(\d)(am|pm)", r"\1 \2", s)
    return s
