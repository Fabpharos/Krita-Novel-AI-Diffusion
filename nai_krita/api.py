"""NovelAI HTTP client on urllib (no pip dependencies). Krita-free and blocking:
call it from a worker thread, never from Krita's UI thread."""
import base64
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from typing import List, Tuple

IMAGE_HOST = "https://image.novelai.net"
API_HOST = "https://api.novelai.net"
USER_AGENT = "nai-krita-plugin/0.1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class NaiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"NovelAI {status}: {message}" if status else message)
        self.status = status
        self.message = message


def _error_message(status: int, raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("message"):
            text = str(data["message"])
    except ValueError:
        pass
    hint = {401: "check the API token in settings",
            402: "not enough Anlas or no active subscription",
            429: "too many requests"}.get(status)
    text = text[:500] or "no message"
    return f"{text} ({hint})" if hint else text


def unpack_images(data: bytes) -> List[bytes]:
    """Response body -> list of PNG bytes. Accepts a ZIP or a bare PNG."""
    if data.startswith(PNG_MAGIC):
        return [data]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = sorted(n for n in z.namelist() if not n.endswith("/"))
            images = [z.read(n) for n in names]
    except zipfile.BadZipFile:
        raise NaiError(0, f"Unexpected response ({len(data)} bytes, not a ZIP or PNG)") from None
    images = [i for i in images if i.startswith(PNG_MAGIC)]
    if not images:
        raise NaiError(0, "Response ZIP contained no PNG images")
    return images


class Client:
    def __init__(self, token: str, timeout: int = 120, retry_wait: float = 5.0):
        if not token:
            raise NaiError(0, "No NovelAI API token set (Settings > API token)")
        self._token = token
        self.timeout = timeout
        self.retry_wait = retry_wait

    def _request(self, method: str, url: str, body=None, multipart=None) -> Tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": USER_AGENT,
                   "Accept": "*/*"}
        if multipart is not None:
            data, headers["Content-Type"] = multipart
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            data = None
        for attempt in (1, 2):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as e:
                raw = e.read() if hasattr(e, "read") else b""
                if e.code == 429 and attempt == 1:
                    time.sleep(self.retry_wait)   # wait and retry once
                    continue
                raise NaiError(e.code, _error_message(e.code, raw)) from None
            except urllib.error.URLError as e:
                raise NaiError(0, f"Network error: {e.reason}") from None
            except TimeoutError:
                raise NaiError(0, f"Request timed out after {self.timeout}s") from None
        raise NaiError(429, "too many requests")

    def _get_json(self, path: str) -> dict:
        """GET on image.novelai.net, falling back to api.novelai.net."""
        last = None
        for host in (IMAGE_HOST, API_HOST):
            try:
                _, raw = self._request("GET", host + path)
                return json.loads(raw.decode("utf-8"))
            except NaiError as e:
                if e.status in (401, 402):
                    raise
                last = e
        raise last

    def subscription(self) -> dict:
        """Subscription with Anlas balance and (reportedly) the V5 usage field.
        Falls back to /user/data, whose 'subscription' key has the same balances."""
        try:
            return self._get_json("/user/subscription")
        except NaiError as e:
            if e.status in (401, 402):
                raise
            return self.user_data()

    def user_data(self) -> dict:
        return self._get_json("/user/data")

    def generate(self, body: dict) -> List[bytes]:
        _, raw = self._request("POST", IMAGE_HOST + "/ai/generate-image", body)
        return unpack_images(raw)

    def encode_vibe(self, png: bytes, information_extracted: float, model: str) -> str:
        """Encode an image for Vibe Transfer (V4/V4.5). Returns the encoding as base64.
        Reported to cost 2 Anlas per encode; callers cache the result."""
        body = {"image": "image", "information_extracted": round(information_extracted, 2),
                "model": model}
        _, raw = self._request("POST", IMAGE_HOST + "/ai/encode-vibe",
                               multipart=multipart_form(body, {"image": png}))
        if not raw:
            raise NaiError(0, "encode-vibe returned no data")
        return base64.b64encode(raw).decode("ascii")

    def upscale(self, png: bytes, model: str = "nai-diffusion-5-curated") -> List[bytes]:
        """2x upscale of one image through /ai/upscale."""
        body = {"image": "image", "model": model, "declared_blur_sigma": 0}
        _, raw = self._request("POST", IMAGE_HOST + "/ai/upscale",
                               multipart=multipart_form(body, {"image": png}))
        return unpack_images(raw)


def multipart_form(body: dict, images: dict):
    """multipart/form-data the way NovelAI's web client sends it: every image as its
    own part named after its field (the field in the JSON holds the part name), plus
    the JSON body as a part called "request". Returns (bytes, content type)."""
    import uuid
    boundary = "----naikrita" + uuid.uuid4().hex
    out = []

    def part(name, content_type, payload):
        out.append(f"--{boundary}\r\n".encode())
        out.append(f'Content-Disposition: form-data; name="{name}"; filename="blob"\r\n'
                   .encode())
        out.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        out.append(payload)
        out.append(b"\r\n")
    for name, data in images.items():
        part(name, "image/png", data)
    part("request", "application/json",
         json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def png_text_chunks(png: bytes) -> dict:
    """Read tEXt/iTXt chunks from a PNG (NovelAI stores its metadata there)."""
    import struct
    import zlib
    out, pos = {}, 8
    while pos + 8 <= len(png):
        length, ctype = struct.unpack(">I4s", png[pos:pos + 8])
        chunk = png[pos + 8:pos + 8 + length]
        if ctype == b"tEXt":
            k, _, v = chunk.partition(b"\0")
            out[k.decode("latin-1")] = v.decode("latin-1")
        elif ctype == b"iTXt":
            k, _, rest = chunk.partition(b"\0")
            compressed, rest = rest[0], rest[2:]
            _, _, rest = rest.partition(b"\0")   # language
            _, _, text = rest.partition(b"\0")   # translated keyword
            out[k.decode("latin-1")] = (zlib.decompress(text) if compressed else text).decode("utf-8")
        elif ctype == b"IEND":
            break
        pos += 12 + length
    return out
