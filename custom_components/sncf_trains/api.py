import base64
import logging
from collections.abc import Mapping

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.exceptions import ConfigEntryAuthFailed

from .disruptions import attach_disruptions

API_BASE = "https://api.sncf.com"
_LOGGER = logging.getLogger(__name__)


def encode_token(api_key: str) -> str:
    """Encode the API key for Basic Auth."""
    token_str = f"{api_key}:"
    return base64.b64encode(token_str.encode()).decode()


class SncfApiClient:
    def __init__(self, session: ClientSession, api_key: str, timeout: int = 10):
        self._session = session
        self._token = encode_token(api_key)
        self._timeout = timeout

    async def fetch_departures(
        self, stop_id: str, max_results: int = 10
    ) -> list[dict] | None:
        if stop_id.startswith("stop_area:"):
            url = f"{API_BASE}/v1/coverage/sncf/stop_areas/{stop_id}/departures"
        elif stop_id.startswith("stop_point:"):
            url = f"{API_BASE}/v1/coverage/sncf/stop_points/{stop_id}/departures"
        else:
            raise ValueError("stop_id must start with 'stop_area:' or 'stop_point:'")

        params_raw: dict[str, object] = {
            "data_freshness": "realtime",
            "count": max_results,
        }
        params: Mapping[str, str] = {k: str(v) for k, v in params_raw.items()}

        headers = {"Authorization": f"Basic {self._token}"}

        try:
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status == 401:
                    # vrai problème d'auth
                    raise ConfigEntryAuthFailed("Unauthorized: check your API key.")
                if resp.status == 429:
                    # rate-limit => pas une auth failure
                    _LOGGER.warning("API rate limit (429) on %s with %s", url, params)
                    raise RuntimeError(
                        "SNCF API rate-limited (429)"
                    )  # sera géré comme non-critique
                resp.raise_for_status()
                data = await resp.json()
                return data.get("departures", [])
        except (TimeoutError, ClientError) as err:
            _LOGGER.error("Network error fetching departures from SNCF API: %s", err)
            _LOGGER.debug("URL: %s, Params: %s", url, params)
            return None

    async def fetch_journeys(
        self,
        from_id: str,
        to_id: str,
        datetime_str: str,
        count: int = 5,
        data_freshness: str = "realtime",
    ) -> list[dict] | None:
        """Fetch journeys between two places.

        ``data_freshness`` is ``realtime`` (actual times, cancelled trains are
        omitted by the API) or ``base_schedule`` (planned timetable).
        The response disruptions are attached to each journey under
        ``sncf_disruptions``.
        """
        url = f"{API_BASE}/v1/coverage/sncf/journeys"
        params_raw: dict[str, object] = {
            "from": from_id,
            "to": to_id,
            "datetime": datetime_str,
            "count": count,
            "data_freshness": data_freshness,
            "datetime_represents": "departure",
        }
        params: Mapping[str, str] = {k: str(v) for k, v in params_raw.items()}

        headers = {"Authorization": f"Basic {self._token}"}
        try:
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status == 401:
                    raise ConfigEntryAuthFailed("Unauthorized: check your API key.")
                if resp.status == 429:
                    raise RuntimeError("Quota exceeded: 429 Too Many Requests.")
                resp.raise_for_status()
                data = await resp.json()
                journeys = data.get("journeys", [])
                if isinstance(journeys, list):
                    attach_disruptions(journeys, data.get("disruptions") or [])
                return journeys
        except (TimeoutError, ClientError) as err:
            _LOGGER.warning("Network error fetching journeys from SNCF API: %s", err)
            return None

    async def search_stations(self, query: str) -> list[dict] | None:
        url = f"{API_BASE}/v1/coverage/sncf/places"
        params_raw: dict[str, object] = {
            "q": query,
            "type[]": "stop_point",
        }
        params: Mapping[str, str] = {k: str(v) for k, v in params_raw.items()}
        headers = {"Authorization": f"Basic {self._token}"}
        try:
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=ClientTimeout(total=self._timeout),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("places", [])
        except (TimeoutError, ClientError) as err:
            _LOGGER.error("Network error searching stations from SNCF API: %s", err)
            return None
