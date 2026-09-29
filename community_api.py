"""Client HTTP pour l'API Cloudflare Worker communautaire de Dofus-Craft (worker/src/index.ts)."""
import requests


class CommunityApiClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        # Une session persistante réutilise la même connexion (TCP + TLS) entre les appels au lieu
        # de renégocier à chaque requête : sensiblement plus rapide quand on enchaîne beaucoup de
        # lectures/envois de suite.
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def get_median(self, resource_id: str, server_id: str, timeout: float = 5.0) -> dict | None:
        try:
            response = self._session.get(
                f"{self.api_url}/community-price",
                params={"resourceId": resource_id, "serverId": server_id},
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        if response.status_code == 404:
            return None
        if not response.ok:
            return None
        return response.json()

    def get_latest_item_price(self, item_id: str, server_id: str, timeout: float = 5.0) -> dict | None:
        """Dernier prix envoyé pour un équipement (kind "item"). /community-price ne calcule la
        médiane que sur les ressources : pour un item, la référence est ce dernier prix."""
        try:
            response = self._session.get(
                f"{self.api_url}/latest-price",
                params={"itemId": item_id, "serverId": server_id},
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        return response.json()

    def submit_price(
        self,
        resource_id: str,
        server_id: str,
        amount_kamas: int,
        device_id: str,
        timeout: float = 5.0,
        kind: str = "resource",
    ) -> bool:
        payload = {
            "resourceId": resource_id,
            "serverId": server_id,
            "amountKamas": amount_kamas,
            "deviceId": device_id,
        }
        # Le worker n'accepte que `kind` absent (ressource) ou "item" : envoyer "resource"
        # explicitement serait refusé (400).
        if kind == "item":
            payload["kind"] = "item"
        try:
            response = self._session.post(
                f"{self.api_url}/community-price",
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException:
            return False
        return response.status_code == 201
