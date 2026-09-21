"""Client HTTP pour l'API Cloudflare Worker communautaire de Dofus-Craft (worker/src/index.ts)."""
import requests


class CommunityApiClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def get_median(self, resource_id: str, server_id: str, timeout: float = 5.0) -> dict | None:
        try:
            response = requests.get(
                f"{self.api_url}/community-price",
                params={"resourceId": resource_id, "serverId": server_id},
                headers=self._headers,
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        if response.status_code == 404:
            return None
        if not response.ok:
            return None
        return response.json()

    def submit_price(
        self, resource_id: str, server_id: str, amount_kamas: int, device_id: str, timeout: float = 5.0
    ) -> bool:
        try:
            response = requests.post(
                f"{self.api_url}/community-price",
                json={
                    "resourceId": resource_id,
                    "serverId": server_id,
                    "amountKamas": amount_kamas,
                    "deviceId": device_id,
                },
                headers=self._headers,
                timeout=timeout,
            )
        except requests.RequestException:
            return False
        return response.status_code == 201
