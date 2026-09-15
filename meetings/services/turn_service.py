import json
import logging
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def get_cloudflare_turn_ice_servers(ttl=86400):
    """Mint short-lived Cloudflare Realtime TURN credentials.

    Free up to 1,000 GB/month. Returns a list of RTCIceServer dicts ready to be
    merged into SMARTMEET.iceServers, or None if not configured / on failure.

    Requires CLOUDFLARE_TURN_KEY_ID and CLOUDFLARE_TURN_API_TOKEN env vars,
    created under Cloudflare Dashboard -> Realtime -> TURN.
    """
    key_id = getattr(settings, "CLOUDFLARE_TURN_KEY_ID", "")
    token = getattr(settings, "CLOUDFLARE_TURN_API_TOKEN", "")
    if not key_id or not token:
        return None

    url = (
        f"https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}"
        "/credentials/generate-ice-servers"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"ttl": ttl}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.exception("Cloudflare TURN credential minting failed")
        return None

    ice_servers = payload.get("iceServers") or []
    # The alternate port 53 URL is blocked by browsers and just adds a timeout.
    for server in ice_servers:
        urls = server.get("urls")
        if isinstance(urls, list):
            server["urls"] = [u for u in urls if not u.endswith(":53")]
    return ice_servers if ice_servers else None