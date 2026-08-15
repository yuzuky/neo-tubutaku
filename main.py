from __future__ import annotations

import asyncio
import html
import json
import os
import random
import re
import secrets
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import discord
import httpx
import uvicorn
from discord.ext import commands, tasks
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# ============================================================
# つぶ卓 Bot + Web
# 1ファイル運用版
# ============================================================
# ============================================================
# 今回のDiscord ID類はコード内に初期値として設定済みです。
#
# Railwayで自分で入力するVariables:
# DISCORD_TOKEN             ← Bot Token（秘密）
# DISCORD_CLIENT_SECRET     ← OAuth2 Client Secret（秘密）
# SESSION_SECRET            ← 自分で作る長いランダム文字列（秘密）
# BASE_URL                  ← Railwayで発行した公開URL
# DATABASE_PATH=/data/tsubutaku.db
#
# 以下はコード内に設定済み。Railway側で上書きも可能:
# DISCORD_CLIENT_ID=1537977355161571461
# GUILD_ID=1244656732672753755
# TRPG_CHANNEL_ID=1244658549330808842
# MADMIS_CHANNEL_ID=1244658381265047642
# UNDECIDED_CATEGORY_ID=1327812864261623890
# SESSION_CATEGORY_ID=1245192932147855401
# JOIN_EMOJI_ID=1486316302308999218
# WATCH_EMOJI_ID=1486316056711794748
#
# Discord Public Key:
# 3be3997de9832c82e60d80080b7591e8e61ae3eb42db25df4e103f5afd13769d
# ※このdiscord.py構成では使用しません。
# ============================================================
# Railway Start Command例:
# pip install discord.py fastapi uvicorn python-multipart httpx itsdangerous && python main.py
#
# Railway Volume: /data にマウント
# Discord OAuth Redirect URL: {BASE_URL}/auth/callback
# ============================================================

JST = ZoneInfo("Asia/Tokyo")


def env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1537977355161571461").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/tsubutaku.db")

GUILD_ID = env_int("GUILD_ID", 1244656732672753755)
TRPG_CHANNEL_ID = env_int("TRPG_CHANNEL_ID", 1244658549330808842)
MADMIS_CHANNEL_ID = env_int("MADMIS_CHANNEL_ID", 1244658381265047642)
UNDECIDED_CATEGORY_ID = env_int("UNDECIDED_CATEGORY_ID", 1327812864261623890)
SESSION_CATEGORY_ID = env_int("SESSION_CATEGORY_ID", 1245192932147855401)
JOIN_EMOJI_ID = env_int("JOIN_EMOJI_ID", 1486316302308999218)
WATCH_EMOJI_ID = env_int("WATCH_EMOJI_ID", 1486316056711794748)

PORT = env_int("PORT", 8000)
DATA_DIR = Path(DATABASE_PATH).parent
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------- DB ---------------------------

