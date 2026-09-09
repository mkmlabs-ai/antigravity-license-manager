"""Profile repository and switching manager for Antigravity licenses."""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import (
    fetch_userinfo,
    get_macos_keychain_token,
    iso_now,
    parse_iso,
    refresh_token,
    sync_active_gemini_files,
)
from .usage import fetch_live_quota_from_google, get_profile_usage_summary

CONFIG_DIR = Path.home() / ".config" / "agy-switcher"
PROFILES_DIR = CONFIG_DIR / "profiles"
METADATA_FILE = CONFIG_DIR / "profiles.json"
ACTIVE_FILE = CONFIG_DIR / "active"
LOCK_FILE = CONFIG_DIR / "manager.lock"


def sanitize_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower()).strip("_")
    return cleaned or "account"


class ProfileManager:
    _lock_depth = 0
    _lock_fd = None
    _thread_lock = threading.RLock()

    def __init__(self, config_dir: Path = CONFIG_DIR):
        self.config_dir = config_dir
        self.profiles_dir = self.config_dir / "profiles"
        self.metadata_file = self.config_dir / "profiles.json"
        self.active_file = self.config_dir / "active"
        self.lock_file = self.config_dir / "manager.lock"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with ProfileManager._thread_lock:
            if ProfileManager._lock_depth == 0:
                ProfileManager._lock_fd = open(self.lock_file, "a+")
                fcntl.flock(ProfileManager._lock_fd.fileno(), fcntl.LOCK_EX)
            ProfileManager._lock_depth += 1
            try:
                yield
            finally:
                ProfileManager._lock_depth -= 1
                if ProfileManager._lock_depth == 0:
                    if ProfileManager._lock_fd:
                        try:
                            fcntl.flock(ProfileManager._lock_fd.fileno(), fcntl.LOCK_UN)
                            ProfileManager._lock_fd.close()
                        except Exception:
                            pass
                        ProfileManager._lock_fd = None

    def _load_metadata(self) -> Dict[str, Any]:
        if not self.metadata_file.exists():
            return {}
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_metadata(self, data: Dict[str, Any]) -> None:
        tmp = self.metadata_file.with_name(f".profiles.{os.getpid()}.{time.time_ns()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.metadata_file)

    def get_active_id(self) -> Optional[str]:
        if self.active_file.exists():
            try:
                name = self.active_file.read_text(encoding="utf-8").strip()
                if name:
                    return name
            except Exception:
                pass
        return None

    def set_active_id(self, profile_id: str) -> None:
        tmp = self.active_file.with_name(f".active.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(profile_id, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.active_file)

    def list_profiles(self) -> List[Dict[str, Any]]:
        with self.lock():
            metadata = self._load_metadata()
            active_id = self.get_active_id()

            profiles = []
            for pid, pdata in metadata.items():
                p = pdata.copy()
                p["id"] = pid
                p["is_active"] = (pid == active_id)

                # Attach usage and live quota
                live_q = p.get("live_quota")
                usage = get_profile_usage_summary(pid, live_q)
                p["usage"] = usage

                # Check token expiry status
                expiry_dt = parse_iso(p.get("expiry"))
                now = time.time()
                if expiry_dt and expiry_dt.timestamp() < now:
                    p["token_status"] = "needs_refresh"
                else:
                    p["token_status"] = "valid"

                profiles.append(p)

            # Sort: active first, then alphabetically by email
            profiles.sort(key=lambda x: (not x["is_active"], x.get("email", "")))
            return profiles

    def get_profile(self, id_or_email: str) -> Optional[Dict[str, Any]]:
        with self.lock():
            metadata = self._load_metadata()
            # Direct match
            if id_or_email in metadata:
                res = metadata[id_or_email].copy()
                res["id"] = id_or_email
                res["is_active"] = (id_or_email == self.get_active_id())
                return res
            # Match by email
            for pid, pdata in metadata.items():
                if pdata.get("email", "").lower() == id_or_email.lower():
                    res = pdata.copy()
                    res["id"] = pid
                    res["is_active"] = (pid == self.get_active_id())
                    return res
            return None

    def add_or_update_profile(self, creds: Dict[str, Any], custom_id: Optional[str] = None) -> str:
        """Add or update profile with given OAuth credentials."""
        with self.lock():
            email = creds.get("email") or "unknown@gmail.com"
            pid = custom_id or sanitize_id(email.split("@")[0])

            # Ensure profile directory exists
            p_dir = self.profiles_dir / pid
            p_dir.mkdir(parents=True, exist_ok=True)

            # Save profile oauth_creds.json
            oauth_file = p_dir / "oauth_creds.json"
            oauth_data = {
                "access_token": creds.get("access_token", ""),
                "refresh_token": creds.get("refresh_token", ""),
                "token_type": creds.get("token_type", "Bearer"),
                "expiry": creds.get("expiry") or iso_now(),
                "expiry_date": creds.get("expiry_date") or int((time.time() + 3600) * 1000),
            }
            tmp = oauth_file.with_name(f".oauth.{os.getpid()}.{time.time_ns()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(oauth_data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, oauth_file)

            metadata = self._load_metadata()
            existing = metadata.get(pid, {})
            # Fetch live Google quota (check local LSP first, then preserve unexpired decrements, then remote API)
            live_quota = fetch_live_quota_from_google(
                oauth_data["access_token"],
                email=email,
                existing_quota=existing.get("live_quota")
            )
            metadata[pid] = {
                "id": pid,
                "email": email,
                "name": creds.get("name") or existing.get("name") or email.split("@")[0],
                "picture": creds.get("picture") or existing.get("picture", ""),
                "added_at": existing.get("added_at") or iso_now(),
                "last_used": existing.get("last_used") or iso_now(),
                "last_refreshed": iso_now(),
                "expiry": oauth_data["expiry"],
                "expiry_date": oauth_data["expiry_date"],
                "status": "ready" if live_quota.get("has_pro_license") else "starter",
                "live_quota": live_quota,
            }
            self._save_metadata(metadata)

            # If no active profile, make this one active
            if not self.get_active_id():
                self._switch_profile_unlocked(pid)

            return pid

    def _switch_profile_unlocked(self, id_or_email: str) -> bool:
        target = self.get_profile(id_or_email)
        if not target:
            raise ValueError(f"Profil '{id_or_email}' nie został znaleziony.")

        pid = target["id"]
        p_dir = self.profiles_dir / pid
        oauth_file = p_dir / "oauth_creds.json"
        if not oauth_file.exists():
            raise FileNotFoundError(f"Brak pliku credentials dla profilu '{pid}'.")

        with open(oauth_file, "r", encoding="utf-8") as f:
            creds = json.load(f)

        # If token expired or close to expiring (< 5 mins), refresh now before activating
        expiry_dt = parse_iso(creds.get("expiry"))
        if not expiry_dt or expiry_dt.timestamp() <= time.time() + 300:
            if creds.get("refresh_token"):
                try:
                    refreshed = refresh_token(creds["refresh_token"])
                    creds.update(refreshed)
                    with open(oauth_file, "w", encoding="utf-8") as f:
                        json.dump(creds, f, indent=2)
                except Exception as e:
                    print(f"[switch] Warning refreshing token: {e}")

        # Sync active files for agy and Orca
        sync_active_gemini_files(creds, target.get("email"))

        # Update active pointer & metadata
        self.set_active_id(pid)
        metadata = self._load_metadata()
        if pid in metadata:
            metadata[pid]["last_used"] = iso_now()
            self._save_metadata(metadata)

        return True

    def switch_profile(self, id_or_email: str) -> bool:
        """Atomically switch active Antigravity profile in Keychain, Gemini config, and Orca."""
        with self.lock():
            return self._switch_profile_unlocked(id_or_email)

    def refresh_profile_token(self, id_or_email: str) -> Dict[str, Any]:
        """Refresh token for a specific profile and query live quota."""
        with self.lock():
            target = self.get_profile(id_or_email)
            if not target:
                raise ValueError(f"Profil '{id_or_email}' nie istnieje.")

            pid = target["id"]
            p_dir = self.profiles_dir / pid
            oauth_file = p_dir / "oauth_creds.json"
            if not oauth_file.exists():
                raise FileNotFoundError(f"Brak credentials dla profilu '{pid}'.")

            with open(oauth_file, "r", encoding="utf-8") as f:
                creds = json.load(f)

            refresh_tok = creds.get("refresh_token")
            if not refresh_tok:
                raise RuntimeError(f"Profil '{pid}' nie posiada refresh_token.")

            refreshed = refresh_token(refresh_tok)
            creds.update(refreshed)

            with open(oauth_file, "w", encoding="utf-8") as f:
                json.dump(creds, f, indent=2)

            # Query live Google quota (check local LSP first, then preserve unexpired decrements, then remote API)
            live_quota = fetch_live_quota_from_google(
                refreshed["access_token"],
                email=target.get("email"),
                existing_quota=target.get("live_quota")
            )

            # Update metadata
            metadata = self._load_metadata()
            if pid in metadata:
                metadata[pid]["last_refreshed"] = iso_now()
                metadata[pid]["expiry"] = refreshed["expiry"]
                metadata[pid]["expiry_date"] = refreshed["expiry_date"]
                metadata[pid]["status"] = "ready" if live_quota.get("has_pro_license") else "starter"
                metadata[pid]["live_quota"] = live_quota
                self._save_metadata(metadata)

            # If this is the active profile, also sync ~/.gemini and Keychain
            if pid == self.get_active_id():
                sync_active_gemini_files(creds, target.get("email"))

            refreshed["live_quota"] = live_quota
            return refreshed

    def refresh_all(self) -> Dict[str, Any]:
        """Refresh all registered profiles and keep all tokens active."""
        results = {}
        with self.lock():
            metadata = self._load_metadata()

        for pid in list(metadata.keys()):
            try:
                ref = self.refresh_profile_token(pid)
                results[pid] = {"success": True, "status": "refreshed", "tier": ref.get("live_quota", {}).get("tier_name")}
            except Exception as e:
                results[pid] = {"success": False, "error": str(e)}

        return results

    def remove_profile(self, id_or_email: str) -> bool:
        with self.lock():
            target = self.get_profile(id_or_email)
            if not target:
                return False
            pid = target["id"]
            p_dir = self.profiles_dir / pid
            if p_dir.exists():
                shutil.rmtree(p_dir, ignore_errors=True)

            metadata = self._load_metadata()
            if pid in metadata:
                del metadata[pid]
                self._save_metadata(metadata)

            if self.get_active_id() == pid:
                if self.active_file.exists():
                    self.active_file.unlink(missing_ok=True)
                remaining = list(metadata.keys())
                if remaining:
                    self._switch_profile_unlocked(remaining[0])

            return True

    def import_existing_profiles(self) -> int:
        """Scan ~/.config/agy-pool and active keychain to import all known accounts."""
        imported_count = 0
        
        # 1. Check legacy agy-pool profiles
        legacy_dir = Path.home() / ".config" / "agy-pool" / "profiles"
        if legacy_dir.is_dir():
            for entry in legacy_dir.iterdir():
                if entry.is_dir():
                    oauth_f = entry / "oauth_creds.json"
                    if oauth_f.exists():
                        try:
                            with open(oauth_f, "r", encoding="utf-8") as f:
                                c = json.load(f)
                            if c.get("refresh_token"):
                                try:
                                    ref = refresh_token(c["refresh_token"])
                                    u = fetch_userinfo(ref["access_token"])
                                    ref["email"] = u.get("email")
                                    ref["name"] = u.get("name")
                                    ref["picture"] = u.get("picture", "")
                                    self.add_or_update_profile(ref, custom_id=entry.name)
                                    imported_count += 1
                                except Exception as e:
                                    print(f"[import] Skipping '{entry.name}': {e}")
                        except Exception:
                            pass

        # 2. Check active keychain
        kc = get_macos_keychain_token()
        if kc and isinstance(kc, dict):
            token_sub = kc.get("token", {})
            ref_tok = token_sub.get("refresh_token")
            if ref_tok:
                try:
                    ref = refresh_token(ref_tok)
                    u = fetch_userinfo(ref["access_token"])
                    ref["email"] = u.get("email")
                    ref["name"] = u.get("name")
                    ref["picture"] = u.get("picture", "")
                    pid = sanitize_id(ref["email"].split("@")[0])
                    self.add_or_update_profile(ref, custom_id=pid)
                    imported_count += 1
                except Exception as e:
                    print(f"[import] Skipping keychain account: {e}")

        return imported_count

    def export_bundle(self, output_path: Optional[Path] = None) -> Path:
        """Export all registered profile credentials into a portable, importable bundle file."""
        with self.lock():
            metadata = self._load_metadata()
            bundle = {
                "format": "antigravity-license-bundle-v1",
                "exported_at": iso_now(),
                "profiles": []
            }
            for pid, pdata in metadata.items():
                oauth_f = self.profiles_dir / pid / "oauth_creds.json"
                if oauth_f.exists():
                    try:
                        with open(oauth_f, "r", encoding="utf-8") as f:
                            creds = json.load(f)
                        bundle["profiles"].append({
                            "id": pid,
                            "email": pdata.get("email"),
                            "name": pdata.get("name"),
                            "picture": pdata.get("picture", ""),
                            "creds": creds,
                            "status": pdata.get("status", "ready"),
                        })
                    except Exception:
                        pass

            if not output_path:
                output_path = Path.home() / "antigravity_licenses.bundle"

            raw_json = json.dumps(bundle, indent=2)
            payload = {
                "type": "AGY_LICENSE_BUNDLE",
                "version": 1,
                "exported_at": iso_now(),
                "count": len(bundle["profiles"]),
                "data_b64": base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.chmod(output_path, 0o600)
            return output_path

    def import_bundle(self, bundle_path: Path) -> int:
        """Import all profile credentials from a license bundle file."""
        with open(bundle_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        bundle_obj = None
        if "data_b64" in data:
            raw_json = base64.b64decode(data["data_b64"]).decode("utf-8")
            bundle_obj = json.loads(raw_json)
        elif "profiles" in data:
            bundle_obj = data
        else:
            raise ValueError("Nieprawidłowy format pliku paczki licencji.")

        imported_count = 0
        for item in bundle_obj.get("profiles", []):
            creds = item.get("creds", {})
            if creds.get("refresh_token") or creds.get("access_token"):
                creds["email"] = item.get("email") or creds.get("email")
                creds["name"] = item.get("name") or creds.get("name")
                creds["picture"] = item.get("picture", "")
                pid = item.get("id") or sanitize_id(creds["email"].split("@")[0])
                try:
                    self.add_or_update_profile(creds, custom_id=pid)
                    imported_count += 1
                except Exception as e:
                    print(f"[import] Error importing '{pid}': {e}")

        # Refresh imported accounts and set the best Pro profile as active
        try:
            self.refresh_all()
            best = self.find_best_profile()
            if best:
                self.switch_profile(best["id"])
        except Exception:
            pass

        return imported_count

    def score_profile(self, profile: Dict[str, Any]) -> float:
        """Compute readiness score for given profile based on subscription tier and live limits.
        
        Only Google AI Pro accounts are eligible for automatic rotation/selection.
        Starter accounts remain in the database for manual switching only.
        """
        u = profile.get("usage", {})
        is_pro = u.get("has_pro_license", False)
        
        # Rule: Do not auto-select or auto-rotate Starter accounts (manual switch only)
        if not is_pro:
            return -2000.0

        g5 = u.get("gemini_5h", {})
        gw = u.get("gemini_weekly", {})

        rem_5h = float(g5.get("remaining_pct", 100.0)) if g5.get("available") else 100.0
        rem_7d = float(gw.get("remaining_pct", 100.0)) if gw.get("available") else 100.0

        # Hard blocks: if 7-day limit is exhausted (<= 5%), account is completely blocked
        if rem_7d <= 5.0:
            return -1000.0

        # 5-hour limit exhausted (<= 5%): temporarily blocked
        if g5.get("available") and rem_5h <= 5.0:
            return -500.0

        # Pro tier baseline preference (+5000)
        score = 5000.0

        # 7-day limit weight: 10x
        score += rem_7d * 10.0

        # 5-hour limit weight: 2x
        score += rem_5h * 2.0

        return score

    def find_best_profile(self) -> Optional[Dict[str, Any]]:
        """Identify the single best Pro profile with the highest remaining quota and health."""
        profiles = self.list_profiles()
        valid_pro = [p for p in profiles if self.score_profile(p) > 0]
        if not valid_pro:
            # If all Pro accounts are exhausted, fallback to Pro account with closest reset
            pro_accounts = [p for p in profiles if p.get("usage", {}).get("has_pro_license")]
            if pro_accounts:
                return pro_accounts[0]
            return profiles[0] if profiles else None
        return max(valid_pro, key=self.score_profile)

    def auto_rotate_if_needed(self, threshold_pct: float = 10.0) -> Dict[str, Any]:
        """Check active profile and auto-switch if active profile is near exhaustion or blocked."""
        with self.lock():
            profiles = self.list_profiles()
            active_id = self.get_active_id()
            active_p = next((p for p in profiles if p["id"] == active_id), None)

            needs_switch = False
            switch_reason = ""

            if not active_p:
                needs_switch = True
                switch_reason = "Brak aktywnego profilu"
            else:
                u = active_p.get("usage", {})
                is_pro = u.get("has_pro_license", False)
                g5 = u.get("gemini_5h", {})
                gw = u.get("gemini_weekly", {})
                rem_5h = float(g5.get("remaining_pct", 100.0)) if g5.get("available") else 100.0
                rem_7d = float(gw.get("remaining_pct", 100.0)) if gw.get("available") else 100.0

                if not is_pro:
                    # Active is Starter, rotate to Pro if any Pro account is ready
                    best_pro = self.find_best_profile()
                    if best_pro and best_pro.get("usage", {}).get("has_pro_license") and self.score_profile(best_pro) > 0:
                        needs_switch = True
                        switch_reason = "Aktywna licencja to Starter — rotacja na konto Google AI Pro"
                elif rem_7d <= threshold_pct:
                    needs_switch = True
                    switch_reason = f"Wyczerpany limit 7-dniowy ({rem_7d:.1f}% zostało)"
                elif g5.get("available") and rem_5h <= threshold_pct:
                    needs_switch = True
                    switch_reason = f"Wyczerpany limit 5-godzinny ({rem_5h:.1f}% zostało)"

            if not needs_switch:
                return {
                    "switched": False,
                    "active_id": active_id,
                    "active_email": active_p.get("email") if active_p else None,
                    "reason": "Aktywna licencja Pro posiada wystarczający limit.",
                }

            # Find best Pro candidate
            best = self.find_best_profile()
            if not best or best["id"] == active_id:
                return {
                    "switched": False,
                    "active_id": active_id,
                    "reason": "Brak lepszego dostępnego konta Pro do rotacji.",
                }

            target_id = best["id"]
            self._switch_profile_unlocked(target_id)

            return {
                "switched": True,
                "previous_id": active_id,
                "previous_email": active_p.get("email") if active_p else None,
                "new_id": target_id,
                "new_email": best.get("email"),
                "reason": switch_reason,
                "new_quota": best.get("usage"),
            }
