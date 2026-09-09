"""Local Web Dashboard & REST API for Antigravity License Hub.

Multithreaded, responsive web server with live Google Quota monitoring,
asynchronous OAuth login flows, and Orca dark-mode UI.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from .auth import run_oauth_login_flow
from .profiles import ProfileManager

# Global state for asynchronous login flow
_OAUTH_FLOW_STATE: Dict[str, Any] = {
    "in_progress": False,
    "status": "idle",
    "email": None,
    "error": None,
    "started_at": 0,
}

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Antigravity License Hub — Orca Switcher</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0c0e14;
            --bg-card: #151821;
            --bg-card-hover: #1c202c;
            --bg-active: #162030;
            --border: #262c3d;
            --border-active: #3b82f6;
            --accent: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --text-main: #f1f5f9;
            --text-dim: #94a3b8;
            --text-dark: #64748b;
            --green: #4ade80;
            --yellow: #facc15;
            --red: #f87171;
            --purple: #c084fc;
            --font-main: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-base);
            color: var(--text-main);
            font-family: var(--font-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 32px 24px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
        }

        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }

        .logo-area {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .logo-icon {
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            font-weight: 700;
            color: #fff;
            box-shadow: 0 4px 14px var(--accent-glow);
        }

        .header-title h1 {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.02em;
        }

        .header-title p {
            font-size: 13px;
            color: var(--text-dim);
            margin-top: 2px;
        }

        .header-actions {
            display: flex;
            gap: 10px;
        }

        button {
            font-family: var(--font-main);
            font-size: 13px;
            font-weight: 500;
            padding: 9px 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text-main);
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        button:hover {
            background: var(--bg-card-hover);
            border-color: #3b4256;
        }

        button.primary {
            background: #2563eb;
            border-color: #3b82f6;
            color: #fff;
        }

        button.primary:hover {
            background: #1d4ed8;
        }

        button.active-badge {
            background: rgba(34, 197, 94, 0.15);
            border-color: rgba(34, 197, 94, 0.4);
            color: var(--green);
            pointer-events: none;
        }

        .status-banner {
            background: rgba(56, 189, 248, 0.06);
            border: 1px solid rgba(56, 189, 248, 0.2);
            border-radius: 10px;
            padding: 12px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
            margin-bottom: 24px;
        }

        .status-banner span {
            color: var(--text-dim);
        }

        .status-banner strong {
            color: var(--accent);
            font-weight: 600;
        }

        .cards-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
            gap: 20px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            position: relative;
            transition: all 0.2s ease;
        }

        .card:hover {
            border-color: #3b4256;
            transform: translateY(-2px);
        }

        .card.active {
            background: var(--bg-active);
            border-color: var(--border-active);
            box-shadow: 0 0 20px rgba(59, 130, 246, 0.15);
        }

        .card-header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
        }

        .account-info {
            display: flex;
            align-items: center;
            gap: 12px;
            overflow: hidden;
        }

        .avatar {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background: #2a3143;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            font-weight: 600;
            color: var(--accent);
            flex-shrink: 0;
            overflow: hidden;
        }

        .avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .account-text {
            overflow: hidden;
        }

        .account-name {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .account-email {
            font-size: 12px;
            color: var(--text-dim);
            font-family: var(--font-mono);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .badges-row {
            display: flex;
            gap: 6px;
            align-items: center;
            flex-wrap: wrap;
        }

        .badge {
            font-size: 10.5px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            padding: 3px 8px;
            border-radius: 20px;
            flex-shrink: 0;
        }

        .badge.active {
            background: rgba(34, 197, 94, 0.15);
            color: var(--green);
            border: 1px solid rgba(34, 197, 94, 0.3);
        }

        .badge.ready {
            background: rgba(148, 163, 184, 0.12);
            color: var(--text-dim);
            border: 1px solid rgba(148, 163, 184, 0.2);
        }

        .badge.tier-pro {
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(168, 85, 247, 0.2));
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.4);
        }

        .badge.tier-starter {
            background: rgba(245, 158, 11, 0.12);
            color: var(--yellow);
            border: 1px solid rgba(245, 158, 11, 0.3);
        }

        .usage-section {
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: rgba(0, 0, 0, 0.25);
            padding: 14px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.04);
        }

        .group-title {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-dark);
            margin-bottom: 2px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .usage-item {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }

        .usage-item-header {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: var(--text-dim);
        }

        .usage-item-header strong {
            color: var(--text-main);
            font-family: var(--font-mono);
        }

        .progress-track {
            height: 6px;
            background: #232938;
            border-radius: 4px;
            overflow: hidden;
            position: relative;
        }

        .progress-bar {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .progress-bar.green { background: var(--green); }
        .progress-bar.yellow { background: var(--yellow); }
        .progress-bar.red { background: var(--red); }

        .usage-footer {
            display: flex;
            justify-content: space-between;
            font-size: 11px;
            color: var(--text-dark);
        }

        .card-actions {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: auto;
            gap: 8px;
        }

        .btn-switch {
            flex: 1;
            justify-content: center;
            padding: 9px 12px;
            font-weight: 600;
        }

        .btn-delete {
            padding: 9px;
            color: var(--text-dark);
            background: transparent;
            border-color: transparent;
        }

        .btn-delete:hover {
            color: var(--red);
            background: rgba(248, 113, 113, 0.1);
        }

        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #1e293b;
            color: #fff;
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid #334155;
            font-size: 13px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
            display: none;
            z-index: 1000;
        }

        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
            backdrop-filter: blur(4px);
        }

        .modal {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 28px;
            max-width: 440px;
            width: 90%;
            display: flex;
            flex-direction: column;
            gap: 16px;
            text-align: center;
        }

        .spinner {
            width: 32px;
            height: 32px;
            border: 3px solid rgba(56, 189, 248, 0.2);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @media (max-width: 600px) {
            header { flex-direction: column; align-items: flex-start; gap: 16px; }
            .header-actions { width: 100%; justify-content: stretch; }
            .header-actions button { flex: 1; justify-content: center; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-area">
                <div class="logo-icon">&#x2726;</div>
                <div class="header-title">
                    <h1>Antigravity License Hub</h1>
                    <p>Zarządzanie kontami i przełącznik licencji dla Orki</p>
                </div>
            </div>
            <div class="header-actions">
                <button class="primary" style="background: linear-gradient(135deg, #059669, #10b981); border-color: #34d399;" onclick="autoSelectBest()" id="btn-auto-select">&#x26a1; Wybierz Najlepszą (Auto)</button>
                <button onclick="refreshAll()" id="btn-refresh-all">&#x21bb; Odśwież statusy</button>
                <button class="primary" onclick="startAddAccount()">&#x2b; Dodaj konto Google</button>
            </div>
        </header>

        <div class="status-banner">
            <div>
                <span>Automatyczna rotacja licencji:</span>
                <strong>Żonglowanie wyłącznie kontami Google AI Pro (Auto-switch gdy limit &lt; 10%)</strong>
            </div>
            <div id="last-updated-text" style="color: var(--text-dark);">Ładowanie...</div>
        </div>

        <div class="cards-grid" id="profiles-grid">
            <!-- Cards injected by JS -->
        </div>
    </div>

    <!-- Login Modal -->
    <div class="modal-overlay" id="login-modal">
        <div class="modal">
            <div class="spinner"></div>
            <h3 style="font-size: 16px;">Oczekiwanie na autoryzację Google...</h3>
            <p style="font-size: 13px; color: var(--text-dim);">
                Otwarto okno logowania w przeglądarce. Zaloguj się na wybrane konto Google.
            </p>
            <button onclick="cancelAddAccount()" style="margin-top: 8px;">Anuluj</button>
        </div>
    </div>

    <div class="toast" id="toast">Komunikat</div>

    <script>
        let pollLoginInterval = null;

        function showToast(msg, duration = 3000) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, duration);
        }

        function getColorClass(pct) {
            if (pct >= 85) return 'red';
            if (pct >= 65) return 'yellow';
            return 'green';
        }

        async function loadProfiles() {
            try {
                const res = await fetch('/api/profiles');
                const data = await res.json();
                renderProfiles(data.profiles, data.best_id);
                document.getElementById('last-updated-text').textContent = 'Ostatnia aktualizacja: ' + new Date().toLocaleTimeString();
            } catch (err) {
                console.error('Błąd pobierania profili:', err);
            }
        }

        function formatTokens(num) {
            if (!num || num === 0) return '0';
            if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
            if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
            return num.toString();
        }

        function renderProfiles(profiles, bestId) {
            const grid = document.getElementById('profiles-grid');
            grid.innerHTML = '';

            if (!profiles || profiles.length === 0) {
                grid.innerHTML = '<p style="color: var(--text-dim); padding: 20px;">Brak zarejestrowanych licencji. Kliknij "+ Dodaj konto Google".</p>';
                return;
            }

            profiles.forEach(p => {
                const u = p.usage || {};
                const isAct = p.is_active;
                const isPro = u.has_pro_license;
                const tierName = u.tier_name || (isPro ? 'Google AI Pro' : 'Starter Quota');

                const g5 = u.gemini_5h || {};
                const gw = u.gemini_weekly || {};
                const c5 = u.claude_5h || {};
                const cw = u.claude_weekly || {};

                const g5_used = g5.used_pct || 0;
                const gw_used = gw.used_pct || 0;
                const c5_used = c5.used_pct || 0;
                const cw_used = cw.used_pct || 0;

                const isBlocked = (gw_used >= 95.0 || (gw.remaining_pct != null && gw.remaining_pct <= 5.0));
                const isBest = (p.id === bestId) && !isBlocked;

                const card = document.createElement('div');
                card.className = 'card' + (isAct ? ' active' : '') + (isBlocked ? ' blocked-card' : '');
                if (isBlocked) {
                    card.style.borderColor = 'rgba(239, 68, 68, 0.35)';
                    card.style.background = 'rgba(30, 15, 20, 0.6)';
                }

                const initial = (p.name || p.email || 'A')[0].toUpperCase();
                const avatarContent = p.picture ? `<img src="${p.picture}" alt="${p.name}">` : initial;

                card.innerHTML = `
                    <div class="card-header">
                        <div class="account-info">
                            <div class="avatar" style="${isBlocked ? 'border: 1px solid var(--red);' : ''}">${avatarContent}</div>
                            <div class="account-text">
                                <div class="account-name">${p.name || p.email.split('@')[0]}</div>
                                <div class="account-email" style="font-weight: 600; color: ${isAct ? 'var(--accent)' : 'var(--text-main)'};">${p.email}</div>
                            </div>
                        </div>
                        <div class="badges-row">
                            <span class="badge ${isPro ? 'tier-pro' : 'tier-starter'}">${isPro ? '✦ ' + tierName : tierName}</span>
                            ${isAct ? `<span class="badge active">● AKTYWNA</span>` : ''}
                            ${isBlocked ? `<span class="badge" style="background: rgba(239, 68, 68, 0.25); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.5);">⛔ ZABLOKOWANA (0% 7D)</span>` : ''}
                            ${(!isAct && isBest) ? `<span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.4);">★ ZALECANA</span>` : ''}
                        </div>
                    </div>

                    <div style="background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.15); border-radius: 6px; padding: 8px 10px; font-size: 11px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="color: var(--text-dim);">Przetworzone tokeny:</span>
                            <strong style="color: var(--accent); font-family: var(--font-mono);">${formatTokens(u.total_all_time || 0)}</strong>
                            ${u.tokens_5h ? `<span style="color: var(--text-dark);"> (${formatTokens(u.tokens_5h)} w 5h)</span>` : ''}
                        </div>
                        ${u.last_model ? `<div style="color: var(--text-dark); font-size: 10px;">${u.last_model}</div>` : ''}
                    </div>

                    <div class="usage-section">
                        <div class="group-title">
                            <span>✦ Gemini (Flash & Pro)</span>
                            <span style="font-size: 10px; color: var(--text-dim);">${g5.available ? '5h & Tydzień' : 'Limit Tygodniowy'}</span>
                        </div>

                        ${g5.available ? `
                        <div class="usage-item">
                            <div class="usage-item-header">
                                <span>Limit 5h:</span>
                                <strong>${g5_used}% zużyte (${g5.remaining_pct != null ? g5.remaining_pct : (100 - g5_used)}% zostało)</strong>
                            </div>
                            <div class="progress-track">
                                <div class="progress-bar ${getColorClass(g5_used)}" style="width: ${Math.min(100, Math.max(2, g5_used))}%"></div>
                            </div>
                            <div class="usage-footer">
                                <span>Reset 5h za: <strong>${g5.reset_formatted || 'teraz'}</strong></span>
                            </div>
                        </div>
                        ` : ''}

                        <div class="usage-item">
                            <div class="usage-item-header">
                                <span>Limit Tygodniowy (7d):</span>
                                <strong style="${isBlocked ? 'color: var(--red);' : ''}">${gw_used}% zużyte (${gw.remaining_pct != null ? gw.remaining_pct : (100 - gw_used)}% zostało)</strong>
                            </div>
                            <div class="progress-track">
                                <div class="progress-bar ${getColorClass(gw_used)}" style="width: ${Math.min(100, Math.max(2, gw_used))}%"></div>
                            </div>
                            <div class="usage-footer">
                                <span>Reset 7d za: <strong>${gw.reset_formatted || 'teraz'}</strong></span>
                            </div>
                        </div>

                        <div class="group-title" style="margin-top: 6px;">
                            <span>✦ Claude & GPT (Opus / Sonnet)</span>
                            <span style="font-size: 10px; color: var(--text-dim);">${c5.available ? `${c5_used}% 5h · ` : ''}${cw_used}% 7d</span>
                        </div>

                        <div class="usage-item">
                            <div class="usage-item-header">
                                <span>Claude / GPT Limit 7d:</span>
                                <strong>${cw_used}% zużyte (${cw.remaining_pct != null ? cw.remaining_pct : (100 - cw_used)}% zostało)</strong>
                            </div>
                            <div class="progress-track">
                                <div class="progress-bar ${getColorClass(cw_used)}" style="width: ${Math.min(100, Math.max(2, cw_used))}%"></div>
                            </div>
                            <div class="usage-footer">
                                <span>Reset 7d za: <strong>${cw.reset_formatted || 'teraz'}</strong></span>
                            </div>
                        </div>
                    </div>

                    <div class="card-actions">
                        ${isAct 
                            ? `<button class="btn-switch active-badge">&#x2713; Aktywna w Orce</button>` 
                            : (isBest 
                                ? `<button class="primary btn-switch" style="background: linear-gradient(135deg, #059669, #10b981); border-color: #34d399;" onclick="switchProfile('${p.id}')">&#x26a1; Przełącz na zalecaną</button>`
                                : (isBlocked 
                                    ? `<button class="btn-switch" style="opacity: 0.6; cursor: not-allowed;" onclick="switchProfile('${p.id}')">Przełącz mimo blokady (Reset za ${gw.reset_formatted})</button>`
                                    : `<button class="primary btn-switch" onclick="switchProfile('${p.id}')">Przełącz na tę licencję</button>`
                                  )
                              )
                        }
                        <button class="btn-delete" title="Usuń konto" onclick="deleteProfile('${p.id}', '${p.email}')">&#x1f5d1;</button>
                    </div>
                `;
                grid.appendChild(card);
            });
        }

        async function switchProfile(id) {
            try {
                showToast('Przełączanie licencji w Orce...');
                const res = await fetch('/api/switch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id})
                });
                const data = await res.json();
                if (data.success) {
                    showToast('✓ Pomyślnie przełączono na konto ' + id);
                    loadProfiles();
                } else {
                    showToast('Błąd: ' + data.error);
                }
            } catch (err) {
                showToast('Błąd przełączania: ' + err.message);
            }
        }

        async function autoSelectBest() {
            const btn = document.getElementById('btn-auto-select');
            btn.disabled = true;
            btn.textContent = 'Wybieranie najlepszej...';
            try {
                const res = await fetch('/api/auto-select', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast('✓ Pomyślnie aktywowano najlepszą licencję: ' + (data.email || data.active_id));
                    loadProfiles();
                } else {
                    showToast('Błąd: ' + data.error);
                }
            } catch (err) {
                showToast('Błąd: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '&#x26a1; Wybierz Najlepszą (Auto)';
            }
        }

        async function refreshAll() {
            const btn = document.getElementById('btn-refresh-all');
            btn.disabled = true;
            btn.textContent = 'Odświeżanie...';
            try {
                const res = await fetch('/api/refresh-all', { method: 'POST' });
                const data = await res.json();
                showToast('✓ Wszystkie tokeny i statusy odświeżone z Google');
                loadProfiles();
            } catch (err) {
                showToast('Błąd: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.textContent = '↻ Odśwież statusy';
            }
        }

        async function startAddAccount() {
            document.getElementById('login-modal').style.display = 'flex';
            try {
                const res = await fetch('/api/start-add-account', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    pollLoginStatus();
                } else {
                    showToast('Błąd: ' + data.error);
                    cancelAddAccount();
                }
            } catch (err) {
                showToast('Błąd: ' + err.message);
                cancelAddAccount();
            }
        }

        function pollLoginStatus() {
            if (pollLoginInterval) clearInterval(pollLoginInterval);
            pollLoginInterval = setInterval(async () => {
                try {
                    const res = await fetch('/api/add-account-status');
                    const data = await res.json();
                    if (data.status === 'success') {
                        clearInterval(pollLoginInterval);
                        document.getElementById('login-modal').style.display = 'none';
                        showToast('✓ Konto ' + data.email + ' zostało pomyślnie dodane!');
                        loadProfiles();
                    } else if (data.status === 'error') {
                        clearInterval(pollLoginInterval);
                        document.getElementById('login-modal').style.display = 'none';
                        showToast('Błąd logowania: ' + data.error);
                    }
                } catch (e) {}
            }, 1500);
        }

        function cancelAddAccount() {
            if (pollLoginInterval) clearInterval(pollLoginInterval);
            document.getElementById('login-modal').style.display = 'none';
            fetch('/api/cancel-add-account', { method: 'POST' });
        }

        async function deleteProfile(id, email) {
            if (!confirm(`Czy na pewno chcesz usunąć konto ${email}?`)) return;
            try {
                const res = await fetch('/api/remove/' + encodeURIComponent(id), { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast('Konto usunięte');
                    loadProfiles();
                }
            } catch (err) {
                showToast('Błąd usuwania: ' + err.message);
            }
        }

        // Auto poll every 15s
        loadProfiles();
        setInterval(loadProfiles, 15000);
    </script>
</body>
</html>
"""


