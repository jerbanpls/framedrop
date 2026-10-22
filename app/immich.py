"""Minimal Immich API client: list an album's photos and download originals."""

import httpx


class ImmichError(Exception):
    pass


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, album_id: str):
        self.album_id = album_id
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api",
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            raise ImmichError(f"Immich {method} {url}: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ImmichError(f"Immich unreachable ({type(exc).__name__})") from exc

    async def album_images(self) -> list[dict]:
        """Return [{id, originalFileName}] for every IMAGE asset in the album."""
        album = (await self._call("GET", f"/albums/{self.album_id}")).json()
        assets = album.get("assets") or []
        if not assets and album.get("assetCount"):
            # Newer Immich versions may omit assets from the album response.
            assets = await self._search_album_assets()
        return [
            {"id": a["id"], "originalFileName": a.get("originalFileName") or a["id"]}
            for a in assets
            if a.get("type", "IMAGE") == "IMAGE"
        ]

    async def _search_album_assets(self) -> list[dict]:
        assets, page = [], 1
        while page:
            body = {"albumIds": [self.album_id], "type": "IMAGE", "page": page, "size": 500}
            result = (await self._call("POST", "/search/metadata", json=body)).json()["assets"]
            assets.extend(result.get("items", []))
            page = int(result["nextPage"]) if result.get("nextPage") else 0
        return assets

    async def download_original(self, asset_id: str) -> bytes:
        resp = await self._call("GET", f"/assets/{asset_id}/original", headers={"Accept": "*/*"})
        return resp.content
