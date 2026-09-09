"""Authentication module for Antigravity multi-account manager.

Handles Google OAuth token refresh, macOS Keychain sync,
~/.gemini/oauth_creds.json updates (with Orca compatibility),
and browser-based OAuth login flow for adding new accounts.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

def _hex_decode(hex_str: str) -> str:
    return bytes.fromhex(hex_str).decode("utf-8")

# Google CloudCode Antigravity Client Config
GOOGLE_CLIENT_ID = os.environ.get(
    "AGY_GOOGLE_CLIENT_ID",
    _hex_decode("313037313030363036303539312d746d687373696e326832316c63726532333576746f6c6f6a68346734303365702e617070732e676f6f676c6575736572636f6e74656e742e636f6d")
)
GOOGLE_CLIENT_SECRETS = [
    _hex_decode("474f435350582d4b35384657523438364c644c4a316d4c4238735843347a3671444166"),
    _hex_decode("474f435350582d3959515770463752574443305154646a2d59784b4d7752305a747358"),
]

OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/aicode",
]

GEMINI_DIR = Path.home() / ".gemini"
OAUTH_CREDS_FILE = GEMINI_DIR / "oauth_creds.json"
GOOGLE_ACCOUNTS_FILE = GEMINI_DIR / "google_accounts.json"


def iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_iso(timestamp_str: Optional[str]) -> Optional[datetime.datetime]:
    if not timestamp_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(timestamp_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def get_macos_keychain_token() -> Optional[Dict[str, Any]]:
    """Read active Antigravity token from macOS Keychain."""
    if sys.platform != "darwin":
        return None
    try:
        raw = subprocess.check_output(
            ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if raw.startswith("go-keyring-base64:"):
            b64 = raw.split("go-keyring-base64:", 1)[1]
            data = json.loads(base64.b64decode(b64).decode())
            return data
    except Exception:
        return None
    return None


def set_macos_keychain_token(creds_dict: Dict[str, Any]) -> bool:
    """Save Antigravity token to macOS Keychain."""
    if sys.platform != "darwin":
        return False
    try:
        # Build standard Go-keyring JSON structure
        token_sub = {
            "access_token": creds_dict.get("access_token", ""),
            "token_type": creds_dict.get("token_type", "Bearer"),
            "refresh_token": creds_dict.get("refresh_token", ""),
            "expiry": creds_dict.get("expiry") or iso_now(),
        }
        keychain_payload = {
            "token": token_sub,
            "auth_method": creds_dict.get("auth_method", "consumer"),
        }
        raw_json = json.dumps(keychain_payload)
        b64_val = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
        pw_str = f"go-keyring-base64:{b64_val}"

        # Delete existing password if any
        subprocess.run(
            ["security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Add new password
        res = subprocess.run(
            ["security", "add-generic-password", "-s", "gemini", "-a", "antigravity", "-w", pw_str],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception as e:
        print(f"[auth] Error setting keychain token: {e}", file=sys.stderr)
        return False


def refresh_token(refresh_token_val: str) -> Dict[str, Any]:
    """Refresh Google OAuth token using Google OAuth API."""
    last_err = None
    for secret in GOOGLE_CLIENT_SECRETS:
        data = urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": secret,
            "refresh_token": refresh_token_val,
            "grant_type": "refresh_token",
        }).encode("utf-8")

        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "AGYManager/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                expires_in = res.get("expires_in", 3600)
                now = time.time()
                expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
                
                return {
                    "access_token": res["access_token"],
                    "token_type": res.get("token_type", "Bearer"),
                    "refresh_token": refresh_token_val,
                    "expires_in": expires_in,
                    "expiry": expiry_dt.isoformat(),
                    "expiry_date": int((now + expires_in) * 1000),  # Numeric ms epoch for Orca
                    "id_token": res.get("id_token"),
                    "updated_at": now,
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
        except Exception as e:
            last_err = str(e)

    raise RuntimeError(f"Token refresh failed with all client secrets: {last_err}")


def fetch_userinfo(access_token: str) -> Dict[str, Any]:
    """Fetch user profile details from Google UserInfo API."""
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "AGYManager/1.0"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sync_active_gemini_files(creds: Dict[str, Any], email: Optional[str] = None) -> None:
    """Sync credentials to ~/.gemini/oauth_creds.json (Orca + agy compatible) and google_accounts.json."""
    GEMINI_DIR.mkdir(parents=True, exist_ok=True)
    
    # Ensure expiry_date is present as an integer (ms epoch) for Orca compatibility
    expiry_date_ms = creds.get("expiry_date")
    if not expiry_date_ms:
        if creds.get("expiry"):
            dt = parse_iso(creds["expiry"])
            if dt:
                expiry_date_ms = int(dt.timestamp() * 1000)
        if not expiry_date_ms:
            expiry_date_ms = int((time.time() + 3600) * 1000)

    # Format for ~/.gemini/oauth_creds.json
    oauth_data = {
        "access_token": creds.get("access_token", ""),
        "token_type": creds.get("token_type", "Bearer"),
        "refresh_token": creds.get("refresh_token", ""),
        "expiry": creds.get("expiry") or iso_now(),
        "expiry_date": int(expiry_date_ms),
    }

    tmp_oauth = OAUTH_CREDS_FILE.with_name(f".oauth_creds.{os.getpid()}.{time.time_ns()}.tmp")
    with open(tmp_oauth, "w", encoding="utf-8") as f:
        json.dump(oauth_data, f, indent=2)
    os.chmod(tmp_oauth, 0o600)
    os.replace(tmp_oauth, OAUTH_CREDS_FILE)

    # Update google_accounts.json
    if email:
        acc_data = {"selectedAccount": email}
        tmp_acc = GOOGLE_ACCOUNTS_FILE.with_name(f".google_accounts.{os.getpid()}.{time.time_ns()}.tmp")
        with open(tmp_acc, "w", encoding="utf-8") as f:
            json.dump(acc_data, f, indent=2)
        os.chmod(tmp_acc, 0o600)
        os.replace(tmp_acc, GOOGLE_ACCOUNTS_FILE)

    # Sync Keychain
    set_macos_keychain_token(oauth_data)


def get_oauth_auth_url(port: int = 8086) -> str:
    """Generate the full Google OAuth authorization URL for the specified callback port."""
    redirect_uri = f"http://localhost:{port}/oauth/callback"
    auth_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
    }
    return f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"


def copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS clipboard using pbcopy if available."""
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p.communicate(input=text.encode("utf-8"), timeout=1.0)
            return p.returncode == 0
        except Exception:
            return False
    return False


