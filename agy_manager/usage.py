"""Usage & Quota tracking module for Antigravity licenses.

Fetches live server-side quota buckets directly from Google's CloudCode / Antigravity API
(retrieveUserQuotaSummary) and tracks local token consumption history.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

RETRIEVE_QUOTA_SUMMARY_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
DB_PATH = Path.home() / ".config" / "agy-switcher" / "usage_history.db"


def parse_iso(timestamp_str: Optional[str]) -> Optional[datetime.datetime]:
    if not timestamp_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "teraz"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _preserved(existing_quota, now_dt, err):
    """Ostatnie znane limity, gdy Google nie odpowiada; kubelki po czasie resetu wracaja do 100%."""
    if not existing_quota or not isinstance(existing_quota, dict) or "gemini_5h" not in existing_quota:
        return None
    kept = json.loads(json.dumps(existing_quota))
    for b_name in ["gemini_5h", "gemini_weekly", "claude_5h", "claude_weekly"]:
        b = kept.get(b_name)
        if not isinstance(b, dict):
            continue
        r_dt = parse_iso(b.get("reset_iso"))
        if r_dt and r_dt <= now_dt:
            b.update(used_pct=0.0, remaining_pct=100.0, reset_formatted="teraz")
        elif r_dt:
            b["reset_formatted"] = format_duration(int((r_dt - now_dt).total_seconds()))
    kept["source"] = "preserved_after_error"
    kept["error"] = err
    return kept


def fetch_live_quota_from_google(
    access_token: str,
    email: Optional[str] = None,
    existing_quota: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Query live server-side quota buckets and tier directly from local LSP or Google CloudCode API."""
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    
    # 0. Check local Antigravity Language Server instances first (real-time session decrements)
    if email:
        try:
            from .local_lsp import discover_local_lsp_quotas
            local_map = discover_local_lsp_quotas()
            if email.lower() in local_map:
                return local_map[email.lower()]
        except Exception:
            pass

    # Zapamietane limity sa tylko zapasem, gdy Google nie odpowie (patrz _preserved).
    # Wczesniej wygrywaly z Google, a bez reset_iso nie wygasaly nigdy - aktywne konto
    # z 0% limitu 5h wisialo jako 80% i auto-rotacja nigdy nie przelaczala.
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    cloudaicompanion_proj = "aicode-consumers"
    tier_name = "Starter / Free Quota"
    has_pro = False

    # 1. Fetch current subscription tier from loadCodeAssist
    try:
        req_lca = urllib.request.Request(
            "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": "Antigravity/1.0",
            }
        )
        with urllib.request.urlopen(req_lca, timeout=6) as resp:
            lca_data = json.loads(resp.read().decode("utf-8"))
            cloudaicompanion_proj = lca_data.get("cloudaicompanionProject") or "aicode-consumers"
            paid_tier = lca_data.get("paidTier", {})
            paid_tier_id = paid_tier.get("id", "")
            has_pro = (paid_tier_id == "g1-pro-tier" or "pro" in paid_tier_id.lower())
            tier_name = paid_tier.get("name") or ("Google AI Pro" if has_pro else "Starter / Free Quota")
    except Exception:
        pass

    # 2. Fetch quota summary with project context
    req = urllib.request.Request(
        RETRIEVE_QUOTA_SUMMARY_URL,
        data=json.dumps({"project": cloudaicompanion_proj}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Antigravity/1.0",
        }
    )

    gemini_5h_used_pct = 0.0
    gemini_5h_remaining_pct = 100.0
    gemini_5h_reset = "teraz"
    gemini_has_5h = False

    gemini_weekly_used_pct = 0.0
    gemini_weekly_remaining_pct = 100.0
    gemini_weekly_reset = "teraz"

    claude_5h_used_pct = 0.0
    claude_5h_remaining_pct = 100.0
    claude_5h_reset = "teraz"
    claude_has_5h = False

    claude_weekly_used_pct = 0.0
    claude_weekly_remaining_pct = 100.0
    claude_weekly_reset = "teraz"
    gemini_5h_reset_iso = gemini_weekly_reset_iso = claude_5h_reset_iso = claude_weekly_reset_iso = None

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

            for group in data.get("groups", []):
                g_name = group.get("displayName", "")
                is_gemini = "Gemini" in g_name
                is_3p = "Claude" in g_name or "GPT" in g_name

                for bucket in group.get("buckets", []):
                    b_id = bucket.get("bucketId", "")
                    rem_fraction = float(bucket.get("remainingFraction", 1.0))
                    rem_pct = round(rem_fraction * 100.0, 1)
                    used_pct = round(max(0.0, min(100.0, (1.0 - rem_fraction) * 100.0)), 1)
                    
                    reset_time_str = bucket.get("resetTime")
                    reset_dt = parse_iso(reset_time_str)
                    reset_str = "teraz"
                    if reset_dt:
                        diff_sec = int((reset_dt - now_dt).total_seconds())
                        if diff_sec > 0:
                            reset_str = format_duration(diff_sec)

                    if is_gemini or "gemini" in b_id:
                        if "5h" in b_id or bucket.get("window") == "5h":
                            gemini_has_5h = True
                            gemini_5h_used_pct = used_pct
                            gemini_5h_remaining_pct = rem_pct
                            gemini_5h_reset = reset_str
                            gemini_5h_reset_iso = reset_time_str
                            # If existing quota was exhausted and reset is still in the future, don't overwrite with false 100%
                            if existing_quota and isinstance(existing_quota, dict):
                                old_5h = existing_quota.get("gemini_5h", {})
                                old_reset = parse_iso(old_5h.get("reset_iso"))
                                if old_5h.get("remaining_pct", 100.0) <= 10.0 and old_reset and (old_reset - now_dt).total_seconds() > 60:
                                    gemini_5h_remaining_pct = 0.0
                                    gemini_5h_used_pct = 100.0
                        elif "weekly" in b_id or bucket.get("window") == "weekly":
                            gemini_weekly_used_pct = used_pct
                            gemini_weekly_remaining_pct = rem_pct
                            gemini_weekly_reset = reset_str
                            gemini_weekly_reset_iso = reset_time_str

                    if is_3p or "3p" in b_id:
                        if "5h" in b_id or bucket.get("window") == "5h":
                            claude_has_5h = True
                            claude_5h_used_pct = used_pct
                            claude_5h_remaining_pct = rem_pct
                            claude_5h_reset = reset_str
                            claude_5h_reset_iso = reset_time_str
                        elif "weekly" in b_id or bucket.get("window") == "weekly":
                            claude_weekly_used_pct = used_pct
                            claude_weekly_remaining_pct = rem_pct
                            claude_weekly_reset = reset_str
                            claude_weekly_reset_iso = reset_time_str

            return {
                "has_pro_license": has_pro,
                "tier_name": tier_name,
                "project": cloudaicompanion_proj,
                "gemini_5h": {
                    "available": gemini_has_5h,
                    "used_pct": gemini_5h_used_pct,
                    "remaining_pct": gemini_5h_remaining_pct,
                    "reset_formatted": gemini_5h_reset,
                    "reset_iso": gemini_5h_reset_iso,
                },
                "gemini_weekly": {
                    "available": True,
                    "used_pct": gemini_weekly_used_pct,
                    "remaining_pct": gemini_weekly_remaining_pct,
                    "reset_formatted": gemini_weekly_reset,
                    "reset_iso": gemini_weekly_reset_iso,
                },
                "claude_5h": {
                    "available": claude_has_5h,
                    "used_pct": claude_5h_used_pct,
                    "remaining_pct": claude_5h_remaining_pct,
                    "reset_formatted": claude_5h_reset,
                    "reset_iso": claude_5h_reset_iso,
                },
                "claude_weekly": {
                    "available": True,
                    "used_pct": claude_weekly_used_pct,
                    "remaining_pct": claude_weekly_remaining_pct,
                    "reset_formatted": claude_weekly_reset,
                    "reset_iso": claude_weekly_reset_iso,
                },
                "error": None,
                "source": "google",
            }
    except urllib.error.HTTPError as e:
        kept = _preserved(existing_quota, now_dt, f"HTTP {e.code}")
        if kept:
            return kept
        return {
            "has_pro_license": has_pro,
            "tier_name": tier_name,
            "project": cloudaicompanion_proj,
            "gemini_5h": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "gemini_weekly": {"available": True, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "claude_5h": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "claude_weekly": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        kept = _preserved(existing_quota, now_dt, str(e))
        if kept:
            return kept
        return {
            "has_pro_license": has_pro,
            "tier_name": tier_name,
            "project": cloudaicompanion_proj,
            "gemini_5h": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "gemini_weekly": {"available": True, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "claude_5h": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "claude_weekly": {"available": False, "used_pct": 0.0, "remaining_pct": 0.0, "reset_formatted": "brak"},
            "error": str(e),
        }


def init_usage_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile TEXT NOT NULL,
            timestamp REAL NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            model TEXT DEFAULT '',
            statusline_5h_pct REAL,
            statusline_7d_pct REAL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_prof_time ON usage_events(profile, timestamp);")
    conn.commit()
    return conn


def get_profile_usage_summary(profile: str, live_quota: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Combine server live quota with local token history."""
    conn = init_usage_db()
    now = time.time()
    try:
        cur = conn.execute("SELECT SUM(total_tokens) FROM usage_events WHERE profile = ?", (profile,))
        total_all_time = cur.fetchone()[0] or 0

        cur = conn.execute("""
            SELECT SUM(total_tokens) FROM usage_events WHERE profile = ? AND timestamp >= ?
        """, (profile, now - 5 * 3600))
        tokens_5h = cur.fetchone()[0] or 0

        cur = conn.execute("""
            SELECT SUM(total_tokens) FROM usage_events WHERE profile = ? AND timestamp >= ?
        """, (profile, now - 7 * 86400))
        tokens_7d = cur.fetchone()[0] or 0

        cur = conn.execute("""
            SELECT timestamp, model
            FROM usage_events
            WHERE profile = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (profile,))
        latest = cur.fetchone()
    finally:
        conn.close()

    res = {
        "profile": profile,
        "tokens_5h": tokens_5h,
        "tokens_7d": tokens_7d,
        "total_all_time": total_all_time,
        "last_activity": latest[0] if latest else None,
        "last_model": latest[1] if latest else None,
    }

    if live_quota:
        res["live_quota"] = live_quota
        res["has_pro_license"] = live_quota.get("has_pro_license", False)
        res["tier_name"] = live_quota.get("tier_name", "Google AI Pro")
        res["gemini_5h"] = live_quota.get("gemini_5h", {})
        res["gemini_weekly"] = live_quota.get("gemini_weekly", {})
        res["claude_5h"] = live_quota.get("claude_5h", {})
        res["claude_weekly"] = live_quota.get("claude_weekly", {})
        res["error"] = live_quota.get("error")
    else:
        res["has_pro_license"] = True
        res["tier_name"] = "Google AI Pro"
        res["gemini_5h"] = {"used_pct": 0.0, "remaining_pct": 100.0, "reset_formatted": "teraz"}
        res["gemini_weekly"] = {"used_pct": 0.0, "remaining_pct": 100.0, "reset_formatted": "teraz"}
        res["claude_5h"] = {"used_pct": 0.0, "remaining_pct": 100.0, "reset_formatted": "teraz"}
        res["claude_weekly"] = {"used_pct": 0.0, "remaining_pct": 100.0, "reset_formatted": "teraz"}

    return res
