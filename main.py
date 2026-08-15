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
:root{--bg:#111827;--card:#1f2937;--line:#374151;--text:#f9fafb;--muted:#9ca3af;--accent:#8b5cf6;--green:#22c55e;--yellow:#eab308;--red:#ef4444}*{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#111827,#1f1833);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{max-width:920px;margin:auto;padding:24px}.card{background:rgba(31,41,55,.94);border:1px solid var(--line);border-radius:18px;padding:20px;margin:16px 0;box-shadow:0 10px 30px #0004}h1,h2,h3{margin-top:0}a{color:#c4b5fd}.btn,button{background:var(--accent);color:white;border:0;border-radius:10px;padding:11px 16px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.btn.alt{background:#374151}.btn.green{background:var(--green)}input,textarea,select{width:100%;padding:11px;border-radius:10px;border:1px solid #4b5563;background:#111827;color:white;margin-top:6px}label{display:block;margin:14px 0;font-weight:650}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.day{padding:10px;border:1px solid #4b5563;border-radius:10px;background:#111827;text-align:center;cursor:pointer}.day.yes{background:#14532d;border-color:#22c55e}.day.maybe{background:#713f12;border-color:#eab308}.muted{color:var(--muted)}table{border-collapse:collapse;width:100%;overflow:auto}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:center}.ok{color:#4ade80;font-weight:800}.maybe{color:#facc15;font-weight:800}.warn{color:#f87171}.flash{padding:12px;background:#312e81;border-radius:10px;margin:10px 0}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.pill{display:inline-block;padding:4px 9px;border-radius:99px;background:#374151;margin:2px}.scroll{overflow:auto}.small{font-size:.9rem}.candidate{border:1px solid #4b5563;border-radius:12px;padding:14px;margin:10px 0}.candidate.good{border-color:#22c55e}.members label{margin:7px 0;font-weight:400}hr{border:0;border-top:1px solid #374151;margin:20px 0}
"""


def page(title: str, body: str, request: Optional[Request] = None) -> HTMLResponse:
    nav = ""
    if request and request.session.get("user_id"):
        nav = f'<a class="btn alt" href="/">ホーム</a> <a class="btn alt" href="/logout">ログアウト</a>'
    else:
        nav = '<a class="btn" href="/login">Discordでログイン</a>'
    return HTMLResponse(f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)} - つぶ卓</title><style>{CSS}</style></head><body><div class='wrap'><div class='top'><h1>🎲 つぶ卓</h1><div>{nav}</div></div>{body}</div></body></html>""")


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
        return
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(int(r["waiting_channel_id"])) if guild else None
    if not guild or not channel:
        return
    member = await fetch_member(guild, uid)
    if not member:
        return
    if allow:
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, reason="つぶ卓 リアクション参加")
    else:
        await channel.set_permissions(member, overwrite=None, reason="つぶ卓 リアクション取消")


