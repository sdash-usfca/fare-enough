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
    # Wired in Phase 1b (docker-compose already provides these services).
    # Postgres in compose/prod; SQLite file when unset so `uvicorn` works
    # with zero infrastructure. Same SQLAlchemy code path either way.
    database_url: str = "sqlite+aiosqlite:///./fare_enough.db"
    redis_url: str | None = None

    # Pricing heuristics (used by estimate providers; override per deploy).
    rideshare_base_fare_usd: float = 2.80
    rideshare_per_mile_usd: float = 1.90
    rental_car_daily_usd: float = 58.00
    rental_insurance_daily_usd: float = 20.00
    default_mpg: float = 28.0


settings = Settings()