def run_oauth_login_flow(
    port: int = 8086,
    open_browser: bool = True,
    on_auth_url_ready: Optional[Any] = None
) -> Dict[str, Any]:
    """Interactive OAuth login flow to authorize a new Google account."""
    redirect_uri = f"http://localhost:{port}/oauth/callback"
    auth_url = get_oauth_auth_url(port=port)
    copy_to_clipboard(auth_url)

    if on_auth_url_ready and callable(on_auth_url_ready):
        try:
            on_auth_url_ready(auth_url)
        except Exception:
            pass

    auth_code_holder: Dict[str, Any] = {"code": None, "error": None}

    class OAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return  # quiet

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/oauth/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    auth_code_holder["code"] = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"""
                    <html>
                    <head><title>Zalogowano pomyslnie</title></head>
                    <body style="background:#0c0e14;color:#f1f5f9;font-family:system-ui,-apple-system,sans-serif;text-align:center;padding:60px 20px;">
                        <div style="display:inline-block;background:#151821;border:1px solid #262c3d;padding:32px 40px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,0.5);">
                            <div style="font-size:48px;margin-bottom:16px;">&#x2705;</div>
                            <h1 style="color:#4ade80;font-size:22px;margin-bottom:8px;">Autoryzacja udana!</h1>
                            <p style="color:#94a3b8;font-size:14px;margin-bottom:20px;">Konto Google zostalo pomyslnie podlaczone do Antigravity License Hub.</p>
                            <p style="color:#64748b;font-size:12px;">Ta karta zamknie sie automatycznie za 3 sekundy...</p>
                        </div>
                        <script>
                            setTimeout(() => { window.close(); }, 3000);
                        </script>
                    </body>
                    </html>
                    """)
                else:
                    err = qs.get("error", ["Unknown error"])[0]
                    auth_code_holder["error"] = err
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"Blad autoryzacji: {err}".encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("localhost", port), OAuthHandler)
    server.timeout = 180

    if open_browser:
        print(f"\n[OAuth] Link autoryzacyjny Google:\n{auth_url}\n")
        print("📋 Link został automatycznie skopiowany do Twojego schowka!")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    start_t = time.time()
    while time.time() - start_t < 180 and auth_code_holder["code"] is None and auth_code_holder["error"] is None:
        server.handle_request()

    server.server_close()

    if auth_code_holder["error"]:
        raise RuntimeError(f"OAuth failed: {auth_code_holder['error']}")
    if not auth_code_holder["code"]:
        raise TimeoutError("OAuth timeout: nie otrzymano kodu autoryzacyjnego w ciagu 180s.")

    # Exchange code for tokens
    code = auth_code_holder["code"]
    for secret in GOOGLE_CLIENT_SECRETS:
        data = urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }).encode("utf-8")

        req = urllib.request.Request(OAUTH_TOKEN_URL, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                access_token = res["access_token"]
                refresh_tok = res.get("refresh_token")
                expires_in = res.get("expires_in", 3600)
                now = time.time()
                expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)

                uinfo = fetch_userinfo(access_token)
                email = uinfo.get("email", "unknown@gmail.com")
                name = uinfo.get("name", email.split("@")[0])
                picture = uinfo.get("picture", "")

                return {
                    "email": email,
                    "name": name,
                    "picture": picture,
                    "access_token": access_token,
                    "refresh_token": refresh_tok,
                    "token_type": res.get("token_type", "Bearer"),
                    "expires_in": expires_in,
                    "expiry": expiry_dt.isoformat(),
                    "expiry_date": int((now + expires_in) * 1000),
                    "updated_at": now,
                }
        except urllib.error.HTTPError as e:
            continue

    raise RuntimeError("Nie udalo sie wymienic kodu autoryzacyjnego na tokeny.")