async def handle_reaction(payload: discord.RawReactionActionEvent, added: bool):
    if payload.user_id == (bot.user.id if bot.user else 0) or payload.guild_id != GUILD_ID:
        return
    eid = payload.emoji.id
    if eid not in (JOIN_EMOJI_ID, WATCH_EMOJI_ID):
        return
    with db() as c:
        r = c.execute("SELECT * FROM recruitments WHERE recruitment_message_id=?", (str(payload.message_id),)).fetchone()
    if not r:
        return
    kind = "participant" if eid == JOIN_EMOJI_ID else "spectator"
    uid = str(payload.user_id)
    guild = bot.get_guild(GUILD_ID)
    member = await fetch_member(guild, uid) if guild else None
    if member:
        with db() as c:
            c.execute(
                "INSERT INTO users(discord_id,username,display_name,avatar_url,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,avatar_url=excluded.avatar_url,updated_at=excluded.updated_at",
                (uid, member.name, member.display_name, str(member.display_avatar.url), iso_now()),
            )
    if added:
        if not member:
            # ギルドに存在しないユーザーは登録しない
            return
        with db() as c:
            c.execute(
                "INSERT INTO members(recruitment_id,discord_id,member_type,active,joined_at) VALUES(?,?,?,?,?) ON CONFLICT(recruitment_id,discord_id,member_type) DO UPDATE SET active=1,joined_at=excluded.joined_at",
                (r["id"], uid, kind, 1, iso_now()),
            )
        await set_waiting_access(r["id"], uid, True)
        if r["waiting_channel_id"] and guild:
            ch = guild.get_channel(int(r["waiting_channel_id"]))
            if ch:
                label = "参加" if kind == "participant" else "観戦希望"
                await ch.send(f'<@{uid}> が「{label}」リアクションを押しました。')
    else:
        with db() as c:
            c.execute("UPDATE members SET active=0 WHERE recruitment_id=? AND discord_id=? AND member_type=?", (r["id"], uid, kind))
            if kind == "participant":
                c.execute("DELETE FROM answers WHERE recruitment_id=? AND discord_id=?", (r["id"], uid))
        # もう片方のリアクションが残っている場合はアクセス維持
        still = is_active_member(r["id"], uid)
        await set_waiting_access(r["id"], uid, still)


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
    if not uid:
        return page("ホーム", "<div class='card'><h2>募集から日程調整まで自動化</h2><p>Discordでログインして募集を作成してください。</p><a class='btn' href='/login'>Discordでログイン</a></div>", request)
    with db() as c:
        mine = c.execute("SELECT * FROM recruitments WHERE gm_discord_id=? ORDER BY id DESC LIMIT 30", (str(uid),)).fetchall()
    rows = "".join(f"<tr><td>{esc(r['scenario_name'])}</td><td>{esc(r['game_type'])}</td><td>{esc(r['status'])}</td><td><a href='/r/{r['id']}'>開く</a></td></tr>" for r in mine)
    return page("ホーム", f"""
    <div class='card'><a class='btn green' href='/new'>＋ 新しい募集を作成</a></div>
    <div class='card'><h2>自分がGMの募集</h2><div class='scroll'><table><tr><th>シナリオ</th><th>種別</th><th>状態</th><th></th></tr>{rows or '<tr><td colspan=4>まだありません</td></tr>'}</table></div></div>
    """, request)


