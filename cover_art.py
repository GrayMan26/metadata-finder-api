import io
import logging
import requests
from PIL import Image

log = logging.getLogger(__name__)


def fetch_cover_art(title: str, artist: str, cover_url: str = "") -> bytes | None:
    """Try direct URL → iTunes. Returns raw image bytes or None."""
    if cover_url:
        data = _fetch_url(cover_url)
        if data:
            return data

    return _fetch_itunes_art(title, artist)


def _fetch_url(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "MP3MetadataFinder/1.0"})
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("Cover URL fetch failed: %s", e)
        return None


def _fetch_itunes_art(title: str, artist: str) -> bytes | None:
    query = f"{artist} {title}".strip()
    if not query:
        return None
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        art_url = results[0].get("artworkUrl100", "")
        if not art_url:
            return None
        art_url = art_url.replace("100x100bb", "600x600bb")
        return _fetch_url(art_url)
    except Exception as e:
        log.warning("iTunes art fetch failed: %s", e)
        return None


def bytes_to_pil(image_bytes: bytes, size: tuple = (210, 210)) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize(size, Image.LANCZOS)
    return img