class HubHTTPHandler(BaseHTTPRequestHandler):
    pm = ProfileManager()

    def log_message(self, format, *args):
        return  # quiet

    def send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/profiles":
            profiles = self.pm.list_profiles()
            best = self.pm.find_best_profile()
            best_id = best["id"] if best else None
            self.send_json({
                "profiles": profiles,
                "active_id": self.pm.get_active_id(),
                "best_id": best_id
            })
        elif parsed.path == "/api/best-candidate":
            best = self.pm.find_best_profile()
            self.send_json({
                "best_profile": best,
                "best_id": best["id"] if best else None
            })
        elif parsed.path == "/api/active":
            act = self.pm.get_active_id()
            prof = self.pm.get_profile(act) if act else None
            self.send_json({"active_id": act, "profile": prof})
        elif parsed.path == "/api/add-account-status":
            self.send_json(_OAUTH_FLOW_STATE)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _OAUTH_FLOW_STATE
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            body = {}

        if parsed.path == "/api/switch":
            target_id = body.get("id") or body.get("email")
            if not target_id:
                self.send_json({"success": False, "error": "Brak ID profilu."}, 400)
                return
            try:
                self.pm.switch_profile(target_id)
                self.send_json({"success": True, "active_id": self.pm.get_active_id()})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, 500)

        elif parsed.path == "/api/auto-select":
            best = self.pm.find_best_profile()
            if not best:
                self.send_json({"success": False, "error": "Brak dostępnych profili."}, 400)
                return
            try:
                self.pm.switch_profile(best["id"])
                self.send_json({
                    "success": True,
                    "active_id": best["id"],
                    "email": best.get("email"),
                    "profile": best
                })
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, 500)

        elif parsed.path == "/api/auto-rotate":
            try:
                res = self.pm.auto_rotate_if_needed(threshold_pct=10.0)
                self.send_json({"success": True, "result": res})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, 500)

        elif parsed.path == "/api/refresh-all":
            try:
                res = self.pm.refresh_all()
                self.send_json({"success": True, "results": res})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, 500)

        elif parsed.path == "/api/start-add-account":
            if _OAUTH_FLOW_STATE["in_progress"] and time.time() - _OAUTH_FLOW_STATE["started_at"] < 90:
                self.send_json({"success": True, "status": "already_in_progress"})
                return

            _OAUTH_FLOW_STATE = {
                "in_progress": True,
                "status": "waiting_browser",
                "email": None,
                "error": None,
                "started_at": time.time(),
            }

            def bg_oauth_worker():
                global _OAUTH_FLOW_STATE
                try:
                    creds = run_oauth_login_flow(port=8086)
                    pid = self.pm.add_or_update_profile(creds)
                    _OAUTH_FLOW_STATE = {
                        "in_progress": False,
                        "status": "success",
                        "email": creds.get("email"),
                        "id": pid,
                        "error": None,
                        "started_at": 0,
                    }
                except Exception as e:
                    _OAUTH_FLOW_STATE = {
                        "in_progress": False,
                        "status": "error",
                        "email": None,
                        "error": str(e),
                        "started_at": 0,
                    }

            t = threading.Thread(target=bg_oauth_worker, daemon=True)
            t.start()
            self.send_json({"success": True, "status": "started"})

        elif parsed.path == "/api/cancel-add-account":
            _OAUTH_FLOW_STATE = {
                "in_progress": False,
                "status": "cancelled",
                "email": None,
                "error": None,
                "started_at": 0,
            }
            self.send_json({"success": True})

        elif parsed.path.startswith("/api/remove/"):
            pid = urllib.parse.unquote(parsed.path.split("/api/remove/", 1)[1])
            success = self.pm.remove_profile(pid)
            self.send_json({"success": success})

        else:
            self.send_response(404)
            self.end_headers()


def start_server(port: int = 3099, background: bool = False):
    server = ThreadingHTTPServer(("127.0.0.1", port), HubHTTPHandler)
    print(f"Antigravity License Hub UI running on: http://127.0.0.1:{port}")
    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down License Hub UI...")
            server.server_close()


if __name__ == "__main__":
    start_server(port=3099, background=False)