@contextmanager
def db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                avatar_url TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recruitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER,
                game_type TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                gm_discord_id TEXT NOT NULL,
                min_players INTEGER NOT NULL,
                max_players INTEGER NOT NULL,
                variable_players INTEGER NOT NULL DEFAULT 0,
                play_time TEXT NOT NULL,
                description TEXT NOT NULL,
                guide_message TEXT,
                image_path TEXT,
                start_time TEXT NOT NULL DEFAULT '21:00',
                deadline TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'RECRUITING',
                recruitment_message_id TEXT,
                recruitment_channel_id TEXT,
                waiting_channel_id TEXT,
                deadline_notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES recruitments(id)
            );

            CREATE TABLE IF NOT EXISTS gm_dates (
                recruitment_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                PRIMARY KEY(recruitment_id, event_date),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS members (
                recruitment_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                member_type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                joined_at TEXT NOT NULL,
                PRIMARY KEY(recruitment_id, discord_id, member_type),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS answers (
                recruitment_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                answer TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(recruitment_id, discord_id, event_date),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                recruitment_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(recruitment_id, discord_id),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruitment_id INTEGER NOT NULL,
                round_no INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                channel_id TEXT,
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(recruitment_id, round_no),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id)
            );

            CREATE TABLE IF NOT EXISTS session_members (
                session_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                PRIMARY KEY(session_id, discord_id),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            """
        )


init_db()


# ------------------------ Helpers -------------------------

def now_jst() -> datetime:
    return datetime.now(JST)


def iso_now() -> str:
    return now_jst().isoformat(timespec="seconds")


def cleanup_old_data():
    """3か月（90日）を超えた募集と関連データを削除する。"""
    cutoff = (now_jst() - timedelta(days=90)).isoformat(timespec="seconds")
    image_paths = []
    session_ids = []

    with db() as c:
        old = c.execute(
            "SELECT id, image_path FROM recruitments WHERE created_at < ?",
            (cutoff,),
        ).fetchall()

        if not old:
            return 0

        ids = [int(x["id"]) for x in old]
        marks = ",".join("?" for _ in ids)

        # 古い親募集を参照している新しい再調整卓があっても削除できるようにする
        c.execute(
            f"UPDATE recruitments SET parent_id=NULL WHERE parent_id IN ({marks}) AND id NOT IN ({marks})",
            tuple(ids) + tuple(ids),
        )

        srows = c.execute(
            f"SELECT id FROM sessions WHERE recruitment_id IN ({marks})",
            tuple(ids),
        ).fetchall()
        session_ids = [int(x["id"]) for x in srows]

        if session_ids:
            smarks = ",".join("?" for _ in session_ids)
            c.execute(
                f"DELETE FROM session_members WHERE session_id IN ({smarks})",
                tuple(session_ids),
            )

        c.execute(
            f"DELETE FROM sessions WHERE recruitment_id IN ({marks})",
            tuple(ids),
        )

        # gm_dates / members / answers / comments は recruitment 削除時にCASCADE
        c.execute(
            f"DELETE FROM recruitments WHERE id IN ({marks})",
            tuple(ids),
        )

        image_paths = [x["image_path"] for x in old if x["image_path"]]

    # DB削除後に画像ファイルも削除
    for p in image_paths:
        try:
            path = Path(p)
            if path.exists() and UPLOAD_DIR in path.parents:
                path.unlink()
        except Exception as e:
            log_error("cleanup_old_image", e)

    # 既に予約済みのリマインドタスクも止める
    for sid in session_ids:
        task = reminder_tasks.get(sid) if "reminder_tasks" in globals() else None
        if task and not task.done():
            task.cancel()

    print(f"[CLEANUP] deleted {len(ids)} recruitments older than 90 days", flush=True)
    return len(ids)


def safe_channel_name(text: str, fallback: str = "taku") -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9a-zA-Zぁ-んァ-ヶ一-龠々ー\-_]", "", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return (text or fallback)[:90]


def split_text(text: str, limit: int = 1900) -> list[str]:
    if not text:
        return []
    out = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        out.append(rest)
    return out


def get_recruitment(rid: int):
    with db() as c:
        return c.execute("SELECT * FROM recruitments WHERE id=?", (rid,)).fetchone()


def get_user(uid: str):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE discord_id=?", (uid,)).fetchone()


def get_gm_dates(rid: int) -> list[str]:
    with db() as c:
        rows = c.execute("SELECT event_date FROM gm_dates WHERE recruitment_id=? ORDER BY event_date", (rid,)).fetchall()
    return [r["event_date"] for r in rows]


def is_active_member(rid: int, uid: str, kind: Optional[str] = None) -> bool:
    with db() as c:
        if kind:
            row = c.execute("SELECT 1 FROM members WHERE recruitment_id=? AND discord_id=? AND member_type=? AND active=1", (rid, uid, kind)).fetchone()
        else:
            row = c.execute("SELECT 1 FROM members WHERE recruitment_id=? AND discord_id=? AND active=1", (rid, uid)).fetchone()
    return bool(row)


def user_display(uid: str) -> str:
    u = get_user(uid)
    return u["display_name"] if u else f"<@{uid}>"


def candidate_rows(rid: int):
    dates = get_gm_dates(rid)
    result = []
    with db() as c:
        for d in dates:
            yes = c.execute(
                """SELECT a.discord_id FROM answers a
                   JOIN members m ON m.recruitment_id=a.recruitment_id AND m.discord_id=a.discord_id
                   WHERE a.recruitment_id=? AND a.event_date=? AND a.answer='yes'
                   AND m.member_type='participant' AND m.active=1""",
                (rid, d),
            ).fetchall()
            maybe = c.execute(
                """SELECT a.discord_id FROM answers a
                   JOIN members m ON m.recruitment_id=a.recruitment_id AND m.discord_id=a.discord_id
                   WHERE a.recruitment_id=? AND a.event_date=? AND a.answer='maybe'
                   AND m.member_type='participant' AND m.active=1""",
                (rid, d),
            ).fetchall()
            result.append({"date": d, "yes": [r[0] for r in yes], "maybe": [r[0] for r in maybe]})
    return result


def month_dates() -> list[str]:
    today = now_jst().date()
    if today.month == 12:
        next_month_start = date(today.year + 1, 1, 1)
        end = date(today.year + 1, 2, 1) - timedelta(days=1)
    else:
        next_month_start = date(today.year, today.month + 1, 1)
        if next_month_start.month == 12:
            after = date(next_month_start.year + 1, 1, 1)
        else:
            after = date(next_month_start.year, next_month_start.month + 1, 1)
        end = after - timedelta(days=1)
    days = []
    d = today
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def require_login(request: Request) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Discordログインが必要です")
    return str(uid)


def esc(s) -> str:
    return html.escape(str(s or ""))


_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}


def sniff_image_ext(head: bytes) -> Optional[str]:
    for sig, ext in _IMAGE_SIGNATURES.items():
        if head.startswith(sig):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


# --------------------------- CSRF ---------------------------

def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def csrf_field(request: Request) -> str:
    return f"<input type='hidden' name='csrf_token' value='{esc(get_csrf_token(request))}'>"


async def require_csrf(request: Request) -> None:
    form = await request.form()
    token = form.get("csrf_token", "")
    expected = request.session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(str(token), str(expected)):
        raise HTTPException(status_code=400, detail="不正なリクエストです。ページを再読み込みしてやり直してください。")


def log_error(context: str, e: Exception) -> None:
    print(f"[ERROR] {context}: {repr(e)}")


CSS = """
:root{
  --bg:#080c14;
  --panel:#111722;
  --panel2:#171e2a;
  --panel3:#1c2431;
  --line:#263142;
  --text:#f7f9fc;
  --muted:#8f9aae;
  --muted2:#647086;
  --purple:#8b5cf6;
  --purple2:#a855f7;
  --blue:#3b82f6;
  --green:#22c55e;
  --orange:#f59e0b;
  --red:#ef4444;
  --shadow:0 24px 70px rgba(0,0,0,.30);
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{
  margin:0;
  min-height:100vh;
  color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif;
  background:
    radial-gradient(circle at 50% -15%,rgba(85,74,255,.18),transparent 34rem),
    linear-gradient(180deg,#090e17 0%,#070a11 100%);
}
a{color:inherit}
button,input,textarea,select{font:inherit}
.wrap{max-width:760px;margin:0 auto;padding:0 18px 70px}
.top{
  min-height:78px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:14px;
  border-bottom:1px solid rgba(255,255,255,.07);
  margin:0 -18px 28px;
  padding:0 24px;
}
.brand{
  font-weight:900;
  font-size:1.78rem;
  letter-spacing:-.08em;
  text-decoration:none;
  white-space:nowrap;
}
.brand .b1{color:#4a8cff}
.brand .b2{color:#ff5a49}
.brand .b3{color:#f6aa1c}
.brand .b4{color:#36c66b}
.top-actions{display:flex;gap:8px;align-items:center}
.icon-btn,.back-link{
  border:1px solid var(--line);
  color:#dce3ee;
  background:#101722;
  border-radius:12px;
  padding:9px 12px;
  text-decoration:none;
  font-weight:700;
}
.back-link{display:inline-flex;align-items:center;gap:7px;margin-bottom:16px}
.hero{
  padding:18px 2px 20px;
  text-align:center;
}
.hero-kicker{
  color:#9d70ff;
  font-size:.85rem;
  font-weight:850;
  letter-spacing:.13em;
  margin-bottom:12px;
}
.hero h1{
  margin:0;
  font-size:clamp(2rem,7vw,3.25rem);
  line-height:1.25;
  letter-spacing:-.06em;
}
.hero p{color:var(--muted);margin:16px 0 0}
.menu-stack{display:grid;gap:18px;margin-top:28px}
.menu-card{
  min-height:150px;
  display:flex;
  align-items:center;
  gap:18px;
  padding:24px;
  border:1px solid var(--line);
  border-radius:24px;
  background:linear-gradient(145deg,#151c28,#101620);
  text-decoration:none;
  box-shadow:var(--shadow);
  transition:.18s ease;
}
.menu-card:hover{transform:translateY(-2px);border-color:#3d4a60}
.menu-card.primary{
  background:linear-gradient(135deg,#6d4aff 0%,#9d4edd 100%);
  border-color:rgba(255,255,255,.13);
}
.menu-icon{
  width:56px;height:56px;
  flex:0 0 56px;
  border-radius:50%;
  display:grid;place-items:center;
  font-size:1.5rem;
  background:rgba(255,255,255,.10);
}
.menu-title{font-size:1.35rem;font-weight:900;letter-spacing:-.03em}
.menu-sub{color:#aeb7c7;font-size:.9rem;margin-top:6px}
.primary .menu-sub{color:rgba(255,255,255,.72)}
.chev{margin-left:auto;font-size:1.7rem;color:#bac3d1}
.primary .chev{color:white}
.info-card,.card{
  background:linear-gradient(145deg,#141b26,#0f151e);
  border:1px solid var(--line);
  border-radius:22px;
  padding:22px;
  box-shadow:var(--shadow);
  margin:20px 0;
}
.info-card{margin-top:24px}
.info-card h3{color:#ae76ff;margin:0 0 7px}
.section-title{
  margin:18px 0 16px;
  text-align:center;
  font-size:1.4rem;
  font-weight:900;
  letter-spacing:-.035em;
}
.form-shell{padding-bottom:24px}
.form-section{
  margin:20px 0 30px;
}
.form-section-title{
  color:#dce3ee;
  font-size:.95rem;
  font-weight:850;
  margin:0 0 12px 4px;
}
.field,
.field-row > label,
.field-row > div{
  display:block;
  margin:0;
}
.field-box{
  position:relative;
  background:linear-gradient(145deg,#171e2a,#121823);
  border:1px solid #293446;
  border-radius:20px;
  padding:7px 16px;
  min-height:76px;
  display:flex;
  align-items:center;
  gap:12px;
}
.field-box.tall{align-items:flex-start;padding-top:14px}
.field-icon{
  width:38px;height:38px;
  flex:0 0 38px;
  border-radius:12px;
  display:grid;place-items:center;
  background:rgba(139,92,246,.15);
}
.field-box input,
.field-box textarea,
.field-box select{
  width:100%;
  border:0;
  outline:0;
  background:transparent;
  color:var(--text);
  padding:12px 4px;
  margin:0;
  font-size:1rem;
}
.field-box textarea{resize:vertical;min-height:110px}
.field-box select{appearance:none}
.field-box input::placeholder,
.field-box textarea::placeholder{color:#778397}
.field-label{
  display:block;
  color:#aab4c4;
  font-size:.76rem;
  font-weight:800;
  margin:2px 0 -5px 4px;
}
.field-stack{width:100%}
.field-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
.checkbox-row{
  display:flex;
  align-items:center;
  gap:10px;
  background:#121923;
  border:1px solid #293446;
  border-radius:15px;
  padding:13px 15px;
  margin-top:12px;
  color:#d7deea;
  font-weight:700;
}
.checkbox-row input{width:auto;margin:0;accent-color:#8b5cf6}
input[type=file]{font-size:.85rem}
.date-heading{
  margin:28px 0 12px 2px;
  color:#dce4ef;
  font-size:.95rem;
  font-weight:900;
}
.date-scroll{
  max-height:410px;
  overflow:auto;
  padding:2px 3px 8px 0;
  scrollbar-color:#667084 transparent;
}
.grid{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:10px;
}
.day{
  min-height:105px;
  border:1px solid #2a3546;
  border-radius:15px;
  background:linear-gradient(145deg,#171f2c,#121923);
  color:#a9b3c3;
  display:flex;
  flex-direction:column;
  justify-content:center;
  align-items:center;
  text-align:center;
  padding:9px 6px;
  cursor:pointer;
  transition:.16s ease;
  user-select:none;
  font-weight:750;
}
.day:hover{transform:translateY(-1px);border-color:#4a586f}
.day .state{
  display:block;
  margin-top:10px;
  font-size:1.35rem;
  line-height:1;
}
.day.yes{
  background:rgba(34,197,94,.12);
  border-color:#22c55e;
  color:#61d88c;
}
.day.maybe{
  background:rgba(245,158,11,.12);
  border-color:#f59e0b;
  color:#f5b74d;
}
.legend{
  display:flex;
  flex-wrap:wrap;
  gap:14px;
  color:#9ca7b8;
  font-size:.82rem;
  margin:14px 2px 20px;
}
.legend span{display:flex;align-items:center;gap:6px}
.submit-btn,.btn,button{
  border:0;
  cursor:pointer;
  color:white;
  text-decoration:none;
  font-weight:900;
  border-radius:16px;
  padding:15px 20px;
  background:linear-gradient(135deg,#7447ee,#a13ddb);
  box-shadow:0 12px 30px rgba(123,71,238,.24);
  display:inline-block;
}
.submit-btn{width:100%;font-size:1.1rem;min-height:62px}
.btn.alt{background:#18202c;box-shadow:none;border:1px solid #2b3749}
.btn.green{background:linear-gradient(135deg,#12b76a,#039855)}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:15px}
table{width:100%;border-collapse:collapse}
th,td{padding:12px;border-bottom:1px solid var(--line);text-align:center}
th{color:#96a2b5;font-size:.83rem}
tr:last-child td{border-bottom:0}
.pill{
  display:inline-flex;padding:5px 9px;border-radius:999px;background:#202938;
  color:#c6cfdd;font-size:.78rem;margin:2px
}
.muted{color:var(--muted)}
.ok{color:#48dc86;font-weight:900}
.maybe{color:#f6b64a;font-weight:900}
.warn{color:#ff7e75}
.candidate{
  border:1px solid #2c3748;border-radius:16px;padding:16px;margin:12px 0;background:#111823
}
.candidate.good{border-color:#259357}
.members label{margin:8px 0;display:block}
hr{border:0;border-top:1px solid var(--line);margin:24px 0}
.mobile-only{display:none}

.list-title{
  color:#a8b4c6;
  font-size:1.05rem;
  font-weight:900;
  margin:8px 4px 22px;
}
.session-list{display:grid;gap:16px}
.session-card{
  position:relative;
  display:block;
  color:var(--text);
  text-decoration:none;
  background:linear-gradient(145deg,#151d29,#101620);
  border:1px solid var(--line);
  border-radius:22px;
  padding:22px 24px;
  box-shadow:0 10px 30px rgba(0,0,0,.20);
  transition:.17s ease;
}
.session-card:hover{transform:translateY(-2px);border-color:#43516a}
.session-card-title{
  font-size:1.25rem;
  font-weight:900;
  letter-spacing:-.035em;
  margin-bottom:11px;
}
.session-card-meta{color:#94a1b5;font-size:.91rem}
.kebab{
  position:absolute;
  right:18px;
  top:19px;
  color:#64748b;
  font-size:1.55rem;
  letter-spacing:2px;
}
.status-badge{
  position:absolute;
  right:52px;
  bottom:20px;
  border-radius:7px;
  padding:6px 9px;
  font-size:.75rem;
  font-weight:900;
  color:white;
  background:#ef4444;
}
.status-badge.confirmed{background:#22a06b}
.status-badge.closed{background:#536174}
.detail-head{
  background:linear-gradient(145deg,#151d29,#101620);
  border:1px solid var(--line);
  border-radius:22px;
  padding:24px;
  margin-bottom:18px;
  box-shadow:var(--shadow);
}
.detail-head h2{margin:0 0 14px;font-size:1.55rem}
.detail-meta{
  display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  border-top:1px solid var(--line);
  padding-top:14px;color:#a6b1c2;
}
.copy-card{
  display:flex;align-items:center;gap:12px;
  padding:14px 16px;
  border:1px solid var(--line);
  border-radius:18px;
  background:#121923;
  margin:18px 0;
}
.copy-url{
  min-width:0;flex:1;
  color:#8492a7;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  font-size:.87rem;
}
.copy-btn{
  flex:0 0 auto;
  background:#2d6cf6;
  box-shadow:none;
  border-radius:12px;
  padding:11px 15px;
}
.all-no-form{margin:18px 0}
.all-no-btn{
  width:100%;
  background:rgba(239,68,68,.04);
  color:#ff5364;
  border:2px solid rgba(255,83,100,.45);
  box-shadow:none;
  min-height:62px;
}
.answer-title{margin:22px 4px 12px;font-size:1rem;color:#dce4ef}
.answer-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.answer-grid .day{min-height:124px}
.answer-grid .day span:first-child{font-size:.93rem}
.answer-grid .state{font-size:1.6rem}
.save-answer{width:100%;margin-top:18px;min-height:58px}
.viewer-note{
  border:1px dashed #3a4659;border-radius:16px;padding:16px;
  color:#909db0;background:rgba(255,255,255,.015)
}
@media(max-width:620px){
  .answer-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .session-card{padding:21px 20px}
  .copy-card{padding:12px}
}

@media(max-width:620px){
  .wrap{padding:0 14px 48px}
  .top{margin:0 -14px 20px;padding:0 18px;min-height:70px}
  .brand{font-size:1.55rem}
  .hero{padding-top:18px}
  .menu-card{min-height:132px;padding:20px;border-radius:20px}
  .menu-icon{width:48px;height:48px;flex-basis:48px}
  .menu-title{font-size:1.2rem}
  .field-row{grid-template-columns:1fr 1fr}
  .grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
  .day{min-height:98px;font-size:.83rem}
  .info-card,.card{padding:18px;border-radius:19px}
}

/* ===== UI v3: 卓作成 / 参加回答 ===== */
.detail-head h2{
  font-size:1.28rem;
  margin-bottom:14px;
}
.detail-head.available{
  border-color:rgba(34,197,94,.34);
  background:
    linear-gradient(145deg,rgba(34,197,94,.08),rgba(16,22,32,.98));
  box-shadow:0 18px 48px rgba(34,197,94,.06);
}
.detail-available-label{
  display:inline-flex;
  align-items:center;
  gap:6px;
  margin:0 0 12px;
  padding:5px 9px;
  border-radius:999px;
  color:#65df94;
  background:rgba(34,197,94,.10);
  border:1px solid rgba(34,197,94,.20);
  font-size:.76rem;
  font-weight:850;
}
.gm-actions{
  display:flex;
  gap:12px;
  flex-wrap:wrap;
  margin-top:20px;
}
.gm-actions .btn{margin:0}
.answer-title{
  margin:24px 4px 8px;
  font-size:1.08rem;
  font-weight:800;
}
.answer-legend{
  display:flex;
  flex-wrap:wrap;
  gap:14px;
  margin:0 4px 14px;
  color:#9da9ba;
  font-size:.86rem;
}
.answer-legend .yes-mark{color:#35d978;font-weight:900}
.answer-legend .maybe-mark{color:#f4a619;font-weight:900}
.answer-legend .no-mark{color:#9aa5b5;font-weight:900}

.answer-grid.status-grid{
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:10px;
}
.answer-day{
  position:relative;
  min-height:150px;
  border:1px solid #2b3749;
  border-radius:16px;
  background:linear-gradient(145deg,#171f2c,#111823);
  color:#aab5c6;
  padding:12px 10px 10px;
  transition:.16s ease;
  overflow:hidden;
}
.answer-day.clickable{cursor:pointer}
.answer-day.clickable:hover{transform:translateY(-1px);border-color:#47566f}
.answer-day.yes{
  border-color:#22c55e;
  background:rgba(34,197,94,.10);
}
.answer-day.maybe{
  border-color:#f59e0b;
  background:rgba(245,158,11,.10);
}
.answer-day-head{
  text-align:center;
  font-weight:850;
  font-size:.92rem;
}
.answer-day-state{
  text-align:center;
  font-size:1.55rem;
  line-height:1;
  margin:11px 0 10px;
  font-weight:900;
}
.answer-day.yes .answer-day-head,
.answer-day.yes .answer-day-state{color:#58df8c}
.answer-day.maybe .answer-day-head,
.answer-day.maybe .answer-day-state{color:#f6b545}
.answer-members{
  border-top:1px solid rgba(255,255,255,.07);
  padding-top:8px;
  display:grid;
  gap:4px;
}
.answer-member{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:6px;
  min-width:0;
  font-size:.72rem;
  color:#9aa6b8;
}
.answer-member-name{
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.answer-member-symbol{font-weight:900;flex:0 0 auto}
.answer-member-symbol.yes{color:#35d978}
.answer-member-symbol.maybe{color:#f4a619}
.answer-member-symbol.no{color:#8490a2}
.field-box.no-icon{padding-left:16px}
.create-date-heading{
  margin:26px 2px 12px;
  color:#9da9ba;
  font-size:.95rem;
  font-weight:600;
}
.form-section.compact{margin:8px 0 18px}
.form-section.compact + .form-section.compact{margin-top:12px}
@media(max-width:620px){
  .answer-grid.status-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .answer-day{min-height:142px;padding:11px 8px 9px}
  .answer-member{font-size:.69rem}
  .gm-actions{gap:10px}
}
"""


def page(title: str, body: str, request: Optional[Request] = None) -> HTMLResponse:
    logged_in = bool(request and request.session.get("user_id"))
    actions = ""
    if logged_in:
        actions = '<a class="icon-btn" href="/logout">ログアウト</a>'
    else:
        actions = '<a class="icon-btn" href="/login">ログイン</a>'

    brand = (
        '<a class="brand" href="/">'
        '<span class="b1">つ</span><span class="b2">ぶ</span>'
        '<span class="b3">た</span><span class="b4">く</span>'
        '</a>'
    )

    return HTMLResponse(
        f"""<!doctype html>
<html lang='ja'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='theme-color' content='#080c14'>
<title>{esc(title)} - つぶたく</title>
<style>{CSS}</style>
</head>
<body>
<div class='wrap'>
  <header class='top'>{brand}<div class='top-actions'>{actions}</div></header>
  {body}
</div>
</body>
</html>"""
    )


# ---------------------- Discord Bot ----------------------

intents = discord.Intents.default()
intents.guilds = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def configured() -> bool:
    values = [DISCORD_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, GUILD_ID, TRPG_CHANNEL_ID, MADMIS_CHANNEL_ID, UNDECIDED_CATEGORY_ID, SESSION_CATEGORY_ID, JOIN_EMOJI_ID, WATCH_EMOJI_ID]
    return all(values)


async def fetch_member(guild: discord.Guild, uid: str) -> Optional[discord.Member]:
    member = guild.get_member(int(uid))
    if member:
        return member
    try:
        return await guild.fetch_member(int(uid))
    except discord.HTTPException:
        return None


def emoji_by_id(eid: int):
    return bot.get_emoji(eid)


async def send_long(channel, text: str):
    for chunk in split_text(text):
        await channel.send(chunk)


async def create_waiting_channel(rid: int) -> discord.TextChannel:
    r = get_recruitment(rid)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        raise RuntimeError("Guildが見つかりません")
    category = guild.get_channel(UNDECIDED_CATEGORY_ID)
    gm = await fetch_member(guild, r["gm_discord_id"])
    if not gm:
        raise RuntimeError("GMがサーバーに見つかりません")
    me = guild.me
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    ch = await guild.create_text_channel(
        safe_channel_name(f'{r["scenario_name"]}-日程調整'),
        category=category,
        overwrites=overwrites,
        topic=f"つぶ卓 募集ID:{rid}",
        reason="つぶ卓 日程調整チャンネル自動作成",
    )
    with db() as c:
        c.execute("UPDATE recruitments SET waiting_channel_id=? WHERE id=?", (str(ch.id), rid))
    deadline = datetime.fromisoformat(r["deadline"]).astimezone(JST)
    await ch.send(
        f'🎲 **「{r["scenario_name"]}」日程調整**\n\n'
        f'回答期限：**{deadline.strftime("%Y/%m/%d 21:00")}**\n'
        f'参加リアクションを押した方は、こちらから回答してください。\n{BASE_URL}/r/{rid}'
    )
    return ch


async def post_recruitment(rid: int):
    r = get_recruitment(rid)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        raise RuntimeError("Guildが見つかりません")
    channel_id = TRPG_CHANNEL_ID if r["game_type"] == "TRPG" else MADMIS_CHANNEL_ID
    channel = guild.get_channel(channel_id)
    if not channel:
        raise RuntimeError("募集板チャンネルが見つかりません")

    player_text = str(r["min_players"]) if r["min_players"] == r["max_players"] else f'{r["min_players"]}〜{r["max_players"]}'
    header = (
        f'# 『{r["scenario_name"]}』\n'
        f'募集人数：**{player_text}人**（GM除く）\n'
        f'プレイ時間：**{r["play_time"]}**\n\n'
        f'{r["description"]}\n\n'
        '参加希望の方は「参加」リアクションを押して日程調整への回答をお願いします！'
    )
    chunks = split_text(header)
    file = None
    if r["image_path"] and Path(r["image_path"]).exists():
        file = discord.File(r["image_path"])
    first = await channel.send(chunks[0], file=file)
    for chunk in chunks[1:]:
        await channel.send(chunk)

    join_emoji = emoji_by_id(JOIN_EMOJI_ID)
    watch_emoji = emoji_by_id(WATCH_EMOJI_ID)
    if not join_emoji or not watch_emoji:
        raise RuntimeError("参加/観戦用カスタム絵文字が見つかりません。絵文字IDを確認してください。")
    await first.add_reaction(join_emoji)
    await first.add_reaction(watch_emoji)
    with db() as c:
        c.execute(
            "UPDATE recruitments SET recruitment_message_id=?, recruitment_channel_id=? WHERE id=?",
            (str(first.id), str(channel.id), rid),
        )


async def set_waiting_access(rid: int, uid: str, allow: bool):
    r = get_recruitment(rid)
    if not r or not r["waiting_channel_id"]:
        raise RuntimeError(f"待機チャンネルが未設定です rid={rid}")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        raise RuntimeError("Guildが見つかりません")
    channel = guild.get_channel(int(r["waiting_channel_id"]))
    if not channel:
        try:
            channel = await guild.fetch_channel(int(r["waiting_channel_id"]))
        except discord.HTTPException:
            channel = None
    if not channel:
        raise RuntimeError(f"待機チャンネルが見つかりません id={r['waiting_channel_id']}")
    member = await fetch_member(guild, uid)
    if not member:
        raise RuntimeError(f"Discordメンバーが見つかりません user={uid}")
    if allow:
        await channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            reason="つぶ卓 リアクション参加",
        )
    else:
        await channel.set_permissions(
            member,
            overwrite=None,
            reason="つぶ卓 リアクション取消",
        )


async def handle_reaction(payload: discord.RawReactionActionEvent, added: bool):
    action = "ADD" if added else "REMOVE"
    try:
        if payload.user_id == (bot.user.id if bot.user else 0):
            return
        if payload.guild_id != GUILD_ID:
            return

        eid = payload.emoji.id
        print(
            f"[REACTION:{action}] message={payload.message_id} user={payload.user_id} "
            f"emoji={eid} guild={payload.guild_id}",
            flush=True,
        )

        if eid not in (JOIN_EMOJI_ID, WATCH_EMOJI_ID):
            print(f"[REACTION:{action}] ignored: emoji id mismatch", flush=True)
            return

        with db() as c:
            r = c.execute(
                "SELECT * FROM recruitments WHERE recruitment_message_id=?",
                (str(payload.message_id),),
            ).fetchone()

        if not r:
            print(
                f"[REACTION:{action}] recruitment not found for message={payload.message_id}",
                flush=True,
            )
            return

        kind = "participant" if eid == JOIN_EMOJI_ID else "spectator"
        uid = str(payload.user_id)
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print(f"[REACTION:{action}] ERROR: guild cache not found", flush=True)
            return

        # リアクション追加時はGatewayからMemberが渡されるので、それを最優先で使用。
        member = getattr(payload, "member", None)
        if member is None:
            member = await fetch_member(guild, uid)

        if not member:
            print(
                f"[REACTION:{action}] ERROR: member not found user={uid}. "
                f"Server Members Intent / bot membership を確認",
                flush=True,
            )
            return

        print(
            f"[REACTION:{action}] member found: {member} ({member.id}) / type={kind}",
            flush=True,
        )

        with db() as c:
            c.execute(
                """INSERT INTO users(discord_id,username,display_name,avatar_url,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(discord_id) DO UPDATE SET
                     username=excluded.username,
                     display_name=excluded.display_name,
                     avatar_url=excluded.avatar_url,
                     updated_at=excluded.updated_at""",
                (uid, member.name, member.display_name, str(member.display_avatar.url), iso_now()),
            )

        if added:
            with db() as c:
                c.execute(
                    """INSERT INTO members(recruitment_id,discord_id,member_type,active,joined_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(recruitment_id,discord_id,member_type)
                       DO UPDATE SET active=1,joined_at=excluded.joined_at""",
                    (r["id"], uid, kind, 1, iso_now()),
                )

            print(
                f"[REACTION:{action}] DB member activated rid={r['id']} user={uid}",
                flush=True,
            )

            await set_waiting_access(r["id"], uid, True)
            print(
                f"[REACTION:{action}] waiting channel permission granted user={uid}",
                flush=True,
            )

            if r["waiting_channel_id"]:
                ch = guild.get_channel(int(r["waiting_channel_id"]))
                if ch:
                    label = "参加" if kind == "participant" else "観戦希望"
                    await ch.send(f'<@{uid}> が「{label}」リアクションを押しました。')
                    print(
                        f"[REACTION:{action}] notification sent channel={ch.id}",
                        flush=True,
                    )
                else:
                    print(
                        f"[REACTION:{action}] WARNING: waiting channel not found id={r['waiting_channel_id']}",
                        flush=True,
                    )

        else:
            with db() as c:
                c.execute(
                    "UPDATE members SET active=0 WHERE recruitment_id=? AND discord_id=? AND member_type=?",
                    (r["id"], uid, kind),
                )
                if kind == "participant":
                    c.execute(
                        "DELETE FROM answers WHERE recruitment_id=? AND discord_id=?",
                        (r["id"], uid),
                    )

            # もう片方のリアクションが残っている場合はアクセス維持
            still = is_active_member(r["id"], uid)
            await set_waiting_access(r["id"], uid, still)
            print(
                f"[REACTION:{action}] deactivated user={uid}; channel_access={still}",
                flush=True,
            )

    except discord.Forbidden as e:
        print(
            f"[REACTION:{action}] DISCORD FORBIDDEN: {e}. "
            f"Botの「チャンネルの管理」と対象カテゴリー/チャンネル権限を確認してください。",
            flush=True,
        )
    except Exception as e:
        print(
            f"[REACTION:{action}] ERROR {type(e).__name__}: {e}",
            flush=True,
        )
        log_error(f"reaction_{action.lower()}", e)


@bot.event
async def on_raw_reaction_add(payload):
    await handle_reaction(payload, True)


@bot.event
async def on_raw_reaction_remove(payload):
    await handle_reaction(payload, False)


async def deadline_check():
    now = now_jst()
    with db() as c:
        rows = c.execute("SELECT * FROM recruitments WHERE status='RECRUITING' AND deadline_notified=0").fetchall()
    for r in rows:
        deadline = datetime.fromisoformat(r["deadline"]).astimezone(JST)
        if now < deadline:
            continue
        candidates = [x for x in candidate_rows(r["id"]) if len(x["yes"]) >= r["min_players"]]
        guild = bot.get_guild(GUILD_ID)
        ch = guild.get_channel(int(r["waiting_channel_id"])) if guild and r["waiting_channel_id"] else None
        if ch:
            if candidates:
                lines = [f'・{x["date"]}：○ {len(x["yes"])}人' for x in candidates]
                await ch.send(
                    '⏰ **募集期限になりました。**\n\n募集人数に到達した開催候補日があります！\n'
                    + "\n".join(lines)
                    + f'\n\nGMは以下から開催日を選択してください。\n{BASE_URL}/r/{r["id"]}/decide'
                )
            else:
                await ch.send(
                    '⏰ **募集期限になりました。**\n\n今回の日程調整では必要人数が集まりませんでした。\n'
                    f'また後日、以下より再調整できます。\n{BASE_URL}/r/{r["id"]}/reschedule'
                )
        with db() as c:
            c.execute("UPDATE recruitments SET deadline_notified=1,status=? WHERE id=?", ("WAITING_GM_DECISION" if candidates else "FAILED", r["id"]))


reminder_tasks: dict[int, asyncio.Task] = {}
_reminders_restored = False


async def session_reminder_task(session_id: int):
    try:
        with db() as c:
            s = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not s or s["reminder_sent"]:
            return

        start = datetime.fromisoformat(f'{s["event_date"]}T{s["start_time"]}:00').replace(tzinfo=JST)
        remind_at = start - timedelta(hours=1)
        now = now_jst()

        if now < remind_at:
            await asyncio.sleep((remind_at - now).total_seconds())

        with db() as c:
            s = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not s or s["reminder_sent"]:
            return

        now = now_jst()
        start = datetime.fromisoformat(f'{s["event_date"]}T{s["start_time"]}:00').replace(tzinfo=JST)
        if now >= start:
            with db() as c:
                c.execute("UPDATE sessions SET reminder_sent=1 WHERE id=?", (session_id,))
            return

        with db() as c:
            r = c.execute("SELECT * FROM recruitments WHERE id=?", (s["recruitment_id"],)).fetchone()
        guild = bot.get_guild(GUILD_ID)
        ch = guild.get_channel(int(s["channel_id"])) if guild and s["channel_id"] else None
        if ch and r:
            await ch.send(f'🔔 **当日リマインド**\n\n『{r["scenario_name"]}』\n本日{s["start_time"]}開始です！')
        with db() as c:
            c.execute("UPDATE sessions SET reminder_sent=1 WHERE id=?", (session_id,))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_error(f"session_reminder_task session_id={session_id}", e)
    finally:
        reminder_tasks.pop(session_id, None)


def schedule_session_reminder(session_id: int):
    old = reminder_tasks.get(session_id)
    if old and not old.done():
        old.cancel()
    reminder_tasks[session_id] = asyncio.create_task(session_reminder_task(session_id))


async def restore_reminder_tasks():
    with db() as c:
        rows = c.execute("SELECT id FROM sessions WHERE reminder_sent=0").fetchall()
    for row in rows:
        schedule_session_reminder(int(row["id"]))


@tasks.loop(time=time(hour=21, minute=0, tzinfo=JST))
async def deadline_scheduler():
    try:
        await deadline_check()
    except Exception as e:
        log_error("deadline_scheduler", e)

    # DB肥大化防止：3か月を超えた卓を1日1回だけ削除
    try:
        cleanup_old_data()
    except Exception as e:
        log_error("old_data_cleanup", e)


@deadline_scheduler.before_loop
async def before_deadline_scheduler():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global _reminders_restored
    print(f"Discord ready: {bot.user}")
    if not _reminders_restored:
        _reminders_restored = True
        await restore_reminder_tasks()


# ------------------------ FastAPI -------------------------

app = FastAPI(title="つぶ卓")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=BASE_URL.startswith("https://"))
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/health")
async def health():
    return {"ok": True, "bot_ready": bot.is_ready()}


@app.get("/login")
async def login(request: Request, next: str = "/"):
    request.session["login_next"] = next if next.startswith("/") else "/"
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/auth/callback",
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str = ""):
    expected_state = request.session.pop("oauth_state", None)
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="ログインリクエストが無効です。もう一度ログインしてください。")
    async with httpx.AsyncClient(timeout=20) as client:
        token_res = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{BASE_URL}/auth/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_res.raise_for_status()
        access = token_res.json()["access_token"]
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access}"})
        user_res.raise_for_status()
        u = user_res.json()
    uid = str(u["id"])
    display = u.get("global_name") or u.get("username") or uid
    avatar = f'https://cdn.discordapp.com/avatars/{uid}/{u["avatar"]}.png' if u.get("avatar") else ""
    with db() as c:
        c.execute(
            "INSERT INTO users(discord_id,username,display_name,avatar_url,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,avatar_url=excluded.avatar_url,updated_at=excluded.updated_at",
            (uid, u.get("username", display), display, avatar, iso_now()),
        )
    request.session["user_id"] = uid
    return RedirectResponse(request.session.pop("login_next", "/"), status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    uid = request.session.get("user_id")

    new_href = "/new" if uid else "/login?next=/new"
    join_href = "/join" if uid else "/login?next=/join"

    return page(
        "ホーム",
        f"""
        <section class='hero'>
          <div class='hero-kicker'>TRPG・マダミス日程調整</div>
          <h1>○と△だけで、<br>もっと手軽に。</h1>
        </section>

        <div class='menu-stack'>
          <a class='menu-card primary' href='{new_href}'>
            <div class='menu-icon'>📅</div>
            <div>
              <div class='menu-title'>卓を立てる</div>
              <div class='menu-sub'>GM・募集と日程調整を作成</div>
            </div>
            <div class='chev'>›</div>
          </a>

          <a class='menu-card' href='{join_href}'>
            <div class='menu-icon'>👥</div>
            <div>
              <div class='menu-title'>卓に参加する</div>
              <div class='menu-sub'>PL・募集一覧と回答状況を見る</div>
            </div>
            <div class='chev'>›</div>
          </a>
        </div>
        """,
        request,
    )


@app.get("/join", response_class=HTMLResponse)
async def join_list(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/join")

    # 一覧を開いたタイミングでも軽量な90日整理を実行
    try:
        cleanup_old_data()
    except Exception as e:
        log_error("join_cleanup", e)

    cutoff = (now_jst() - timedelta(days=90)).isoformat(timespec="seconds")

    with db() as c:
        rows = c.execute(
            """SELECT r.*,
                      COALESCE(u.display_name, u.username, r.gm_discord_id) AS gm_name,
                      (
                        SELECT COUNT(DISTINCT a.discord_id)
                        FROM answers a
                        JOIN members m
                          ON m.recruitment_id=a.recruitment_id
                         AND m.discord_id=a.discord_id
                         AND m.member_type='participant'
                         AND m.active=1
                        WHERE a.recruitment_id=r.id
                      ) AS answered_count,
                      (
                        SELECT COUNT(*)
                        FROM sessions s
                        WHERE s.recruitment_id=r.id
                      ) AS session_count
               FROM recruitments r
               LEFT JOIN users u ON u.discord_id=r.gm_discord_id
               WHERE r.created_at >= ?
               ORDER BY r.id DESC
               LIMIT 100""",
            (cutoff,),
        ).fetchall()

    cards = []
    for r in rows:
        try:
            possible = any(
                len(x["yes"]) >= int(r["min_players"])
                for x in candidate_rows(int(r["id"]))
            )
        except Exception:
            possible = False

        if r["session_count"]:
            badge = "<span class='status-badge confirmed'>開催決定</span>"
        elif possible:
            badge = "<span class='status-badge'>開催可能！</span>"
        elif r["status"] in ("FAILED", "ERROR"):
            badge = "<span class='status-badge closed'>調整終了</span>"
        else:
            badge = ""

        cards.append(
            f"""
            <a class='session-card' href='/r/{r["id"]}'>
              <div class='kebab'>⋮</div>
              {badge}
              <div class='session-card-title'>{esc(r["scenario_name"])}</div>
              <div class='session-card-meta'>
                GM: {esc(r["gm_name"] or r["gm_discord_id"])}
                &nbsp;/&nbsp; {int(r["answered_count"])}人回答
              </div>
            </a>
            """
        )

    return page(
        "卓に参加する",
        f"""
        <a class='back-link' href='/'>‹ 戻る</a>
        <div class='list-title'>最近3か月の卓一覧</div>
        <div class='session-list'>
          {''.join(cards) if cards else "<div class='viewer-note'>現在表示できる卓はありません。</div>"}
        </div>
        """,
        request,
    )


@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/new")

    default_deadline = (now_jst().date() + timedelta(days=7)).isoformat()
    days = month_dates()

    weekday_jp = ["月","火","水","木","金","土","日"]
    cards = []
    for ds in days:
        d = date.fromisoformat(ds)
        label = f"{d.month}/{d.day}({weekday_jp[d.weekday()]})"
        cards.append(
            f'<div class="day" data-date="{ds}" onclick="toggleGM(this)">'
            f'<span>{label}</span><span class="state">-</span></div>'
        )
    day_html = "".join(cards)

    return page(
        "卓を立てる",
        f"""
        <a class='back-link' href='/'>‹ 戻る</a>
        <div class='section-title'>卓を立てる</div>

        <form class='form-shell' action='/new' method='post' enctype='multipart/form-data'>
          {csrf_field(request)}

          <div class='form-section compact'>
            <label class='field'>
              <div class='field-box no-icon'>
                <select name='game_type' required>
                  <option value='TRPG'>TRPG</option>
                  <option value='マダミス'>マダミス</option>
                </select>
              </div>
            </label>

            <label class='field' style='margin-top:12px'>
              <div class='field-box no-icon'>
                <input name='scenario_name' placeholder='シナリオ名を入力' required>
              </div>
            </label>

            <div class='field-row'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>募集人数</span>
                    <input id='fixed_players' type='number' min='1' name='fixed_players' value='4'>
                  </div>
                </div>
              </label>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>プレイ時間</span>
                    <input name='play_time' placeholder='例：4〜5時間' required>
                  </div>
                </div>
              </label>
            </div>

            <label class='checkbox-row'>
              <input type='checkbox' id='variable' name='variable_players' value='1' onchange='vp()'>
              人数を可変にする
            </label>

            <div id='range' class='field-row' style='display:none'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>最小人数</span>
                    <input type='number' min='1' name='min_players' value='2'>
                  </div>
                </div>
              </label>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>最大人数</span>
                    <input type='number' min='1' name='max_players' value='4'>
                  </div>
                </div>
              </label>
            </div>
          </div>

          <div class='form-section compact'>
            <label class='field'>
              <div class='field-box tall no-icon'>
                <textarea name='description' placeholder='シナリオ概要を入力' required></textarea>
              </div>
            </label>

            <label class='field' style='margin-top:12px'>
              <div class='field-box no-icon'>
                <div class='field-stack'>
                  <span class='field-label'>関連画像（任意）</span>
                  <input type='file' name='image' accept='image/*'>
                </div>
              </div>
            </label>

            <label class='field' style='margin-top:12px'>
              <div class='field-box tall no-icon'>
                <textarea name='guide_message'
                  placeholder='卓成立時の案内文（任意）&#10;事前準備・キャラクター作成など'></textarea>
              </div>
            </label>
          </div>

          <div class='form-section'>
            <div class='form-section-title'>日程調整</div>

            <div class='field-row'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>開始時間</span>
                    <input type='time' name='start_time' value='21:00' required>
                  </div>
                </div>
              </label>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>回答期限</span>
                    <input type='date' name='deadline_date' value='{default_deadline}' required>
                  </div>
                </div>
              </label>
            </div>

            <div class='create-date-heading'>開催候補日を選択（今月と来月末まで）</div>

            <input type='hidden' id='gm_dates' name='gm_dates'>
            <div class='date-scroll'><div class='grid'>{day_html}</div></div>

            <div class='legend'>
              <span><b style='color:#22c55e'>○</b> 開催できる</span>
              <span><b>-</b> 開催できない</span>
            </div>
          </div>

          <button class='submit-btn' type='submit'>卓を作成する</button>
        </form>

        <script>
        function vp(){{
          const checked=document.getElementById('variable').checked;
          document.getElementById('range').style.display=checked?'grid':'none';
          document.getElementById('fixed_players').disabled=checked;
        }}

        let selected=[];
        function toggleGM(el){{
          const d=el.dataset.date;
          const state=el.querySelector('.state');

          if(selected.includes(d)){{
            selected=selected.filter(x=>x!==d);
            el.classList.remove('yes');
            state.textContent='-';
          }}else{{
            selected.push(d);
            el.classList.add('yes');
            state.textContent='○';
          }}

          document.getElementById('gm_dates').value=selected.join(',');
        }}
        </script>
        """,
        request,
    )


@app.post("/new")
async def new_submit(
    request: Request,
    game_type: str = Form(...), scenario_name: str = Form(...), play_time: str = Form(...),
    description: str = Form(...), guide_message: str = Form(""), variable_players: Optional[str] = Form(None),
    fixed_players: int = Form(4), min_players: int = Form(2), max_players: int = Form(4),
    start_time: str = Form("21:00"), deadline_date: str = Form(...), gm_dates: str = Form(...),
    image: Optional[UploadFile] = File(None),
):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/new", status_code=303)
    await require_csrf(request)
    if variable_players:
        mn, mx, var = min_players, max_players, 1
    else:
        mn = mx = fixed_players
        var = 0
    if mn < 1 or mx < mn:
        raise HTTPException(400, "募集人数が不正です")
    dates = sorted({d for d in gm_dates.split(",") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)})
    if not dates:
        raise HTTPException(400, "開催可能日を1日以上選んでください")
    deadline = datetime.fromisoformat(deadline_date + "T21:00:00").replace(tzinfo=JST)
    image_path = None
    if image and image.filename:
        content = await image.read()
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(400, "画像は8MB以下にしてください")
        real_ext = sniff_image_ext(content[:16])
        if not real_ext:
            raise HTTPException(400, "画像ファイル（PNG/JPG/GIF/WEBP）を選択してください")
        image_path = str(UPLOAD_DIR / f"{uuid.uuid4().hex}{real_ext}")
        Path(image_path).write_bytes(content)
    with db() as c:
        cur = c.execute(
            """INSERT INTO recruitments(game_type,scenario_name,gm_discord_id,min_players,max_players,variable_players,play_time,description,guide_message,image_path,start_time,deadline,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_type, scenario_name.strip(), str(uid), mn, mx, var, play_time.strip(), description.strip(), guide_message.strip(), image_path, start_time, deadline.isoformat(), "RECRUITING", iso_now()),
        )
        rid = cur.lastrowid
        c.executemany("INSERT INTO gm_dates(recruitment_id,event_date) VALUES(?,?)", [(rid, d) for d in dates])
    try:
        await create_waiting_channel(rid)
        await post_recruitment(rid)
    except Exception as e:
        log_error(f"new_submit rid={rid}", e)
        with db() as c:
            c.execute("UPDATE recruitments SET status='ERROR' WHERE id=?", (rid,))
        return page("エラー", "<div class='card'><h2>Discordへの作成中にエラーが発生しました</h2><p class='warn'>DiscordのID・権限・絵文字IDを確認してください。詳細はサーバーログをご確認ください。</p></div>", request)
    return RedirectResponse(f"/r/{rid}", status_code=303)


@app.get("/r/{rid}", response_class=HTMLResponse)
async def recruitment_page(rid: int, request: Request):
    r = get_recruitment(rid)
    if not r:
        raise HTTPException(404)

    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/r/{rid}")
    uid = str(uid)

    gm = uid == r["gm_discord_id"]
    participant = is_active_member(rid, uid, "participant")
    spectator = is_active_member(rid, uid, "spectator")
    dates = get_gm_dates(rid)

    with db() as c:
        my_answers = {
            x["event_date"]: x["answer"]
            for x in c.execute(
                "SELECT * FROM answers WHERE recruitment_id=? AND discord_id=?",
                (rid, uid),
            ).fetchall()
        }

        cm = c.execute(
            "SELECT comment FROM comments WHERE recruitment_id=? AND discord_id=?",
            (rid, uid),
        ).fetchone()

        gm_user = c.execute(
            "SELECT display_name, username FROM users WHERE discord_id=?",
            (r["gm_discord_id"],),
        ).fetchone()

        # GMは回答状況のPL一覧には出さない
        active = c.execute(
            """SELECT discord_id
               FROM members
               WHERE recruitment_id=?
                 AND member_type='participant'
                 AND active=1
                 AND discord_id<>?
               ORDER BY joined_at""",
            (rid, r["gm_discord_id"]),
        ).fetchall()

        comments = c.execute(
            """SELECT *
               FROM comments
               WHERE recruitment_id=?
                 AND comment<>''
                 AND discord_id<>?""",
            (rid, r["gm_discord_id"]),
        ).fetchall()

        allans = {
            (x["discord_id"], x["event_date"]): x["answer"]
            for x in c.execute(
                "SELECT * FROM answers WHERE recruitment_id=?",
                (rid,),
            ).fetchall()
        }

    comment = cm["comment"] if cm else ""
    gm_name = (
        (gm_user["display_name"] or gm_user["username"])
        if gm_user else user_display(r["gm_discord_id"])
    )

    pl_uids = [x["discord_id"] for x in active]
    weekday_jp = ["月","火","水","木","金","土","日"]

    rows = candidate_rows(rid)
    available = any(len(x["yes"]) >= int(r["min_players"]) for x in rows)

    deadline_dt = datetime.fromisoformat(r["deadline"])
    deadline_label = deadline_dt.strftime("%Y-%m-%d")
    answer_url = f"{BASE_URL}/r/{rid}"

    # --------------------------------------------------------
    # 日程カード
    # 自分が参加者ならクリック可能。回答状況もカード内に表示。
    # --------------------------------------------------------
    js_obj = json.dumps(my_answers, ensure_ascii=False)
    cards = []

    for ds in dates:
        d = date.fromisoformat(ds)
        day_label = f"{d.month}/{d.day}({weekday_jp[d.weekday()]})"

        current = my_answers.get(ds, "") if participant else ""
        cls = "yes" if current == "yes" else "maybe" if current == "maybe" else ""
        symbol = "○" if current == "yes" else "△" if current == "maybe" else "-"

        member_lines = []
        for puid in pl_uids:
            a = allans.get((puid, ds), "")
            if a == "yes":
                mark = "○"
                mcls = "yes"
            elif a == "maybe":
                mark = "△"
                mcls = "maybe"
            else:
                mark = "-"
                mcls = "no"

            member_lines.append(
                f"<div class='answer-member'>"
                f"<span class='answer-member-name'>{esc(user_display(puid))}</span>"
                f"<span class='answer-member-symbol {mcls}'>{mark}</span>"
                f"</div>"
            )

        onclick = " onclick='togglePL(this)'" if participant else ""
        clickable = " clickable" if participant else ""

        cards.append(
            f"<div class='answer-day {cls}{clickable}' data-date='{ds}'{onclick}>"
            f"<div class='answer-day-head'>{day_label}</div>"
            f"<div class='answer-day-state'>{symbol}</div>"
            f"<div class='answer-members'>"
            f"{''.join(member_lines) if member_lines else '<div class=\"muted small\">まだ回答なし</div>'}"
            f"</div>"
            f"</div>"
        )

    schedule_block = ""

    if participant:
        schedule_block = f"""
        <form class='all-no-form' method='post' action='/r/{rid}/all-unavailable'
              onsubmit="return confirm('すべての日程を参加不可にしますか？')">
          {csrf_field(request)}
          <button class='all-no-btn' type='submit'>✕ 全ての日程が無理</button>
        </form>

        <div class='answer-title'>日程回答</div>
        <div class='answer-legend'>
          <span><b class='yes-mark'>○</b>：参加可能</span>
          <span><b class='maybe-mark'>△</b>：未定</span>
          <span><b class='no-mark'>-</b>：無理</span>
        </div>

        <form method='post' action='/r/{rid}/answer'>
          {csrf_field(request)}
          <input type='hidden' name='answers' id='answers'>

          <div class='answer-grid status-grid'>
            {''.join(cards)}
          </div>

          <label class='field' style='margin-top:18px'>
            <div class='field-box tall no-icon'>
              <textarea name='comment' rows='3'
                placeholder='GMへコメント（任意）'>{esc(comment)}</textarea>
            </div>
          </label>

          <button class='save-answer' type='submit'>回答を保存</button>
        </form>

        <script>
        let ans={js_obj};

        function refreshHidden(){{
          const hidden=document.getElementById('answers');
          if(hidden) hidden.value=JSON.stringify(ans);
        }}

        function togglePL(el){{
          const d=el.dataset.date;
          let s=ans[d]||'';

          s = s==='' ? 'yes' : (s==='yes' ? 'maybe' : '');

          if(s) ans[d]=s;
          else delete ans[d];

          el.classList.remove('yes','maybe');
          if(s) el.classList.add(s);

          el.querySelector('.answer-day-state').textContent =
            s==='yes' ? '○' : (s==='maybe' ? '△' : '-');

          refreshHidden();
        }}

        refreshHidden();
        </script>
        """
    else:
        if gm:
            note = "GMは回答対象ではありません。PLの回答状況を確認できます。"
        elif spectator:
            note = "観戦希望では日程回答はありません。PLの回答状況を確認できます。"
        else:
            note = "回答するにはDiscord募集メッセージの「参加」リアクションを押してください。"

        schedule_block = f"""
        <div class='viewer-note'>{note}</div>

        <div class='answer-title'>日程回答</div>
        <div class='answer-legend'>
          <span><b class='yes-mark'>○</b>：参加可能</span>
          <span><b class='maybe-mark'>△</b>：未定</span>
          <span><b class='no-mark'>-</b>：無理</span>
        </div>

        <div class='answer-grid status-grid'>
          {''.join(cards)}
        </div>
        """

    comment_html = "".join(
        f"<p><b>{esc(user_display(x['discord_id']))}</b>：{esc(x['comment'])}</p>"
        for x in comments
    )

    gm_buttons = ""
    if gm:
        gm_buttons = (
            f"<div class='gm-actions'>"
            f"<a class='btn green' href='/r/{rid}/decide'>開催日を決定</a>"
            f"<a class='btn alt' href='/r/{rid}/reschedule'>再日程調整</a>"
            f"</div>"
        )

    detail_cls = "detail-head available" if available else "detail-head"
    available_label = (
        "<div class='detail-available-label'>● 開催可能な日程があります</div>"
        if available else ""
    )

    return page(
        r["scenario_name"],
        f"""
        <a class='back-link' href='/join'>‹ 戻る</a>

        <div class='{detail_cls}'>
          {available_label}
          <h2>{esc(r["scenario_name"])}</h2>
          <div class='detail-meta'>
            <span>GM: {esc(gm_name)}</span>
            <span>期限: {esc(deadline_label)}</span>
          </div>
          {gm_buttons}
        </div>

        <div class='copy-card'>
          <div class='copy-url' id='answerUrl'>{esc(answer_url)}</div>
          <button type='button' class='copy-btn'
                  onclick="copyAnswerUrl()">URLコピー</button>
        </div>

        {schedule_block}

        <div class='card'>
          <h3>コメント</h3>
          {comment_html or '<p class="muted">コメントはありません。</p>'}
        </div>

        <script>
        async function copyAnswerUrl(){{
          const url=document.getElementById('answerUrl').textContent.trim();
          try{{
            await navigator.clipboard.writeText(url);
            const btn=document.querySelector('.copy-btn');
            const old=btn.textContent;
            btn.textContent='コピーしました';
            setTimeout(()=>btn.textContent=old,1300);
          }}catch(e){{
            window.prompt('このURLをコピーしてください',url);
          }}
        }}
        </script>
        """,
        request,
    )


@app.post("/r/{rid}/all-unavailable")
async def all_unavailable(rid: int, request: Request):
    uid = require_login(request)
    await require_csrf(request)

    if not is_active_member(rid, uid, "participant"):
        raise HTTPException(403, "参加者のみ回答できます")

    with db() as c:
        c.execute(
            "DELETE FROM answers WHERE recruitment_id=? AND discord_id=?",
            (rid, uid),
        )

    return RedirectResponse(f"/r/{rid}", status_code=303)


@app.post("/r/{rid}/answer")
async def save_answer(rid: int, request: Request, answers: str = Form("{}"), comment: str = Form("")):
    uid = require_login(request)
    await require_csrf(request)
    if not is_active_member(rid, uid, "participant"):
        raise HTTPException(403, "参加者のみ回答できます")
    allowed_dates = set(get_gm_dates(rid))
    try:
        obj = json.loads(answers)
    except json.JSONDecodeError:
        obj = {}
    with db() as c:
        c.execute("DELETE FROM answers WHERE recruitment_id=? AND discord_id=?", (rid, uid))
        for d, a in obj.items():
            if d in allowed_dates and a in {"yes","maybe"}:
                c.execute("INSERT INTO answers(recruitment_id,discord_id,event_date,answer,updated_at) VALUES(?,?,?,?,?)", (rid, uid, d, a, iso_now()))
        c.execute("INSERT INTO comments(recruitment_id,discord_id,comment,updated_at) VALUES(?,?,?,?) ON CONFLICT(recruitment_id,discord_id) DO UPDATE SET comment=excluded.comment,updated_at=excluded.updated_at", (rid, uid, comment.strip(), iso_now()))
    return RedirectResponse(f"/r/{rid}", status_code=303)


@app.get("/r/{rid}/decide", response_class=HTMLResponse)
async def decide_form(rid: int, request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/r/{rid}/decide")
    r = get_recruitment(rid)
    if not r or str(uid) != r["gm_discord_id"]:
        raise HTTPException(403)
    candidates = [x for x in candidate_rows(rid) if len(x["yes"]) >= r["min_players"]]
    if not candidates:
        return page("開催日決定", f"<div class='card'><p>現在、最小人数{r['min_players']}人を満たす日がありません。</p><a class='btn' href='/r/{rid}/reschedule'>再日程調整</a></div>", request)
    cards = ""
    for x in candidates:
        checkboxes = "".join(f"<label><input style='width:auto' type='checkbox' name='member_{x['date']}' value='{uid2}' checked> {esc(user_display(uid2))}</label>" for uid2 in x["yes"])
        maybe = ", ".join(esc(user_display(u)) for u in x["maybe"]) or "なし"
        over = len(x["yes"]) > r["max_players"]
        cards += f"""<div class='candidate {'good' if not over else ''}'><label><input style='width:auto' type='radio' name='event_date' value='{x['date']}' required> <b>{x['date']}</b>　○{len(x['yes'])}人 {'⚠ 最大人数超過' if over else ''}</label><div class='members'>{checkboxes}</div><p class='small muted'>△：{maybe}</p>{f'<button type="button" class="btn alt" onclick="randomPick(\'{x["date"]}\',{r["max_players"]})">ランダムで{r["max_players"]}人選ぶ</button>' if over else ''}</div>"""
    return page("開催日決定", f"""
    <form class='card' method='post' action='/r/{rid}/decide'>{csrf_field(request)}<h2>開催日を決定</h2><label>何陣目？<input type='number' min='1' name='round_no' value='1' required></label>{cards}<button>この内容で卓を成立させる</button></form>
    <script>function randomPick(d,max){{let xs=[...document.querySelectorAll(`[name="member_${{d}}"]`)];xs.forEach(x=>x.checked=false);xs.sort(()=>Math.random()-.5).slice(0,max).forEach(x=>x.checked=true)}}</script>
    """, request)


@app.post("/r/{rid}/decide")
async def decide_submit(request: Request, rid: int, event_date: str = Form(...), round_no: int = Form(...)):
    uid = require_login(request)
    await require_csrf(request)
    r = get_recruitment(rid)
    if not r or uid != r["gm_discord_id"]:
        raise HTTPException(403)
    candidates = {x["date"]: x for x in candidate_rows(rid)}
    if event_date not in candidates or len(candidates[event_date]["yes"]) < r["min_players"]:
        raise HTTPException(400, "開催条件を満たさない日です")
    form = await request.form()
    selected = form.getlist(f"member_{event_date}")
    allowed = set(candidates[event_date]["yes"])
    selected = list(dict.fromkeys([str(x) for x in selected if str(x) in allowed]))
    if not (r["min_players"] <= len(selected) <= r["max_players"]):
        raise HTTPException(400, f"参加者を{r['min_players']}〜{r['max_players']}人選択してください")
    with db() as c:
        existing = c.execute("SELECT 1 FROM sessions WHERE recruitment_id=? AND round_no=?", (rid, round_no)).fetchone()
        if existing:
            raise HTTPException(400, "その陣数はすでに使われています")
        cur = c.execute("INSERT INTO sessions(recruitment_id,round_no,event_date,start_time,created_at) VALUES(?,?,?,?,?)", (rid, round_no, event_date, r["start_time"], iso_now()))
        sid = cur.lastrowid
        c.executemany("INSERT INTO session_members(session_id,discord_id) VALUES(?,?)", [(sid, u) for u in selected])
    try:
        guild = bot.get_guild(GUILD_ID)
        category = guild.get_channel(SESSION_CATEGORY_ID) if guild else None
        gm = await fetch_member(guild, uid) if guild else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for puid in selected:
            member = await fetch_member(guild, puid)
            if member:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        # 観戦希望者は成立卓にも追加
        with db() as c:
            spectators = c.execute("SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='spectator' AND active=1", (rid,)).fetchall()
        for sp in spectators:
            member = await fetch_member(guild, sp[0])
            if member:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        ch = await guild.create_text_channel(safe_channel_name(f'{r["scenario_name"]}-{round_no}陣'), category=category, overwrites=overwrites, topic=f"つぶ卓 成立卓 ID:{sid}")
        mentions = "\n".join(f"・<@{x}>" for x in selected)
        msg = f'# 『{r["scenario_name"]}』\n**{round_no}陣が成立しました🎉**\n\n開催日：**{event_date}**\n開催時間：**{r["start_time"]}〜**\n\nGM：<@{uid}>\n参加者：\n{mentions}'
        if r["guide_message"]:
            msg += f'\n\n## 【事前準備】\n{r["guide_message"]}'
        await send_long(ch, msg)
        with db() as c:
            c.execute("UPDATE sessions SET channel_id=? WHERE id=?", (str(ch.id), sid))
            c.execute("UPDATE recruitments SET status='CONFIRMED' WHERE id=?", (rid,))
        schedule_session_reminder(sid)
    except Exception as e:
        log_error(f"decide_submit rid={rid}", e)
        return page("Discordエラー", "<div class='card'><p class='warn'>Discord側でエラーが発生し、卓の作成に失敗しました。権限やチャンネル設定を確認するか、再度お試しください。詳細はサーバーログをご確認ください。</p></div>", request)
    return page("卓成立", f"<div class='card'><h2>🎉 {esc(r['scenario_name'])} {round_no}陣が成立しました！</h2><p>{event_date} {esc(r['start_time'])}〜</p><a class='btn' href='/r/{rid}'>日程ページへ戻る</a></div>", request)


@app.get("/r/{rid}/reschedule", response_class=HTMLResponse)
async def reschedule_form(rid: int, request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/r/{rid}/reschedule")
    r = get_recruitment(rid)
    if not r or str(uid) != r["gm_discord_id"]:
        raise HTTPException(403)
    default_deadline = (now_jst().date() + timedelta(days=7)).isoformat()
    day_html = "".join(f'<div class="day" data-date="{d}" onclick="toggleGM(this)">{d[5:]}</div>' for d in month_dates())
    return page("再日程調整", f"""
    <form class='card' method='post' action='/r/{rid}/reschedule'>{csrf_field(request)}<h2>『{esc(r['scenario_name'])}』を再日程調整</h2><p>シナリオ概要・募集人数・案内文などはそのまま引き継ぎます。</p><label>開始時間<input type='time' name='start_time' value='{esc(r['start_time'])}' required></label><label>回答期限<input type='date' name='deadline_date' value='{default_deadline}' required></label><input type='hidden' id='gm_dates' name='gm_dates'><div class='grid'>{day_html}</div><br><button>新しい日程調整を作成</button></form>
    <script>let selected=[];function toggleGM(el){{let d=el.dataset.date;if(selected.includes(d)){{selected=selected.filter(x=>x!==d);el.classList.remove('yes')}}else{{selected.push(d);el.classList.add('yes')}}document.getElementById('gm_dates').value=selected.join(',')}}</script>
    """, request)


@app.post("/r/{rid}/reschedule")
async def reschedule_submit(rid: int, request: Request, start_time: str = Form(...), deadline_date: str = Form(...), gm_dates: str = Form(...)):
    uid = require_login(request)
    await require_csrf(request)
    r = get_recruitment(rid)
    if not r or uid != r["gm_discord_id"]:
        raise HTTPException(403)
    dates = sorted({d for d in gm_dates.split(",") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)})
    if not dates:
        raise HTTPException(400, "開催可能日を選択してください")
    deadline = datetime.fromisoformat(deadline_date + "T21:00:00").replace(tzinfo=JST)
    with db() as c:
        cur = c.execute(
            """INSERT INTO recruitments(parent_id,game_type,scenario_name,gm_discord_id,min_players,max_players,variable_players,play_time,description,guide_message,image_path,start_time,deadline,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, r["game_type"], r["scenario_name"], uid, r["min_players"], r["max_players"], r["variable_players"], r["play_time"], r["description"], r["guide_message"], r["image_path"], start_time, deadline.isoformat(), "RECRUITING", iso_now()),
        )
        new_id = cur.lastrowid
        c.executemany("INSERT INTO gm_dates(recruitment_id,event_date) VALUES(?,?)", [(new_id,d) for d in dates])
    await create_waiting_channel(new_id)
    await post_recruitment(new_id)
    return RedirectResponse(f"/r/{new_id}", status_code=303)


# ------------------------- Startup ------------------------

async def main():
    if not configured():
        print("WARNING: 必須環境変数が未設定です。Webは起動しますがDiscord Bot機能は正常動作しません。")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    web_task = asyncio.create_task(server.serve())
    if DISCORD_TOKEN:
        deadline_scheduler.start()
        bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
        await asyncio.gather(web_task, bot_task)
    else:
        await web_task


if __name__ == "__main__":
    asyncio.run(main())
