"""Curated metro → airport mapping.

Nearby-airport logic needs curated data, not an API: "Los Angeles" means
LAX plus BUR/LGB/SNA/ONT, and no API will tell you that mapping reliably.
`miles_to_city_center` prices the "airport → final destination" leg.
"""

AIRPORTS: dict[str, dict] = {
    "los angeles": {
        "primary": "LAX",
        "nearby": ["BUR", "LGB", "SNA", "ONT"],
        "miles_to_city_center": {"LAX": 18, "BUR": 12, "LGB": 22, "SNA": 35, "ONT": 40},
    },
    "san francisco": {
        "primary": "SFO",
        "nearby": ["SJC", "OAK"],
        "miles_to_city_center": {"SFO": 13, "SJC": 35, "OAK": 20},
    },
    "san diego": {
        "primary": "SAN",
        "nearby": [],
        "miles_to_city_center": {"SAN": 3},
    },
    "las vegas": {
        "primary": "LAS",
        "nearby": [],
        "miles_to_city_center": {"LAS": 3},
    },
    "portland": {
        "primary": "PDX",
        "nearby": [],
        "miles_to_city_center": {"PDX": 10},
    },
    "phoenix": {
        "primary": "PHX",
        "nearby": [],
        "miles_to_city_center": {"PHX": 4},
    },
    "denver": {
        "primary": "DEN",
        "nearby": [],
        "miles_to_city_center": {"DEN": 25},
    },
}


def lookup_city(city: str) -> dict | None:
    return AIRPORTS.get(city.strip().lower())


# Origin airport candidates with coordinates. The geocoded origin's nearest
# entry wins. Kept small and curated: on the departure side, "nearest major
# airport" is a stable, human-verifiable fact — no API needed.
ORIGIN_AIRPORTS: dict[str, tuple[float, float]] = {
    "SEA": (47.4502, -122.3088),  # Seattle–Tacoma
    "PDX": (45.5898, -122.5969),  # Portland
    "SFO": (37.6213, -122.3790),  # San Francisco
    "SJC": (37.3639, -121.9289),  # San Jose
    "OAK": (37.7126, -122.2197),  # Oakland
}
