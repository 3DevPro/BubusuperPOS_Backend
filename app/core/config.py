from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anything shorter than this is treated as "not really configured". The dev
# default below clears it; a blank `JWT_SECRET=` in an env file does not.
_MIN_JWT_SECRET_LENGTH = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://loyverse:loyverse@localhost:5432/loyverse"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    refresh_token_expire_days: int = 180

    # Barcode -> product lookup (name/image/price prefill on product creation).
    # Both providers below are free tiers; upcitemdb_api_key is unused for now
    # (the trial endpoint needs no key) but lets us upgrade to a paid plan
    # later without touching the provider class, only this setting.
    product_lookup_timeout_seconds: int = 8
    product_lookup_cache_days: int = 30
    upcitemdb_api_key: str = ""

    # Flutter web runs on a different origin/port than the API in dev, so the
    # browser preflights every request. Override via env for the prod domain.
    cors_allow_origin_regex: str = r"http://localhost:\d+"

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_is_usable(cls, value: str) -> str:
        """Refuse to boot on a blank secret rather than sign tokens with it.
        `JWT_SECRET=` with nothing after it — exactly what .env.prod.example
        ships — overrides the default above with an empty string, and
        python-jose signs and verifies HS256 with an empty key perfectly
        happily, so every token in the system would be forgeable with no
        error anywhere to notice. Length alone is checked here so the dev
        default keeps working; deploy.sh is what rejects that placeholder
        for production."""
        if len(value.strip()) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LENGTH} characters "
                "— generate one with `openssl rand -hex 32`"
            )
        return value


settings = Settings()
