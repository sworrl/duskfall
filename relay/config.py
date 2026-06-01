"""Feeder-relay configuration."""
from pydantic_settings import BaseSettings


class RelaySettings(BaseSettings):
    RELAY_NAME: str = "duskfall-relay"
    # The upstream Duskfall instance (over VPN)
    UPSTREAM_URL: str = "http://YOUR_DUSKFALL_HOST:8500"
    UPSTREAM_API_KEY: str = ""
    # Bind settings
    HOST: str = "0.0.0.0"
    PORT: int = 8501
    # TLS (use reverse proxy like Caddy/nginx for production TLS)
    # These are for direct TLS if no reverse proxy
    TLS_CERT: str = ""
    TLS_KEY: str = ""
    # Data retention (hours) — how long to cache relayed data
    DATA_RETENTION_HOURS: int = 72
    # Max video chunk size (bytes)
    MAX_VIDEO_CHUNK: int = 10 * 1024 * 1024  # 10MB
    # Relay secret — used to derive the master key
    RELAY_SECRET: str = "change-this-in-production"

    class Config:
        env_file = ".env"
        env_prefix = "RELAY_"


relay_settings = RelaySettings()
