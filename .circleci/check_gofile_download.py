import hashlib
import json
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
FILE_ID = "e2488a04-f65d-48a9-b2e1-f3387ffe569b"
FILENAME = "CloverProject-4.0-20261109-OFFICIAL-peridot-GMS.zip"
WEBSITE_SALT = "12af056dacea0b"
MAX_BYTES = 5 * 1024 ** 3


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path, headers=None, method="GET"):
    request = urllib.request.Request(
        "https://api.gofile.io" + path,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read(2 * 1024 * 1024))
    if payload.get("status") != "ok":
        raise SystemExit(f"FAIL: Gofile API status {payload.get('status')}")
    return payload["data"]


def main():
    token = api("/accounts", method="POST")["token"]
    token = api("/accounts/website", {"Authorization": "Bearer " + token}).get("token") or token
    raw = "::".join([USER_AGENT, "en-US", token, str(int(time.time()) // 14400), WEBSITE_SALT])
    content = api("/contents/" + FILE_ID + "?cache=true", {
        "Authorization": "Bearer " + token,
        "Cookie": "accountToken=" + token,
        "X-BL": "en-US",
        "X-Website-Token": hashlib.sha256(raw.encode()).hexdigest(),
    })
    if content.get("type") != "file" or content.get("name") != FILENAME:
        raise SystemExit("FAIL: Gofile returned an unexpected file")
    expected_size = content["size"]
    if not 0 < expected_size <= MAX_BYTES:
        raise SystemExit("FAIL: file exceeds the 5-GiB test limit")
    url = content["link"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".gofile.io"):
        raise SystemExit("FAIL: download URL is not an HTTPS Gofile host")
    print(f"Resolved file: {FILENAME}", flush=True)
    print(f"Expected bytes: {expected_size}", flush=True)
    print("Temporary guest session ready; token is not logged", flush=True)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Cookie": "accountToken=" + token,
        "X-BL": "en-US",
    })
    opener = urllib.request.build_opener(NoRedirect())
    digest = hashlib.sha256()
    downloaded = 0
    started = time.monotonic()
    last_report = started
    with tempfile.TemporaryDirectory(prefix="clover-ota-") as directory:
        artifact = Path(directory) / FILENAME
        with opener.open(request, timeout=30) as response, artifact.open("wb") as output:
            print(f"HTTP status: {response.status}", flush=True)
            print(f"Content-Type: {response.headers.get('Content-Type')}", flush=True)
            chunk = response.read(4)
            if chunk != b"PK\x03\x04":
                raise SystemExit("FAIL: response is not a ZIP; refusing to download a landing page")
            while chunk:
                now = time.monotonic()
                if now - started > 600:
                    raise SystemExit("FAIL: download exceeded the 10-minute limit")
                downloaded += len(chunk)
                if downloaded > expected_size:
                    raise SystemExit("FAIL: download exceeded the advertised file size")
                output.write(chunk)
                digest.update(chunk)
                if now - last_report >= 10:
                    print(f"Progress: {downloaded}/{expected_size} bytes", flush=True)
                    last_report = now
                chunk = response.read(1024 * 1024)
        elapsed = time.monotonic() - started
        if downloaded != expected_size:
            raise SystemExit(f"FAIL: expected {expected_size} bytes, received {downloaded}")
        print(f"Downloaded bytes: {downloaded}", flush=True)
        print(f"Download seconds: {elapsed:.3f}", flush=True)
        print(f"Average MiB/s: {downloaded / elapsed / 1024 ** 2:.2f}", flush=True)
        with zipfile.ZipFile(artifact) as archive:
            if "payload.bin" not in archive.namelist():
                raise SystemExit("FAIL: ZIP does not contain payload.bin")
            bad = archive.testzip()
            if bad is not None:
                raise SystemExit(f"FAIL: ZIP CRC check failed for {bad}")
            print(f"ZIP entries: {len(archive.infolist())}", flush=True)
        print(f"SHA-256: {digest.hexdigest()}", flush=True)
        print("PASS: full OTA downloaded and ZIP CRC checks passed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"FAIL: HTTP {error.code} {error.reason}") from None
    except urllib.error.URLError as error:
        raise SystemExit(f"FAIL: network request failed: {error.reason}") from None
