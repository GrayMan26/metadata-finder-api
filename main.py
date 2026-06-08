import base64
import io
import os
import tempfile

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from mutagen.id3 import (
    APIC, ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TRCK,
)
from mutagen.mp3 import MP3
from PIL import Image
import requests

load_dotenv()
DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN", "")

from audio_id import identify_file
from cover_art import fetch_cover_art, bytes_to_pil

app = FastAPI(title="Metadata Finder API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Identify ──────────────────────────────────────────────────────────────────

@app.post("/identify")
async def identify(file: UploadFile = File(...)):
    """Upload an MP3 → run Shazam identification → return metadata dict."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "track.mp3")[1] or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name
    try:
        result = await identify_file(tmp_path, acoustid_key=None)
    finally:
        os.unlink(tmp_path)

    if not result:
        return {"found": False}

    # Fetch cover art and return as base64 so frontend can display it
    cover_bytes = None
    if result.get("title") or result.get("artist"):
        cover_bytes = fetch_cover_art(
            result.get("title", ""),
            result.get("artist", ""),
            result.get("cover_url"),
        )

    return {
        "found": True,
        "title":  result.get("title", ""),
        "artist": result.get("artist", ""),
        "album":  result.get("album", ""),
        "year":   result.get("year", ""),
        "track":  result.get("track", ""),
        "cover_base64": base64.b64encode(cover_bytes).decode() if cover_bytes else None,
    }


# ── Discogs search ────────────────────────────────────────────────────────────

@app.get("/search/discogs")
def search_discogs(query: str, artist: str = ""):
    """Search Discogs and return a list of results."""
    if not DISCOGS_TOKEN:
        raise HTTPException(500, "DISCOGS_TOKEN not configured")
    search_q = f"{artist} {query}".strip()
    try:
        r = requests.get(
            "https://api.discogs.com/database/search",
            params={"q": search_q, "type": "release", "per_page": 10, "page": 1},
            headers={
                "Authorization": f"Discogs token={DISCOGS_TOKEN}",
                "User-Agent": "MetadataFinderApp/1.0",
            },
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        raise HTTPException(502, f"Discogs error: {e}")

    out = []
    for item in results:
        title_parts = item.get("title", "").split(" - ", 1)
        out.append({
            "id":        item.get("id"),
            "title":     title_parts[1] if len(title_parts) > 1 else item.get("title", ""),
            "artist":    title_parts[0] if len(title_parts) > 1 else "",
            "year":      item.get("year", ""),
            "label":     ", ".join(item.get("label", [])),
            "thumb":     item.get("thumb", ""),
            "cover_url": item.get("cover_image", ""),
            "format":    ", ".join(item.get("format", [])),
        })
    return out


# ── Cover art ─────────────────────────────────────────────────────────────────

@app.get("/cover-art")
def get_cover_art(title: str = "", artist: str = "", cover_url: str = ""):
    """Return cover art image bytes for given title/artist (or direct URL)."""
    data = fetch_cover_art(title, artist, cover_url or None)
    if not data:
        raise HTTPException(404, "Cover art not found")
    # Detect MIME type from bytes magic
    mime = "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    return Response(content=data, media_type=mime)


# ── Write tags ────────────────────────────────────────────────────────────────

@app.post("/write-tags")
async def write_tags(
    file:        UploadFile = File(...),
    title:       str = Form(""),
    artist:      str = Form(""),
    album:       str = Form(""),
    year:        str = Form(""),
    track:       str = Form(""),
    cover_url:   str = Form(""),
    cover_base64: str = Form(""),
):
    """Upload MP3 + metadata → write ID3 tags → return the modified file."""
    content = await file.read()
    suffix  = os.path.splitext(file.filename or "track.mp3")[1] or ".mp3"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        try:
            tags = ID3(tmp_path)
        except ID3NoHeaderError:
            tags = ID3()

        if title:  tags["TIT2"] = TIT2(encoding=3, text=title)
        if artist: tags["TPE1"] = TPE1(encoding=3, text=artist)
        if album:  tags["TALB"] = TALB(encoding=3, text=album)
        if year:   tags["TDRC"] = TDRC(encoding=3, text=year)
        if track:  tags["TRCK"] = TRCK(encoding=3, text=track)

        # Cover art: prefer base64 payload, fall back to URL/iTunes fetch
        cover_bytes = None
        if cover_base64:
            try:
                cover_bytes = base64.b64decode(cover_base64)
            except Exception:
                pass
        if not cover_bytes and (cover_url or title or artist):
            cover_bytes = fetch_cover_art(title, artist, cover_url or None)

        if cover_bytes:
            mime = "image/png" if cover_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            tags["APIC"] = APIC(
                encoding=3, mime=mime, type=3,
                desc="Cover", data=cover_bytes,
            )

        tags.save(tmp_path, v2_version=3)

        with open(tmp_path, "rb") as f:
            result_bytes = f.read()
    finally:
        os.unlink(tmp_path)

    safe_name = (title or "track").replace("/", "_").replace("\\", "_")
    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.mp3"'},
    )
