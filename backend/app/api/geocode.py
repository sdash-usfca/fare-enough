"""Address autocomplete — GET /geocode/suggest.

Feeds the From/To type-ahead in the UI: as the traveler types, they get
concrete address candidates ("350 5th Ave, New York, NY 10118") instead of
typing a bare city and hoping. Picking a suggestion pins the exact spot,
so pricing runs hotel-address → home-address, not city-centroid guesses.

Read-only and keyless (Photon); failures degrade to an empty list so the
form keeps working with free-typed text.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.providers.geo import PhotonSuggester, PlaceSuggestion

router = APIRouter(prefix="/geocode", tags=["geocode"])

_suggester = PhotonSuggester()


class PlaceSuggestionOut(BaseModel):
    label: str
    lat: float
    lon: float


@router.get("/suggest", response_model=list[PlaceSuggestionOut])
async def suggest_places(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=5, ge=1, le=10),
) -> list[PlaceSuggestion]:
    return await _suggester.suggest(q, limit=limit)
