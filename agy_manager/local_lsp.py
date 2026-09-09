"""Local Antigravity Language Server discovery and Connect RPC client.

Discovers local agy-real / language_server processes and queries their Connect RPC
endpoints (/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary and GetUserStatus)
to get exact, real-time per-account quota fractions and reset timestamps.
"""

from __future__ import annotations

import datetime
import json
import ssl
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


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


def discover_local_lsp_quotas() -> Dict[str, Dict[str, Any]]:
    """Scan local agy-real listening ports and retrieve live quota for all running accounts."""
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-c", "agy-real"],
            text=True,
            timeout=1.0,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return {}

    ports = set()
    for line in out.splitlines():
        if "LISTEN" in line:
            parts = line.split()
            for p in parts:
                if ":" in p:
                    try:
                        port = int(p.split(":")[-1])
                        ports.add(port)
                    except ValueError:
                        pass

    results: Dict[str, Dict[str, Any]] = {}
    ctx = ssl._create_unverified_context()
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    for port in sorted(ports):
        for proto in ["http", "https"]:
            status_url = f"{proto}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
            quota_url = f"{proto}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"

            try:
                req_st = urllib.request.Request(
                    status_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req_st, context=ctx, timeout=0.25) as resp:
                    st_data = json.loads(resp.read().decode("utf-8"))

                user_status = st_data.get("userStatus", {})
                email = user_status.get("email")
                if not email:
                    continue

                req_q = urllib.request.Request(
                    quota_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req_q, context=ctx, timeout=0.25) as resp:
                    q_data = json.loads(resp.read().decode("utf-8"))

                q_resp = q_data.get("response", {})
                
                # Parse buckets
                has_pro = False
                tier_info = user_status.get("userTier", {})
                tier_id = tier_info.get("id", "")
                if tier_id == "g1-pro-tier" or "pro" in tier_id.lower() or "PRO" in str(user_status.get("planStatus", {})):
                    has_pro = True
                tier_name = tier_info.get("name") or ("Google AI Pro" if has_pro else "Antigravity Starter Quota")

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

                for group in q_resp.get("groups", []):
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

                results[email.lower()] = {
                    "has_pro_license": has_pro,
                    "tier_name": tier_name,
                    "project": "aicode-consumers",
                    "source": "local_lsp",
                    "port": port,
                    "gemini_5h": {
                        "available": gemini_has_5h,
                        "used_pct": gemini_5h_used_pct,
                        "remaining_pct": gemini_5h_remaining_pct,
                        "reset_formatted": gemini_5h_reset,
                        "reset_iso": gemini_5h_reset_iso if gemini_has_5h else None,
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
                        "reset_iso": claude_5h_reset_iso if claude_has_5h else None,
                    },
                    "claude_weekly": {
                        "available": True,
                        "used_pct": claude_weekly_used_pct,
                        "remaining_pct": claude_weekly_remaining_pct,
                        "reset_formatted": claude_weekly_reset,
                        "reset_iso": claude_weekly_reset_iso,
                    },
                    "error": None,
                }
                break
            except Exception:
                continue

    return results


def get_ancestor_agy_pid() -> Optional[int]:
    """Find PID of the parent/ancestor agy-real process that spawned this statusline invocation."""
    import os
    curr = os.getppid()
    while curr > 1:
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(curr), "-o", "ppid=,comm="],
                text=True,
                timeout=0.2,
                stderr=subprocess.DEVNULL,
            ).strip()
            if not out:
                break
            parts = out.split()
            ppid = int(parts[0])
            comm = " ".join(parts[1:])
            if "agy" in comm or "antigravity" in comm or "language_server" in comm:
                return curr
            curr = ppid
        except Exception:
            break
    return None


def get_quota_for_pid(pid: int) -> Optional[Dict[str, Any]]:
    """Query live quota directly from the specific Language Server belonging to given PID."""
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            text=True,
            timeout=0.3,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    ports = []
    for line in out.splitlines():
        if "LISTEN" in line:
            for p in line.split():
                if ":" in p:
                    try:
                        ports.append(int(p.split(":")[-1]))
                    except ValueError:
                        pass

    ctx = ssl._create_unverified_context()
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    for port in sorted(ports):
        for proto in ["http", "https"]:
            status_url = f"{proto}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
            quota_url = f"{proto}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"

            try:
                req_st = urllib.request.Request(
                    status_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req_st, context=ctx, timeout=0.25) as resp:
                    st_data = json.loads(resp.read().decode("utf-8"))

                user_status = st_data.get("userStatus", {})
                email = user_status.get("email")
                if not email:
                    continue

                req_q = urllib.request.Request(
                    quota_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req_q, context=ctx, timeout=0.25) as resp:
                    q_data = json.loads(resp.read().decode("utf-8"))

                q_resp = q_data.get("response", {})

                has_pro = False
                tier_info = user_status.get("userTier", {})
                tier_id = tier_info.get("id", "")
                if tier_id == "g1-pro-tier" or "pro" in tier_id.lower() or "PRO" in str(user_status.get("planStatus", {})):
                    has_pro = True
                tier_name = tier_info.get("name") or ("Google AI Pro" if has_pro else "Antigravity Starter Quota")

                gemini_5h_used_pct = 0.0
                gemini_5h_remaining_pct = 100.0
                gemini_5h_reset = "teraz"
                gemini_has_5h = False
                gemini_5h_reset_iso = None

                gemini_weekly_used_pct = 0.0
                gemini_weekly_remaining_pct = 100.0
                gemini_weekly_reset = "teraz"
                gemini_weekly_reset_iso = None

                claude_5h_used_pct = 0.0
                claude_5h_remaining_pct = 100.0
                claude_5h_reset = "teraz"
                claude_has_5h = False
                claude_5h_reset_iso = None

                claude_weekly_used_pct = 0.0
                claude_weekly_remaining_pct = 100.0
                claude_weekly_reset = "teraz"
                claude_weekly_reset_iso = None

                for group in q_resp.get("groups", []):
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
                    "email": email,
                    "has_pro_license": has_pro,
                    "tier_name": tier_name,
                    "project": "aicode-consumers",
                    "source": "local_window_lsp",
                    "pid": pid,
                    "port": port,
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
                }
            except Exception:
                continue

    return None
