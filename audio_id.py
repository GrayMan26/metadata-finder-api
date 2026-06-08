import asyncio
import logging
import sys

log = logging.getLogger(__name__)

# Windows needs SelectorEventLoop for aiohttp (used by shazamio)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def identify_with_shazam(filepath: str) -> dict:
    try:
        from shazamio import Shazam

        shazam = Shazam()
        result = await shazam.recognize(filepath)

        if not result or "track" not in result:
            return {}

        track = result["track"]
        metadata = {
            "title":  track.get("title", ""),
            "artist": track.get("subtitle", ""),
            "source": "Shazam",
        }

        for section in track.get("sections", []):
            if section.get("type") == "SONG":
                for item in section.get("metadata", []):
                    t = item.get("title", "").lower()
                    if t == "album":
                        metadata["album"] = item.get("text", "")
                    elif t == "released":
                        metadata["year"] = item.get("text", "")[:4]

        images = track.get("images", {})
        cover_url = images.get("coverarthq") or images.get("coverart", "")
        if cover_url:
            metadata["cover_url"] = cover_url

        return metadata

    except Exception as e:
        log.warning("Shazam identification failed: %s", e)
        return {}


def identify_with_acoustid(filepath: str, api_key: str) -> dict:
    if not api_key:
        return {}
    try:
        import acoustid
        results = list(acoustid.match(api_key, filepath, meta="recordings releases"))
        if not results:
            return {}
        score, rid, title, artist = results[0]
        if score < 0.5:
            return {}
        return {"title": title or "", "artist": artist or "", "source": "AcoustID", "score": score}
    except Exception as e:
        log.warning("AcoustID failed: %s", e)
        return {}


async def identify_file(filepath: str, acoustid_key: str = "") -> dict:
    """Try AcoustID then Shazam. Returns whatever was found (may be empty)."""
    if acoustid_key:
        result = identify_with_acoustid(filepath, acoustid_key)
        if result:
            return result

    return await identify_with_shazam(filepath)
