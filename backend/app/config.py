"""Environment-based configuration (12-factor).

Everything tunable or secret comes from the environment — never hardcoded.
See ARCHITECTURE.md decision 9.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "fare-enough"
    # Real provider keys are optional: without them the app runs on stubs.
    duffel_api_key: str | None = None
    # Free at https://www.eia.gov/opendata/register.php (emailed instantly).
    # Without it, fuel stays on the labeled stub estimate.
    eia_api_key: str | None = None
    # Google Cloud key with "Places API (New)" enabled. Station-level pump
    # prices near the origin; outranks EIA. 1,000 free calls/month (the
    # fuelOptions field bills as Nearby Search Enterprise + Atmosphere).
    google_places_api_key: str | None = None
    # Wired in Phase 1b (docker-compose already provides these services).
    # Postgres in compose/prod; SQLite file when unset so `uvicorn` works
    # with zero infrastructure. Same SQLAlchemy code path either way.
    database_url: str = "sqlite+aiosqlite:///./fare_enough.db"
    redis_url: str | None = None
    # Browser origins allowed to call the API. JSON list in the env, e.g.
    # CORS_ORIGINS='["https://fare-enough.example"]' for a deployed UI.
    cors_origins: list[str] = ["http://localhost:5173"]

    # Pricing heuristics (used by estimate providers; override per deploy).
    rideshare_base_fare_usd: float = 2.80
    rideshare_per_mile_usd: float = 1.90
    rental_car_daily_usd: float = 58.00
    rental_insurance_daily_usd: float = 20.00
    default_mpg: float = 28.0
    # What a mile costs your own car beyond fuel: maintenance, repairs and
    # tires. AAA "Your Driving Costs" 2025, average of all vehicle types.
    # Deliberately NOT the IRS rate (76¢ from Jul 2026): that one bundles
    # fuel (already priced) plus insurance and depreciation, which you pay
    # whether or not you take this trip.
    own_car_wear_per_mile_usd: float = 0.1104
    # Long drives: past this many hours behind the wheel, the day ends in a
    # hotel. 10h stays under the 11h federal limit for *professional* truck
    # drivers — a sane ceiling for a family road trip.
    max_drive_hours_per_day: float = 10.0
    # One hotel room for a night: CoStar/STR US average daily rate, 2025.
    lodging_per_night_usd: float = 160.54


settings = Settings()
