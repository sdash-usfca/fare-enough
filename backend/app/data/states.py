"""US state lookup for place strings — which state's fuel price applies.

The origin arrives as free text in a handful of shapes:
  "Auburn, WA 98092"                                    (typed)
  "Ames, Iowa"                                          (typed)
  "Auburn, King County, Washington, 98092, United States" (Nominatim label)
The state is one of the comma-separated parts after the first (the first
is the street or city), either as a full name or a leading 2-letter code.
Parts are scanned from the end so the state wins over street fragments.
"""

STATE_CODES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
_CODES = set(STATE_CODES.values())


def state_from_place(place: str) -> str | None:
    """2-letter state code found in a place string, or None if there isn't one.

    None is the honest answer for "Boise" with no state — callers fall back
    to a national figure instead of guessing a state.
    """
    parts = [p.strip() for p in place.split(",")]
    for part in reversed(parts[1:]):
        name = part.lower()
        if name in STATE_CODES:
            return STATE_CODES[name]
        tokens = part.split()
        # "WA 98092" or just "WA" — a 2-letter code leading the part.
        if tokens and len(tokens[0]) == 2 and tokens[0].upper() in _CODES:
            return tokens[0].upper()
    # No commas ("Auburn WA 98092"): accept an uppercase code as typed, so a
    # lowercase word like "or"/"in" inside a street name never matches.
    for token in reversed(place.split()):
        if token in _CODES:
            return token
    return None
