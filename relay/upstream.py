"""Upstream sync — pulls data from the local Duskfall instance over VPN."""
import asyncio
import logging
import time

import httpx

from relay.config import relay_settings
from relay.store import relay_store

logger = logging.getLogger(__name__)


class UpstreamSync:
    """Periodically pulls feed data from the upstream Duskfall instance."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30, follow_redirects=True, http2=True
        )
        self.connected = False
        self.last_sync = 0

    async def sync_loop(self, interval: int = 60):
        """Main sync loop — runs forever, pulling from upstream."""
        while True:
            try:
                await self._pull_feeds()
                await self._pull_tak()
                self.connected = True
                self.last_sync = time.time()
            except Exception as e:
                logger.warning(f"Upstream sync failed: {e}")
                self.connected = False

            await asyncio.sleep(interval)

    async def _pull_feeds(self):
        """Pull all recent feed events from upstream."""
        url = f"{relay_settings.UPSTREAM_URL}/api/feeds/"
        headers = {}
        if relay_settings.UPSTREAM_API_KEY:
            headers["X-Federation-Key"] = relay_settings.UPSTREAM_API_KEY

        resp = await self.client.get(
            url, params={"hours": 24, "limit": 2000}, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

        events = data.get("events", [])
        if events:
            relay_store.update_feeds(events)
            logger.info(f"Synced {len(events)} feed events from upstream")

    async def _pull_tak(self):
        """Pull TAK status from upstream."""
        try:
            url = f"{relay_settings.UPSTREAM_URL}/api/tak/status"
            resp = await self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") != "bridge_pending":
                    relay_store.add_tak_event(
                        {"type": "status", "upstream_tak": data}
                    )
        except Exception:
            pass  # TAK status is non-critical
