"""WebDAV client for the sk.mimac.slideshow app running on the picture frame."""

import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlsplit

import httpx

log = logging.getLogger(__name__)

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>'
)


class FrameError(Exception):
    pass


class FrameClient:
    def __init__(self, base_url: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.dav_url = f"{self.base_url}/webdav/"
        self._client = httpx.AsyncClient(
            auth=httpx.BasicAuth(user, password),
            timeout=httpx.Timeout(60.0, connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, name: str) -> str:
        return self.dav_url + quote(name)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise FrameError(f"Frame unreachable ({type(exc).__name__})") from exc
        if resp.status_code == 401:
            raise FrameError("Frame rejected credentials (check FRAME_USER / FRAME_PASS)")
        return resp

    async def is_reachable(self) -> bool:
        try:
            await self._client.get(self.base_url, timeout=3.0)
            return True
        except httpx.HTTPError:
            return False

    async def list_files(self) -> list[str]:
        resp = await self._request(
            "PROPFIND",
            self.dav_url,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            content=PROPFIND_BODY,
        )
        if resp.status_code != 207:
            raise FrameError(f"PROPFIND failed: HTTP {resp.status_code}")
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise FrameError("Frame returned an invalid WebDAV listing") from exc

        names = []
        for entry in root.iter("{DAV:}response"):
            if entry.find(".//{DAV:}collection") is not None:
                continue
            href = entry.findtext("{DAV:}href") or ""
            name = unquote(urlsplit(href).path).rstrip("/").rsplit("/", 1)[-1]
            if name:
                names.append(name)
        return names

    async def upload(self, name: str, data: bytes, content_type: str = "image/jpeg") -> None:
        resp = await self._request(
            "PUT", self._url(name), content=data, headers={"Content-Type": content_type}
        )
        if resp.status_code not in (200, 201, 204):
            raise FrameError(f"Upload of {name} failed: HTTP {resp.status_code}")

    async def delete(self, name: str) -> None:
        resp = await self._request("DELETE", self._url(name))
        if resp.status_code not in (200, 204, 404):
            raise FrameError(f"Delete of {name} failed: HTTP {resp.status_code}")

    async def download(self, name: str) -> bytes:
        resp = await self._request("GET", self._url(name))
        if resp.status_code != 200:
            raise FrameError(f"Download of {name} failed: HTTP {resp.status_code}")
        return resp.content