@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/new")
    default_deadline = (now_jst().date() + timedelta(days=7)).isoformat()
    days = month_dates()
    day_html = "".join(f'<div class="day" data-date="{d}" onclick="toggleGM(this)">{d[5:]}</div>' for d in days)
    return page("募集作成", f"""
    <form class='card' action='/new' method='post' enctype='multipart/form-data'>
      {csrf_field(request)}
      <h2>1. 募集内容</h2>
      <label>種類<select name='game_type'><option>TRPG</option><option>マダミス</option></select></label>
      <label>シナリオ名<input name='scenario_name' required></label>
      <label>プレイ時間<input name='play_time' placeholder='例：4〜5時間' required></label>
      <label><input style='width:auto' type='checkbox' id='variable' name='variable_players' value='1' onchange='vp()'> 人数可変</label>
      <div id='fixed'><label>募集人数（GMを含まない）<input type='number' min='1' name='fixed_players' value='4'></label></div>
      <div id='range' style='display:none'><label>最小人数<input type='number' min='1' name='min_players' value='2'></label><label>最大人数<input type='number' min='1' name='max_players' value='4'></label></div>
      <label>シナリオ概要<textarea name='description' rows='10' required></textarea></label>
      <label>関連画像（任意）<input type='file' name='image' accept='image/*'></label>
      <label>卓成立時の案内文（任意）<textarea name='guide_message' rows='5' placeholder='事前準備やキャラクター作成についてなど'></textarea></label>
      <hr><h2>2. 日程調整</h2>
      <label>開始時間<input type='time' name='start_time' value='21:00' required></label>
      <label>回答期限（21:00締切）<input type='date' name='deadline_date' value='{default_deadline}' required></label>
      <p>GMが開催可能な日だけタップしてください。もう一度押すと解除されます。</p>
      <input type='hidden' id='gm_dates' name='gm_dates'>
      <div class='grid'>{day_html}</div>
      <br><button type='submit'>募集を開始する</button>
    </form>
    <script>
    function vp(){{document.getElementById('fixed').style.display=document.getElementById('variable').checked?'none':'block';document.getElementById('range').style.display=document.getElementById('variable').checked?'block':'none'}}
    let selected=[];function toggleGM(el){{let d=el.dataset.date;if(selected.includes(d)){{selected=selected.filter(x=>x!==d);el.classList.remove('yes')}}else{{selected.push(d);el.classList.add('yes')}}document.getElementById('gm_dates').value=selected.join(',')}}
    </script>
    """, request)


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
    if not (gm or participant or spectator):
        return page("アクセス不可", "<div class='card'>参加または観戦希望リアクションを押してから開いてください。</div>", request)
    dates = get_gm_dates(rid)
    with db() as c:
        my_answers = {x["event_date"]: x["answer"] for x in c.execute("SELECT * FROM answers WHERE recruitment_id=? AND discord_id=?", (rid, uid)).fetchall()}
        cm = c.execute("SELECT comment FROM comments WHERE recruitment_id=? AND discord_id=?", (rid, uid)).fetchone()
    comment = cm["comment"] if cm else ""
    controls = ""
    if participant:
        controls = ("<h3>あなたの日程回答</h3><p>タップ：無回答 → ○ → △ → 無回答</p><form method='post' action='/r/%d/answer'>" % rid) + csrf_field(request) + "<input type='hidden' name='answers' id='answers'><div class='grid'>"
        state_map = {"yes":"yes","maybe":"maybe"}
        js_obj = json.dumps(my_answers, ensure_ascii=False)
        for d in dates:
            st = state_map.get(my_answers.get(d), "")
            label = "○" if st == "yes" else "△" if st == "maybe" else ""
            controls += f'<div class="day {st}" data-date="{d}" onclick="togglePL(this)">{d[5:]}<br><b>{label}</b></div>'
        controls += f"</div><label>コメント（任意）<textarea name='comment' rows='3'>{esc(comment)}</textarea></label><button>回答を保存</button></form><script>let ans={js_obj};function togglePL(el){{let d=el.dataset.date,s=ans[d]||'';s=s==''?'yes':s=='yes'?'maybe':'';if(s)ans[d]=s;else delete ans[d];el.classList.remove('yes','maybe');if(s)el.classList.add(s);el.querySelector('b').textContent=s=='yes'?'○':s=='maybe'?'△':'';document.getElementById('answers').value=JSON.stringify(ans)}}document.getElementById('answers').value=JSON.stringify(ans);</script>"
    rows = candidate_rows(rid)
    with db() as c:
        active = c.execute("SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='participant' AND active=1 ORDER BY joined_at", (rid,)).fetchall()
        comments = c.execute("SELECT * FROM comments WHERE recruitment_id=? AND comment<>''", (rid,)).fetchall()
    uids = [x[0] for x in active]
    table = "<div class='scroll'><table><tr><th>名前</th>" + "".join(f"<th>{d[5:]}</th>" for d in dates) + "</tr>"
    with db() as c:
        allans = {(x["discord_id"],x["event_date"]):x["answer"] for x in c.execute("SELECT * FROM answers WHERE recruitment_id=?", (rid,)).fetchall()}
    for puid in uids:
        table += f"<tr><td>{esc(user_display(puid))}</td>"
        for d in dates:
            a = allans.get((puid,d),"")
            table += "<td class='ok'>○</td>" if a=="yes" else "<td class='maybe'>△</td>" if a=="maybe" else "<td>—</td>"
        table += "</tr>"
    table += "<tr><th>○人数</th>" + "".join(f"<th>{len(x['yes'])}</th>" for x in rows) + "</tr></table></div>"
    comment_html = "".join(f"<p><b>{esc(user_display(x['discord_id']))}</b>：{esc(x['comment'])}</p>" for x in comments)
    gm_buttons = ""
    if gm:
        gm_buttons = f"<p><a class='btn green' href='/r/{rid}/decide'>開催日を決定</a> <a class='btn alt' href='/r/{rid}/reschedule'>再日程調整</a></p>"
    return page(r["scenario_name"], f"""
    <div class='card'><h2>『{esc(r['scenario_name'])}』</h2><span class='pill'>{esc(r['game_type'])}</span><span class='pill'>{esc(r['status'])}</span><p>募集人数：{r['min_players']}〜{r['max_players']}人 / 開始：{esc(r['start_time'])}</p>{gm_buttons}</div>
    <div class='card'>{controls or '<p class="muted">観戦希望者は日程回答の対象外です。</p>'}</div>
    <div class='card'><h3>回答状況</h3>{table}</div>
    <div class='card'><h3>コメント</h3>{comment_html or '<p class="muted">コメントはありません。</p>'}</div>
    """, request)


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
