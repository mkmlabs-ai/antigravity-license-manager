"""CLI Interface for Antigravity License Hub & Switcher."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import List

from .auth import run_oauth_login_flow
from .daemon import run_daemon
from .profiles import ProfileManager
from .web_server import start_server

RST = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[38;5;73m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;221m"
RED = "\033[38;5;203m"
PURPLE = "\033[38;5;141m"
GRAY = "\033[38;5;244m"
DIM = "\033[2m"


def format_bar(pct: float, width: int = 8) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round(clamped * width / 100.0))
    empty = width - filled
    col = RED if clamped >= 85 else (YELLOW if clamped >= 65 else GREEN)
    return f"{col}{'█' * filled}{GRAY}{'░' * empty}{RST}"


def cmd_list(pm: ProfileManager):
    profiles = pm.list_profiles()
    active_id = pm.get_active_id()

    print(f"\n{BOLD}{CYAN}✦ Antigravity License Hub — Lista kont i limity Google:{RST}\n")
    if not profiles:
        print(f"{GRAY}Brak zarejestrowanych kont. Użyj: agy-switch add{RST}\n")
        return

    print(f"{'#':<3} {'ID':<15} {'EMAIL':<30} {'TIER':<18} {'GEMINI 5H':<18} {'GEMINI 7D':<18} {'STATUS'}")
    print(f"{'-'*3} {'-'*15} {'-'*30} {'-'*18} {'-'*18} {'-'*18} {'-'*10}")

    for idx, p in enumerate(profiles, 1):
        pid = p["id"]
        email = p.get("email", "")
        u = p.get("usage", {})
        is_pro = u.get("has_pro_license", False)
        tier = u.get("tier_name", "Google AI Pro" if is_pro else "Starter")

        tier_col = f"{PURPLE}✦ {tier}{RST}" if is_pro else f"{YELLOW}{tier}{RST}"

        g5 = u.get("gemini_5h", {})
        gw = u.get("gemini_weekly", {})
        g5_used = g5.get("used_pct", 0.0)
        gw_used = gw.get("used_pct", 0.0)
        r5 = g5.get("reset_formatted", "teraz")
        rw = gw.get("reset_formatted", "teraz")

        if g5.get("available", False):
            bar5 = f"{format_bar(g5_used, 5)} {g5_used:>4.1f}%"
        else:
            bar5 = f"{GRAY}brak 5h{RST}"
        barw = f"{format_bar(gw_used, 5)} {gw_used:>4.1f}%"

        is_act = p["is_active"]
        stat_str = f"{GREEN}● AKTYWNA{RST}" if is_act else f"{GRAY}○ Gotowa{RST}"

        print(f"{idx:<3} {BOLD}{pid:<15}{RST} {email:<30} {tier_col:<28} {bar5:<26} {barw:<26} {stat_str}")
        sub_5h = f"Reset 5h: {r5} · " if g5.get("available") else ""
        print(f"    {DIM}└─ {sub_5h}Reset 7d: {rw} · Claude 7d: {u.get('claude_weekly', {}).get('used_pct', 0.0)}%{RST}")

    print()


def cmd_switch(pm: ProfileManager, target: str):
    try:
        pm.switch_profile(target)
        active = pm.get_active_id()
        prof = pm.get_profile(active) if active else None
        email = prof.get("email") if prof else active
        print(f"\n{GREEN}✓ Pomyślnie przełączono Antigravity na konto:{RST} {BOLD}{email}{RST} (ID: {active})")
        print(f"{DIM}Wszystkie nowe terminale i okna Orki natychmiast korzystają z tej licencji.{RST}\n")
    except Exception as e:
        print(f"\n{RED}✗ Błąd przełączania: {e}{RST}\n", file=sys.stderr)
        sys.exit(1)


def cmd_interactive_switch(pm: ProfileManager):
    profiles = pm.list_profiles()
    if not profiles:
        print(f"{YELLOW}Brak zarejestrowanych kont. Użyj: agy-switch add{RST}")
        return

    cmd_list(pm)
    try:
        choice = input(f"{BOLD}Wybierz numer lub ID konta do aktywacji [1-{len(profiles)}]: {RST}").strip()
        if not choice:
            return
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(profiles):
                cmd_switch(pm, profiles[idx]["id"])
            else:
                print(f"{RED}Niepoprawny numer.{RST}")
        else:
            cmd_switch(pm, choice)
    except (KeyboardInterrupt, EOFError):
        print()


def cmd_refresh(pm: ProfileManager):
    print(f"\n{CYAN}Odświeżanie tokenów OAuth i limitów Google dla wszystkich kont...{RST}")
    results = pm.refresh_all()
    for pid, res in results.items():
        if res.get("success"):
            print(f" {GREEN}✓{RST} Konto [{pid}]: Odświeżono pomyślnie ({res.get('tier')})")
        else:
            print(f" {RED}✗{RST} Konto [{pid}]: {res.get('error')}")
    print(f"\n{GREEN}✓ Gotowe. Wszystkie aktywne sesje zachowane.{RST}\n")


def cmd_add(pm: ProfileManager):
    print(f"\n{CYAN}Inicjalizacja dodawania nowego konta Google...{RST}")
    try:
        creds = run_oauth_login_flow(port=8086)
        pid = pm.add_or_update_profile(creds)
        print(f"\n{GREEN}✓ Pomyślnie dodano konto:{RST} {BOLD}{creds.get('email')}{RST} (ID: {pid})")
        print(f"{DIM}Konto zostało zarejestrowane i będzie automatycznie utrzymywane przy życiu.{RST}\n")
    except Exception as e:
        print(f"\n{RED}✗ Błąd podczas logowania: {e}{RST}\n", file=sys.stderr)
        sys.exit(1)


def cmd_best(pm: ProfileManager):
    print(f"\n{CYAN}Wyszukiwanie optymalnego konta Google o największym dostępnym limicie...{RST}")
    best = pm.find_best_profile()
    if not best:
        print(f"{RED}✗ Brak dostępnych zarejestrowanych kont.{RST}\n")
        return

    active_id = pm.get_active_id()
    if best["id"] == active_id:
        print(f"{GREEN}✓ Aktualnie aktywne konto [{BOLD}{best['id']}{RST}{GREEN}] posiada już największy dostępny limit.{RST}\n")
        return

    print(f"{YELLOW}⚡ Przełączanie na najlepsze dostępne konto:{RST} {BOLD}{best.get('email')}{RST} (ID: {best['id']})")
    cmd_switch(pm, best["id"])


def cmd_auto_rotate(pm: ProfileManager):
    res = pm.auto_rotate_if_needed(threshold_pct=10.0)
    if res.get("switched"):
        print(f"\n{YELLOW}⚡ AUTO-ROTACJA:{RST} Wykryto wyczerpanie limitu na koncie {BOLD}{res.get('previous_email')}{RST} ({res.get('reason')}).")
        print(f"{GREEN}✓ Pomyślnie przełączono na optymalne konto:{RST} {BOLD}{res.get('new_email')}{RST} (ID: {res.get('new_id')})\n")
    else:
        print(f"\n{GREEN}✓ {res.get('reason')}{RST} Aktywne konto: {BOLD}{res.get('active_email') or res.get('active_id')}{RST}\n")


def cmd_export(pm: ProfileManager, path_str: Optional[str] = None):
    out_p = Path(path_str).expanduser() if path_str else Path.home() / "antigravity_licenses.bundle"
    print(f"\n{CYAN}Eksportowanie wszystkich zarejestrowanych licencji Google do paczki...{RST}")
    try:
        saved = pm.export_bundle(out_p)
        print(f"\n{GREEN}✓ Pomyślnie wyeksportowano licencje do pliku:{RST} {BOLD}{saved}{RST}")
        print(f"{DIM}Możesz przesłać ten plik (np. Matiemu), aby mógł zaimportować wszystkie konta 1 poleceniem:{RST}")
        print(f"  {BOLD}agy-switch import {saved.name}{RST}\n")
    except Exception as e:
        print(f"\n{RED}✗ Błąd podczas eksportu: {e}{RST}\n", file=sys.stderr)
        sys.exit(1)


def cmd_import(pm: ProfileManager, path_str: Optional[str] = None):
    if not path_str:
        default_p = Path.home() / "antigravity_licenses.bundle"
        if default_p.exists():
            path_str = str(default_p)
        else:
            print(f"{RED}Podaj ścieżkę do pliku paczki licencji: agy-switch import <ścieżka/do/pliku.bundle>{RST}")
            return

    bundle_p = Path(path_str).expanduser()
    if not bundle_p.exists():
        print(f"{RED}Plik '{bundle_p}' nie istnieje.{RST}")
        sys.exit(1)

    print(f"\n{CYAN}Importowanie licencji z pliku paczki: {bundle_p}...{RST}")
    try:
        count = pm.import_bundle(bundle_p)
        active = pm.get_active_id()
        prof = pm.get_profile(active) if active else None
        print(f"\n{GREEN}✓ Pomyślnie zaimportowano {count} licencji Google!{RST}")
        print(f"{GREEN}✓ Aktywowano optymalne konto Pro:{RST} {BOLD}{prof.get('email') if prof else active}{RST} (ID: {active})\n")
    except Exception as e:
        print(f"\n{RED}✗ Błąd podczas importu: {e}{RST}\n", file=sys.stderr)
        sys.exit(1)


def main():
    pm = ProfileManager()
    args = sys.argv[1:]

    if not args:
        cmd_interactive_switch(pm)
        return

    cmd = args[0].lower()
    if cmd in ["list", "ls", "status"]:
        cmd_list(pm)
    elif cmd in ["refresh", "sync"]:
        cmd_refresh(pm)
    elif cmd in ["add", "login", "new"]:
        cmd_add(pm)
    elif cmd in ["best", "optimal"]:
        cmd_best(pm)
    elif cmd in ["auto", "rotate", "auto-rotate"]:
        cmd_auto_rotate(pm)
    elif cmd in ["export", "bundle-export"]:
        path_arg = args[1] if len(args) > 1 else None
        cmd_export(pm, path_arg)
    elif cmd in ["import", "import-bundle", "bundle-import"]:
        path_arg = args[1] if len(args) > 1 else None
        cmd_import(pm, path_arg)
    elif cmd in ["bundle", "bandl", "paczka"]:
        sub = args[1].lower() if len(args) > 1 else "export"
        path_arg = args[2] if len(args) > 2 else None
        if sub in ["import", "in", "load"]:
            cmd_import(pm, path_arg)
        else:
            cmd_export(pm, path_arg)
    elif cmd in ["daemon"]:
        interval = 60
        if len(args) > 1 and args[1].isdigit():
            interval = int(args[1])
        run_daemon(interval_seconds=interval)
    elif cmd in ["ui", "hub", "dashboard"]:
        port = 3099
        if len(args) > 1 and args[1].isdigit():
            port = int(args[1])
        webbrowser.open(f"http://127.0.0.1:{port}")
        start_server(port=port, background=False)
    elif cmd in ["switch", "use"]:
        if len(args) > 1:
            cmd_switch(pm, args[1])
        else:
            cmd_interactive_switch(pm)
    elif cmd in ["-h", "--help", "help"]:
        print("Użycie: agy-switch [list | best | auto | refresh | switch <id> | add | export | import <file> | ui | daemon]")
    else:
        cmd_switch(pm, cmd)


if __name__ == "__main__":
    main()
