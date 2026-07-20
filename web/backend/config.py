"""Runtime configuration for the EcoFlow Ocean web dashboard."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _parse_serials(raw: str) -> frozenset[str] | None:
    items = {part.strip().upper() for part in raw.split(",") if part.strip()}
    return frozenset(items) if items else None


@dataclass(frozen=True)
class SiteConfig:
    """One physical home / EcoFlow installation."""

    id: str
    label: str
    email: str
    password: str
    region: str = "us"
    # If set, only these serials belong to this site. If None, all devices on
    # the account (that aren't claimed by another same-account site) are used.
    serials: frozenset[str] | None = None


@dataclass(frozen=True)
class Settings:
    sites: tuple[SiteConfig, ...]
    data_dir: str = "/data"
    sample_interval_s: float = 30.0
    rest_poll_interval_s: float = 60.0
    history_retention_days: int = 90
    web_auth_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "ecoflow_history.sqlite3")

    @classmethod
    def from_env(cls) -> Settings:
        sites = _load_sites()
        if not sites:
            raise RuntimeError(
                "No sites configured. Set SITES=forest,desert with SITE_<ID>_EMAIL/"
                "SITE_<ID>_PASSWORD, or legacy ECOFLOW_EMAIL/ECOFLOW_PASSWORD."
            )
        return cls(
            sites=tuple(sites),
            data_dir=_env("DATA_DIR", "/data") or "/data",
            sample_interval_s=_env_float("SAMPLE_INTERVAL_S", 30.0),
            rest_poll_interval_s=_env_float("REST_POLL_INTERVAL_S", 60.0),
            history_retention_days=_env_int("HISTORY_RETENTION_DAYS", 90),
            web_auth_token=_env("WEB_AUTH_TOKEN"),
            host=_env("HOST", "0.0.0.0") or "0.0.0.0",
            port=_env_int("PORT", 8080),
        )


def _load_sites() -> list[SiteConfig]:
    """Load multi-site config, with legacy single-account fallback."""
    site_ids = [part.strip().lower() for part in _env("SITES").split(",") if part.strip()]
    if not site_ids:
        email = _env("ECOFLOW_EMAIL")
        password = _env("ECOFLOW_PASSWORD")
        if not email or not password:
            return []
        site_id = _env("ECOFLOW_SITE_ID", "desert") or "desert"
        label = _env("ECOFLOW_SITE_LABEL", "Desert House (CDO)") or "Desert House (CDO)"
        return [
            SiteConfig(
                id=site_id.lower(),
                label=label,
                email=email,
                password=password,
                region=_env("ECOFLOW_REGION", "us") or "us",
                serials=_parse_serials(_env("ECOFLOW_SERIALS") or _env("ECOFLOW_SERIAL")),
            )
        ]

    sites: list[SiteConfig] = []
    legacy_email = _env("ECOFLOW_EMAIL")
    legacy_password = _env("ECOFLOW_PASSWORD")
    legacy_region = _env("ECOFLOW_REGION", "us") or "us"

    for site_id in site_ids:
        prefix = f"SITE_{site_id.upper()}_"
        email = _env(f"{prefix}EMAIL") or legacy_email
        password = _env(f"{prefix}PASSWORD") or legacy_password
        if not email or not password:
            raise RuntimeError(
                f"Site '{site_id}' needs {prefix}EMAIL/{prefix}PASSWORD "
                "(or legacy ECOFLOW_EMAIL/ECOFLOW_PASSWORD)."
            )
        label = _env(f"{prefix}LABEL") or site_id.replace("-", " ").replace("_", " ").title()
        region = _env(f"{prefix}REGION") or legacy_region
        serials = _parse_serials(_env(f"{prefix}SERIALS"))
        sites.append(
            SiteConfig(
                id=site_id,
                label=label,
                email=email,
                password=password,
                region=region,
                serials=serials,
            )
        )
    return sites
