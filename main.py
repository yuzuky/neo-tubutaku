from __future__ import annotations

import asyncio
import calendar as pycalendar
import html
import json
import os
import random
import re
import secrets
import sqlite3
import tempfile
import unicodedata
import uuid
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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from database import DATABASE_PATH, RARITY_LABELS, cutoff_resync_v83, cutoff_resync_v83_done, calendar_resync_v90, calendar_resync_v90_done, temporary_v92_madamis_year_fix_done, apply_temporary_v92_madamis_year_fix, temporary_v93_madamis_year_fix_done, apply_temporary_v93_madamis_year_fix, temporary_v94_madamis_year_fix_done, apply_temporary_v94_madamis_year_fix, temporary_v95_madamis_year_fix_done, apply_temporary_v95_madamis_year_fix, temporary_v96_madamis_year_fix_done, apply_temporary_v96_madamis_year_fix, full_derived_rebuild_v75, full_derived_rebuild_v75_done, achievement_bootstrapped, achievement_collection, achievement_run_done, achievement_unlocks_for_user, add_manual_calendar_session, apply_profile_daily_delta, archive_confirmed_session, calendar_conflict_dates, calendar_conflicts_for_users, calendar_entries, calendar_manual_options, calendar_session_detail, calendar_stats, equipped_title, equipped_titles_map, evaluate_achievements, hide_calendar_session, mark_achievement_bootstrapped, mark_achievement_run, new_scenario_count, permanently_delete_calendar_session, profile_cache_initialized, profile_cache_v74_resynced, mark_profile_cache_v74_resynced, profile_data, profile_delta_initialized, refresh_profile_caches, scenario_gm_counter_initialized, ensure_scenario_gm_counter_initialized, refresh_registered_member_profile, registered_member, registered_members, scenario_detail, scenario_progress_data, set_equipped_title, set_scenario_progress_status, update_calendar_session_details, update_calendar_session_members, sync_linked_session_from_calendar_edit, upsert_registered_member, cancel_confirmed_session, confirm_session_reschedule, create_session_reschedule, save_session_reschedule_answers, session_management_detail, session_reschedule_detail, set_recruitment_schedule_slots, recruitment_schedule_slots, save_slot_answers, recruitment_slot_answer_map, candidate_slot_rows, set_session_slots, get_session_slots, sync_calendar_session_slots, session_reschedule_slot_detail, save_session_reschedule_slot_answers, confirm_session_reschedule_slots, db

# ============================================================
# つぶ卓 Bot + Web
# 2ファイル運用版（main.py + database.py）
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

# 同じ作成フォームからの連続POSTをサーバー側でも防止する。
# tokenはフォーム表示時に毎回新しく発行され、最初の1回だけ有効。
_USED_SUBMISSION_TOKENS: set[str] = set()

def claim_submission_once(token: str) -> bool:
    token = str(token or "").strip()
    if not token or len(token) > 200:
        return False
    if token in _USED_SUBMISSION_TOKENS:
        return False
    _USED_SUBMISSION_TOKENS.add(token)

    # 長時間運用で増え続けないよう、十分大きくなったらリセット。
    # 通常利用ではまず到達しない。
    if len(_USED_SUBMISSION_TOKENS) > 10000:
        _USED_SUBMISSION_TOKENS.clear()
        _USED_SUBMISSION_TOKENS.add(token)
    return True

HOME_CREATE_IMAGE = "/static/home-create-4d77743c3efc.png"
HOME_JOIN_IMAGE = "/static/home-join-c09c1e290a9a.png"



def env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1537977355161571461").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

GUILD_ID = env_int("GUILD_ID", 1244656732672753755)
TRPG_CHANNEL_ID = env_int("TRPG_CHANNEL_ID", 1244658549330808842)
MADMIS_CHANNEL_ID = env_int("MADMIS_CHANNEL_ID", 1244658381265047642)
UNDECIDED_CATEGORY_ID = env_int("UNDECIDED_CATEGORY_ID", 1327812864261623890)
SESSION_CATEGORY_ID = env_int("SESSION_CATEGORY_ID", 1245192932147855401)
JOIN_EMOJI_ID = env_int("JOIN_EMOJI_ID", 1486316302308999218)
WATCH_EMOJI_ID = env_int("WATCH_EMOJI_ID", 1486316056711794748)
TSUBUTTER_CHANNEL_ID = int(os.getenv('TSUBUTTER_CHANNEL_ID', '1278698568231817317'))
DEVELOPER_USER_ID = "804350794371039272"  # yuzuky / 開発者テスト用

PORT = env_int("PORT", 8000)
DATA_DIR = Path(DATABASE_PATH).parent
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------- DB ---------------------------
# DB接続・テーブル定義は database.py に分離


# ------------------------ Helpers -------------------------

def now_jst() -> datetime:
    return datetime.now(JST)


def iso_now() -> str:
    return now_jst().isoformat(timespec="seconds")


def safe_channel_name(name: str) -> str:
    """
    Discordのテキストチャンネル名として安全な形へ整形する。
    日本語は残し、空白や一部記号だけ置換する。
    """
    name = (name or "").strip().lower()

    # 空白類をハイフンへ
    name = re.sub(r"\s+", "-", name)

    # Discordチャンネル名で扱いづらい記号をハイフンへ
    name = re.sub(r'[\\/#?:*"<>|`~!@$%^&+=,.;]+', "-", name)

    # 連続ハイフンを1個に
    name = re.sub(r"-{2,}", "-", name)

    # 先頭末尾の不要文字を削除
    name = name.strip("-_. ")

    # 空になった場合のフォールバック
    if not name:
        name = "session"

    # Discordのチャンネル名上限を考慮
    return name[:90]


def split_text(text: str, limit: int = 1900) -> list[str]:
    """
    Discordの1メッセージ上限を超えないように、
    改行をなるべく維持しながら分割する。
    """
    text = str(text or "")
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n", 0, limit)

        # 良い改行位置が無ければ空白、それも無ければ強制分割
        if cut < int(limit * 0.5):
            cut = remaining.rfind(" ", 0, limit)

        if cut < int(limit * 0.5):
            cut = limit

        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)

        remaining = remaining[cut:].lstrip()

    return chunks


def cleanup_old_data():
    """3か月（90日）を超えた募集と関連データ・未参照画像を削除する。"""
    cutoff = (now_jst() - timedelta(days=90)).isoformat(timespec="seconds")
    candidate_image_paths = []
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

        img_rows = c.execute(
            f"""SELECT image_path
                FROM recruitment_images
                WHERE recruitment_id IN ({marks})""",
            tuple(ids),
        ).fetchall()
        candidate_image_paths.extend(
            [str(x["image_path"]) for x in img_rows if x["image_path"]]
        )
        candidate_image_paths.extend(
            [str(x["image_path"]) for x in old if x["image_path"]]
        )

        c.execute(
            f"""UPDATE recruitments
                SET parent_id=NULL
                WHERE parent_id IN ({marks})
                  AND id NOT IN ({marks})""",
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
        c.execute(
            f"DELETE FROM recruitments WHERE id IN ({marks})",
            tuple(ids),
        )

        # 他の現存卓から参照されていない画像だけ削除対象にする
        deletable = []
        for path in set(candidate_image_paths):
            still_multi = c.execute(
                "SELECT 1 FROM recruitment_images WHERE image_path=? LIMIT 1",
                (path,),
            ).fetchone()
            still_legacy = c.execute(
                "SELECT 1 FROM recruitments WHERE image_path=? LIMIT 1",
                (path,),
            ).fetchone()
            if not still_multi and not still_legacy:
                deletable.append(path)

    for p in deletable:
        try:
            path = Path(p)
            if path.exists() and UPLOAD_DIR in path.parents:
                path.unlink()
        except Exception as e:
            log_error("cleanup_old_image", e)

    for sid in session_ids:
        task = reminder_tasks.get(sid) if "reminder_tasks" in globals() else None
        if task and not task.done():
            task.cancel()

    print(f"[CLEANUP] deleted {len(ids)} recruitments older than 90 days", flush=True)
    return len(ids)


def get_recruitment(rid: int):
    with db() as c:
        return c.execute("SELECT * FROM recruitments WHERE id=?", (rid,)).fetchone()


def latest_reschedule_id(rid: int) -> int:
    """元募集から再日程調整の子をたどり、現在の最新募集IDを返す。"""
    current = int(rid)
    visited = {current}

    with db() as c:
        while True:
            child = c.execute(
                """SELECT id
                   FROM recruitments
                   WHERE parent_id=?
                   ORDER BY id DESC
                   LIMIT 1""",
                (current,),
            ).fetchone()

            if not child:
                break

            cid = int(child["id"])
            if cid in visited:
                break

            visited.add(cid)
            current = cid

    return current


def original_recruitment_with_message(rid: int):
    """再日程調整の祖先をたどり、元のDiscord募集投稿を持つ募集を返す。"""
    current = get_recruitment(int(rid))
    visited = set()

    while current:
        current_id = int(current["id"])
        if current_id in visited:
            break
        visited.add(current_id)

        if current["recruitment_message_id"] and current["recruitment_channel_id"]:
            return current

        parent_id = current["parent_id"]
        if not parent_id:
            break
        current = get_recruitment(int(parent_id))

    return None


async def fetch_current_reaction_members(rid: int):
    """
    元の募集投稿に現在付いている参加・観戦リアクションをDiscordから再取得する。
    成功時:
      {"participant": {uid...}, "spectator": {uid...}}
    取得不能時:
      None
    """
    source = original_recruitment_with_message(rid)
    if not source:
        return None

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return None

    try:
        channel = guild.get_channel(int(source["recruitment_channel_id"]))
        if not channel:
            channel = await guild.fetch_channel(int(source["recruitment_channel_id"]))

        message = await channel.fetch_message(int(source["recruitment_message_id"]))

        result = {
            "participant": set(),
            "spectator": set(),
        }

        for reaction in message.reactions:
            emoji_id = getattr(reaction.emoji, "id", None)

            if emoji_id == JOIN_EMOJI_ID:
                kind = "participant"
            elif emoji_id == WATCH_EMOJI_ID:
                kind = "spectator"
            else:
                continue

            async for reactor in reaction.users(limit=None):
                reactor_id = str(reactor.id)

                # Bot自身の自動リアクションは参加者に含めない
                if bot.user and reactor.id == bot.user.id:
                    continue

                result[kind].add(reactor_id)

                # 表示名キャッシュも可能な範囲で更新
                member = guild.get_member(reactor.id)
                if member:
                    with db() as c:
                        c.execute(
                            """INSERT INTO users(
                                   discord_id,username,display_name,avatar_url,updated_at
                               )
                               VALUES(?,?,?,?,?)
                               ON CONFLICT(discord_id) DO UPDATE SET
                                 username=excluded.username,
                                 display_name=excluded.display_name,
                                 avatar_url=excluded.avatar_url,
                                 updated_at=excluded.updated_at""",
                            (
                                reactor_id,
                                member.name,
                                member.display_name,
                                str(member.display_avatar.url),
                                iso_now(),
                            ),
                        )

        print(
            f"[RESCHEDULE] Discord reactions refreshed "
            f"rid={rid} participants={len(result['participant'])} "
            f"spectators={len(result['spectator'])}",
            flush=True,
        )
        return result

    except Exception as e:
        log_error(f"fetch_current_reaction_members rid={rid}", e)
        return None


def get_user(uid: str):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE discord_id=?", (uid,)).fetchone()


def get_recruitment_images(rid: int) -> list[str]:
    with db() as c:
        rows = c.execute(
            """SELECT image_path
               FROM recruitment_images
               WHERE recruitment_id=?
               ORDER BY sort_order, rowid""",
            (rid,),
        ).fetchall()
        paths = [str(x["image_path"]) for x in rows if x["image_path"]]

        # 旧DB互換
        if not paths:
            legacy = c.execute(
                "SELECT image_path FROM recruitments WHERE id=?",
                (rid,),
            ).fetchone()
            if legacy and legacy["image_path"]:
                paths = [str(legacy["image_path"])]
        return paths




def cleanup_posted_recruitment_images(rid: int | None = None) -> int:
    """
    Discordへの募集投稿が完了した画像をローカル保存から解放する。
    Discord側の添付画像はDiscordが保持するため、投稿後のローカルコピーは不要。
    DB上の画像参照も削除し、他の募集から参照されていない実ファイルだけ消す。
    rid=None の場合は、既に投稿済みの募集をまとめて掃除する。
    """
    with db() as c:
        if rid is None:
            rows = c.execute(
                """SELECT id, image_path
                   FROM recruitments
                   WHERE recruitment_message_id IS NOT NULL
                     AND recruitment_message_id<>''"""
            ).fetchall()
            target_ids = [int(x["id"]) for x in rows]
            legacy_paths = [str(x["image_path"]) for x in rows if x["image_path"]]
        else:
            row = c.execute(
                "SELECT id, image_path FROM recruitments WHERE id=?",
                (rid,),
            ).fetchone()
            if not row:
                return 0
            target_ids = [int(row["id"])]
            legacy_paths = [str(row["image_path"])] if row["image_path"] else []

        if not target_ids:
            return 0

        marks = ",".join("?" for _ in target_ids)
        image_rows = c.execute(
            f"SELECT image_path FROM recruitment_images WHERE recruitment_id IN ({marks})",
            tuple(target_ids),
        ).fetchall()
        candidate_paths = {str(x["image_path"]) for x in image_rows if x["image_path"]}
        candidate_paths.update(legacy_paths)

        c.execute(
            f"DELETE FROM recruitment_images WHERE recruitment_id IN ({marks})",
            tuple(target_ids),
        )
        c.execute(
            f"UPDATE recruitments SET image_path=NULL WHERE id IN ({marks})",
            tuple(target_ids),
        )

        deletable = []
        for path_str in candidate_paths:
            still_multi = c.execute(
                "SELECT 1 FROM recruitment_images WHERE image_path=? LIMIT 1",
                (path_str,),
            ).fetchone()
            still_legacy = c.execute(
                "SELECT 1 FROM recruitments WHERE image_path=? LIMIT 1",
                (path_str,),
            ).fetchone()
            if not still_multi and not still_legacy:
                deletable.append(path_str)

    deleted = 0
    for path_str in deletable:
        try:
            img_path = Path(path_str)
            if img_path.exists() and UPLOAD_DIR in img_path.parents:
                img_path.unlink()
                deleted += 1
        except Exception as e:
            log_error("cleanup_posted_recruitment_image", e)

    return deleted

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
    # Discordサーバー上の現在の表示名を最優先。
    # OAuth未利用などでusersテーブルに未登録のメンバーでも、
    # Botのmember cacheにいればサーバー表示名で表示する。
    try:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(int(uid))
            if member:
                return member.display_name
    except Exception:
        pass

    u = get_user(uid)
    if u:
        return u["display_name"] or u["username"] or str(uid)

    # 最終フォールバックもメンション形式にせずID文字列だけにする。
    return str(uid)


def candidate_rows(rid: int):
    dates = get_gm_dates(rid)
    result = []
    r = get_recruitment(rid)
    if not r:
        return result
    gm_id = str(r["gm_discord_id"])

    with db() as c:
        for d in dates:
            yes = c.execute(
                """SELECT DISTINCT a.discord_id
                   FROM answers a
                   JOIN members m
                     ON m.recruitment_id=a.recruitment_id
                    AND m.discord_id=a.discord_id
                   WHERE a.recruitment_id=?
                     AND a.event_date=?
                     AND a.answer='yes'
                     AND m.member_type='participant'
                     AND m.active=1
                     AND a.discord_id<>?""",
                (rid, d, gm_id),
            ).fetchall()

            maybe = c.execute(
                """SELECT DISTINCT a.discord_id
                   FROM answers a
                   JOIN members m
                     ON m.recruitment_id=a.recruitment_id
                    AND m.discord_id=a.discord_id
                   WHERE a.recruitment_id=?
                     AND a.event_date=?
                     AND a.answer='maybe'
                     AND m.member_type='participant'
                     AND m.active=1
                     AND a.discord_id<>?""",
                (rid, d, gm_id),
            ).fetchall()

            result.append({
                "date": d,
                "yes": [x[0] for x in yes],
                "maybe": [x[0] for x in maybe],
            })
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



def parse_schedule_slots(dates: list[str], default_time: str, per_day_time: bool, raw_json: str = "") -> list[tuple[str,str]]:
    """高度な時間設定を開催slotへ正規化。通常モードは各日1slotだけ。"""
    clean_dates=[str(d) for d in dict.fromkeys(dates) if re.fullmatch(r"\d{4}-\d{2}-\d{2}",str(d))]
    raw_base=str(default_time or '').strip()
    if not per_day_time:
        base=raw_base if re.fullmatch(r"\d{2}:\d{2}",raw_base) else '未定'
        return [(d,base) for d in clean_dates]
    base=raw_base if re.fullmatch(r"\d{2}:\d{2}",raw_base) else '21:00'
    try:
        obj=json.loads(raw_json or '{}')
    except Exception:
        obj={}
    out=[]
    for d in clean_dates:
        vals=obj.get(d,[]) if isinstance(obj,dict) else []
        times=[]
        if isinstance(vals,list):
            for t in vals:
                t=str(t or '').strip()
                if re.fullmatch(r"\d{2}:\d{2}",t) and t not in times:
                    times.append(t)
        if not times:
            times=[base]
        for t in sorted(times):
            out.append((d,t))
    return out


def advanced_schedule_controls_html() -> str:
    """通常UIを保ち、ON時だけ日別の時間slot編集を展開する共通UI。"""
    return r"""
      <label class='checkbox-row' style='margin-top:14px'>
        <input type='checkbox' id='perDayTime' name='per_day_time' value='1' onchange='refreshAdvancedSlots()'>
        開催時間を日別に設定する
      </label>
      <input type='hidden' id='schedule_slots_json' name='schedule_slots_json' value='{}'>
      <div id='advancedTimePanel' style='display:none;margin-top:12px'></div>
      <script>
      let advancedSlotMap={};
      function advBaseTime(){
        const x=document.querySelector('input[name="start_time"]');
        return (x && x.value) ? x.value : '21:00';
      }
      function advDateLabel(ds){
        const d=new Date(ds+'T00:00:00');
        const w=['日','月','火','水','木','金','土'][d.getDay()];
        return `${d.getMonth()+1}/${d.getDate()}(${w})`;
      }
      function syncAdvancedHidden(){
        const h=document.getElementById('schedule_slots_json');
        if(h) h.value=JSON.stringify(advancedSlotMap);
      }
      function refreshAdvancedSlots(){
        const enabled=document.getElementById('perDayTime')?.checked;
        const panel=document.getElementById('advancedTimePanel');
        if(!panel) return;
        const dates=(typeof selected!=='undefined' ? selected : []);
        const keep={};
        dates.forEach(d=>{
          let xs=Array.isArray(advancedSlotMap[d]) ? advancedSlotMap[d].filter(Boolean) : [];
          if(!xs.length) xs=[advBaseTime()];
          keep[d]=[...new Set(xs)].sort();
        });
        advancedSlotMap=keep;
        syncAdvancedHidden();
        if(!enabled){ panel.style.display='none'; panel.innerHTML=''; return; }
        panel.style.display='block';
        if(!dates.length){ panel.innerHTML="<div class='muted small'>先に開催候補日を選択してください。</div>"; return; }
        panel.innerHTML=dates.slice().sort().map(d=>{
          const times=advancedSlotMap[d]||[advBaseTime()];
          const rows=times.map(t=>`<div style="display:flex;align-items:center;gap:8px;padding:7px 0"><span style="font-weight:700">${t}〜</span>${times.length>1?`<button type="button" class="btn alt" style="width:auto;padding:5px 9px" onclick="removeAdvTime('${d}','${t}')">削除</button>`:''}</div>`).join('');
          return `<div class="field-box no-icon" style="margin-bottom:10px"><div class="field-stack"><span class="field-label">${advDateLabel(d)}</span>${rows}<div id="addrow-${d}" style="display:none;gap:8px;align-items:center;margin-top:6px"><input type="time" id="addtime-${d}" value="${advBaseTime()}" style="flex:1"><button type="button" class="btn green" style="width:auto" onclick="commitAdvTime('${d}')">追加</button></div><button type="button" class="btn alt" style="margin-top:8px;width:100%" onclick="showAdvAdd('${d}')">＋ 時間を追加</button></div></div>`;
        }).join('');
      }
      function showAdvAdd(d){ const r=document.getElementById('addrow-'+d); if(r) r.style.display='flex'; }
      function commitAdvTime(d){
        const i=document.getElementById('addtime-'+d); const t=i?.value;
        if(!t) return;
        advancedSlotMap[d]=[...new Set([...(advancedSlotMap[d]||[]),t])].sort();
        refreshAdvancedSlots();
      }
      function removeAdvTime(d,t){
        const xs=(advancedSlotMap[d]||[]).filter(x=>x!==t);
        advancedSlotMap[d]=xs.length?xs:[advBaseTime()];
        refreshAdvancedSlots();
      }
      document.addEventListener('change',e=>{ if(e.target && e.target.name==='start_time' && document.getElementById('perDayTime')?.checked){ refreshAdvancedSlots(); }});
      </script>
    """

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
.menu-card.schedule{
  background:linear-gradient(135deg,#0f9f8b 0%,#18b981 100%);
  border-color:rgba(255,255,255,.13);
}
.menu-card.schedule .menu-sub{color:rgba(255,255,255,.76)}
.menu-card.schedule .chev{color:white}
.menu-card.calendar{
  background:linear-gradient(135deg,#17325c 0%,#2457a6 100%);
  border-color:rgba(255,255,255,.13);
}
.menu-card.calendar .menu-sub{color:rgba(255,255,255,.76)}
.menu-card.calendar .chev{color:white}
.calendar-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:8px 0 18px}
.calendar-head h2{margin:0;font-size:1.45rem}
.calendar-nav{display:inline-flex;align-items:center;justify-content:center;min-width:42px;height:42px;border:1px solid var(--line);border-radius:12px;background:#101722;color:#fff;text-decoration:none;font-weight:900}

/* カレンダー本体：7列を完全に同じ幅にする */
.calendar-grid{
  display:grid;
  grid-template-columns:repeat(7,minmax(0,1fr));
  gap:1px;
  width:100%;
  overflow:hidden;
  border:1px solid #202733;
  border-radius:14px;
  background:#202733;
}
.calendar-weekday{
  min-width:0;
  text-align:center;
  color:#8f9aad;
  background:#0b1017;
  font-size:.76rem;
  font-weight:850;
  padding:8px 2px;
}
.calendar-weekday.sun{color:#ef6a71}
.calendar-weekday.sat{color:#6b91ff}

/* 日付マスは内容量に関係なく常に同じ高さ */
.calendar-day{
  box-sizing:border-box;
  min-width:0;
  width:100%;
  height:150px;
  border:0;
  background:#0d131c;
  border-radius:0;
  padding:7px;
  overflow:hidden;
}
.calendar-day.outside{
  opacity:.30;
  background:#0a0f16;
}
.calendar-day.today{
  background:#171d26;
  box-shadow:inset 0 0 0 1px #3a4350;
}
.calendar-date{
  font-size:.82rem;
  font-weight:900;
  color:#dfe6ef;
  height:18px;
  line-height:18px;
  margin-bottom:5px;
  white-space:nowrap;
}
.calendar-date.sun{color:#ef6a71}
.calendar-date.sat{color:#6b91ff}
/* 今日の日付だけ、数字を紫の丸で強調する。カレンダー集計処理には影響しない表示専用CSS。 */
.calendar-day.today .calendar-date{
  display:flex;
  align-items:center;
  justify-content:center;
  width:18px;
  height:18px;
  margin:0 0 5px 0;
  font-size:.68rem;
  border-radius:0;
  background:radial-gradient(circle at center, #7667e8 0 7.5px, transparent 8px);
  color:#fff;
  line-height:18px;
  box-shadow:none;
}

/* 1つの卓が他のマスの幅・高さを押し広げないようにする */
.cal-session{
  min-width:0;
  width:100%;
  margin-top:4px;
  padding-top:4px;
  overflow:hidden;
  border-top:1px solid rgba(255,255,255,.05);
}
.cal-title,.cal-person{
  display:block;
  box-sizing:border-box;
  width:100%;
  max-width:100%;
  border-radius:5px;
  padding:3px 5px;
  margin:2px 0;
  font-size:.62rem;
  font-weight:850;
  line-height:1.25;

  /* 折り返し禁止。狭いセルでは末尾をそのまま切る */
  white-space:nowrap;
  overflow:hidden;
  text-overflow:clip;
}
.cal-title.trpg{background:rgba(57,168,104,.18);color:#63d893}
.cal-title.madamis{background:rgba(231,129,45,.18);color:#f19a4f}
.cal-title.event{background:rgba(48,130,219,.18);color:#69a9f0}
.cal-person.gm{background:rgba(160,92,217,.16);color:#c28aec}
.cal-person.pl{background:rgba(207,165,42,.15);color:#e3bd52}
.calendar-empty{text-align:center;color:var(--muted);padding:30px 10px}

@media(max-width:620px){
  .calendar-head{margin-bottom:12px}
  .calendar-head h2{font-size:1.2rem}
  .calendar-nav{min-width:38px;height:38px}

  .calendar-grid{
    gap:1px;
    border-radius:10px;
  }
  .calendar-weekday{
    font-size:.67rem;
    padding:6px 1px;
  }
  .calendar-day{
    height:118px;
    padding:4px 3px;
  }
  .calendar-date{
    font-size:.70rem;
    margin-bottom:3px;
  }
  .cal-session{
    margin-top:3px;
    padding-top:3px;
  }
  .cal-title,.cal-person{
    font-size:.49rem;
    padding:2px 3px;
    margin:2px 0;
    border-radius:4px;
  }
}

@media(max-width:390px){
  .calendar-day{height:110px}
  .cal-title,.cal-person{font-size:.46rem;padding:2px}
}

.cal-session{
  cursor:pointer;
}
.cal-session:active{
  opacity:.78;
}

/* カレンダー予定の詳細ポップアップ */
.calendar-modal{
  position:fixed;
  inset:0;
  display:none;
  align-items:center;
  justify-content:center;
  padding:20px;
  background:rgba(0,0,0,.68);
  z-index:9999;
}
.calendar-modal.open{display:flex}
.calendar-modal-card{
  width:min(440px,100%);
  max-height:78vh;
  overflow:auto;
  border:1px solid #334155;
  border-radius:20px;
  background:#111925;
  box-shadow:0 24px 70px rgba(0,0,0,.45);
  padding:22px;
}
.calendar-modal-title{
  margin:0 0 14px;
  font-size:1.25rem;
  font-weight:950;
  line-height:1.45;
  overflow-wrap:anywhere;
}
.calendar-modal-meta{
  display:grid;
  gap:10px;
  color:#c9d2df;
}
.calendar-modal-row{
  padding:10px 12px;
  border-radius:12px;
  background:#0b121c;
  border:1px solid #263244;
}
.calendar-modal-label{
  display:block;
  margin-bottom:6px;
  color:#8190a4;
  font-size:.72rem;
  font-weight:850;
}
.calendar-modal-members{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
}
.calendar-modal-member{
  display:inline-block;
  padding:4px 8px;
  border-radius:7px;
  background:rgba(207,165,42,.15);
  color:#e3bd52;
  font-size:.8rem;
  font-weight:850;
}
.calendar-modal-gm{
  color:#c28aec;
  font-weight:900;
}
.calendar-modal-close{
  width:100%;
  margin-top:16px;
  padding:11px;
  border:1px solid #334155;
  border-radius:12px;
  background:#182231;
  color:#fff;
  font-weight:900;
  cursor:pointer;
}
.calendar-head{
  position:relative;
}
.stats-circle{
  width:42px;
  height:42px;
  flex:0 0 42px;
  border-radius:50%;
  border:1px solid #334155;
  background:#101722;
  display:flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
}
.stats-circle-floating{
  position:absolute;
  right:0;
  top:-58px;
  z-index:5;
}
.stats-bars{
  height:19px;
  display:flex;
  align-items:flex-end;
  gap:3px;
}
.stats-bars i{
  display:block;
  width:4px;
  border-radius:3px;
  background:#e2e8f0;
}
.stats-bars i:nth-child(1){height:9px}
.stats-bars i:nth-child(2){height:18px}
.stats-bars i:nth-child(3){height:12px}

.manual-type-toggle{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:8px;
  margin:14px 0 18px;
}
.manual-type-toggle label{
  cursor:pointer;
}
.manual-type-toggle input{
  position:absolute;
  opacity:0;
  pointer-events:none;
}
.manual-type-toggle span{
  display:flex;
  align-items:center;
  justify-content:center;
  height:54px;
  border-radius:14px;
  border:1px solid #334155;
  background:#101824;
  color:#b7c2d2;
  font-size:1rem;
  font-weight:900;
  transition:.15s ease;
}
.manual-type-toggle input:checked + span{
  border-color:#64748b;
  background:#1a2432;
  color:#fff;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.05);
}
.manual-field-spaced{
  margin-top:14px;
}
.manual-time-box{
  min-height:88px;
  padding-top:12px;
  padding-bottom:10px;
}
.manual-time-box input[type='time']{
  min-height:36px;
  padding-top:2px;
  padding-bottom:2px;
}
.manual-gm-field{
  margin-top:18px;
}
.manual-disabled{
  opacity:.35;
  pointer-events:none;
}

.calendar-edit-open{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:100%;
  margin-top:16px;
  padding:11px;
  border:1px solid #46566d;
  border-radius:12px;
  background:#172131;
  color:#fff;
  font-weight:900;
  cursor:pointer;
}
.calendar-edit-panel{
  display:none;
  margin-top:14px;
  padding-top:14px;
  border-top:1px solid #2c394c;
}
.calendar-edit-panel.open{display:block}
/* v107: カレンダー編集のGMラベルと選択欄が重ならないよう、シナリオ名欄と同じ縦配置に揃える */
.calendar-edit-gm-field .field-label{margin:2px 0 0 4px}
.calendar-edit-gm-field select{background:transparent;padding:8px 4px 10px}
.calendar-edit-date-field{margin-top:0!important;margin-bottom:14px!important}
.calendar-edit-date-field .field-box{width:100%;box-sizing:border-box}
.calendar-edit-date-field input[type='date']{width:100%;box-sizing:border-box}
.calendar-danger-block{
  margin-top:16px;
  padding:13px;
  border:1px solid rgba(234,179,8,.34);
  border-radius:14px;
  background:rgba(234,179,8,.05);
}
.calendar-danger-block.delete{
  border-color:rgba(239,68,68,.35);
  background:rgba(239,68,68,.05);
}
.danger-confirm-line{
  display:flex;
  align-items:center;
  gap:8px;
  color:#d9e2ee;
  font-weight:850;
}
.danger-confirm-line input{
  width:auto;
}
.danger-question{
  margin:8px 0 10px;
  color:#9eabbc;
  font-size:.78rem;
}
.calendar-hide-btn,
.calendar-delete-btn{
  width:100%;
  padding:10px;
  border-radius:11px;
  border:1px solid transparent;
  font-weight:900;
  cursor:pointer;
}
.calendar-hide-btn{
  background:#4a3a13;
  color:#f8d66d;
}
.calendar-delete-btn{
  background:#4a171d;
  color:#ff8f9a;
}
.calendar-hide-btn:disabled,
.calendar-delete-btn:disabled{
  opacity:.35;
  cursor:not-allowed;
}

.manual-user-list{
  max-height:230px;
  overflow:auto;
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:7px;
}
.guest-member-toggle{
  grid-column:1 / -1;
  width:100%;
  box-sizing:border-box;
  display:flex;
  align-items:center;
  gap:9px;
  margin-top:9px;
  padding:11px 12px;
  border-radius:11px;
  background:#161d29;
  border:1px solid #354156;
  font-size:.85rem;
  font-weight:800;
  cursor:pointer;
}
.guest-member-toggle input{width:auto;margin:0;}
.guest-member-input-wrap{display:none;margin-top:8px;}
.guest-member-input-wrap.open{display:block;}
.guest-member-input-wrap textarea{width:100%;box-sizing:border-box;}
.manual-user-check{
  display:flex;
  align-items:center;
  gap:7px;
  min-width:0;
  padding:8px 9px;
  border-radius:10px;
  background:#101824;
  border:1px solid #263244;
  font-size:.8rem;
}
.manual-user-check input{
  width:auto;
  flex:0 0 auto;
}
.manual-user-check span{
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.manual-actions{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
  margin-top:16px;
}

.stats-card{max-width:540px}
.stats-section{
  border:1px solid #2a3749;
  background:#0d151f;
  border-radius:16px;
  padding:15px;
  margin-top:12px;
}
.stats-section.total{background:#0a1119}
.stats-section-title{
  font-size:1rem;
  font-weight:950;
  margin-bottom:12px;
}
.stats-section-title span{
  color:#7f8b9d;
  font-size:.7rem;
  margin-left:6px;
}
.stats-number-grid{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:7px;
  margin-bottom:15px;
}
.stats-number-grid div{
  min-width:0;
  text-align:center;
  border-radius:12px;
  background:#121c29;
  padding:10px 4px;
}
.stats-number-grid b{
  display:block;
  font-size:1.25rem;
}
.stats-number-grid span{
  display:block;
  color:#8e9bad;
  font-size:.62rem;
  margin-top:2px;
}
.stats-ranking-title{
  margin:14px 0 6px;
  color:#9ba8ba;
  font-size:.72rem;
  font-weight:900;
}
.rank-row{
  display:grid;
  grid-template-columns:44px 1fr auto;
  align-items:center;
  gap:8px;
  padding:7px 4px;
}
.rank-badge{
  position:relative;
  width:38px;
  height:38px;
  display:flex;
  align-items:center;
  justify-content:center;
}
.rank-badge::before{
  content:"";
  position:absolute;
  width:28px;
  height:28px;
  transform:rotate(45deg);
  border-radius:5px;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.28),
    0 4px 12px rgba(0,0,0,.22);
}
.rank-badge b{
  position:relative;
  z-index:2;
  color:#fff;
  font-size:.88rem;
  font-weight:1000;
}
.rank-one::before{
  background:linear-gradient(145deg,#f8d86b,#c99a19);
}
.rank-two::before{
  background:linear-gradient(145deg,#dbe2ea,#8f9aa7);
}
.rank-three::before{
  background:linear-gradient(145deg,#d99a6c,#9a5d34);
}
.rank-name{
  min-width:0;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  font-weight:850;
}
.rank-count{
  color:#e5edf6;
  font-size:.78rem;
  font-weight:900;
}
@media(max-width:620px){
  .manual-type-toggle span{
    height:48px;
    font-size:.82rem;
  }
  .stats-number-grid{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .manual-user-list{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
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
.field-box select{
  appearance:none;
  color-scheme:dark;
  background-color:#121923;
  color:var(--text);
}

/* PC版 Chrome / Edge / Windows のネイティブ選択メニュー対策。
   option側にも背景色と文字色を明示して白背景＋白文字を防ぐ。 */
select{
  color-scheme:dark;
}
select option,
select optgroup{
  background-color:#111827;
  color:#f7f9fc;
}
select option:checked{
  background-color:#2563eb;
  color:#ffffff;
}
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
.btn.danger{
  background:#3a171b;
  color:#ff7777;
  border:1px solid #6b2a31;
  box-shadow:none;
}
.btn.danger:hover{background:#461b20;border-color:#87343d}
.btn.danger:disabled{opacity:.42;cursor:not-allowed}

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
.calendar-conflict-badge{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:1.15em;
  height:1.15em;
  border-radius:50%;
  background:rgba(248,113,113,.18);
  border:1px solid rgba(248,113,113,.38);
  color:#ef4444;
  font-size:1em;
  font-weight:1000;
  line-height:1;
  flex:0 0 auto;
}
.answer-member-result{
  display:inline-flex;
  align-items:center;
  gap:4px;
  flex:0 0 auto;
}
.answer-day-state-wrap{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:7px;
  margin:11px 0 10px;
}
.answer-day-state-wrap .answer-day-state{
  margin:0;
}
.conflict-legend-mark{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:18px;
  height:18px;
  border-radius:50%;
  background:rgba(248,113,113,.18);
  color:#ef4444;
  font-weight:1000;
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

.menu-icon.asset{
  width:64px;
  height:64px;
  flex:0 0 64px;
  padding:0;
  border-radius:16px;
  overflow:hidden;
  background:rgba(255,255,255,.08);
}
.menu-icon.asset img{
  width:100%;
  height:100%;
  display:block;
  object-fit:contain;
}
.menu-card.primary .menu-icon.asset{
  background:rgba(255,255,255,.15);
}
@media(max-width:620px){
  .menu-icon.asset{
    width:56px;
    height:56px;
    flex-basis:56px;
  }
}

/* ===== 卓一覧 削除メニュー ===== */
.session-card-wrap{
  position:relative;
}
.session-card-wrap .session-card{
  display:block;
  padding-right:86px;
}
.kebab-outside{
  position:absolute;
  right:24px;
  top:24px;
  pointer-events:none;
}
.table-menu{
  position:absolute;
  top:18px;
  right:18px;
  z-index:40;
}
.table-menu summary{
  list-style:none;
  cursor:pointer;
  color:#7184a3;
  font-size:28px;
  line-height:1;
  padding:8px 10px;
  border-radius:10px;
}
.table-menu summary:hover{
  background:rgba(255,255,255,.05);
}
.table-menu summary::-webkit-details-marker{
  display:none;
}
.table-menu form{
  position:absolute;
  top:42px;
  right:0;
  width:172px;
  padding:8px;
  border:1px solid #2b3950;
  border-radius:14px;
  background:#121b29;
  box-shadow:0 14px 32px rgba(0,0,0,.42);
}
.delete-table-btn{
  width:100%;
  border:0;
  border-radius:10px;
  padding:12px 14px;
  background:#3a1820;
  color:#ff6b7a;
  font-weight:800;
  cursor:pointer;
}
.delete-table-btn:hover{
  background:#4a1c26;
}

/* ===== 開催日決定 UI 微調整 ===== */
.round-number-row{
  display:flex;
  align-items:center;
  gap:10px;
  margin-bottom:18px;
  font-size:1rem;
  font-weight:700;
}

.round-number-row input{
  width:110px;
  min-width:110px;
  margin:0;
}

.round-number-row span{
  white-space:nowrap;
}

.random-session-btn{
  width:100%;
  margin:10px 0 18px;
  padding-top:15px;
  padding-bottom:15px;
  font-size:.88rem;
  font-weight:800;
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
<style>{CSS}
/* v50: calendar detail modal polish */
#calendarDetailTitle{{
  text-align:center;
  width:100%;
}}
.calendar-modal-gm-tag{{
  background:transparent;
  color:#d08aff;
  border:0;
  font-weight:900;
}}


/* v51 UI polish */
.manual-date-line{{
  margin:2px 0 12px;
  text-align:center;
  color:#d9e2ee;
  font-size:.98rem;
  font-weight:900;
}}
.manual-scenario-box{{
  min-height:78px;
  padding-top:10px;
  padding-bottom:10px;
}}
.manual-field-spaced{{
  margin-top:16px;
}}
.manual-control-box{{
  padding-top:16px;
}}
.manual-control-box .field-label{{
  margin-bottom:8px;
}}
.manual-gm-box{{
  padding-top:18px;
  padding-bottom:12px;
}}
.manual-gm-box select{{
  margin-top:4px;
}}
.calendar-save-btn{{
  width:100%;
  margin-top:14px;
  padding:12px;
  border:1px solid rgba(34,197,94,.38);
  border-radius:12px;
  background:rgba(34,197,94,.18);
  color:#69e89b;
  font-weight:950;
  cursor:pointer;
}}
.calendar-danger-actions{{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:8px;
  margin-top:12px;
}}
.calendar-hide-small,
.calendar-delete-small{{
  padding:8px 10px;
  border-radius:10px;
  font-size:.78rem;
  font-weight:900;
  cursor:pointer;
}}
.calendar-hide-small{{
  border:1px solid rgba(234,179,8,.35);
  background:rgba(234,179,8,.10);
  color:#f3cc5c;
}}
.calendar-delete-small{{
  border:1px solid rgba(239,68,68,.38);
  background:rgba(239,68,68,.10);
  color:#ff8d96;
}}
.calendar-danger-confirm{{
  display:none;
  margin-top:12px;
  padding:12px;
  border:1px solid rgba(234,179,8,.35);
  border-radius:12px;
  background:rgba(234,179,8,.05);
}}
.calendar-danger-confirm.delete{{
  border-color:rgba(239,68,68,.36);
  background:rgba(239,68,68,.05);
}}
.calendar-danger-confirm.open{{
  display:block;
}}
.calendar-danger-confirm p{{
  margin:0 0 10px;
  color:#d8e0ea;
  font-size:.8rem;
  line-height:1.5;
}}
.stats-wide-card{{
  min-height:86px;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  margin-bottom:9px;
  border-radius:13px;
  background:#121c29;
  text-align:center;
}}
.stats-wide-card b{{
  font-size:1.45rem;
}}
.stats-wide-card span{{
  margin-top:3px;
  color:#8e9bad;
  font-size:.68rem;
}}
.stats-wide-card.total-count{{
  min-height:102px;
}}
.stats-wide-card.total-count b{{
  font-size:1.75rem;
}}
.stats-type-row{{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:8px;
  margin-bottom:15px;
}}
.stats-type-row div{{
  min-width:0;
  padding:12px 5px;
  border-radius:12px;
  background:#121c29;
  text-align:center;
}}
.stats-type-row b{{
  display:block;
  font-size:1.28rem;
}}
.stats-type-row span{{
  display:block;
  margin-top:3px;
  color:#8e9bad;
  font-size:.62rem;
}}


/* v52 confirmation typography */
.calendar-danger-confirm{{
  text-align:center;
}}
.calendar-danger-confirm p{{
  text-align:center;
  font-weight:900;
  font-size:.82rem;
}}
.calendar-danger-confirm .danger-confirm-line{{
  justify-content:center;
  font-weight:400;
  font-size:.72rem;
  color:#9eabbc;
}}
.calendar-danger-confirm .danger-confirm-line span{{
  font-weight:400;
}}

/* v52 progress / scenario detail */
.calendar-floating-tools{{
  position:absolute;
  right:0;
  top:-58px;
  z-index:5;
  display:flex;
  gap:8px;
}}
.stats-circle-floating{{
  position:static;
}}
.progress-list-icon{{
  display:flex;
  flex-direction:column;
  gap:3px;
}}
.progress-list-icon i{{
  display:block;
  width:18px;
  height:3px;
  border-radius:4px;
  background:#e2e8f0;
}}
.progress-list-icon i:nth-child(2){{width:14px}}
.progress-list-icon i:nth-child(3){{width:10px}}

.progress-card{{
  width:min(700px,100%);
  max-height:84vh;
}}
.progress-legend{{
  display:flex;
  flex-wrap:wrap;
  justify-content:center;
  gap:12px;
  margin-bottom:12px;
  color:#9ba8ba;
  font-size:.72rem;
}}
.progress-dot{{
  font-size:1rem;
  font-weight:1000;
}}
.progress-dot.passed{{color:#32d875}}
.progress-dot.watched{{color:#4ea2ff}}
.progress-tabs{{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:8px;
  margin-bottom:12px;
}}
.progress-tab{{
  padding:10px;
  border:1px solid #334155;
  border-radius:12px;
  background:#101824;
  color:#9eabbc;
  font-weight:900;
  cursor:pointer;
}}
.progress-tab.active{{
  background:#1a2636;
  color:#fff;
  border-color:#64748b;
}}
.progress-panel{{display:none}}
.progress-panel.active{{display:block}}
.progress-table-scroll{{
  overflow:auto;
  max-height:52vh;
  border:1px solid #263244;
  border-radius:13px;
}}
.progress-table{{
  border-collapse:separate;
  border-spacing:0;
  min-width:max-content;
  width:100%;
  background:#0b121b;
}}
.progress-table th,
.progress-table td{{
  min-width:66px;
  height:48px;
  padding:5px;
  text-align:center;
  border-right:1px solid #202b39;
  border-bottom:1px solid #202b39;
}}
.progress-table thead th{{
  position:sticky;
  top:0;
  z-index:3;
  background:#111b28;
}}
.progress-corner,
.progress-user-name{{
  position:sticky;
  left:0;
  z-index:4;
  min-width:110px !important;
  max-width:110px;
  background:#111b28 !important;
}}
.progress-user-name{{
  text-align:left !important;
  font-size:.72rem;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}}
.progress-scenario-head{{
  max-width:90px;
  font-size:.65rem;
  line-height:1.25;
  cursor:pointer;
  white-space:normal;
}}
.progress-cell{{
  width:38px;
  height:38px;
  border:0;
  border-radius:50%;
  background:transparent;
  color:#748195;
  font-size:1rem;
  font-weight:1000;
  cursor:pointer;
}}
.progress-cell.passed{{
  color:#32d875;
  background:rgba(50,216,117,.10);
}}
.progress-cell.watched{{
  color:#4ea2ff;
  background:rgba(78,162,255,.10);
}}
.progress-hint{{
  margin:10px 0 0;
  text-align:center;
  color:#778599;
  font-size:.66rem;
}}
.scenario-detail-count{{
  text-align:center;
  color:#a7b3c3;
  margin-bottom:14px;
}}
.scenario-detail-section{{
  margin-top:12px;
  padding:12px;
  border:1px solid #263244;
  border-radius:13px;
  background:#0b121b;
}}
.scenario-detail-heading{{
  margin-bottom:8px;
  font-size:.75rem;
  font-weight:950;
}}
.scenario-detail-heading.passed{{color:#32d875}}
.scenario-detail-heading.watched{{color:#4ea2ff}}
.scenario-detail-names{{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
}}
.scenario-name-chip{{
  display:inline-block;
  padding:5px 8px;
  border-radius:8px;
  font-size:.76rem;
  font-weight:850;
}}
.scenario-name-chip.passed{{
  color:#62e595;
  background:rgba(50,216,117,.11);
}}
.scenario-name-chip.watched{{
  color:#79baff;
  background:rgba(78,162,255,.11);
}}


.admin-gear-wrap{{display:flex;justify-content:center;align-items:center;gap:10px;margin:26px 0 4px}}
.admin-gear{{width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;text-decoration:none;background:#111925;border:1px solid #2b3748;color:#8e9bad;font-size:18px}}
.admin-gear svg{{width:19px;height:19px;display:block;fill:none;stroke:currentColor;stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round}}
.admin-card{{max-width:620px;margin:0 auto}} .admin-title{{text-align:center}}
.admin-sub{{text-align:center;color:#7f8b9d;font-size:.75rem}}
.admin-id-form{{display:flex;gap:8px;margin:14px 0}} .admin-id-form input{{flex:1;min-width:0}}
.admin-member-list{{display:flex;flex-direction:column;gap:8px;margin-top:14px}}
.admin-member-row{{display:flex;align-items:center;gap:10px;padding:10px;border-radius:12px;background:#111925;border:1px solid #263244}}
.admin-avatar{{width:38px;height:38px;border-radius:50%;object-fit:cover;background:#263244}}
.admin-member-main{{min-width:0;flex:1}} .admin-display{{font-weight:900}}
.admin-username{{font-size:.7rem;color:#7f8b9d}} .admin-confirm{{margin-top:16px;padding:14px;border-radius:14px;background:#101824;border:1px solid #314056;text-align:center}}
.admin-confirm .admin-avatar{{width:58px;height:58px}} .admin-confirm-name{{font-size:1.05rem;font-weight:950}}
.admin-confirm-user{{font-size:.75rem;color:#8e9bad}} .admin-confirm-actions{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}}


/* v54 home */
.home-mini-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.home-mini-card{{min-height:64px;padding:11px 13px}}
.home-mini-card .menu-title{{font-size:.88rem}}
.home-mini-card .menu-sub{{font-size:.62rem;margin-top:2px}}
.home-mini-card .chev{{font-size:1.1rem}}

/* v54 progress: vertical scenario -> vertical member */
.progress-scenario-list{{display:flex;flex-direction:column;gap:8px;max-height:55vh;overflow-y:auto;padding:1px}}
.progress-scenario-row{{width:100%;border:1px solid #263244;background:#0d1621;border-radius:12px;padding:12px 13px;color:#e8edf5;display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;text-align:left}}
.progress-scenario-name{{font-weight:900;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.progress-scenario-counts{{display:flex;align-items:center;gap:8px;flex-shrink:0;font-size:.68rem}}
.progress-scenario-counts .passed{{color:#32d875}}
.progress-scenario-counts .watched{{color:#4ea2ff}}
.progress-person-card{{width:min(460px,100%);max-height:84vh}}
.progress-person-legend{{display:flex;justify-content:center;flex-wrap:wrap;gap:10px;color:#8b98aa;font-size:.68rem;margin-bottom:10px}}
.progress-person-legend .passed{{color:#32d875}}
.progress-person-legend .watched{{color:#4ea2ff}}
.progress-person-list{{display:flex;flex-direction:column;gap:6px;max-height:56vh;overflow-y:auto}}
.progress-person-row{{width:100%;display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:1px solid #263244;border-radius:11px;background:#0d1621;color:#e8edf5;cursor:pointer}}
.progress-person-name{{font-weight:850;text-align:left}}
.progress-person-state{{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:1000;color:#718096;background:#111925}}
.progress-person-state.passed{{color:#32d875;background:rgba(50,216,117,.11)}}
.progress-person-state.watched{{color:#4ea2ff;background:rgba(78,162,255,.11)}}

/* Old matrix no longer used */
.progress-table-scroll{{display:none}}


/* v55 */
.progress-person-legend{{align-items:center}}
.progress-person-legend span{{display:inline-flex;align-items:center;justify-content:center;min-height:22px;line-height:1}}
.progress-save-btn{{width:100%;margin-top:14px}}
.progress-cancel-btn{{width:100%;margin-top:8px}}
.progress-person-state.unpassed{{color:#718096}}
.admin-back-btn{{display:inline-flex;width:auto;margin-bottom:12px;padding:7px 11px;font-size:.72rem}}


/* v56 final UI polish */
.home-mini-card{{
  justify-content:center;
  text-align:center;
}}
.home-mini-card > div:first-child{{
  width:100%;
  text-align:center;
}}
.home-mini-card .menu-title,
.home-mini-card .menu-sub{{
  text-align:center;
}}
.home-mini-card .chev{{
  position:absolute;
  right:12px;
}}
.home-mini-card{{
  position:relative;
}}

#progressScenarioTitle{{
  text-align:center;
  width:100%;
}}
.progress-person-legend .passed,
.progress-person-legend .watched{{
  font-size:1.02rem;
  font-weight:1000;
}}


/* v57 final progress alignment */
.progress-scenario-row{{
  position:relative;
  justify-content:center;
  text-align:center;
  padding-left:92px;
  padding-right:92px;
}}
.progress-scenario-name{{
  width:100%;
  text-align:center;
}}
.progress-scenario-counts{{
  position:absolute;
  right:13px;
  top:50%;
  transform:translateY(-50%);
}}
.progress-legend{{
  align-items:center;
}}
.progress-legend > span{{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-height:24px;
  line-height:1;
}}
@media(max-width:620px){{
  .progress-scenario-row{{
    padding-left:72px;
    padding-right:72px;
  }}
  .progress-scenario-counts{{
    right:10px;
    gap:5px;
    font-size:.62rem;
  }}
}}



/* v62: scenario names stay on one line, left aligned, and truncate with ellipsis */
.progress-scenario-row{{
  display:grid;
  grid-template-columns:minmax(0,1fr) auto;
  align-items:center;
  column-gap:10px;
  padding:11px 13px;
  text-align:left;
}}
.progress-scenario-name{{
  display:block;
  width:100%;
  min-width:0;
  text-align:left;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  line-height:1.28;
}}
.progress-scenario-counts{{
  position:static;
  transform:none;
  align-self:center;
  white-space:nowrap;
}}
@media(max-width:620px){{
  .progress-scenario-row{{
    grid-template-columns:minmax(0,1fr) auto;
    padding:11px 10px;
    column-gap:7px;
  }}
  .progress-scenario-counts{{
    position:static;
    transform:none;
    right:auto;
    top:auto;
    gap:4px;
    font-size:.60rem;
  }}
}}

</style>
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

def in_quiet_hours(dt: datetime | None = None) -> bool:
    """
    21:00〜翌09:00はDiscord通知をサイレント送信する。
    """
    dt = dt or now_jst()
    return dt.hour >= 21 or dt.hour < 9


SIMPLE_SCHEDULE_ALLOWED_CATEGORIES = {
    "募集掲示板",
    "イベント",
    "卓一覧",
    "未定卓",
    "つぶ活",
}

YUZUKY_SPECIAL_USER_ID = "804350794371039272"

def require_yuzuky_admin(request: Request) -> str:
    uid = require_login(request)
    if uid != YUZUKY_SPECIAL_USER_ID:
        raise HTTPException(status_code=403, detail="この管理画面はyuzukyのみ利用できます")
    return uid



async def visible_sendable_channels_for_user(uid: str) -> list[discord.TextChannel]:
    """
    簡単日程調整で選択できるDiscordチャンネルを返す。

    条件:
    - 指定カテゴリ（募集掲示板 / イベント / 卓一覧 / 未定卓 / つぶ活）配下
    - ログイン中ユーザー本人が View Channel を持つ
      （ロール/メンバー上書きで参加しているプライベートチャンネルも含む）
    - つぶ卓Botが View Channel / Send Messages を持つ

    ユーザー本人が見られないプライベートチャンネルは一覧へ出さない。
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return []
    member = await fetch_member(guild, uid)
    me = guild.me
    if not member or not me:
        return []

    out: list[discord.TextChannel] = []
    for ch in guild.text_channels:
        try:
            category_name = (ch.category.name or "").strip() if ch.category else ""
            if category_name not in SIMPLE_SCHEDULE_ALLOWED_CATEGORIES:
                continue

            user_perms = ch.permissions_for(member)
            bot_perms = ch.permissions_for(me)
            if not user_perms.view_channel:
                continue
            if not (bot_perms.view_channel and bot_perms.send_messages):
                continue

            out.append(ch)
        except Exception:
            pass

    out.sort(key=lambda x: ((x.category.position if x.category else 9999), x.position, x.name))
    return out


def is_simple_schedule(r) -> bool:
    try:
        return bool(int(r["simple_schedule"] or 0))
    except Exception:
        return False


def submitted_user_ids(rid: int) -> set[str]:
    with db() as c:
        rows=c.execute("SELECT discord_id FROM answer_submissions WHERE recruitment_id=?",(rid,)).fetchall()
    return {str(x["discord_id"]) for x in rows}


def simple_schedule_all_answered(rid: int) -> bool:
    with db() as c:
        rows=c.execute("SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='participant' AND active=1",(rid,)).fetchall()
    targets={str(x["discord_id"]) for x in rows}
    return bool(targets) and targets.issubset(submitted_user_ids(rid))


async def notify_availability_if_needed(rid: int):
    """
    最小募集人数を初めて満たす開催候補日ができた瞬間だけ、
    日程調整チャンネルへ通知する。
    """
    r = get_recruitment(rid)
    if not r:
        return

    # 既に通知済みなら何もしない
    try:
        already = int(r["availability_notified"] or 0)
    except Exception:
        already = 0
    if already:
        return
    if is_simple_schedule(r) and not r["target_players"]:
        return

    per_day_time, _ = recruitment_schedule_slots(rid)
    source_candidates = candidate_slot_rows(rid) if per_day_time else candidate_rows(rid)
    candidates = [
        x for x in source_candidates
        if len(x["yes"]) >= int(r["min_players"])
    ]
    if not candidates:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild or not r["waiting_channel_id"]:
        return

    ch = guild.get_channel(int(r["waiting_channel_id"]))
    if not ch:
        try:
            ch = await guild.fetch_channel(int(r["waiting_channel_id"]))
        except Exception:
            ch = None
    if not ch:
        return

    lines = [
        (f'・{x["date"]} {x.get("time", "")}〜：○ {len(x["yes"])}人' if per_day_time else f'・{x["date"]}：○ {len(x["yes"])}人')
        for x in candidates
    ]

    message = (
        "🎉 **開催できるようになりました！**\n\n"
        "必要人数を満たした日程があります。\n"
        + "\n".join(lines)
        + f"\n\nGMは開催日を決定できます。\n{BASE_URL}/r/{rid}/decide"
    )

    await ch.send(
        message,
        silent=in_quiet_hours(),
    )

    with db() as c:
        c.execute(
            "UPDATE recruitments SET availability_notified=1 WHERE id=?",
            (rid,),
        )

    print(
        f"[AVAILABILITY] notified rid={rid} quiet={in_quiet_hours()}",
        flush=True,
    )




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
    if int(r["schedule_pending"] or 0):
        await ch.send(
            f'🎲 **「{r["scenario_name"]}」募集開始**\n\n'
            '日程調整は後日行います！\n\n'
            '参加リアクションを押した方はこちらのチャンネルへ追加されます。\n'
            'GMからの日程調整開始の案内をお待ちください。\n\n'
            f'GMの方へ：日程調整は以下のリンクよりお願いします！\n{BASE_URL}/r/{rid}/schedule/start',
            silent=in_quiet_hours(),
        )
    else:
        deadline = datetime.fromisoformat(
            r["deadline"]
        ).astimezone(JST)

        await ch.send(
            f'🎲 **「{r["scenario_name"]}」日程調整**\n\n'
            f'回答期限：**{deadline.strftime("%Y/%m/%d 21:00")}**\n'
            '参加リアクションを押した方は、こちらから回答してください。\n'
            f'{BASE_URL}/r/{rid}',
            silent=in_quiet_hours(),
        )

    return ch


async def post_recruitment(rid: int):
    """
    初回募集専用。
    TRPGならTRPG募集掲示板、マダミスならマダミス募集掲示板へ投稿する。
    """
    r = get_recruitment(rid)
    if not r:
        raise RuntimeError(f"募集が見つかりません rid={rid}")

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        raise RuntimeError("Guildが見つかりません")

    channel_id = (
        TRPG_CHANNEL_ID
        if r["game_type"] == "TRPG"
        else MADMIS_CHANNEL_ID
    )

    channel = guild.get_channel(channel_id)
    if not channel:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            channel = None

    if not channel:
        raise RuntimeError(
            f"募集掲示板チャンネルが見つかりません channel_id={channel_id}"
        )

    player_text = (
        str(r["min_players"])
        if r["min_players"] == r["max_players"]
        else f'{r["min_players"]}〜{r["max_players"]}'
    )

    gm_name = user_display(str(r["gm_discord_id"]))

    if int(r["schedule_pending"] or 0):
        closing = (
            '参加希望の方は「参加」リアクションを押してください！\n'
            '日程調整は後日行います！'
        )
    else:
        closing = (
            '参加希望の方は「参加」リアクションを押して'
            '日程調整への回答をお願いします！'
        )

    header = (
        f'## 『{r["scenario_name"]}』\n'
        f'GM：**{gm_name}**\n'
        f'募集人数：**{player_text}人**\n'
        f'プレイ時間：**{r["play_time"]}**\n\n'
        f'{r["description"]}\n\n'
        f'{closing}'
    )

    chunks = split_text(header)

    image_paths = [
        p for p in get_recruitment_images(rid)
        if Path(p).exists()
    ][:10]
    files = [discord.File(p) for p in image_paths]

    # 開発者テスト用：
    # シナリオ名が完全一致で「テスト」の場合は、時間帯に関係なく
    # 募集掲示板への投稿をすべてミュートメッセージにする。
    recruitment_silent = in_quiet_hours() or r["scenario_name"].strip() == "テスト"

    first = await channel.send(
        chunks[0],
        files=files if files else None,
        silent=recruitment_silent,
    )

    for chunk in chunks[1:]:
        await channel.send(
            chunk,
            silent=recruitment_silent,
        )

    join_emoji = emoji_by_id(JOIN_EMOJI_ID)
    watch_emoji = emoji_by_id(WATCH_EMOJI_ID)

    if not join_emoji or not watch_emoji:
        raise RuntimeError(
            "参加/観戦用カスタム絵文字が見つかりません。"
            "絵文字IDを確認してください。"
        )

    await first.add_reaction(join_emoji)
    await first.add_reaction(watch_emoji)

    with db() as c:
        c.execute(
            """UPDATE recruitments
               SET recruitment_message_id=?,
                   recruitment_channel_id=?
               WHERE id=?""",
            (str(first.id), str(channel.id), rid),
        )

    # Discordへの投稿が正常完了したら、Railway側の画像コピーは不要。
    # DB参照と実ファイルをすぐ解放してVolume消費を抑える。
    deleted_images = cleanup_posted_recruitment_images(rid)

    print(
        f"[RECRUITMENT] initial post sent rid={rid} channel={channel.id} images_cleaned={deleted_images}",
        flush=True,
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
            # 元の募集投稿に付いたリアクションは、元募集だけでなく
            # 現在の最新再日程調整にも同期する。
            # これにより、再日程調整後に初めて参加リアクションを押した人も
            # 既存の再日程調整リンクからそのまま回答できる。
            target_rids = [int(r["id"])]
            latest_rid = latest_reschedule_id(int(r["id"]))
            if latest_rid not in target_rids:
                target_rids.append(latest_rid)

            with db() as c:
                for target_rid in target_rids:
                    c.execute(
                        """INSERT INTO members(recruitment_id,discord_id,member_type,active,joined_at)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(recruitment_id,discord_id,member_type)
                           DO UPDATE SET active=1,joined_at=excluded.joined_at""",
                        (target_rid, uid, kind, 1, iso_now()),
                    )

            print(
                f"[REACTION:{action}] DB member activated "
                f"rids={target_rids} user={uid}",
                flush=True,
            )

            # Discordチャンネル権限は元募集側のwaiting_channelに対してのみ付与。
            # 再日程調整は基本的に同じwaiting_channelを引き継ぐため、
            # 重複して権限操作しない。
            await set_waiting_access(r["id"], uid, True)
            print(
                f"[REACTION:{action}] waiting channel permission granted user={uid}",
                flush=True,
            )

            if r["waiting_channel_id"]:
                ch = guild.get_channel(int(r["waiting_channel_id"]))
                if ch:
                    label = "参加" if kind == "participant" else "観戦希望"
                    emoji = "🎉" if kind == "participant" else "👀"
                    await ch.send(
                        f'<@{uid}> が{label}を押しました{emoji}',
                        silent=True,
                    )
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
            target_rids = [int(r["id"])]
            latest_rid = latest_reschedule_id(int(r["id"]))
            if latest_rid not in target_rids:
                target_rids.append(latest_rid)

            with db() as c:
                for target_rid in target_rids:
                    c.execute(
                        "UPDATE members SET active=0 WHERE recruitment_id=? AND discord_id=? AND member_type=?",
                        (target_rid, uid, kind),
                    )
                    if kind == "participant":
                        c.execute(
                            "DELETE FROM answers WHERE recruitment_id=? AND discord_id=?",
                            (target_rid, uid),
                        )
                        c.execute(
                            "DELETE FROM answer_submissions WHERE recruitment_id=? AND discord_id=?",
                            (target_rid, uid),
                        )

            # リアクション解除時はDiscordチャンネル権限を変更しない。
            # サイト側の参加/観戦状態だけ解除する。
            print(
                f"[REACTION:{action}] deactivated "
                f"rids={target_rids} user={uid}; Discord channel permissions unchanged",
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


async def predeadline_unanswered_check():
    """v109: 回答期限の前日20:00に、未回答操作の参加者だけメンション通知する。

    通常の日程調整/募集と、成立後の再日程調整の両方が対象。
    全て「-」で保存済みの人は回答済みとして扱う。
    """
    target_day = now_jst().date() + timedelta(days=1)
    guild=bot.get_guild(GUILD_ID)

    # 通常の募集・ホーム日程調整・再募集
    with db() as c:
        rows=c.execute(
            """SELECT * FROM recruitments
               WHERE COALESCE(predeadline_notified,0)=0
                 AND status IN ('RECRUITING','WAITING_GM_DECISION','FAILED')"""
        ).fetchall()
    for r in rows:
        try: deadline=datetime.fromisoformat(r['deadline']).astimezone(JST)
        except Exception: continue
        if deadline.date()!=target_day: continue
        rid=int(r['id'])
        with db() as c:
            targets={str(x['discord_id']) for x in c.execute(
                "SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='participant' AND active=1",(rid,)
            ).fetchall()}
            submitted={str(x['discord_id']) for x in c.execute(
                "SELECT discord_id FROM answer_submissions WHERE recruitment_id=?",(rid,)
            ).fetchall()}
        targets.discard(str(r['gm_discord_id']))
        unanswered=sorted(targets-submitted)
        if not unanswered:
            with db() as c: c.execute("UPDATE recruitments SET predeadline_notified=1 WHERE id=?",(rid,))
            continue
        ch=None
        if guild and r['waiting_channel_id']:
            ch=guild.get_channel(int(r['waiting_channel_id']))
            if ch is None:
                try: ch=await guild.fetch_channel(int(r['waiting_channel_id']))
                except Exception: ch=None
        if ch is None: continue
        mentions=' '.join(f'<@{uid}>' for uid in unanswered)
        await ch.send('📅 **日程回答のお願い**\n'+mentions+'\n\n回答期限が明日です！まだ回答していない方は日程入力をお願いします。')
        with db() as c: c.execute("UPDATE recruitments SET predeadline_notified=1 WHERE id=?",(rid,))
        print(f"[PREDEADLINE] recruitment rid={rid} count={len(unanswered)}",flush=True)

    # 成立後のGM専用URLから開始した再日程調整
    with db() as c:
        reschedules=c.execute(
            """SELECT sr.*,s.channel_id,r.gm_discord_id
                 FROM session_reschedules sr
                 JOIN sessions s ON s.id=sr.session_id
                 JOIN recruitments r ON r.id=s.recruitment_id
                WHERE sr.status='OPEN' AND sr.deadline IS NOT NULL
                  AND COALESCE(sr.predeadline_notified,0)=0"""
        ).fetchall()
    for rs in reschedules:
        try:
            raw=str(rs['deadline'])
            deadline=datetime.fromisoformat(raw if 'T' in raw else raw+'T21:00:00').replace(tzinfo=JST) if '+' not in raw else datetime.fromisoformat(raw).astimezone(JST)
        except Exception:
            continue
        if deadline.date()!=target_day: continue
        rsid=int(rs['id']); session_id=int(rs['session_id'])
        with db() as c:
            targets={str(x['discord_id']) for x in c.execute("SELECT discord_id FROM session_members WHERE session_id=?",(session_id,)).fetchall()}
            submitted={str(x['discord_id']) for x in c.execute("SELECT discord_id FROM session_reschedule_submissions WHERE reschedule_id=?",(rsid,)).fetchall()}
        targets.discard(str(rs['gm_discord_id']))
        unanswered=sorted(targets-submitted)
        if not unanswered:
            with db() as c: c.execute("UPDATE session_reschedules SET predeadline_notified=1 WHERE id=?",(rsid,))
            continue
        ch=None
        if guild and rs['channel_id']:
            ch=guild.get_channel(int(rs['channel_id']))
            if ch is None:
                try: ch=await guild.fetch_channel(int(rs['channel_id']))
                except Exception: ch=None
        if ch is None: continue
        mentions=' '.join(f'<@{uid}>' for uid in unanswered)
        await ch.send('📅 **日程回答のお願い**\n'+mentions+'\n\n回答期限が明日です！まだ回答していない方は日程入力をお願いします。')
        with db() as c: c.execute("UPDATE session_reschedules SET predeadline_notified=1 WHERE id=?",(rsid,))
        print(f"[PREDEADLINE] session-reschedule id={rsid} count={len(unanswered)}",flush=True)


async def deadline_check():
    """
    募集期限日の20:00に、その日が期限の卓だけ1回通知する。
    20:00を逃した過去日の卓には後追い通知しない。
    """
    today = now_jst().date()

    with db() as c:
        rows = c.execute(
            """SELECT *
               FROM recruitments
               WHERE deadline_notified=0
                 AND status IN ('RECRUITING','WAITING_GM_DECISION','FAILED')"""
        ).fetchall()

    for r in rows:
        deadline = datetime.fromisoformat(r["deadline"]).astimezone(JST)

        # 当日だけ。昨日以前・明日以降は通知しない。
        if deadline.date() != today:
            continue

        candidates = [
            x for x in candidate_rows(r["id"])
            if len(x["yes"]) >= int(r["min_players"])
        ]

        guild = bot.get_guild(GUILD_ID)
        ch = (
            guild.get_channel(int(r["waiting_channel_id"]))
            if guild and r["waiting_channel_id"]
            else None
        )

        if not ch and guild and r["waiting_channel_id"]:
            try:
                ch = await guild.fetch_channel(int(r["waiting_channel_id"]))
            except Exception:
                ch = None

        if ch:
            if candidates:
                lines = [
                    f'・{x["date"]}：○ {len(x["yes"])}人'
                    for x in candidates
                ]
                await ch.send(
                    '⏰ **本日が募集期限です。**\n\n'
                    '現在、募集人数に到達している開催候補日があります。\n'
                    + "\n".join(lines)
                    + f'\n\nGMは以下から開催日を選択できます。\n{BASE_URL}/r/{r["id"]}/decide'
                )
            else:
                await ch.send(
                    '⏰ **本日が募集期限です。**\n\n'
                    '現時点では必要人数が集まっている日程がありません。\n'
                    f'必要に応じて、以下より再日程調整できます。\n'
                    f'{BASE_URL}/r/{r["id"]}/reschedule'
                )

        with db() as c:
            c.execute(
                "UPDATE recruitments SET deadline_notified=1 WHERE id=?",
                (r["id"],),
            )

        print(
            f"[DEADLINE] notified once for deadline date rid={r['id']}",
            flush=True,
        )


reminder_tasks: dict[int, asyncio.Task] = {}
_reminders_restored = False


async def session_reminder_task(session_id: int):
    """v106: 1卓につき1task。未通知slotのうち次の1件だけ待ち、送信後に次へ進む。"""
    try:
        while True:
            slots=get_session_slots(session_id)
            pending=[x for x in slots if not int(x.get('reminder_sent') or 0)]
            if not pending:
                return
            now=now_jst()
            candidates=[]
            for sl in pending:
                try:
                    start=datetime.fromisoformat(f"{sl['event_date']}T{sl['start_time']}:00").replace(tzinfo=JST)
                except Exception:
                    with db() as c:
                        c.execute('UPDATE session_slots SET reminder_sent=1 WHERE id=?',(int(sl['id']),))
                    continue
                candidates.append((start,sl))
            if not candidates:
                return
            candidates.sort(key=lambda x:x[0])
            start,sl=candidates[0]
            remind_at=start-timedelta(hours=1)
            if now < remind_at:
                await asyncio.sleep((remind_at-now).total_seconds())
                continue
            now=now_jst()
            if now>=start:
                with db() as c:
                    c.execute('UPDATE session_slots SET reminder_sent=1 WHERE id=?',(int(sl['id']),))
                continue
            with db() as c:
                sess=c.execute('SELECT * FROM sessions WHERE id=?',(int(session_id),)).fetchone()
                r=c.execute('SELECT * FROM recruitments WHERE id=?',(int(sess['recruitment_id']),)).fetchone() if sess else None
            guild=bot.get_guild(GUILD_ID)
            ch=guild.get_channel(int(sess['channel_id'])) if guild and sess and sess['channel_id'] else None
            if ch and r:
                await ch.send(f'🔔 **開始1時間前リマインド**\n\n『{r["scenario_name"]}』\n本日{sl["start_time"]}開始です！')
            with db() as c:
                c.execute('UPDATE session_slots SET reminder_sent=1 WHERE id=?',(int(sl['id']),))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_error(f'session_reminder_task session_id={session_id}',e)
    finally:
        reminder_tasks.pop(session_id,None)


def schedule_session_reminder(session_id: int):
    old=reminder_tasks.get(session_id)
    if old and not old.done(): old.cancel()
    reminder_tasks[session_id]=asyncio.create_task(session_reminder_task(session_id))


async def restore_reminder_tasks():
    with db() as c:
        rows=c.execute('SELECT DISTINCT session_id FROM session_slots WHERE reminder_sent=0').fetchall()
    for row in rows:
        schedule_session_reminder(int(row['session_id']))


async def post_achievement_notifications(new_rows):
    if not new_rows:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(TSUBUTTER_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(TSUBUTTER_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    grouped = {}
    with db() as c:
        for r in new_rows:
            title = str(r.get("title_name") or "")
            rarity = RARITY_LABELS.get(str(r.get("rarity") or ""), "🏷️")
            name_row = c.execute(
                "SELECT COALESCE(display_name,username,discord_id) AS n FROM registered_members WHERE discord_id=?",
                (str(r.get("discord_id") or ""),),
            ).fetchone()
            name = str(name_row["n"]) if name_row else str(r.get("discord_id") or "")
            grouped.setdefault((rarity,title), []).append(name)
    lines=["🏆 **称号獲得！**"]
    for (rarity,title), names in grouped.items():
        # 同一人・同一称号の動的解除が同時に複数あっても名前は重複表示しない
        names=list(dict.fromkeys(names))
        lines.append(f"\n{rarity} **{title}** 取得🎉\n" + "、".join(names))
    await channel.send("\n".join(lines), silent=True)


async def run_achievement_check(run_date=None, notify=True):
    d = str(run_date or now_jst().date().isoformat())
    if achievement_run_done(d):
        return []
    now_text = iso_now()
    # v69: 20時は「当日の未処理卓」だけを差分加算。過去履歴の全再集計はしない。
    apply_profile_daily_delta(d, now_text)
    new_rows = evaluate_achievements(d, now_text)
    mark_achievement_run(d, now_text)
    if notify:
        await post_achievement_notifications(new_rows)
    return new_rows


async def bootstrap_achievements():
    """v65初回だけ過去実績を静かに反映。以後は20時判定で新規解除を通知。"""
    now = now_jst()
    today = now.date()
    if not achievement_bootstrapped():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        stamp = iso_now()
        # 初回導入時だけ現在の履歴からキャッシュを作成。以後の通常更新は毎日20時のみ。
        refresh_profile_caches(as_of.isoformat(), stamp)
        evaluate_achievements(as_of.isoformat(), stamp)
        mark_achievement_bootstrapped()
        if now.time() >= time(20,0):
            mark_achievement_run(today.isoformat(), stamp)
        return
    # v75初回だけ、現在のカレンダーを唯一の正としてプロフィール・称号派生データを完全再構築。
    # 称号は一度全削除して再判定するが、過去分の獲得通知は送らない。
    # 以後は20時の当日差分更新だけに戻る。
    if not full_derived_rebuild_v75_done():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        stamp = iso_now()
        full_derived_rebuild_v75(as_of.isoformat(), stamp)
        # 20時以降に再構築した場合、当日分は既に再構築へ含まれているので処理済みにする。
        if now.time() >= time(20,0):
            mark_achievement_run(today.isoformat(), stamp)
        return

    # v83初回だけ、未来卓を除外して現在時点の確定卓だけから派生データを再同期。
    # 20時前なら昨日まで、20時以降なら今日までを含める。未来卓は処理済みにしないので、
    # 開催日20時に通常の日次差分で一度だけ加算される。
    if not cutoff_resync_v83_done():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        stamp = iso_now()
        cutoff_resync_v83(as_of.isoformat(), stamp)
        if now.time() >= time(20,0):
            mark_achievement_run(today.isoformat(), stamp)
        return

    # v90: テスト中の変更で派生集計が乱れたため、一度だけカレンダーを正として再取得。
    # 20時以降なら今日まで、20時前なら昨日まで。未来卓は含めない。
    if not calendar_resync_v90_done():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        stamp = iso_now()
        calendar_resync_v90(as_of.isoformat(), stamp)
        # v92: 今回だけ、テスト中に減った2026年度のマダミス1卓分を4人へ戻す。
        # achievement_meta の専用マーカーで二重加算を防ぐ。
        if not temporary_v92_madamis_year_fix_done():
            apply_temporary_v92_madamis_year_fix(stamp)
        if now.time() >= time(20,0):
            mark_achievement_run(today.isoformat(), stamp)
        return

    # v74初回だけ、プロフィール集計キャッシュをカレンダー履歴から正しい値で作り直す。
    # TRPG / マダミスは GM + PL の両方を含む値で再構築し、その後は20時の当日差分だけ更新する。
    if not profile_cache_v74_resynced():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        stamp = iso_now()
        refresh_profile_caches(as_of.isoformat(), stamp)
        mark_profile_cache_v74_resynced(stamp)
    # 旧版からの移行保険。通常は上のv74再同期で同時に初期化済みになる。
    elif not profile_cache_initialized() or not profile_delta_initialized():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        refresh_profile_caches(as_of.isoformat(), iso_now())
    # v73初回だけシナリオ別GM回数と5回称号をカレンダー履歴から再同期。以後は20時に当日分だけ+1。
    if not scenario_gm_counter_initialized():
        as_of = today if now.time() >= time(20,0) else today - timedelta(days=1)
        ensure_scenario_gm_counter_initialized(as_of.isoformat(), iso_now())
        # 既に5回以上回しているシナリオも、移行時は通知を飛ばさず解除だけ反映。
        evaluate_achievements(as_of.isoformat(), iso_now())
    # 20時にBotが落ちていた場合は、その日の起動時に1回だけ追いつく。
    if now.time() >= time(20,0) and not achievement_run_done(today.isoformat()):
        await run_achievement_check(today.isoformat(), notify=True)


@tasks.loop(time=time(hour=20, minute=0, tzinfo=JST))
async def deadline_scheduler():
    try:
        await predeadline_unanswered_check()
    except Exception as e:
        log_error("predeadline_unanswered_scheduler", e)
    try:
        await deadline_check()
    except Exception as e:
        log_error("deadline_scheduler", e)

    # v69: 当日20時に当日分だけ差分反映して称号を判定
    try:
        await run_achievement_check(now_jst().date().isoformat(), notify=True)
    except Exception as e:
        log_error("achievement_scheduler", e)

    # DB肥大化防止：3か月を超えた卓を1日1回だけ削除
    try:
        cleanup_old_data()
    except Exception as e:
        log_error("old_data_cleanup", e)


@deadline_scheduler.before_loop
async def before_deadline_scheduler():
    await bot.wait_until_ready()



async def sync_registered_member_profiles():
    guild=bot.get_guild(GUILD_ID)
    if guild is None: return
    for row in registered_members():
        uid=str(row["discord_id"])
        if not uid.isdigit(): continue
        member=guild.get_member(int(uid))
        if member is None:
            try: member=await guild.fetch_member(int(uid))
            except (discord.NotFound,discord.Forbidden,discord.HTTPException): continue
        refresh_registered_member_profile(uid,member.name,member.display_name,str(member.display_avatar.url),iso_now())

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID: return
    refresh_registered_member_profile(str(after.id),after.name,after.display_name,str(after.display_avatar.url),iso_now())

@bot.event
async def on_ready():
    global _reminders_restored
    print(f"Discord ready: {bot.user}")
    try:
        await sync_registered_member_profiles()
    except Exception as e:
        log_error("registered_member_sync", e)
    try:
        await bootstrap_achievements()
    except Exception as e:
        log_error("achievement_bootstrap", e)
    # v109: 20時以降にBotが再起動した場合も、同日の「期限前日」未回答通知だけ追いつく。
    try:
        if now_jst().time() >= time(20,0):
            await predeadline_unanswered_check()
    except Exception as e:
        log_error("predeadline_unanswered_catchup", e)
    # v94: bootstrap_achievements() 内の移行処理が return しても必ず最後に到達する臨時補正。
    # v93が既に成功済みなら何もしない。未適用の場合だけ今回の1卓分を年別キャッシュへ戻す。
    try:
        if not temporary_v94_madamis_year_fix_done():
            apply_temporary_v94_madamis_year_fix(iso_now())
    except Exception as e:
        log_error("temporary_v94_madamis_year_fix", e)
    # v95: 過去版の補正マーカーは無視し、今回の誤減算1卓分を新しい専用マーカーで一度だけ戻す。
    try:
        if not temporary_v95_madamis_year_fix_done():
            result = apply_temporary_v95_madamis_year_fix(iso_now())
            print(f"[V95 FIX] applied annual madamis +1: {result}", flush=True)
    except Exception as e:
        log_error("temporary_v95_madamis_year_fix", e)
    # v96: 今回だけの誤減算補正。対象者ごとの専用マーカーで一度だけ実行。
    try:
        if not temporary_v96_madamis_year_fix_done():
            result = apply_temporary_v96_madamis_year_fix(iso_now())
            print(f"[V96 FIX] annual madamis +1: {result}", flush=True)
    except Exception as e:
        log_error("temporary_v96_madamis_year_fix", e)
    try:
        deleted_images = cleanup_posted_recruitment_images()
        if deleted_images:
            print(f"[CLEANUP] removed {deleted_images} posted recruitment images", flush=True)
    except Exception as e:
        log_error("posted_recruitment_image_cleanup", e)
    if not _reminders_restored:
        _reminders_restored = True
        await restore_reminder_tasks()


# ------------------------ FastAPI -------------------------

app = FastAPI(title="つぶ卓")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=BASE_URL.startswith("https://"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")
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
    uid=request.session.get("user_id")
    schedule_href="/schedule/new" if uid else "/login?next=/schedule/new"
    new_href="/new" if uid else "/login?next=/new"
    join_href="/join" if uid else "/login?next=/join"
    return page("ホーム",f"""
      <section class='hero'><div class='hero-kicker'>Discord × 日程調整</div><h1>○と△だけで、<br>もっと手軽に。</h1></section>
      <div class='menu-stack'>
        <a class='menu-card primary' href='{new_href}'><div class='menu-icon asset'><img src='{HOME_CREATE_IMAGE}' alt='卓を立てる'></div><div><div class='menu-title'>卓を立てる</div><div class='menu-sub'>投稿・チャンネル作成・日程調整</div></div><div class='chev'>›</div></a>
        <a class='menu-card' href='{join_href}'><div class='menu-icon asset'><img src='{HOME_JOIN_IMAGE}' alt='卓に参加する'></div><div><div class='menu-title'>卓に参加する</div><div class='menu-sub'>募集一覧と回答状況</div></div><div class='chev'>›</div></a>
        <div class='home-mini-grid'>
          <a class='menu-card home-mini-card calendar' href='/calendar'><div><div class='menu-title'>カレンダー</div><div class='menu-sub'>成立卓の予定</div></div><div class='chev'>›</div></a>
          <a class='menu-card home-mini-card schedule' href='{schedule_href}'><div><div class='menu-title'>日程調整</div><div class='menu-sub'>日程調整のみ作成</div></div><div class='chev'>›</div></a>
        </div>
      </div>
      {("<div class='admin-gear-wrap'>"
         "<a class='admin-gear' href='/admin' aria-label='管理画面' title='管理画面'>⚙</a>"
         "<a class='admin-gear' href='/admin/database-backup' aria-label='データベースをバックアップ' title='DBバックアップ'>"
         "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 3v11'/><path d='m7.5 10 4.5 4.5 4.5-4.5'/><path d='M5 15.5V20h14v-4.5'/></svg>"
         "</a></div>" if str(uid or "") == YUZUKY_SPECIAL_USER_ID else "")}
      """,request)



def _remove_backup_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@app.get("/admin/database-backup")
async def admin_database_backup(request: Request):
    # ボタンを隠すだけではなく、URLへ直接アクセスされた場合もyuzuky本人だけ許可する。
    require_yuzuky_admin(request)

    source_path = Path(DATABASE_PATH)
    if not source_path.exists():
        raise HTTPException(status_code=500, detail="データベースファイルが見つかりません。")

    # 稼働中DBファイルを直接配信せず、SQLite公式backup APIで一貫したスナップショットを作る。
    fd, tmp_path = tempfile.mkstemp(prefix="tsubutaku_backup_", suffix=".db")
    os.close(fd)
    try:
        source = sqlite3.connect(str(source_path), timeout=30)
        destination = sqlite3.connect(tmp_path, timeout=30)
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
            source.close()

        stamp = now_jst().strftime("%Y-%m-%d_%H%M")
        filename = f"tsubutaku_backup_{stamp}.db"
        return FileResponse(
            tmp_path,
            media_type="application/vnd.sqlite3",
            filename=filename,
            background=BackgroundTask(_remove_backup_file, tmp_path),
            headers={"Cache-Control": "no-store"},
        )
    except Exception:
        _remove_backup_file(tmp_path)
        raise


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_yuzuky_admin(request)
    rows=registered_members()
    member_rows="".join(
        "<div class='admin-member-row'><img class='admin-avatar' src='"+esc(r["avatar_url"] or "")+"' alt=''>"
        "<div class='admin-member-main'><div class='admin-display'>"+esc(r["display_name"])+"</div>"
        "<div class='admin-username'>@"+esc(r["username"])+" ・ ID "+esc(r["discord_id"])+"</div></div></div>"
        for r in rows
    ) or "<div class='muted small'>登録済みメンバーはいません。</div>"
    body="<div class='card admin-card'><a class='btn alt admin-back-btn' href='/'>← ホームに戻る</a><h2 class='admin-title'>⚙ ネオ・ツブタク管理</h2><p class='admin-sub'>yuzuky専用管理画面</p>"          "<h3>Discordユーザーを登録</h3><form class='admin-id-form' method='post' action='/admin/member/lookup'>"+csrf_field(request)+          "<input type='text' inputmode='numeric' name='discord_id' placeholder='DiscordユーザーID' required><button class='btn green' type='submit'>確認</button></form>"          "<div class='muted small'>BotがこのDiscordサーバーの実在メンバーか確認してから登録します。</div>"          "<h3 style='margin-top:24px'>登録済みメンバー</h3><div class='admin-member-list'>"+member_rows+"</div></div>"
    return page("管理",body,request)

@app.post("/admin/member/lookup", response_class=HTMLResponse)
async def admin_member_lookup(request: Request, discord_id: str=Form(...)):
    require_yuzuky_admin(request); await require_csrf(request)
    uid=str(discord_id or "").strip()
    if not uid.isdigit() or len(uid)>30: raise HTTPException(400,"DiscordユーザーIDの形式が正しくありません")
    guild=bot.get_guild(GUILD_ID)
    if guild is None: raise HTTPException(503,"Discordサーバー情報を取得できません")
    member=guild.get_member(int(uid))
    if member is None:
        try: member=await guild.fetch_member(int(uid))
        except discord.NotFound: raise HTTPException(404,"このDiscordサーバーのメンバーではありません")
        except discord.Forbidden: raise HTTPException(503,"Botがメンバー情報を取得できません")
        except discord.HTTPException: raise HTTPException(503,"Discordからメンバー情報を取得できません")
    exists=registered_member(uid)
    msg="このユーザーはすでに登録済みです。最新情報へ更新しますか？" if exists else "この人を追加しますか？"
    button="最新情報に更新" if exists else "追加する"
    body="<div class='card admin-card'><h2 class='admin-title'>メンバー確認</h2><div class='admin-confirm'>"          "<img class='admin-avatar' src='"+esc(str(member.display_avatar.url))+"' alt=''><div class='admin-confirm-name'>"+esc(member.display_name)+"</div>"          "<div class='admin-confirm-user'>Discord名："+esc(member.name)+"</div><div class='admin-confirm-user'>ID："+esc(uid)+"</div>"          "<p><b>"+esc(msg)+"</b></p><form method='post' action='/admin/member/add'>"+csrf_field(request)+          "<input type='hidden' name='discord_id' value='"+esc(uid)+"'><div class='admin-confirm-actions'><a class='btn alt' href='/admin'>キャンセル</a>"          "<button class='btn green' type='submit'>"+esc(button)+"</button></div></form></div></div>"
    return page("メンバー確認",body,request)

@app.post("/admin/member/add")
async def admin_member_add(request: Request, discord_id: str=Form(...)):
    require_yuzuky_admin(request); await require_csrf(request)
    uid=str(discord_id or "").strip()
    if not uid.isdigit(): raise HTTPException(400,"DiscordユーザーIDの形式が正しくありません")
    guild=bot.get_guild(GUILD_ID)
    if guild is None: raise HTTPException(503,"Discordサーバー情報を取得できません")
    member=guild.get_member(int(uid))
    if member is None:
        try: member=await guild.fetch_member(int(uid))
        except discord.NotFound: raise HTTPException(404,"このDiscordサーバーのメンバーではありません")
        except discord.Forbidden: raise HTTPException(503,"Botがメンバー情報を取得できません")
        except discord.HTTPException: raise HTTPException(503,"Discordからメンバー情報を取得できません")
    upsert_registered_member(uid,member.name,member.display_name,str(member.display_avatar.url),iso_now())
    return RedirectResponse("/admin",status_code=303)

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, month: str = ""):
    today = now_jst().date()
    try:
        if month and re.fullmatch(r"\d{4}-\d{2}", month):
            year, mon = map(int, month.split("-"))
            current = date(year, mon, 1)
        else:
            current = date(today.year, today.month, 1)
    except ValueError:
        current = date(today.year, today.month, 1)

    prev_month = (current - timedelta(days=1)).replace(day=1)
    if current.month == 12:
        next_month = date(current.year + 1, 1, 1)
    else:
        next_month = date(current.year, current.month + 1, 1)

    entries = calendar_entries(current.isoformat(), next_month.isoformat())
    by_date: dict[str, list[tuple]] = {}
    for row, members in entries:
        by_date.setdefault(str(row["event_date"]), []).append((row, members))

    scenarios, known_users = calendar_manual_options()
    can_edit_calendar = bool(request.session.get("user_id"))

    # つぶぐみ年度は 6/1〜翌5/31
    activity_year = today.year if today.month >= 6 else today.year - 1
    activity_start = date(activity_year, 6, 1)
    activity_end = date(activity_year + 1, 6, 1)

    # つぶぐみ創立: 2024/6/1
    # 2026/6/1〜2027/5/31 は「3年目」
    activity_term_number = activity_year - 2023

    year_stats = calendar_stats(activity_start.isoformat(), activity_end.isoformat())
    year_stats["new_scenarios"] = new_scenario_count(activity_start.isoformat(), activity_end.isoformat())
    total_stats = calendar_stats()
    yearly_stats = []
    for term in range(1, activity_term_number + 1):
        y = 2023 + term
        ys = calendar_stats(date(y,6,1).isoformat(), date(y+1,6,1).isoformat())
        ys["term"] = term; ys["year"] = y
        yearly_stats.append(ys)

    progress_users, progress_scenarios, progress_statuses = scenario_progress_data()
    viewer_id = str(request.session.get("user_id") or "")
    profile_href = f"/profile/{viewer_id}" if viewer_id else "/login?next=/calendar"

    def rank_html(rows):
        if not rows:
            return "<div class='muted small'>まだデータがありません</div>"
        classes = ["rank-one", "rank-two", "rank-three"]
        out = []
        for i, row in enumerate(rows):
            out.append(
                f"<div class='rank-row'>"
                f"<span class='rank-badge {classes[i]}'>"
                f"<b>{i+1}</b></span>"
                f"<span class='rank-name'>{esc(str(row['display_name']))}</span>"
                f"<span class='rank-count'>{int(row['n'])}回</span>"
                f"</div>"
            )
        return "".join(out)

    def annual_stats_html():
        panels=[]
        for ys in yearly_stats:
            panels.append(
                f"<div class='annual-stats-panel {'active' if ys['term']==activity_term_number else ''}' data-term='{ys['term']}'>"
                f"<div class='stats-section-title'>{ys['term']}年目<span>{ys['year']}/6/1〜{ys['year']+1}/5/31</span></div>"
                f"<div class='stats-wide-card total-count'><b>{ys['total']}</b><span>卓数</span></div>"
                f"<div class='stats-type-row'><div><b>{ys['trpg']}</b><span>TRPG</span></div><div><b>{ys['madamis']}</b><span>マダミス</span></div></div>"
                f"<div class='stats-wide-card'><b>{ys['scenario_count']}</b><span>シナリオ種類</span></div>"
                f"<div class='stats-type-row'><div><b>{ys['trpg_scenarios']}</b><span>TRPG</span></div><div><b>{ys['madamis_scenarios']}</b><span>マダミス</span></div></div>"
                f"<div class='stats-ranking-title'>GM TOP3</div>{rank_html(ys['gm_top'])}"
                f"<div class='stats-ranking-title'>PL TOP3</div>{rank_html(ys['pl_top'])}</div>"
            )
        buttons=''.join(f"<button type='button' class='annual-term-btn {'active' if x['term']==activity_term_number else ''}' onclick='switchAnnualStats({x['term']},this)'>{x['term']}年目</button>" for x in yearly_stats)
        return f"<div class='annual-term-tabs'>{buttons}</div>{''.join(panels)}"

    annual_stats_markup = annual_stats_html()

    scenario_opts = "".join(
        f"<option value='{esc(str(x['scenario_name']))}'>"
        f"{esc(str(x['scenario_name']))}</option>"
        for x in scenarios
    )
    user_opts = "".join(
        f"<option value='{esc(str(x['discord_id']))}'>"
        f"{esc(str(x['display_name']))}</option>"
        for x in known_users
    )
    pl_checks = "".join(
        f"<label class='manual-user-check'>"
        f"<input type='checkbox' name='participant_ids' "
        f"value='{esc(str(x['discord_id']))}' "
        f"data-name='{esc(str(x['display_name']))}'>"
        f"<span>{esc(str(x['display_name']))}</span>"
        f"</label>"
        for x in known_users
    )

    cal = pycalendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(current.year, current.month)
    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    weekday_html = "".join(
        f"<div class='calendar-weekday {'sat' if i==5 else 'sun' if i==6 else ''}'>{name}</div>"
        for i, name in enumerate(weekday_names)
    )

    day_cells = []
    for week in weeks:
        for d in week:
            cls = "calendar-day" + (" outside" if d.month != current.month else "") + (" today" if d == today else "")
            dow = d.weekday()
            dcls = "sat" if dow == 5 else "sun" if dow == 6 else ""
            blocks = []

            if d.month == current.month:
                for row, members in by_date.get(d.isoformat(), []):
                    gt = str(row["game_type"] or "")
                    normalized_gt = "MADMIS" if gt == "マダミス" else gt
                    type_cls = (
                        "trpg" if normalized_gt == "TRPG"
                        else "madamis" if normalized_gt == "MADMIS"
                        else "event"
                    )

                    title_text = str(row["scenario_name"] or "")
                    time_text = str(row["start_time"] or "未定")

                    if type_cls == "event":
                        gm_text = str(row["gm_name"] or "")
                        member_text = " / ".join(str(m["display_name"] or "") for m in members)
                        member_ids = ",".join(str(m.get("discord_id") or "") for m in members if not m.get("is_guest"))
                        guest_members = "\n".join(str(m["display_name"] or "") for m in members if m.get("is_guest"))
                        member_detail = [
                            {"name":str(mm["display_name"] or ""), "id":str(mm.get("discord_id") or ""),
                             "guest":bool(mm.get("is_guest"))}
                            for mm in members
                        ]
                        blocks.append(
                            "<div class='cal-session' "
                            f"data-id='{int(row['id'])}' "
                            f"data-title='{esc(title_text)}' "
                            f"data-time='{esc(time_text)}' data-date='{esc(str(row['event_date'] or ''))}' "
                            f"data-gm='{esc(gm_text)}' data-gmid='{esc(str(row['gm_discord_id'] or ''))}' data-gmguest='{esc(str(row['gm_guest_name'] or ''))}' "
                            f"data-members='{esc(member_text)}' data-memberids='{esc(member_ids)}' data-memberdetail='{esc(json.dumps(member_detail, ensure_ascii=False))}' data-guestmembers='{esc(guest_members)}' data-game-type='EVENT' data-event='1' "
                            "onclick='event.stopPropagation();openCalendarDetail(this)'>"
                            f"<span class='cal-title event'>{esc(title_text)}</span>"
                            + (f"<span class='cal-person gm'>{esc(gm_text)}</span>" if gm_text else "")
                            + "".join(
                                f"<span class='cal-person pl'>{esc(m['display_name'])}</span>"
                                for m in members[:max(0, 5 - (1 if gm_text else 0))]
                            )
                            + "</div>"
                        )
                    else:
                        gm_text = str(row["gm_name"] or "")
                        member_text = " / ".join(str(m["display_name"] or "") for m in members)
                        member_ids = ",".join(str(m.get("discord_id") or "") for m in members if not m.get("is_guest"))
                        guest_members = "\n".join(str(m["display_name"] or "") for m in members if m.get("is_guest"))
                        member_detail = [
                            {"name":str(mm["display_name"] or ""), "id":str(mm.get("discord_id") or ""),
                             "guest":bool(mm.get("is_guest"))}
                            for mm in members
                        ]
                        blocks.append(
                            "<div class='cal-session' "
                            f"data-id='{int(row['id'])}' "
                            f"data-title='{esc(title_text)}' "
                            f"data-time='{esc(time_text)}' data-date='{esc(str(row['event_date'] or ''))}' "
                            f"data-gm='{esc(gm_text)}' "
                            f"data-gmid='{esc(str(row['gm_discord_id'] or ''))}' "
                            f"data-gmguest='{esc(str(row['gm_guest_name'] or ''))}' "
                            f"data-members='{esc(member_text)}' "
                            f"data-memberids='{esc(member_ids)}' data-memberdetail='{esc(json.dumps(member_detail, ensure_ascii=False))}' data-guestmembers='{esc(guest_members)}' data-game-type='{esc(normalized_gt)}' "
                            "data-event='0' "
                            "onclick='event.stopPropagation();openCalendarDetail(this)'>"
                            f"<span class='cal-title {type_cls}'>{esc(title_text)}</span>"
                            + (f"<span class='cal-person gm'>{esc(gm_text)}</span>" if gm_text else "")
                            + "".join(
                                f"<span class='cal-person pl'>{esc(m['display_name'])}</span>"
                                for m in members[:max(0, 5 - (1 if gm_text else 0))]
                            )
                            + "</div>"
                        )

            day_click = (
                "onclick='openManualAdd(this,event)'"
                if can_edit_calendar and d.month == current.month
                else ""
            )

            day_cells.append(
                f"<div class='{cls}' data-date='{d.isoformat()}' {day_click}>"
                f"<div class='calendar-date {dcls}'>{d.day}</div>"
                f"{''.join(blocks)}</div>"
            )

    def progress_scenario_list(game_type):
        scenarios_for_type = [
            x for x in progress_scenarios
            if str(x["game_type"]) == game_type
        ]
        if not scenarios_for_type:
            return "<div class='progress-empty'>シナリオデータはまだありません。</div>"

        cards = []
        for scenario in scenarios_for_type:
            name = str(scenario["scenario_name"])
            passed_count = sum(
                1 for user in progress_users
                if progress_statuses.get((game_type, name, str(user["discord_id"]))) == "PASSED"
            )
            watched_count = sum(
                1 for user in progress_users
                if progress_statuses.get((game_type, name, str(user["discord_id"]))) == "WATCHED"
            )
            cards.append(
                f"<button type='button' class='progress-scenario-row' "
                f"data-game-type='{esc(game_type)}' data-scenario='{esc(name)}' "
                f"onclick='openProgressScenario(this)'>"
                f"<span class='progress-scenario-name'>{esc(name)}</span>"
                f"<span class='progress-scenario-counts'>"
                f"<b class='passed'>通過 {passed_count}</b>"
                f"<b class='watched'>視聴 {watched_count}</b>"
                f"<span class='chev'>›</span></span></button>"
            )
        return "<div class='progress-scenario-list'>" + "".join(cards) + "</div>"

    progress_trpg_html = progress_scenario_list("TRPG")
    progress_madamis_html = progress_scenario_list("MADMIS")
    progress_csrf = esc(get_csrf_token(request))

    scenario_detail_map = {}
    for scenario in progress_scenarios:
        gt = str(scenario["game_type"])
        name = str(scenario["scenario_name"])
        count, detail_rows = scenario_detail(gt, name)
        scenario_detail_map[f"{gt}\x1f{name}"] = {
            "count": count,
            "passed": [
                str(x["display_name"])
                for x in detail_rows if str(x["status"]) == "PASSED"
            ],
            "watched": [
                str(x["display_name"])
                for x in detail_rows if str(x["status"]) == "WATCHED"
            ],
        }

    scenario_detail_json = json.dumps(
        scenario_detail_map, ensure_ascii=False
    ).replace("</", "<\\/")


    progress_editor_map = {}
    for scenario in progress_scenarios:
        gt = str(scenario["game_type"])
        name = str(scenario["scenario_name"])
        progress_editor_map[f"{gt}\x1f{name}"] = [
            {
                "id": str(user["discord_id"]),
                "name": str(user["display_name"]),
                "status": progress_statuses.get(
                    (gt, name, str(user["discord_id"])), ""
                ),
            }
            for user in progress_users
        ]
    progress_editor_json = json.dumps(
        progress_editor_map, ensure_ascii=False
    ).replace("</", "<\\/")

    return page(
        "カレンダー",
        f"""
        <a class='back-link' href='/'>‹ 戻る</a>

        <div class='calendar-head'>
          <a class='calendar-nav' href='/calendar?month={prev_month.strftime('%Y-%m')}'>‹</a>
          <h2>{current.year}年 {current.month}月</h2>
          <a class='calendar-nav' href='/calendar?month={next_month.strftime('%Y-%m')}'>›</a>

          <div class='calendar-floating-tools'>
            <a class='stats-circle profile-circle' href='{profile_href}' aria-label='プロフィール' title='プロフィール'>👤</a>
            <button class='stats-circle progress-circle' type='button'
                    onclick='openProgress(event)' aria-label='通過済みリスト'>
              <span class='progress-list-icon'><i></i><i></i><i></i></span>
            </button>
            <button class='stats-circle' type='button'
                    onclick='openStats(event)' aria-label='データ'>
              <span class='stats-bars'><i></i><i></i><i></i></span>
            </button>
          </div>
        </div>

        <div class='calendar-grid'>
          {weekday_html}
          {''.join(day_cells)}
        </div>

        <div class='calendar-modal' id='manualAddModal' onclick='closeManualAdd(event)'>
          <div class='calendar-modal-card' onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title'>卓をカレンダーに追加</h3>
            <form method='post' action='/calendar/manual-add'>
              {csrf_field(request)}
              <input type='hidden' name='event_date' id='manualEventDate'>

              <div class='manual-date-line' id='manualDateLabel'></div>

              <div class='manual-type-toggle'>
                <label>
                  <input type='radio' name='game_type' value='TRPG' checked>
                  <span>TRPG</span>
                </label>
                <label>
                  <input type='radio' name='game_type' value='MADMIS'>
                  <span>マダミス</span>
                </label>
                <label>
                  <input type='radio' name='game_type' value='EVENT'>
                  <span>イベント</span>
                </label>
              </div>

              <label class='field manual-field-spaced'>
                <div class='field-box no-icon manual-scenario-box'>
                  <div class='field-stack'>
                    <input type='text' id='manualScenarioInput' name='scenario_name'
                           placeholder='シナリオ名を入力'
                           autocomplete='off' required>
                  </div>
                </div>
              </label>

              <label class='field manual-field-spaced' id='manualTimeField'>
                <div class='field-box no-icon manual-time-box manual-control-box'>
                  <div class='field-stack'>
                    <span class='field-label'>開催時間</span>
                    <input type='time' name='start_time' value='21:00'>
                  </div>
                </div>
              </label>

              <label class='field manual-field-spaced manual-gm-field' id='manualGmField'>
                <div class='field-box no-icon manual-control-box manual-gm-box'>
                  <div class='field-stack'>
                    <span class='field-label' id='manualGmLabel'>GM</span>
                    <select name='gm_discord_id'>
                      <option value='' disabled selected>選択してください</option>
                      {user_opts}
                      <option value=''>GMなし</option>
                    </select>
                  </div>
                </div>
              </label>

              <label class='manual-event-members' id='manualEventMembersToggle' style='display:none'>
                <input type='checkbox' name='event_has_members' value='1' onchange='syncManualType()'> メンバーを決める
              </label>

              <div class='calendar-modal-row' id='manualPlField'>
                <span class='calendar-modal-label' id='manualPlLabel'>PL（複数選択可）</span>
                <div class='manual-user-list'>{pl_checks}</div>
                <label class='guest-member-toggle' id='manualGuestMemberToggle'>
                  <input type='checkbox' id='manualGuestMemberCheck' onchange='syncGuestMemberInput("manual")'>
                  <span>新規参加者を追加</span>
                </label>
                <div class='guest-member-input-wrap' id='manualGuestMemberInputWrap'>
                  <textarea name='guest_participant_names' rows='2' placeholder='参加者名を入力（1行で一名追加）'></textarea>
                </div>
              </div>

              <div class='manual-actions'>
                <button class='btn alt' type='button' onclick='closeManualAdd()'>キャンセル</button>
                <button class='btn green' type='submit'>追加</button>
              </div>
            </form>
          </div>
        </div>

        <div class='calendar-modal' id='progressModal'
             onclick='closeProgress(event)'>
          <div class='calendar-modal-card progress-card'
               onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title'>通過済みリスト</h3>

            <div class='progress-legend'>
              <span><b class='progress-dot passed'>○</b></span>
              <span><b class='progress-dot watched'>○</b></span>
              <span><b>－</b> 未通過</span>
            </div>

            <div class='progress-tabs'>
              <button class='progress-tab active' type='button'
                      onclick="switchProgressTab('TRPG',this)">TRPG</button>
              <button class='progress-tab' type='button'
                      onclick="switchProgressTab('MADMIS',this)">マダミス</button>
            </div>

            <div class='progress-panel active' id='progressPanelTRPG'>
              {progress_trpg_html}
            </div>
            <div class='progress-panel' id='progressPanelMADMIS'>
              {progress_madamis_html}
            </div>

            <p class='progress-hint'>
              シナリオを選ぶと、メンバーごとの通過状況を編集できます。
            </p>

            <button class='calendar-modal-close' type='button'
                    onclick='closeProgress()'>閉じる</button>
          </div>
        </div>

        <div class='calendar-modal' id='progressScenarioModal'
             onclick='closeProgressScenario(event)'>
          <div class='calendar-modal-card progress-person-card'
               onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title' id='progressScenarioTitle'></h3>
            <div class='progress-person-legend'>
              <span class='passed'>○</span>
              <span class='watched'>○</span>
              <span>－ 未通過</span>
            </div>
            <div class='progress-person-list' id='progressPersonList'></div>
            <button class='btn green progress-save-btn' type='button'
                    onclick='saveProgressScenario()'>保存</button>
            <button class='calendar-modal-close progress-cancel-btn' type='button'
                    onclick='closeProgressScenario(null, true)'>保存せずに閉じる</button>
          </div>
        </div>

        <div class='calendar-modal' id='scenarioDetailModal'
             onclick='closeScenarioDetail(event)'>
          <div class='calendar-modal-card scenario-detail-card'
               onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title' id='scenarioDetailTitle'></h3>

            <div class='scenario-detail-count'>
              開催回数：<b id='scenarioDetailCount'>0</b>回
            </div>

            <div class='scenario-detail-section'>
              <div class='scenario-detail-heading passed'>通過済み</div>
              <div class='scenario-detail-names' id='scenarioPassedNames'></div>
            </div>

            <div class='scenario-detail-section'>
              <div class='scenario-detail-heading watched'>視聴済み</div>
              <div class='scenario-detail-names' id='scenarioWatchedNames'></div>
            </div>

            <button class='calendar-modal-close' type='button'
                    onclick='closeScenarioDetail()'>閉じる</button>
          </div>
        </div>

        <style>
          .annual-stats-panel {{ display:none; }}
          .annual-stats-panel.active {{ display:block; }}
          .annual-term-tabs {{ display:flex; gap:7px; overflow-x:auto; margin:10px 0 14px; padding-bottom:3px; }}
          .annual-term-btn {{ flex:0 0 auto; border:1px solid #4b5563; background:#252936; color:#cbd5e1; border-radius:10px; padding:7px 12px; font-weight:700; }}
          .profile-circle {{ color:#aab6c7; text-decoration:none; font-size:1.05rem; }}
          .calendar-person-link {{ color:inherit; text-decoration:none; display:inline-flex; flex-direction:column; gap:1px; }}
          .calendar-person-link:hover {{ text-decoration:none; }}
          .calendar-modal-member {{ display:inline-flex; flex-direction:column; align-items:flex-start; gap:2px; padding:0; background:transparent; color:#e3bd52; border:0; vertical-align:top; }}
          .calendar-modal-name {{ display:inline-flex; color:#e3bd52; background:rgba(207,165,42,.15); padding:4px 8px; border-radius:7px; font-size:.8rem; font-weight:900; line-height:1.25; white-space:nowrap; }}
          .calendar-modal-gm-tag .calendar-modal-name {{ color:#d08aff; background:rgba(168,85,247,.18); border:1px solid rgba(168,85,247,.08); }}
          #calendarDetailGm {{ display:inline-flex; flex-direction:column; align-items:flex-start; gap:2px; }}
          a.calendar-modal-name {{ text-decoration:none; }}
          a.calendar-modal-name:hover {{ text-decoration:none; filter:brightness(1.12); }}
          .annual-term-btn.active {{ background:#5865f2; border-color:#5865f2; color:white; }}
          #manualPlField textarea,#calendarEditPanel textarea {{ width:100%; margin-top:10px; box-sizing:border-box; border:1px solid #42485a; background:#1e212b; color:#fff; border-radius:10px; padding:10px; resize:vertical; }}
          .manual-event-members {{ display:block; margin:12px 0; padding:11px 13px; border-radius:10px; background:#252936; }}
        </style>

        <div class='calendar-modal' id='statsModal' onclick='closeStats(event)'>
          <div class='calendar-modal-card stats-card' onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title'>つぶぐみ卓データ</h3>

            <div class='stats-section total'>
              <div class='stats-section-title'>累計</div>
              <div class='stats-wide-card total-count'><b>{total_stats["total"]}</b><span>累計卓数</span></div>
              <div class='stats-type-row'><div><b>{total_stats["trpg"]}</b><span>TRPG卓数</span></div><div><b>{total_stats["madamis"]}</b><span>マダミス卓数</span></div></div>
              <div class='stats-wide-card'><b>{total_stats["scenario_count"]}</b><span>累計シナリオ種類</span></div>
              <div class='stats-type-row'><div><b>{total_stats["trpg_scenarios"]}</b><span>TRPGシナリオ</span></div><div><b>{total_stats["madamis_scenarios"]}</b><span>マダミスシナリオ</span></div></div>
              <div class='stats-ranking-title'>GM TOP3</div>{rank_html(total_stats["gm_top"])}
              <div class='stats-ranking-title'>PL TOP3</div>{rank_html(total_stats["pl_top"])}
            </div>
            <div class='stats-section'>
              <div class='stats-section-title'>年度別</div>
              {annual_stats_markup}
            </div>

            <button class='calendar-modal-close' type='button' onclick='closeStats()'>閉じる</button>
          </div>
        </div>

        <div class='calendar-modal' id='calendarDetailModal' onclick='closeCalendarDetail(event)'>
          <div class='calendar-modal-card' onclick='event.stopPropagation()'>
            <h3 class='calendar-modal-title' id='calendarDetailTitle'></h3>
            <div class='calendar-modal-meta'>
              <div class='calendar-modal-row' id='calendarDetailDateRow'>
                <span class='calendar-modal-label'>開催日</span>
                <span id='calendarDetailDate'></span>
              </div>
              <label class='field calendar-edit-top-date' id='calendarEditTopDate' style='display:none'>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>開催日</span>
                    <input id='calendarEditDate' name='event_date' type='date' form='calendarMembersEditForm' required>
                  </div>
                </div>
              </label>
              <div class='calendar-modal-row'>
                <span class='calendar-modal-label'>開催時間</span>
                <span id='calendarDetailTime'></span>
              </div>
              <div class='calendar-modal-row' id='calendarDetailGmRow'>
                <span class='calendar-modal-label'>GM</span>
                <div class='calendar-modal-members'>
                  <span class='calendar-modal-member calendar-modal-gm-tag'
                        id='calendarDetailGm'></span>
                </div>
              </div>
              <div class='calendar-modal-row' id='calendarDetailMembersRow'>
                <span class='calendar-modal-label'>PL</span>
                <div class='calendar-modal-members' id='calendarDetailMembers'></div>
              </div>
            </div>
            <button class='calendar-edit-open' id='calendarEditOpenBtn'
                    type='button' onclick='openCalendarEdit()'>編集</button>

            <div class='calendar-edit-panel' id='calendarEditPanel'>
              <form method='post' id='calendarMembersEditForm'>
                {csrf_field(request)}
                <input type='hidden' id='calendarEditSessionId' name='calendar_session_id'>
                <input type='hidden' id='calendarEditOriginalDate' name='original_event_date'>
                <input type='hidden' id='calendarEditOriginalTime' name='original_start_time'>

                <div class='manual-type-toggle calendar-edit-type-toggle'>
                  <label><input type='radio' name='game_type' value='TRPG'><span>TRPG</span></label>
                  <label><input type='radio' name='game_type' value='MADMIS'><span>マダミス</span></label>
                  <label><input type='radio' name='game_type' value='EVENT'><span>イベント</span></label>
                </div>

                <label class='field manual-field-spaced'><div class='field-box no-icon'><div class='field-stack'><span class='field-label'>シナリオ名 / イベント名</span><input id='calendarEditScenario' name='scenario_name' required></div></div></label>
                <label class='field manual-field-spaced calendar-edit-gm-field'><div class='field-box no-icon'><div class='field-stack'><span class='field-label' id='calendarEditGmLabel'>GM</span><select id='calendarEditGm' name='gm_discord_id'>{user_opts}<option value=''>GMなし</option></select></div></div></label>
                <div class='calendar-modal-row'>
                  <span class='calendar-modal-label' id='calendarEditPlLabel'>PLを編集</span>
                  <div class='manual-user-list' id='calendarEditPlList'>
                    {pl_checks}
                  </div>
                  <label class='guest-member-toggle' id='editGuestMemberToggle'>
                    <input type='checkbox' id='editGuestMemberCheck' onchange='syncGuestMemberInput("edit")'>
                    <span>新規参加者を追加</span>
                  </label>
                  <div class='guest-member-input-wrap' id='editGuestMemberInputWrap'>
                    <textarea id='calendarEditGuestMembers' name='guest_participant_names' rows='2' placeholder='参加者名を入力（1行で一名追加）'></textarea>
                  </div>
                </div>

                <button class='calendar-save-btn' type='submit'
                        formaction='/calendar/edit-details'>保存</button>
              </form>

              <div class='calendar-danger-actions'>
                <button class='calendar-hide-small' type='button'
                        onclick="openDangerConfirm('hide')">非表示</button>
                <button class='calendar-delete-small' type='button'
                        onclick="openDangerConfirm('delete')">削除</button>
              </div>

              <div class='calendar-danger-confirm' id='hideDangerConfirm'>
                <p>本当にこの卓をカレンダーから非表示にしますか？</p>
                <label class='danger-confirm-line'>
                  <input type='checkbox' id='hideConfirmCheck'
                         onchange='syncDangerButtons()'>
                  <span>確認しました</span>
                </label>
                <form method='post' action='/calendar/hide'>
                  {csrf_field(request)}
                  <input type='hidden' class='calendarDangerSessionId'
                         name='calendar_session_id'>
                  <button class='calendar-hide-btn' id='hideCalendarBtn'
                          type='submit' disabled>カレンダーから非表示</button>
                </form>
              </div>

              <div class='calendar-danger-confirm delete' id='deleteDangerConfirm'>
                <p>本当にこの卓データを完全に削除しますか？</p>
                <label class='danger-confirm-line'>
                  <input type='checkbox' id='deleteConfirmCheck'
                         onchange='syncDangerButtons()'>
                  <span>確認しました</span>
                </label>
                <form method='post' action='/calendar/delete'>
                  {csrf_field(request)}
                  <input type='hidden' class='calendarDangerSessionId'
                         name='calendar_session_id'>
                  <button class='calendar-delete-btn' id='deleteCalendarBtn'
                          type='submit' disabled>卓データを完全削除</button>
                </form>
              </div>
            </div>

            <button class='calendar-modal-close' type='button'
                    onclick='closeCalendarDetail()'>閉じる</button>
          </div>
        </div>

        <script>
        function syncGuestMemberInput(which){{
          const isEdit=which==='edit';
          const check=document.getElementById(isEdit?'editGuestMemberCheck':'manualGuestMemberCheck');
          const wrap=document.getElementById(isEdit?'editGuestMemberInputWrap':'manualGuestMemberInputWrap');
          if(!check || !wrap) return;
          wrap.classList.toggle('open',!!check.checked);
          const input=wrap.querySelector('textarea');
          if(input) input.disabled=!check.checked;
        }}

        function syncManualType(){{
          const selected=document.querySelector(
            "#manualAddModal input[name='game_type']:checked"
          );
          const isEvent=selected && selected.value==='EVENT';
          const scenarioInput=document.getElementById('manualScenarioInput');
          if(scenarioInput){{
            scenarioInput.placeholder=isEvent?'イベント名を入力':'シナリオ名を入力';
          }}

          const timeField=document.getElementById('manualTimeField');
          const gmField=document.getElementById('manualGmField');
          const plField=document.getElementById('manualPlField');
          const eventToggle=document.getElementById('manualEventMembersToggle');
          const eventHas=!!document.querySelector("#manualAddModal input[name='event_has_members']:checked");
          const gmLabel=document.getElementById('manualGmLabel'); const plLabel=document.getElementById('manualPlLabel');
          if(gmLabel) gmLabel.textContent=isEvent?'主催者':'GM';
          if(plLabel) plLabel.textContent=isEvent?'参加者（複数選択可）':'PL（複数選択可）';
          if(eventToggle) eventToggle.style.display=isEvent?'block':'none';
          const disablePeople=isEvent && !eventHas;

          if(timeField) timeField.classList.toggle('manual-disabled',isEvent);
          [gmField,plField].forEach(el=>{{ if(el) el.classList.toggle('manual-disabled',disablePeople); }});

          const timeInput=document.querySelector(
            "#manualAddModal input[name='start_time']"
          );
          const gmSelect=document.querySelector(
            "#manualAddModal select[name='gm_discord_id']"
          );
          if(timeInput) timeInput.disabled=isEvent;
          if(gmSelect) gmSelect.disabled=disablePeople;

          document.querySelectorAll(
            "#manualAddModal input[name='participant_ids']"
          ).forEach(cb=>cb.disabled=disablePeople);
          const guestCheck=document.getElementById('manualGuestMemberCheck');
          if(guestCheck) guestCheck.disabled=disablePeople;
          syncGuestMemberInput('manual');
          const guestPl=document.querySelector("#manualAddModal textarea[name='guest_participant_names']");
          if(guestPl && disablePeople) guestPl.disabled=true;
        }}

        document.querySelectorAll(
          "#manualAddModal input[name='game_type']"
        ).forEach(el=>el.addEventListener('change',syncManualType));

        function openManualAdd(cell,ev){{
          if(ev && ev.target.closest('.cal-session')) return;
          const modal=document.getElementById('manualAddModal');
          const ds=cell.dataset.date;
          if(!modal || !ds) return;
          document.getElementById('manualEventDate').value=ds;
          const wd=['日','月','火','水','木','金','土'];
          const dt=new Date(ds+'T00:00:00');
          document.getElementById('manualDateLabel').textContent=
            ds+'（'+wd[dt.getDay()]+'）';
          const guestCheck=document.getElementById('manualGuestMemberCheck');
          const guestText=document.querySelector("#manualAddModal textarea[name='guest_participant_names']");
          if(guestCheck) guestCheck.checked=false;
          if(guestText) guestText.value='';
          syncGuestMemberInput('manual');
          syncManualType();
          modal.classList.add('open');
          document.body.style.overflow='hidden';
        }}

        function closeManualAdd(ev){{
          if(ev && ev.target!==document.getElementById('manualAddModal')) return;
          const modal=document.getElementById('manualAddModal');
          if(modal) modal.classList.remove('open');
          document.body.style.overflow='';
        }}

        const scenarioDetailData={scenario_detail_json};

        function openProgress(ev){{
          if(ev) ev.stopPropagation();
          const modal=document.getElementById('progressModal');
          if(modal) modal.classList.add('open');
          document.body.style.overflow='hidden';
        }}

        function closeProgress(ev){{
          if(ev && ev.target!==document.getElementById('progressModal')) return;
          const modal=document.getElementById('progressModal');
          if(modal) modal.classList.remove('open');
          document.body.style.overflow='';
        }}

        function switchProgressTab(type,btn){{
          document.querySelectorAll('.progress-tab').forEach(
            x=>x.classList.remove('active')
          );
          if(btn) btn.classList.add('active');

          document.querySelectorAll('.progress-panel').forEach(
            x=>x.classList.remove('active')
          );
          const panel=document.getElementById('progressPanel'+type);
          if(panel) panel.classList.add('active');
        }}

        const progressEditorData={progress_editor_json};
        let currentProgressScenarioKey='';

        function openProgressScenario(el){{
          const gt=el.dataset.gameType||'';
          const name=el.dataset.scenario||'';
          const key=gt+'\x1f'+name;
          currentProgressScenarioKey=key;
          document.getElementById('progressScenarioTitle').textContent=name;
          const list=document.getElementById('progressPersonList');
          list.innerHTML='';
          (progressEditorData[key]||[]).forEach(person=>{{
            const row=document.createElement('button');
            row.type='button'; row.className='progress-person-row';
            row.dataset.gameType=gt; row.dataset.scenario=name; row.dataset.userId=person.id;
            row.dataset.originalStatus=person.status||''; row.dataset.status=person.status||'';
            const n=document.createElement('span'); n.className='progress-person-name'; n.textContent=person.name;
            const state=document.createElement('span'); state.className='progress-person-state';
            applyProgressState(state,person.status||'');
            row.appendChild(n); row.appendChild(state);
            row.onclick=()=>cycleProgressPerson(row,state);
            list.appendChild(row);
          }});
          document.getElementById('progressScenarioModal').classList.add('open');
        }}

        function applyProgressState(el,status){{
          el.className='progress-person-state';
          if(status==='PASSED'){{el.classList.add('passed');el.textContent='○';}}
          else if(status==='WATCHED'){{el.classList.add('watched');el.textContent='○';}}
          else{{el.classList.add('unpassed');el.textContent='－';}}
        }}

        function cycleProgressPerson(row,stateEl){{
          const current=row.dataset.status||'';
          const next=current===''?'PASSED':current==='PASSED'?'WATCHED':'';
          row.dataset.status=next;
          applyProgressState(stateEl,next);
        }}

        async function saveProgressScenario(){{
          const modal=document.getElementById('progressScenarioModal');
          const changed=[...modal.querySelectorAll('.progress-person-row')]
            .filter(row=>(row.dataset.status||'')!==(row.dataset.originalStatus||''));
          if(changed.length===0){{modal.classList.remove('open');return;}}
          const btn=modal.querySelector('.progress-save-btn');
          btn.disabled=true;btn.textContent='保存中…';
          try{{
            for(const row of changed){{
              const fd=new FormData();
              fd.append('csrf_token','{progress_csrf}');
              fd.append('game_type',row.dataset.gameType||'');
              fd.append('scenario_name',row.dataset.scenario||'');
              fd.append('discord_id',row.dataset.userId||'');
              fd.append('status',row.dataset.status||'');
              const res=await fetch('/calendar/progress-set',{{method:'POST',body:fd}});
              if(!res.ok)throw new Error('save failed');
              const people=progressEditorData[currentProgressScenarioKey]||[];
              const person=people.find(x=>String(x.id)===String(row.dataset.userId));
              if(person)person.status=row.dataset.status||'';
            }}
            window.location.reload();
          }}catch(e){{
            btn.disabled=false;btn.textContent='保存';
            alert('更新に失敗しました。もう一度お試しください。');
          }}
        }}

        function closeProgressScenario(ev,discard=false){{
          const modal=document.getElementById('progressScenarioModal');
          if(!modal)return;
          if(ev && ev.target!==modal)return;
          if(ev && !discard)return;
          modal.classList.remove('open');
          currentProgressScenarioKey='';
        }}

        function openScenarioDetail(el){{
          const gt=el.dataset.gameType||'';
          const name=el.dataset.scenario||'';
          const key=gt+'\x1f'+name;
          const data=scenarioDetailData[key]||{{count:0,passed:[],watched:[]}};

          document.getElementById('scenarioDetailTitle').textContent=name;
          document.getElementById('scenarioDetailCount').textContent=data.count||0;

          const passed=document.getElementById('scenarioPassedNames');
          const watched=document.getElementById('scenarioWatchedNames');
          passed.innerHTML='';
          watched.innerHTML='';

          (data.passed||[]).forEach(n=>{{
            const chip=document.createElement('span');
            chip.className='scenario-name-chip passed';
            chip.textContent=n;
            passed.appendChild(chip);
          }});

          (data.watched||[]).forEach(n=>{{
            const chip=document.createElement('span');
            chip.className='scenario-name-chip watched';
            chip.textContent=n;
            watched.appendChild(chip);
          }});

          if(!passed.children.length){{
            passed.innerHTML='<span class="muted small">なし</span>';
          }}
          if(!watched.children.length){{
            watched.innerHTML='<span class="muted small">なし</span>';
          }}

          document.getElementById('scenarioDetailModal').classList.add('open');
        }}

        function closeScenarioDetail(ev){{
          if(ev && ev.target!==document.getElementById('scenarioDetailModal')) return;
          const modal=document.getElementById('scenarioDetailModal');
          if(modal) modal.classList.remove('open');
        }}

        function switchAnnualStats(term,btn){{
          document.querySelectorAll('.annual-stats-panel').forEach(x=>x.classList.toggle('active',x.dataset.term===String(term)));
          document.querySelectorAll('.annual-term-btn').forEach(x=>x.classList.toggle('active',x===btn));
        }}

        function openStats(ev){{
          if(ev) ev.stopPropagation();
          const modal=document.getElementById('statsModal');
          if(modal) modal.classList.add('open');
          document.body.style.overflow='hidden';
        }}

        function closeStats(ev){{
          if(ev && ev.target!==document.getElementById('statsModal')) return;
          const modal=document.getElementById('statsModal');
          if(modal) modal.classList.remove('open');
          document.body.style.overflow='';
        }}

        function openCalendarDetail(el){{
          const modal=document.getElementById('calendarDetailModal');
          const isEvent=el.dataset.event==='1';
          const hasEventMembers=isEvent && !!((el.dataset.gm||'') || (el.dataset.members||''));
          const sessionId=el.dataset.id||'';

          document.getElementById('calendarEditSessionId').value=sessionId;
          document.querySelectorAll('.calendarDangerSessionId').forEach(
            x=>x.value=sessionId
          );

          document.getElementById('calendarEditPanel').classList.remove('open');
          const detailDateRow=document.getElementById('calendarDetailDateRow');
          if(detailDateRow) detailDateRow.style.display='block';
          document.getElementById('hideDangerConfirm').classList.remove('open');
          document.getElementById('deleteDangerConfirm').classList.remove('open');
          document.getElementById('hideConfirmCheck').checked=false;
          document.getElementById('deleteConfirmCheck').checked=false;
          syncDangerButtons();

          const editBtn=document.getElementById('calendarEditOpenBtn');
          if(editBtn) editBtn.style.display='inline-flex';

          document.getElementById('calendarDetailTitle').textContent=
            el.dataset.title||'';
          const detailDate=el.dataset.date||'';
          document.getElementById('calendarDetailDate').textContent=
            detailDate ? detailDate.replaceAll('-', '/') : '未定';
          document.getElementById('calendarDetailTime').textContent=
            (el.dataset.time && el.dataset.time!=='未定')
              ? el.dataset.time : '未定';

          const gmRow=document.getElementById('calendarDetailGmRow');
          const membersRow=document.getElementById('calendarDetailMembersRow');

          if(isEvent && !hasEventMembers){{
            gmRow.style.display='none'; membersRow.style.display='none';
          }}else{{
            gmRow.style.display='block'; membersRow.style.display='block';
            document.querySelector('#calendarDetailGmRow .calendar-modal-label').textContent=isEvent?'主催者':'GM';
            document.querySelector('#calendarDetailMembersRow .calendar-modal-label').textContent=isEvent?'参加者':'PL';
            gmRow.style.display='block';
            membersRow.style.display='block';
            const gmBox=document.getElementById('calendarDetailGm');
            gmBox.innerHTML='';
            const gmId=el.dataset.gmid||'';
            const gmName=el.dataset.gm||'';
            // GMなしの卓では空の紫タグを出さない。
            if(gmName || gmId){{
              gmRow.style.display='block';
              const gmNameEl=document.createElement(gmId?'a':'span');
              gmNameEl.className='calendar-modal-name';
              if(gmId) gmNameEl.href='/profile/'+encodeURIComponent(gmId);
              gmNameEl.textContent=gmName || 'GM';
              gmBox.appendChild(gmNameEl);
            }}else{{
              gmRow.style.display='none';
            }}

            let memberDetail=[];
            try{{memberDetail=JSON.parse(el.dataset.memberdetail||'[]');}}catch(e){{memberDetail=[];}}
            const fallbackNames=(el.dataset.members||'').split(' / ').filter(Boolean);
            if(!memberDetail.length){{
              memberDetail=fallbackNames.map(name=>({{name,id:'',guest:true}}));
            }}else{{
              // 古い/不完全な詳細データでも、data-members の表示名を必ず使えるよう補完する。
              memberDetail=memberDetail.map((person,i)=>({{...person,name:(person && person.name) || fallbackNames[i] || ''}}));
            }}
            const box=document.getElementById('calendarDetailMembers');
            box.innerHTML='';
            memberDetail.forEach(person=>{{
              const chip=document.createElement('span');
              chip.className='calendar-modal-member';
              const nameEl=document.createElement(person.id && !person.guest?'a':'span');
              nameEl.className='calendar-modal-name';
              if(person.id && !person.guest) nameEl.href='/profile/'+encodeURIComponent(person.id);
              nameEl.textContent=person.name||'';
              chip.appendChild(nameEl);
              box.appendChild(chip);
            }});

            document.querySelectorAll(
              "#calendarEditPlList input[name='participant_ids']"
            ).forEach(cb=>{{
              cb.checked=(el.dataset.memberids||'').split(',').filter(Boolean).includes(cb.value||'');
              cb.disabled=false;
            }});
          }}

          document.getElementById('calendarEditScenario').value=el.dataset.title||'';
          document.getElementById('calendarEditDate').value=el.dataset.date||'';
          document.getElementById('calendarEditOriginalDate').value=el.dataset.date||'';
          document.getElementById('calendarEditOriginalTime').value=el.dataset.time||'';
          const editGameType=el.dataset.gameType || (isEvent ? 'EVENT' : 'TRPG');
          document.querySelectorAll("#calendarMembersEditForm input[name='game_type']").forEach(r=>{{
            r.checked=(r.value===editGameType);
          }});
          document.getElementById('calendarEditGm').value=el.dataset.gmid||'';
          document.getElementById('calendarEditGuestMembers').value=el.dataset.guestmembers||'';
          const editGuestCheck=document.getElementById('editGuestMemberCheck');
          if(editGuestCheck) editGuestCheck.checked=!!(el.dataset.guestmembers||'').trim();
          syncGuestMemberInput('edit');
          document.getElementById('calendarEditGmLabel').textContent=isEvent?'主催者':'GM';
          document.getElementById('calendarEditPlLabel').textContent=isEvent?'参加者を編集':'PLを編集';
          document.querySelectorAll("#calendarEditPlList input[name='participant_ids']").forEach(cb=>cb.disabled=false);
          syncCalendarEditType();

          modal.classList.add('open');
          document.body.style.overflow='hidden';
        }}

        function syncCalendarEditType(){{
          const selected=document.querySelector("#calendarMembersEditForm input[name='game_type']:checked");
          const isEventEdit=!!selected && selected.value==='EVENT';
          const gmLabel=document.getElementById('calendarEditGmLabel');
          const plLabel=document.getElementById('calendarEditPlLabel');
          if(gmLabel) gmLabel.textContent=isEventEdit?'主催者':'GM';
          if(plLabel) plLabel.textContent=isEventEdit?'参加者を編集':'PLを編集';
          const scenario=document.getElementById('calendarEditScenario');
          if(scenario) scenario.placeholder=isEventEdit?'イベント名を入力':'シナリオ名を入力';
        }}

        document.querySelectorAll("#calendarMembersEditForm input[name='game_type']").forEach(r=>{{
          r.addEventListener('change',syncCalendarEditType);
        }});

        function refreshCalendarEditPreview(){{
          const box=document.getElementById('calendarDetailMembers');
          if(!box) return;
          box.innerHTML='';
          document.querySelectorAll(
            "#calendarEditPlList input[name='participant_ids']:checked"
          ).forEach(cb=>{{
            const span=document.createElement('span');
            span.className='calendar-modal-member';
            span.textContent=cb.dataset.name||'';
            box.appendChild(span);
          }});
        }}

        document.querySelectorAll(
          "#calendarEditPlList input[name='participant_ids']"
        ).forEach(cb=>{{
          cb.addEventListener('change',refreshCalendarEditPreview);
        }});

        function openCalendarEdit(){{
          const panel=document.getElementById('calendarEditPanel');
          const btn=document.getElementById('calendarEditOpenBtn');
          const detailDateRow=document.getElementById('calendarDetailDateRow');
          const editTopDate=document.getElementById('calendarEditTopDate');
          if(detailDateRow) detailDateRow.style.display='none';
          if(editTopDate) editTopDate.style.display='block';
          if(panel){{
            panel.classList.add('open');
            if(btn) btn.style.display='none';
          }}
        }}

        function openDangerConfirm(kind){{
          const hide=document.getElementById('hideDangerConfirm');
          const del=document.getElementById('deleteDangerConfirm');
          if(hide) hide.classList.toggle('open',kind==='hide');
          if(del) del.classList.toggle('open',kind==='delete');

          if(kind==='hide'){{
            document.getElementById('hideConfirmCheck').checked=false;
          }}else{{
            document.getElementById('deleteConfirmCheck').checked=false;
          }}
          syncDangerButtons();
        }}

        function syncDangerButtons(){{
          const hideCheck=document.getElementById('hideConfirmCheck');
          const deleteCheck=document.getElementById('deleteConfirmCheck');
          const hideBtn=document.getElementById('hideCalendarBtn');
          const deleteBtn=document.getElementById('deleteCalendarBtn');

          if(hideBtn) hideBtn.disabled=!(hideCheck && hideCheck.checked);
          if(deleteBtn) deleteBtn.disabled=!(deleteCheck && deleteCheck.checked);
        }}

        function closeCalendarDetail(ev){{
          const editTopDate=document.getElementById('calendarEditTopDate');
          if(editTopDate) editTopDate.style.display='none';
          if(ev && ev.target!==document.getElementById('calendarDetailModal')) return;
          const modal=document.getElementById('calendarDetailModal');
          if(modal) modal.classList.remove('open');
          document.body.style.overflow='';
        }}

        document.addEventListener('keydown',e=>{{
          if(e.key==='Escape'){{
            closeCalendarDetail();
            closeManualAdd();
            closeStats();
            closeProgress();
            closeScenarioDetail();
          }}
        }});
        </script>
        """,
        request,
    )


@app.post("/calendar/manual-add")
async def calendar_manual_add(
    request: Request,
    event_date: str = Form(...),
    game_type: str = Form(...),
    scenario_name: str = Form(...),
    start_time: str = Form(""),
    gm_discord_id: str = Form(""),
    participant_ids: list[str] = Form(default=[]),
    gm_guest_name: str = Form(""),
    guest_participant_names: str = Form(""),
    event_has_members: str = Form(""),
):
    require_login(request)
    await require_csrf(request)

    if game_type not in {"TRPG", "MADMIS", "EVENT"}:
        raise HTTPException(400, "種類が不正です")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
        raise HTTPException(400, "日付が不正です")
    if not scenario_name.strip():
        raise HTTPException(400, "シナリオ名を選択してください")

    if game_type == "EVENT":
        start_time = "未定"
        if not event_has_members:
            gm_discord_id = ""; gm_guest_name = ""; participant_ids = []; guest_participant_names = ""
    # TRPG/マダミスはGMなしでも登録可能。

    add_manual_calendar_session(
        game_type=game_type,
        scenario_name=scenario_name.strip(),
        event_date=event_date,
        start_time=start_time.strip() or "未定",
        gm_discord_id=str(gm_discord_id),
        participant_ids=[str(x) for x in participant_ids],
        created_at=iso_now(),
        gm_guest_name=gm_guest_name.strip(),
        guest_participant_names=[x.strip() for x in guest_participant_names.splitlines() if x.strip()],
    )

    d = date.fromisoformat(event_date)
    return RedirectResponse(
        f"/calendar?month={d.strftime('%Y-%m')}",
        status_code=303,
    )



@app.post("/calendar/edit-details")
async def calendar_edit_details(
    request: Request, calendar_session_id: int = Form(...), scenario_name: str = Form(...),
    event_date: str = Form(...), game_type: str = Form(...), gm_discord_id: str = Form(""), gm_guest_name: str = Form(""),
    participant_ids: list[str] = Form(default=[]), guest_participant_names: str = Form(""),
    original_event_date: str = Form(""), original_start_time: str = Form(""),
):
    require_login(request); await require_csrf(request)
    detail_before, _ = calendar_session_detail(calendar_session_id)
    if not scenario_name.strip(): raise HTTPException(400, "シナリオ名 / イベント名を入力してください")
    if game_type not in {"TRPG", "MADMIS", "EVENT"}: raise HTTPException(400, "種別が不正です")
    try:
        edited_day = date.fromisoformat(event_date)
    except ValueError:
        raise HTTPException(400, "開催日が不正です")
    sync_result=sync_linked_session_from_calendar_edit(
        calendar_session_id, original_event_date or str(detail_before['event_date'] if detail_before else ''),
        original_start_time or str(detail_before['start_time'] if detail_before else ''),
        event_date, scenario_name, gm_discord_id, [str(x) for x in participant_ids], game_type=game_type
    )
    if sync_result and sync_result.get('error')=='duplicate_slot':
        raise HTTPException(400, "同じ卓に同一の開催日時がすでに登録されています")
    if not update_calendar_session_details(calendar_session_id, scenario_name, gm_discord_id,
        [str(x) for x in participant_ids], gm_guest_name,
        [x.strip() for x in guest_participant_names.splitlines() if x.strip()], game_type=game_type,
        event_date=event_date):
        raise HTTPException(404, "予定が見つかりません")
    # 複数日卓ではcalendar_sessionsの代表日時を最初のslotへ揃える。
    with db() as c:
        first=c.execute("SELECT event_date,start_time FROM calendar_session_slots WHERE calendar_session_id=? ORDER BY event_date,start_time LIMIT 1",(int(calendar_session_id),)).fetchone()
        if first:
            c.execute("UPDATE calendar_sessions SET event_date=?,start_time=? WHERE id=?",(str(first['event_date']),str(first['start_time']),int(calendar_session_id)))
    if sync_result and sync_result.get('session_id'):
        schedule_session_reminder(int(sync_result['session_id']))
    return RedirectResponse(f"/calendar?month={edited_day.strftime('%Y-%m')}", status_code=303)

@app.post("/calendar/hide")
async def calendar_hide(
    request: Request,
    calendar_session_id: int = Form(...),
):
    require_login(request)
    await require_csrf(request)
    detail_before, _ = calendar_session_detail(calendar_session_id)

    if not hide_calendar_session(calendar_session_id):
        raise HTTPException(404, "予定が見つかりません")

    if detail_before and detail_before["event_date"]:
        try:
            d = date.fromisoformat(str(detail_before["event_date"]))
            return RedirectResponse(f"/calendar?month={d.strftime('%Y-%m')}", status_code=303)
        except ValueError:
            pass
    return RedirectResponse("/calendar", status_code=303)


@app.post("/calendar/delete")
async def calendar_delete(
    request: Request,
    calendar_session_id: int = Form(...),
):
    require_login(request)
    await require_csrf(request)
    detail_before, _ = calendar_session_detail(calendar_session_id)

    if not permanently_delete_calendar_session(
        calendar_session_id,
        iso_now(),
    ):
        raise HTTPException(404, "予定が見つかりません")

    if detail_before and detail_before["event_date"]:
        try:
            d = date.fromisoformat(str(detail_before["event_date"]))
            return RedirectResponse(f"/calendar?month={d.strftime('%Y-%m')}", status_code=303)
        except ValueError:
            pass
    return RedirectResponse("/calendar", status_code=303)



@app.post("/calendar/progress-set")
async def calendar_progress_set(
    request: Request,
    game_type: str = Form(...),
    scenario_name: str = Form(...),
    discord_id: str = Form(...),
    status: str = Form(""),
):
    require_login(request)
    await require_csrf(request)

    if not set_scenario_progress_status(
        game_type,
        scenario_name,
        discord_id,
        status,
        iso_now(),
    ):
        raise HTTPException(400, "通過状態を更新できません")

    return {"ok": True}



@app.get("/profile/{discord_id}", response_class=HTMLResponse)
async def profile_page(request: Request, discord_id: str):
    # v97永続補正: 起動イベントに依存せず、表示値を読む直前に今回の+1を保証する。
    try:
        if not temporary_v96_madamis_year_fix_done():
            result = apply_temporary_v96_madamis_year_fix(iso_now())
            print(f"[V97 FIX/profile] annual madamis +1: {result}", flush=True)
    except Exception as e:
        log_error("temporary_v96_madamis_year_fix_profile", e)
    data = profile_data(discord_id)
    if not data:
        raise HTTPException(404, "プロフィールが見つかりません")
    own = str(request.session.get("user_id") or "") == str(discord_id)
    member=data["member"]; total=data["total"]; eq=data.get("equipped")
    eq_rarity = str(eq.get("rarity") or "bronze") if eq else ""
    title_line = f"<div class='profile-equipped title-frame rarity-{esc(eq_rarity)}' data-detail='{esc(str(eq.get('context_label') or '') if eq and eq.get('achievement_key')=='pair_250' else '')}'><span>{esc(eq['title_name'])}</span></div>" if eq else ""
    if eq and eq.get("achievement_key")=="pair_250" and eq.get("context_label"):
        title_line = f"<button class='profile-equipped title-frame rarity-{esc(eq_rarity)} special' type='button' onclick=\"alert('{esc(str(eq['context_label']))}')\"><span>{esc(eq['title_name'])}</span></button>"
    years=data["years"]
    current_term=years[-1]["term"] if years else 1
    tabs=''.join(f"<button type='button' class='profile-year-tab {'active' if y['term']==current_term else ''}' onclick='switchProfileYear({y['term']},this)'>{y['term']}年目</button>" for y in years)
    panels=''.join(
        f"<div class='profile-year-panel {'active' if y['term']==current_term else ''}' data-term='{y['term']}'>"
        f"<div class='profile-total-wide'><b>{int(y['gm']) + int(y['pl'])}</b><span>卓数</span></div>"
        f"<div class='profile-grid'><div><b>{y['gm']}</b><span>GM</span></div><div><b>{y['pl']}</b><span>PL</span></div>"
        f"<div><b>{y['trpg']}</b><span>TRPG卓数</span></div><div><b>{y['madamis']}</b><span>マダミス卓数</span></div></div>"
        f"<div class='profile-year-range'>{y['year']}/6/1〜{y['year']+1}/5/31</div></div>" for y in years
    ) or "<div class='muted small'>まだ年度データがありません</div>"
    medals=['🥇','🥈','🥉']
    pair=''.join(
        f"<a class='profile-pair-row' href='/profile/{esc(x['discord_id'])}'><span>{medals[i]} {esc(x['display_name'])}</span><b>{x['n']}卓 ›</b></a>"
        for i,x in enumerate(data["pair_top"])
    ) or "<div class='muted small'>まだ同卓データがありません</div>"
    own_btn = "<a class='btn profile-title-list-btn' href='/profile/{}/titles'>称号一覧を見る</a>".format(esc(str(discord_id))) if own else ""

    # 自分のプロフィールでは、その下に登録メンバー全員のプロフィールを縦に表示する。
    people_markup = ""
    if own:
        members_all = [dict(x) for x in registered_members() if str(x["discord_id"]) != str(discord_id)]
        title_by_user = equipped_titles_map([x["discord_id"] for x in members_all])
        people_rows = []
        for person in members_all:
            pid = str(person.get("discord_id") or "")
            pname = str(person.get("display_name") or person.get("username") or pid)
            avatar = str(person.get("avatar_url") or "")
            pt = title_by_user.get(pid)
            ptitle = ""
            if pt:
                rarity = str(pt.get("rarity") or "bronze")
                ptitle = f"<span class='profile-list-title title-frame rarity-{esc(rarity)}'><span>{esc(str(pt.get('title_name') or ''))}</span></span>"
            people_rows.append(
                f"<a class='profile-list-row' href='/profile/{esc(pid)}'>"
                f"<img class='profile-list-avatar' src='{esc(avatar)}' alt=''>"
                f"<span class='profile-list-main'><b>{esc(pname)}</b>{ptitle}</span>"
                f"<span class='profile-list-chevron'>›</span></a>"
            )
        if people_rows:
            people_markup = "<section id='profile-list' class='profile-section profile-people'><h3>みんなのプロフィール</h3>" + "".join(people_rows) + "</section>"

    viewer_id = str(request.session.get("user_id") or "")
    if own:
        profile_back = "<a class='back-link' href='/calendar'>‹ カレンダーへ</a>"
    elif viewer_id and registered_member(viewer_id):
        profile_back = f"<a class='back-link' href='/profile/{esc(viewer_id)}#profile-list'>‹ プロフィール一覧へ戻る</a>"
    else:
        profile_back = "<a class='back-link' href='/calendar'>‹ カレンダーへ</a>"

    body=f"""
    <style>
      .profile-wrap{{max-width:620px;margin:0 auto}} .profile-hero{{text-align:center;margin:12px 0 20px}}
      .profile-avatar{{width:76px;height:76px;border-radius:50%;object-fit:cover;background:#202a38;border:2px solid #334155}}
      .profile-name{{font-size:1.45rem;font-weight:1000;margin-top:8px}}
      .title-frame{{--frame:#b8734b;--frame-soft:rgba(184,115,75,.16);position:relative;display:inline-flex;align-items:center;justify-content:center;min-width:172px;max-width:92%;min-height:38px;padding:7px 28px;border:1.5px solid var(--frame);border-radius:10px;background:linear-gradient(180deg,var(--frame-soft),rgba(7,11,17,.96));box-shadow:inset 0 0 0 1px rgba(255,255,255,.035),0 5px 18px rgba(0,0,0,.25);color:#f4eee8;font-weight:950;letter-spacing:.025em;box-sizing:border-box}}
      .title-frame::before,.title-frame::after{{content:'◆';position:absolute;top:50%;transform:translateY(-50%) rotate(45deg);font-size:.62rem;color:var(--frame);text-shadow:0 0 8px var(--frame-soft)}}
      .title-frame::before{{left:8px}} .title-frame::after{{right:8px}}
      .rarity-bronze{{--frame:#b8734b;--frame-soft:rgba(184,115,75,.18)}} .rarity-silver{{--frame:#c9d0da;--frame-soft:rgba(201,208,218,.13)}}
      .rarity-gold{{--frame:#e3b93f;--frame-soft:rgba(227,185,63,.18)}} .rarity-black{{--frame:#b8a77f;--frame-soft:rgba(184,167,127,.10)}}
      .rarity-gold{{border-width:2px;box-shadow:inset 0 0 0 1px rgba(255,220,120,.14),0 0 14px rgba(227,185,63,.12),0 6px 20px rgba(0,0,0,.3)}}
      .rarity-gold::before,.rarity-gold::after{{content:'✦';font-size:.84rem}}
      .rarity-black{{border-width:2px;background:linear-gradient(180deg,rgba(28,29,31,.98),rgba(5,7,10,.98));box-shadow:inset 0 0 0 1px rgba(210,195,154,.10),0 0 16px rgba(66,91,155,.12),0 6px 22px rgba(0,0,0,.38)}}
      .rarity-black::before,.rarity-black::after{{content:'❖';font-size:.8rem;color:#c3af7e}}
      .profile-equipped{{margin:7px auto 0;font-size:.66rem;min-width:0;min-height:25px;padding:4px 22px;border-radius:7px;background-color:transparent}} .profile-equipped.special{{cursor:pointer}}
      .profile-equipped::before{{left:6px}} .profile-equipped::after{{right:6px}}
      .profile-section{{margin-top:14px;padding:15px;border:1px solid #263244;border-radius:16px;background:#0d141e}}
      .profile-list-row{{display:flex;align-items:center;gap:11px;padding:11px 2px;border-bottom:1px solid #202b39;color:#e8edf5;text-decoration:none}}
      .profile-list-row:last-child{{border-bottom:0}} .profile-list-avatar{{width:44px;height:44px;border-radius:50%;object-fit:cover;background:#202a38;border:1px solid #334155;flex:0 0 auto}}
      .profile-list-main{{min-width:0;display:flex;flex:1;flex-direction:column;align-items:flex-start;gap:4px}} .profile-list-main>b{{font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}}
      .profile-list-title{{font-size:.55rem;min-width:0;min-height:21px;padding:3px 18px;border-radius:6px;max-width:100%}} .profile-list-title::before{{left:5px;font-size:.32rem}} .profile-list-title::after{{right:5px;font-size:.32rem}}
      .profile-list-chevron{{color:#7f8da0;font-size:1.2rem;font-weight:900;margin-left:auto}}
      .profile-section h3{{margin:0 0 14px;font-size:.92rem}}
      .profile-total-wide{{width:100%;box-sizing:border-box;min-height:116px;padding:18px 14px;margin-bottom:10px;border-radius:14px;background:#111b28;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center}}
      .profile-total-wide b{{font-size:1.95rem;line-height:1;font-weight:1000;color:#f2f5fa;font-variant-numeric:tabular-nums}}
      .profile-total-wide span{{color:#8c99ab;font-size:.76rem;font-weight:800;line-height:1.2}}
      .profile-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
      .profile-grid>div{{min-height:68px;padding:10px 8px;border-radius:12px;background:#111b28;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;text-align:center}}
      .profile-grid b{{font-size:1.38rem;line-height:1;font-weight:1000;color:#f2f5fa;font-variant-numeric:tabular-nums}}
      .profile-grid span{{color:#8c99ab;font-size:.68rem;font-weight:800;line-height:1.25}}
      .profile-year-tabs{{display:flex;gap:7px;overflow:auto;margin-bottom:12px}}
      .profile-year-tab{{border:1px solid #334155;background:#111925;color:#aeb9c7;border-radius:10px;padding:7px 11px;font-weight:850;white-space:nowrap}}
      .profile-year-tab.active{{background:#5865f2;color:white;border-color:#5865f2}} .profile-year-panel{{display:none}} .profile-year-panel.active{{display:block}}
      .profile-year-range{{text-align:right;color:#687689;font-size:.62rem;margin-top:7px}} .profile-pair-row{{display:flex;justify-content:space-between;align-items:center;text-decoration:none;color:#e8edf5;padding:11px 3px;border-bottom:1px solid #202b39}}
      .profile-pair-row:last-child{{border-bottom:0}} .profile-pair-row b{{font-size:.75rem;color:#95a4b6}} .profile-title-list-btn{{margin-top:16px;width:100%;box-sizing:border-box;text-align:center}}
    </style>
    {profile_back}
    <div class='profile-wrap'>
      <div class='profile-hero'>
        <img class='profile-avatar' src='{esc(member.get("avatar_url") or "")}' alt=''>
        <div class='profile-name'>{esc(member.get("display_name") or member.get("username") or discord_id)}</div>
        {title_line}
      </div>
      <section class='profile-section'><h3>累計</h3>
        <div class='profile-total-wide'><b>{int(total['gm']) + int(total['pl'])}</b><span>累計卓数</span></div>
        <div class='profile-grid'>
          <div><b>{total['gm']}</b><span>GM</span></div><div><b>{total['pl']}</b><span>PL</span></div>
          <div><b>{total['trpg']}</b><span>TRPG卓数</span></div><div><b>{total['madamis']}</b><span>マダミス卓数</span></div>
        </div>
      </section>
      <section class='profile-section'><h3>年度別</h3><div class='profile-year-tabs'>{tabs}</div>{panels}</section>
      <section class='profile-section'><h3>🤝 同卓した数TOP3</h3>{pair}</section>
      {own_btn}
      {people_markup}
    </div>
    <script>function switchProfileYear(term,btn){{document.querySelectorAll('.profile-year-panel').forEach(x=>x.classList.toggle('active',x.dataset.term===String(term)));document.querySelectorAll('.profile-year-tab').forEach(x=>x.classList.toggle('active',x===btn));}}</script>
    """
    return page("プロフィール", body, request)


@app.get("/profile/{discord_id}/titles", response_class=HTMLResponse)
async def profile_titles_page(request: Request, discord_id: str):
    uid=require_login(request)
    if str(uid)!=str(discord_id):
        raise HTTPException(403, "自分の称号一覧のみ確認できます")
    data=profile_data(discord_id)
    if not data: raise HTTPException(404,"プロフィールが見つかりません")
    cards=achievement_collection(discord_id)
    equipped=data.get("equipped")
    out=[]
    for item in cards:
        d=item["definition"]; u=item["unlock"]; secret=bool(d.get("secret")); unlocked=bool(u)
        rarity_key=str(d.get("rarity") or "bronze")
        if secret and not unlocked:
            out.append(f"<div class='title-card title-rarity-black locked secret'><div class='title-card-main'><div class='title-badge title-frame rarity-black'><span>🔒 ？？？？？？？</span></div><div class='title-cond'>？？？？？？？？？</div></div></div>")
            continue
        name=str(u["title_name"] if u else d.get("name") or "")
        selected=bool(equipped and u and int(equipped["id"])==int(u["id"]))
        # 自分の一覧では、シークレットも解除後だけ条件を確認できる。
        # 未解除シークレットは上の分岐で「？？？」のまま。
        cond=str(d.get("condition") or "")
        progress=""
        if not secret:
            value=int(item.get("value") or 0); target=int(d.get("target") or 1); pct=max(0,min(100,int(value*100/target)))
            progress=f"<div class='title-progress-text'>{min(value,target)} / {target}</div><div class='title-progress'><i style='width:{pct}%'></i></div>"
        detail=""
        if u and str(u.get("achievement_key"))=="pair_250" and u.get("context_label"):
            detail=f"<div class='title-special-detail'>{esc(str(u['context_label']))}</div>"
        radio=f"<input type='radio' name='unlock_id' value='{u['id']}' {'checked' if selected else ''}>" if u else ""
        out.append(f"<label class='title-card title-rarity-{esc(rarity_key)} {'unlocked' if unlocked else 'locked'}'>{radio}<div class='title-card-main'><div class='title-badge title-frame rarity-{esc(rarity_key)}'><span>{esc(name)}</span></div>{detail}<div class='title-cond'>{esc(cond)}</div>{progress}</div></label>")
    body=f"""
    <style>
      .titles-wrap{{max-width:620px;margin:0 auto}} .titles-head{{text-align:center;margin-bottom:14px}} .titles-head h2{{margin:0 0 5px}}
      .titles-grid{{display:grid;grid-template-columns:1fr;gap:11px}} .title-card{{display:flex;gap:10px;padding:13px;border-radius:14px;border:1px solid #293447;background:linear-gradient(180deg,#111a26,#0d141e);color:#e9eef5;transition:.16s ease}}
      .title-card:has(input:checked){{border-color:#35c77a;box-shadow:0 0 0 1px rgba(53,199,122,.28),0 0 18px rgba(53,199,122,.08)}}
      .title-card.locked{{opacity:.46;filter:saturate(.55)}} .title-card.secret.locked{{opacity:.58}} .title-card input{{margin-top:12px;accent-color:#34d17b}}
      .title-card-main{{min-width:0;flex:1}} .title-badge{{width:100%;max-width:100%;min-height:42px;margin:0 0 8px;font-size:.9rem}}
      .title-frame{{--frame:#b8734b;--frame-soft:rgba(184,115,75,.16);position:relative;display:inline-flex;align-items:center;justify-content:center;padding:8px 30px;border:1.5px solid var(--frame);border-radius:10px;background:linear-gradient(180deg,var(--frame-soft),rgba(7,11,17,.96));box-shadow:inset 0 0 0 1px rgba(255,255,255,.035),0 4px 15px rgba(0,0,0,.25);color:#f2f0ec;font-weight:950;letter-spacing:.025em;box-sizing:border-box}}
      .title-frame::before,.title-frame::after{{content:'◆';position:absolute;top:50%;transform:translateY(-50%) rotate(45deg);font-size:.62rem;color:var(--frame)}} .title-frame::before{{left:9px}} .title-frame::after{{right:9px}}
      .rarity-bronze{{--frame:#b8734b;--frame-soft:rgba(184,115,75,.18)}} .rarity-silver{{--frame:#c9d0da;--frame-soft:rgba(201,208,218,.13)}} .rarity-gold{{--frame:#e3b93f;--frame-soft:rgba(227,185,63,.18)}} .rarity-black{{--frame:#b8a77f;--frame-soft:rgba(184,167,127,.10)}}
      .rarity-gold{{border-width:2px;box-shadow:inset 0 0 0 1px rgba(255,220,120,.14),0 0 13px rgba(227,185,63,.12),0 5px 18px rgba(0,0,0,.3)}} .rarity-gold::before,.rarity-gold::after{{content:'✦';font-size:.84rem}}
      .rarity-black{{border-width:2px;background:linear-gradient(180deg,rgba(27,29,33,.99),rgba(4,6,9,.99));box-shadow:inset 0 0 0 1px rgba(210,195,154,.10),0 0 16px rgba(67,88,145,.13),0 6px 20px rgba(0,0,0,.38)}} .rarity-black::before,.rarity-black::after{{content:'❖';font-size:.8rem;color:#c3af7e}}
      .title-cond{{font-size:.72rem;color:#93a1b3;margin-top:5px;padding:0 2px}}
      .title-progress-text{{font-size:.65rem;color:#8290a2;text-align:right;margin-top:8px}} .title-progress{{height:7px;border-radius:99px;background:#263142;overflow:hidden;margin-top:3px}}
      .title-progress i{{display:block;height:100%;background:#5865f2;border-radius:99px}} .title-special-detail{{font-size:.72rem;color:#c7b6e8;margin:4px 2px 0}}
      .title-actions{{position:sticky;bottom:8px;margin-top:14px;padding:10px;border:1px solid #2b384a;border-radius:14px;background:rgba(10,16,25,.96);backdrop-filter:blur(8px)}}
      .title-equip-btn{{width:100%;background:#20b66d!important}} .title-remove-btn{{display:block;width:100%;margin-top:7px;border:0;background:transparent;color:#7f8da0;font-size:.7rem;padding:7px;cursor:pointer}}
    </style>
    <a class='back-link' href='/profile/{esc(discord_id)}'>‹ プロフィールへ</a>
    <div class='titles-wrap'><div class='titles-head'><h2>🏷️ 称号一覧</h2><div class='muted small'>解除済みの称号を選んで装備できます</div></div>
      <form method='post' action='/profile/{esc(discord_id)}/title-equip'>
        {csrf_field(request)}<div class='titles-grid'>{''.join(out)}</div>
        <div class='title-actions'><button class='btn green title-equip-btn' type='submit' id='equipTitleBtn' disabled>称号を変更する</button>
        <button class='title-remove-btn' type='submit' name='remove' value='1' formnovalidate>称号を外す</button></div>
      </form>
    </div>
    <script>const eb=document.getElementById('equipTitleBtn');document.querySelectorAll("input[name='unlock_id']").forEach(r=>r.addEventListener('change',()=>eb.disabled=false));</script>
    """
    return page("称号一覧",body,request)


@app.post("/profile/{discord_id}/title-equip")
async def profile_title_equip(request: Request, discord_id: str, unlock_id: str = Form(""), remove: str = Form("")):
    uid=require_login(request); await require_csrf(request)
    if str(uid)!=str(discord_id): raise HTTPException(403,"自分の称号のみ変更できます")
    if remove=="1":
        set_equipped_title(uid,None,iso_now())
    else:
        if not unlock_id.isdigit() or not set_equipped_title(uid,int(unlock_id),iso_now()):
            raise HTTPException(400,"装備できない称号です")
    return RedirectResponse(f"/profile/{uid}",status_code=303)


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
                          AND a.discord_id<>r.gm_discord_id
                      ) AS answered_count,
                      (
                        SELECT COUNT(*)
                        FROM sessions s
                        WHERE s.recruitment_id=r.id
                      ) AS session_count
               FROM recruitments r
               LEFT JOIN users u ON u.discord_id=r.gm_discord_id
               WHERE r.created_at >= ?
                 AND (
                   r.gm_discord_id=?
                   OR EXISTS (
                     SELECT 1 FROM members mine
                     WHERE mine.recruitment_id=r.id
                       AND mine.discord_id=?
                       AND mine.member_type='participant'
                       AND mine.active=1
                   )
                 )
               ORDER BY r.id DESC
               LIMIT 100""",
            (cutoff, str(uid), str(uid)),
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

        is_gm = str(uid) == str(r["gm_discord_id"])
        can_delete = is_gm or str(uid) == DEVELOPER_USER_ID

        if can_delete:
            if is_simple_schedule(r):
                delete_confirm = "この日程調整を削除しますか？"
            elif is_gm:
                delete_confirm = "この募集を削除しますか？\\n※削除は作成者のみ行えます"
            else:
                delete_confirm = "この募集を削除しますか？\\n※開発者テスト権限で削除します"
            delete_label = "日程調整を削除" if is_simple_schedule(r) else "募集を削除"
            menu = f"""
              <details class='table-menu'>
                <summary aria-label='募集メニュー'>⋮</summary>
                <form method='post' action='/r/{r["id"]}/delete'
                      onsubmit="return confirm('{delete_confirm}');">
                  {csrf_field(request)}
                  <button type='submit' class='delete-table-btn'>{delete_label}</button>
                </form>
              </details>
            """
        else:
            menu = "<div class='kebab kebab-outside'>⋮</div>"

        cards.append(
            f"""
            <div class='session-card-wrap'>
              <a class='session-card' href='/r/{r["id"]}'>
                {badge}
                <div class='session-card-title'>{esc(r["scenario_name"])}</div>
                <div class='session-card-meta'>
                  GM: {esc(r["gm_name"] or r["gm_discord_id"])}
                  &nbsp;/&nbsp; {int(r["answered_count"])}人回答
                </div>
              </a>
              {menu}
            </div>
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


@app.get("/schedule/new", response_class=HTMLResponse)
async def simple_schedule_form(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/schedule/new")

    channels = await visible_sendable_channels_for_user(str(uid))
    options = "".join(
        f"<option value='{x.id}'>【{esc(x.category.name if x.category else 'その他')}】{esc(x.name)}</option>"
        for x in channels
    ) or "<option value=''>送信可能なチャンネルがありません</option>"

    submission_token = secrets.token_urlsafe(32)
    default_deadline = (now_jst().date() + timedelta(days=7)).isoformat()
    days = month_dates()
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"]
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
        "日程調整",
        f"""
        <a class='back-link' href='/'>‹ 戻る</a>
        <div class='section-title' style='text-align:center'>日程調整</div>

        <form id='simpleScheduleCreateForm' class='form-shell' method='post' action='/schedule/new'>
          {csrf_field(request)}
          <input type='hidden' name='submission_token' value='{submission_token}'>

          <div class='form-section compact'>
            <label class='field'>
              <div class='field-box no-icon'>
                <div class='field-stack'>
                  <span class='field-label'>イベント名</span>
                  <input name='event_name' placeholder='例：夏のボドゲ会' required>
                </div>
              </div>
            </label>

            <label class='checkbox-row' style='margin-top:10px;margin-bottom:8px'>
              <input type='checkbox' name='calendar_visible' value='1'>
              カレンダーに掲載する
            </label>

            <div class='field-row'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>開始時間（任意）</span>
                    <div style='display:flex;align-items:center;gap:8px'>
                      <input id='simpleStartTime' type='time' name='start_time' value='21:00' style='flex:1'>
                      <button type='button' class='btn alt' style='width:auto;min-width:48px;padding:6px 9px;font-size:12px;line-height:1.1;flex:none' onclick="document.getElementById('simpleStartTime').value=''">削除</button>
                    </div>
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

            <label class='checkbox-row'>
              <input type='checkbox' id='pc' name='player_count_enabled' value='1' onchange='tp()'>
              募集人数を設定する
            </label>

            <div id='pf' style='display:none;margin-top:12px'>
              <label class='field'>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>募集人数</span>
                    <input id='fp' type='number' min='1' name='fixed_players' value='4'>
                  </div>
                </div>
              </label>

              <label class='checkbox-row'>
                <input type='checkbox' id='vp2' name='variable_players' value='1' onchange='tv()'>
                人数を可変にする
              </label>

              <div id='vr2' class='field-row' style='display:none'>
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
          </div>

          <div class='form-section'>
            <div class='form-section-title'>日程調整</div>
            <div class='create-date-heading'>開催候補日を選択（今月と来月末まで）</div>
            <input type='hidden' id='gm_dates' name='gm_dates'>
            <div class='date-scroll'><div class='grid'>{day_html}</div></div>
            <div class='legend'>
              <span><b style='color:#22c55e'>○</b> 開催できる</span>
              <span><b>-</b> 開催できない</span>
            </div>
            {advanced_schedule_controls_html()}
          </div>

          <div class='form-section'>
            <div class='form-section-title'>Discordへ送信</div>
            <label class='field'>
              <div class='field-box no-icon'>
                <div class='field-stack'>
                  <span class='field-label' style='margin-bottom:8px'>送信先チャンネル</span>
                  <select name='channel_id' style='padding-top:14px;padding-bottom:10px' required>{options}</select>
                </div>
              </div>
            </label>
            <p class='muted small'>募集掲示板・イベント・卓一覧・未定卓・つぶ活からあなたが閲覧できるチャンネルのみ表示</p>
          </div>

          <button id='simpleScheduleSubmitBtn' class='submit-btn' type='submit'>Discordへ送信して日程調整を作成</button>
        </form>

        <script>
        let selected=[];
        function toggleGM(el){{
          const d=el.dataset.date;
          const s=el.querySelector('.state');
          if(selected.includes(d)){{
            selected=selected.filter(x=>x!==d);
            el.classList.remove('yes');
            s.textContent='-';
          }}else{{
            selected.push(d);
            el.classList.add('yes');
            s.textContent='○';
          }}
          document.getElementById('gm_dates').value=selected.join(',');
          refreshAdvancedSlots();
        }}
        function tp(){{
          document.getElementById('pf').style.display=document.getElementById('pc').checked?'block':'none';
        }}
        function tv(){{
          const x=document.getElementById('vp2').checked;
          document.getElementById('vr2').style.display=x?'grid':'none';
          document.getElementById('fp').disabled=x;
        }}

        // 二重送信防止
        const simpleForm=document.getElementById('simpleScheduleCreateForm');
        const simpleSubmitBtn=document.getElementById('simpleScheduleSubmitBtn');

        if(simpleForm && simpleSubmitBtn){{
          simpleForm.addEventListener('submit',function(e){{
            // すでに1度送信処理に入っていれば、2回目以降は止める
            if(simpleForm.dataset.submitting==='1'){{
              e.preventDefault();
              return false;
            }}

            simpleForm.dataset.submitting='1';
            simpleSubmitBtn.disabled=true;
            simpleSubmitBtn.textContent='送信中…';
            simpleSubmitBtn.style.opacity='.65';
            simpleSubmitBtn.style.cursor='wait';
            simpleSubmitBtn.style.pointerEvents='none';
          }});
        }}
        </script>
        """,
        request,
    )


@app.post("/schedule/new")
async def simple_schedule_submit(
    request: Request,
    event_name: str = Form(...),
    start_time: str = Form("21:00"),
    deadline_date: str = Form(...),
    gm_dates: str = Form(...),
    channel_id: str = Form(...),
    player_count_enabled: Optional[str] = Form(None),
    variable_players: Optional[str] = Form(None),
    fixed_players: int = Form(4),
    min_players: int = Form(2),
    max_players: int = Form(4),
    calendar_visible: Optional[str] = Form(None),
    submission_token: str = Form(...),
):
    uid = require_login(request)
    await require_csrf(request)

    if not claim_submission_once(submission_token):
        return page(
            "送信済み",
            "<div class='card' style='text-align:center'>"
            "<h2>この日程調整はすでに送信されています</h2>"
            "<p class='muted'>連続送信は防止されました。</p>"
            "<a class='btn alt' style='display:flex;justify-content:center;text-align:center;margin-top:18px' href='/join'>一覧へ戻る</a>"
            "</div>",
            request,
        )

    dates = sorted({x for x in gm_dates.split(',') if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x)})
    if not dates:
        raise HTTPException(400, "開催可能日を1日以上選んでください")
    adv_form = await request.form()
    per_day_time = str(adv_form.get('per_day_time') or '') == '1'
    schedule_slots = parse_schedule_slots(dates, start_time, per_day_time, str(adv_form.get('schedule_slots_json') or ''))

    allowed = {str(x.id): x for x in await visible_sendable_channels_for_user(str(uid))}
    ch = allowed.get(str(channel_id))
    if not ch:
        raise HTTPException(403, "このDiscordチャンネルは選択できません")

    if player_count_enabled:
        if variable_players:
            mn, mx, var, target = min_players, max_players, 1, min_players
        else:
            mn = mx = fixed_players
            var = 0
            target = fixed_players
        if mn < 1 or mx < mn:
            raise HTTPException(400, "募集人数が不正です")
    else:
        mn, mx, var, target = 1, 99, 1, None

    deadline = datetime.fromisoformat(deadline_date + "T21:00:00").replace(tzinfo=JST)
    sv = start_time.strip() or "未定"
    creator_name = user_display(str(uid))

    with db() as c:
        cur = c.execute(
            """INSERT INTO recruitments(game_type,scenario_name,gm_discord_id,min_players,max_players,variable_players,play_time,description,guide_message,image_path,start_time,deadline,status,schedule_pending,created_at,simple_schedule,target_players,gm_name_override,target_channel_id,waiting_channel_id,calendar_visible) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("EVENT", event_name.strip(), str(uid), mn, mx, var, "", "", "", None, sv, deadline.isoformat(), "RECRUITING", 0, iso_now(), 1, target, "", str(ch.id), str(ch.id), 1 if calendar_visible else 0),
        )
        rid = cur.lastrowid
        c.executemany(
            "INSERT INTO gm_dates(recruitment_id,event_date) VALUES(?,?)",
            [(rid, x) for x in dates],
        )
    set_recruitment_schedule_slots(rid, schedule_slots, per_day_time)

    guild = bot.get_guild(GUILD_ID)
    targets = []
    if guild:
        for member in guild.members:
            if member.bot or str(member.id) == str(uid):
                continue
            try:
                if not ch.permissions_for(member).view_channel:
                    continue

                # Administrator権限だけで全チャンネルが見えている運営者は、
                # 明示的にこのチャンネルへ参加している場合だけ回答対象にする。
                if member.guild_permissions.administrator:
                    explicit = ch.overwrites_for(member).view_channel is True
                    if not explicit:
                        explicit = any(
                            ch.overwrites_for(role).view_channel is True
                            for role in member.roles
                        )
                    if not explicit:
                        continue
                targets.append(str(member.id))
            except Exception:
                pass

    with db() as c:
        c.executemany(
            "INSERT OR IGNORE INTO members(recruitment_id,discord_id,member_type,active,joined_at) VALUES(?,?,?,?,?)",
            [(rid, x, "participant", 1, iso_now()) for x in targets],
        )

    time_line = f"開始時間：**{sv}**\n" if start_time.strip() else ""
    players_line = ""
    if target:
        players_line = f"募集人数：**{mn}〜{mx}人**\n" if var else f"募集人数：**{mn}人**\n"

    sent = await ch.send(
        f"**{event_name.strip()}の日程調整**\n"
        f"作成者：**{creator_name}**\n"
        f"{time_line}{players_line}"
        f"回答期限：**{deadline.strftime('%Y/%m/%d')}**\n\n"
        f"以下のURLよりご回答ください！\n"
        f"{BASE_URL}/r/{rid}"
    )

    with db() as c:
        c.execute(
            "UPDATE recruitments SET target_message_id=? WHERE id=?",
            (str(sent.id), rid),
        )

    url = f"{BASE_URL}/r/{rid}"
    return page(
        "日程調整を作成しました",
        f"""<div class='card'><h2>📅 日程調整を作成しました</h2><p>チャンネル【<b>{esc(ch.name)}</b>】へ送信しました！</p><div class='copy-card'><div class='copy-url' id='answerUrl'>{esc(url)}</div><button type='button' class='copy-btn' onclick='copyAnswerUrl()'>URLコピー</button></div><p class='muted small'>回答対象：{len(targets)}人</p><a class='btn green' style='display:flex;justify-content:center;text-align:center' href='/r/{rid}'>日程調整ページを開く</a></div><script>async function copyAnswerUrl(){{const u=document.getElementById('answerUrl').textContent.trim();try{{await navigator.clipboard.writeText(u);const b=document.querySelector('.copy-btn');const old=b.textContent;b.textContent='コピーしました';setTimeout(()=>b.textContent=old,1300)}}catch(e){{window.prompt('このURLをコピーしてください',u)}}}}</script>""",
        request,
    )


@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/new")

    submission_token = secrets.token_urlsafe(32)
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

        <form id='recruitmentCreateForm' class='form-shell' action='/new' method='post' enctype='multipart/form-data'>
          {csrf_field(request)}
          <input type='hidden' name='submission_token' value='{submission_token}'>

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
                  <span class='field-label'>関連画像（任意・最大10枚）</span>
                  <input type='file' name='images' accept='image/*' multiple>
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

          <label class='checkbox-row' style='margin-top:18px'>
            <input type='checkbox'
                   id='schedule_later'
                   name='schedule_later'
                   value='1'
                   onchange='toggleScheduleLater()'>
            日程調整を後日行う
          </label>

          <div id='scheduleFields' class='form-section'>
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
            {advanced_schedule_controls_html()}
          </div>

          <button id='recruitmentSubmitBtn' class='submit-btn' type='submit'>卓を作成する</button>
        </form>

        <script>
        function vp(){{
          const checked=document.getElementById('variable').checked;
          document.getElementById('range').style.display=checked?'grid':'none';
          document.getElementById('fixed_players').disabled=checked;
        }}

        function toggleScheduleLater(){{
          const checked=document.getElementById('schedule_later').checked;
          const fields=document.getElementById('scheduleFields');
          const deadline=document.querySelector('[name="deadline_date"]');

          fields.style.display=checked?'none':'block';

          if(deadline){{
            deadline.required=!checked;
          }}
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
          refreshAdvancedSlots();
        }}

        // 二重送信防止（画面側）
        const recruitmentForm=document.getElementById('recruitmentCreateForm');
        const recruitmentSubmitBtn=document.getElementById('recruitmentSubmitBtn');

        if(recruitmentForm && recruitmentSubmitBtn){{
          recruitmentForm.addEventListener('submit',function(e){{
            if(recruitmentForm.dataset.submitting==='1'){{
              e.preventDefault();
              return false;
            }}
            recruitmentForm.dataset.submitting='1';
            recruitmentSubmitBtn.disabled=true;
            recruitmentSubmitBtn.textContent='送信中…';
            recruitmentSubmitBtn.style.opacity='.65';
            recruitmentSubmitBtn.style.cursor='wait';
            recruitmentSubmitBtn.style.pointerEvents='none';
          }});
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
    start_time: str = Form("21:00"), deadline_date: str = Form(""), gm_dates: str = Form(""),
    schedule_later: Optional[str] = Form(None),
    submission_token: str = Form(...),
    images: list[UploadFile] = File(default=[]),
):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login?next=/new", status_code=303)
    await require_csrf(request)

    if not claim_submission_once(submission_token):
        return page(
            "送信済み",
            "<div class='card' style='text-align:center'>"
            "<h2>この募集はすでに送信されています</h2>"
            "<p class='muted'>連続送信は防止されました。</p>"
            "<a class='btn alt' style='display:flex;justify-content:center;text-align:center;margin-top:18px' href='/join'>募集一覧へ戻る</a>"
            "</div>",
            request,
        )
    if variable_players:
        mn, mx, var = min_players, max_players, 1
    else:
        mn = mx = fixed_players
        var = 0
    if mn < 1 or mx < mn:
        raise HTTPException(400, "募集人数が不正です")
    pending_schedule = bool(schedule_later)

    dates = sorted({
        d for d in gm_dates.split(",")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
    })

    if not pending_schedule and not dates:
        raise HTTPException(
            400,
            "開催可能日を1日以上選んでください"
        )
    adv_form = await request.form()
    per_day_time = str(adv_form.get('per_day_time') or '') == '1'
    schedule_slots = parse_schedule_slots(dates, start_time, per_day_time, str(adv_form.get('schedule_slots_json') or '')) if not pending_schedule else []

    if not deadline_date:
        deadline_date = (
            now_jst().date() + timedelta(days=7)
        ).isoformat()

    deadline = datetime.fromisoformat(
        deadline_date + "T21:00:00"
    ).replace(tzinfo=JST)

    initial_status = (
        "SCHEDULE_PENDING"
        if pending_schedule
        else "RECRUITING"
    )
    saved_images = []
    uploads = [x for x in (images or []) if x and x.filename]
    if len(uploads) > 10:
        raise HTTPException(400, "画像は最大10枚までです")

    for image in uploads:
        content = await image.read()
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(400, "画像は1枚8MB以下にしてください")

        real_ext = sniff_image_ext(content[:16])
        if not real_ext:
            raise HTTPException(
                400,
                "画像ファイル（PNG/JPG/GIF/WEBP）を選択してください",
            )

        image_path = str(
            UPLOAD_DIR / f"{uuid.uuid4().hex}{real_ext}"
        )
        Path(image_path).write_bytes(content)
        saved_images.append(image_path)

    # 旧コード互換用に1枚目だけimage_pathにも保持
    image_path = saved_images[0] if saved_images else None
    with db() as c:
        cur = c.execute(
            """INSERT INTO recruitments(
                   game_type,scenario_name,gm_discord_id,
                   min_players,max_players,variable_players,
                   play_time,description,guide_message,image_path,
                   start_time,deadline,status,schedule_pending,created_at
               )
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                game_type,
                scenario_name.strip(),
                str(uid),
                mn,
                mx,
                var,
                play_time.strip(),
                description.strip(),
                guide_message.strip(),
                image_path,
                start_time,
                deadline.isoformat(),
                initial_status,
                1 if pending_schedule else 0,
                iso_now(),
            ),
        )
        rid = cur.lastrowid
        c.executemany("INSERT INTO gm_dates(recruitment_id,event_date) VALUES(?,?)", [(rid, d) for d in dates])
        if saved_images:
            c.executemany(
                """INSERT OR IGNORE INTO recruitment_images(
                       recruitment_id,image_path,sort_order
                   ) VALUES(?,?,?)""",
                [(rid, p, i) for i, p in enumerate(saved_images)],
            )
    if not pending_schedule:
        set_recruitment_schedule_slots(rid, schedule_slots, per_day_time)
    try:
        await create_waiting_channel(rid)
        await post_recruitment(rid)
    except Exception as e:
        log_error(f"new_submit rid={rid}", e)
        with db() as c:
            c.execute("UPDATE recruitments SET status='ERROR' WHERE id=?", (rid,))
        return page("エラー", "<div class='card'><h2>Discordへの作成中にエラーが発生しました</h2><p class='warn'>DiscordのID・権限・絵文字IDを確認してください。詳細はサーバーログをご確認ください。</p></div>", request)
    return RedirectResponse(f"/r/{rid}", status_code=303)



@app.post("/r/{rid}/delete")
async def delete_recruitment(rid: int, request: Request):
    uid = require_login(request)
    await require_csrf(request)

    r = get_recruitment(rid)
    if not r:
        raise HTTPException(404)

    if str(uid) != str(r["gm_discord_id"]) and str(uid) != DEVELOPER_USER_ID:
        raise HTTPException(403, "募集を削除できるのはGM本人だけです")

    # この募集と、そこから派生した再日程調整をすべて収集
    ids = [rid]

    with db() as c:
        i = 0
        while i < len(ids):
            children = c.execute(
                "SELECT id FROM recruitments WHERE parent_id=?",
                (ids[i],),
            ).fetchall()

            for child in children:
                cid = int(child["id"])
                if cid not in ids:
                    ids.append(cid)

            i += 1

        placeholders = ",".join("?" for _ in ids)

        targets = c.execute(
            f"""SELECT id,
                       recruitment_channel_id,
                       recruitment_message_id,
                       waiting_channel_id,
                       simple_schedule,
                       target_channel_id,
                       target_message_id
                FROM recruitments
                WHERE id IN ({placeholders})""",
            tuple(ids),
        ).fetchall()

        image_rows = c.execute(
            f"""SELECT image_path
                FROM recruitment_images
                WHERE recruitment_id IN ({placeholders})""",
            tuple(ids),
        ).fetchall()

        session_rows = c.execute(
            f"""SELECT id
                FROM sessions
                WHERE recruitment_id IN ({placeholders})""",
            tuple(ids),
        ).fetchall()

    # --------------------------------------------------------
    # Discord側を削除
    # --------------------------------------------------------
    guild = bot.get_guild(GUILD_ID)

    if guild:
        # 簡単日程調整は、Botが送ったメッセージだけ削除する。
        # 既存Discordチャンネルの削除・移動・カテゴリ変更は絶対に行わない。
        for x in targets:
            if not int(x["simple_schedule"] or 0):
                continue
            if not (x["target_channel_id"] and x["target_message_id"]):
                continue
            try:
                ch = guild.get_channel(int(x["target_channel_id"]))
                if not ch:
                    ch = await guild.fetch_channel(int(x["target_channel_id"]))
                msg = await ch.fetch_message(int(x["target_message_id"]))
                await msg.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                log_error(f"delete simple schedule message rid={x['id']}", e)

        # 募集板の投稿
        for x in targets:
            if int(x["simple_schedule"] or 0):
                continue
            if not (
                x["recruitment_channel_id"]
                and x["recruitment_message_id"]
            ):
                continue

            try:
                channel_id = int(x["recruitment_channel_id"])
                message_id = int(x["recruitment_message_id"])

                ch = guild.get_channel(channel_id)
                if not ch:
                    ch = await guild.fetch_channel(channel_id)

                msg = await ch.fetch_message(message_id)
                await msg.delete()

            except discord.NotFound:
                pass
            except Exception as e:
                log_error(
                    f"delete recruitment message rid={x['id']}",
                    e,
                )

        # Discordチャンネルは絶対に削除しない。
        # このBotが行うチャンネル操作は作成のみ。

    # --------------------------------------------------------
    # DB側を削除
    # --------------------------------------------------------
    with db() as c:
        placeholders = ",".join("?" for _ in ids)

        session_ids = [
            int(x["id"])
            for x in session_rows
        ]

        if session_ids:
            s_marks = ",".join("?" for _ in session_ids)

            c.execute(
                f"""DELETE FROM session_members
                    WHERE session_id IN ({s_marks})""",
                tuple(session_ids),
            )

            c.execute(
                f"""DELETE FROM sessions
                    WHERE id IN ({s_marks})""",
                tuple(session_ids),
            )

        # 子テーブル
        for table in (
            "answers",
            "comments",
            "gm_dates",
            "members",
            "recruitment_images",
        ):
            c.execute(
                f"""DELETE FROM {table}
                    WHERE recruitment_id IN ({placeholders})""",
                tuple(ids),
            )

        # 子募集→親募集の順で削除
        for target_id in reversed(ids):
            c.execute(
                "DELETE FROM recruitments WHERE id=?",
                (target_id,),
            )

    # --------------------------------------------------------
    # DBから参照されなくなった画像ファイルを削除
    # --------------------------------------------------------
    candidate_paths = {
        str(x["image_path"])
        for x in image_rows
        if x["image_path"]
    }

    with db() as c:
        for path_str in candidate_paths:
            still_multi = c.execute(
                """SELECT 1
                   FROM recruitment_images
                   WHERE image_path=?
                   LIMIT 1""",
                (path_str,),
            ).fetchone()

            still_legacy = c.execute(
                """SELECT 1
                   FROM recruitments
                   WHERE image_path=?
                   LIMIT 1""",
                (path_str,),
            ).fetchone()

            if still_multi or still_legacy:
                continue

            try:
                p = Path(path_str)

                if p.exists() and UPLOAD_DIR in p.parents:
                    p.unlink()

            except Exception as e:
                log_error(
                    f"delete image rid={rid}",
                    e,
                )

    print(
        f"[DELETE] recruitment rid={rid} ids={ids}",
        flush=True,
    )

    return RedirectResponse("/join", status_code=303)


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

    # 簡単日程調整は、Discordログイン済みでGM本人ではないユーザーなら
    # 作成時の回答対象に含まれていなくても回答できる。
    # 未登録ユーザーは回答保存時にparticipantへ自動追加する。
    simple_open_answer = is_simple_schedule(r) and not gm
    can_answer = participant or simple_open_answer

    spectator = is_active_member(rid, uid, "spectator")
    dates = get_gm_dates(rid)
    per_day_time, schedule_slots = recruitment_schedule_slots(rid)
    schedule_pending = bool(
        int(r["schedule_pending"] or 0)
    )

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

        # 回答状況には「実際に回答を保存した参加者」だけ表示する。
        # 作成時に回答対象として登録されていても、未回答者は一覧へ出さない。
        active = c.execute(
            """SELECT m.discord_id
               FROM members m
               JOIN answer_submissions s
                 ON s.recruitment_id=m.recruitment_id
                AND s.discord_id=m.discord_id
               WHERE m.recruitment_id=?
                 AND m.member_type='participant'
                 AND m.active=1
                 AND m.discord_id<>?
               ORDER BY s.submitted_at, m.joined_at""",
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
    gm_name = r["gm_name_override"] if is_simple_schedule(r) and r["gm_name_override"] else ((gm_user["display_name"] or gm_user["username"]) if gm_user else user_display(r["gm_discord_id"]))

    pl_uids = [x["discord_id"] for x in active]
    weekday_jp = ["月","火","水","木","金","土","日"]

    rows = candidate_slot_rows(rid) if per_day_time else candidate_rows(rid)
    # 回答状況に表示される各参加者について、その日に別の成立卓があるかを取得。
    # 自分自身の大きな○/△にも同じ警告を出せるようuidも含める。
    conflict_users = list(dict.fromkeys([*pl_uids, uid]))
    user_conflicts = calendar_conflicts_for_users(conflict_users, dates)
    available = any(len(x["yes"]) >= int(r["min_players"]) for x in rows)
    all_answered = simple_schedule_all_answered(rid) if is_simple_schedule(r) else False

    deadline_dt = datetime.fromisoformat(r["deadline"])
    deadline_label = (
        "未設定"
        if schedule_pending
        else deadline_dt.strftime("%Y-%m-%d")
    )
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

        current = my_answers.get(ds, "") if can_answer else ""
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

            conflict_mark = (
                "<span class='calendar-conflict-badge' "
                "title='この日は別の開催予定があります'>!</span>"
                if (str(puid), ds) in user_conflicts else ""
            )

            member_lines.append(
                f"<div class='answer-member'>"
                f"<span class='answer-member-name'>{esc(user_display(puid))}</span>"
                f"<span class='answer-member-result'>"
                f"{conflict_mark}"
                f"<span class='answer-member-symbol {mcls}'>{mark}</span>"
                f"</span>"
                f"</div>"
            )

        onclick = " onclick='togglePL(this)'" if can_answer else ""
        clickable = " clickable" if can_answer else ""

        cards.append(
            f"<div class='answer-day {cls}{clickable}' data-date='{ds}'{onclick}>"
            f"<div class='answer-day-head'>{day_label}</div>"
            f"<div class='answer-day-state'>{symbol}</div>"
            f"<div class='answer-members'>"
            f"{''.join(member_lines) if member_lines else '<div class=\"muted small\">未回答</div>'}"
            f"</div>"
            f"</div>"
        )

    if per_day_time:
        slot_all = recruitment_slot_answer_map(rid)
        my_slot_answers = {
            f"{d}|{t}": a
            for (puid,d,t),a in slot_all.items()
            if str(puid)==uid
        }
        js_obj = json.dumps(my_slot_answers, ensure_ascii=False)
        cards=[]
        for sl in schedule_slots:
            ds=str(sl['event_date']); tm=str(sl['start_time']); key=f"{ds}|{tm}"
            d=date.fromisoformat(ds)
            day_label=f"{d.month}/{d.day}({weekday_jp[d.weekday()]}) {tm}〜"
            current=my_slot_answers.get(key,'') if can_answer else ''
            cls='yes' if current=='yes' else 'maybe' if current=='maybe' else ''
            symbol='○' if current=='yes' else '△' if current=='maybe' else '-'
            member_lines=[]
            for puid in pl_uids:
                a=slot_all.get((str(puid),ds,tm),'')
                mark='○' if a=='yes' else '△' if a=='maybe' else '-'
                mcls='yes' if a=='yes' else 'maybe' if a=='maybe' else 'no'
                conflict_mark=("<span class='calendar-conflict-badge' title='この日は別の開催予定があります'>!</span>" if (str(puid),ds) in user_conflicts else '')
                member_lines.append(
                    f"<div class='answer-member'><span class='answer-member-name'>{esc(user_display(puid))}</span>"
                    f"<span class='answer-member-result'>{conflict_mark}<span class='answer-member-symbol {mcls}'>{mark}</span></span></div>"
                )
            onclick=" onclick='togglePL(this)'" if can_answer else ''
            clickable=' clickable' if can_answer else ''
            cards.append(
                f"<div class='answer-day {cls}{clickable}' data-date='{ds}' data-key='{esc(key)}'{onclick}>"
                f"<div class='answer-day-head'>{day_label}</div><div class='answer-day-state'>{symbol}</div>"
                f"<div class='answer-members'>{''.join(member_lines) if member_lines else '<div class=\"muted small\">未回答</div>'}</div></div>"
            )
        available=any(len(x['yes'])>=int(r['min_players']) for x in rows)

    schedule_block = ""

    if schedule_pending:
        if gm:
            pending_note = (
                "<div class='viewer-note'>"
                "現在は参加者だけを募集しています。"
                "日程を決める準備ができたら「日程調整を開始」から設定してください。"
                "</div>"
            )
        else:
            pending_note = (
                "<div class='viewer-note'>"
                "日程調整は後日行われます。"
                "GMからの案内をお待ちください。"
                "</div>"
            )

        schedule_block = pending_note

    elif can_answer:
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
          <span><b class='conflict-legend-mark'>!</b>：すでに開催予定あり</span>
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
          const d=el.dataset.key||el.dataset.date;
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
            note_html = ""
        elif spectator:
            note_html = "<div class='viewer-note'>観戦希望では日程回答はありません。PLの回答状況を確認できます。</div>"
        else:
            note_html = "<div class='viewer-note'>この日程調整の回答対象ではありません。</div>" if is_simple_schedule(r) else "<div class='viewer-note'>回答するにはDiscord募集メッセージの「参加」リアクションを押してください。</div>"

        schedule_block = f"""
        {note_html}

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
        if schedule_pending:
            gm_buttons = (
                f"<div class='gm-actions'>"
                f"<a class='btn green' href='/r/{rid}/schedule/start'>"
                "日程調整を開始"
                "</a>"
                "</div>"
            )
        else:
            if is_simple_schedule(r):
                dbtn=f"<a class='btn green' href='/r/{rid}/decide'>日程を決定</a>" if available else "<span class='btn alt' style='opacity:.55;pointer-events:none'>必要人数待ち</span>"
                done_ids = submitted_user_ids(rid)
                with db() as c:
                    reminder_targets = c.execute(
                        """SELECT discord_id
                           FROM members
                           WHERE recruitment_id=?
                             AND member_type='participant'
                             AND active=1
                             AND discord_id<>?
                           ORDER BY joined_at""",
                        (rid, r["gm_discord_id"]),
                    ).fetchall()
                pending_ids = [
                    str(x["discord_id"])
                    for x in reminder_targets
                    if str(x["discord_id"]) not in done_ids
                ]
                guild_for_names = bot.get_guild(GUILD_ID)
                pending_names = []
                for pending_id in pending_ids:
                    member_obj = guild_for_names.get_member(int(pending_id)) if guild_for_names else None
                    pending_names.append(member_obj.display_name if member_obj else user_display(pending_id))
                pending_text = "、".join(pending_names) if pending_names else "なし"
                confirm_text = json.dumps(
                    f"リマインド対象：{pending_text}\n\n上記の方へリマインド通知を送信しますがよろしいですか？",
                    ensure_ascii=False,
                )
                nudge_disabled = " disabled style='opacity:.55;cursor:not-allowed'" if not pending_ids else ""
                nudge_confirm = "" if not pending_ids else f" onclick='return confirm({confirm_text})'"
                # 上段の「日程を決定」と「リマインド」は完全に同じサイズで横並び。
                if available:
                    dbtn = (
                        f"<a class='btn green' "
                        f"style='width:100%;height:100%;box-sizing:border-box;"
                        f"display:flex;align-items:center;justify-content:center;text-align:center' "
                        f"href='/r/{rid}/decide'>日程を決定</a>"
                    )
                else:
                    dbtn = (
                        "<span class='btn alt' "
                        "style='width:100%;height:100%;box-sizing:border-box;"
                        "display:flex;align-items:center;justify-content:center;text-align:center;"
                        "opacity:.55;pointer-events:none'>必要人数待ち</span>"
                    )

                gm_buttons=(
                    f"<div class='gm-actions'>"
                    f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;width:100%;align-items:stretch'>"
                    f"<div style='min-width:0;display:flex'>{dbtn}</div>"
                    f"<form method='post' action='/r/{rid}/nudge' "
                    f"style='margin:0;min-width:0;display:flex'>{csrf_field(request)}"
                    f"<button class='btn alt' "
                    f"style='width:100%;height:100%;box-sizing:border-box;"
                    f"display:flex;align-items:center;justify-content:center;text-align:center' "
                    f"type='submit'{nudge_disabled}{nudge_confirm}>リマインド</button>"
                    f"</form></div>"
                    f"<a class='btn alt' style='width:100%;box-sizing:border-box;text-align:center;justify-content:center' "
                    f"href='/r/{rid}/reschedule'>再日程調整</a>"
                    f"</div>"
                )
            else:
                gm_buttons=(
                    f"<div class='gm-actions'>"
                    f"<div style='display:flex;gap:10px;align-items:stretch;width:100%'>"
                    f"<a class='btn green' style='flex:1;justify-content:center;text-align:center' href='/r/{rid}/decide'>開催日を決定</a>"
                    f"<a class='btn alt' style='flex:1;justify-content:center;text-align:center' href='/r/{rid}/reschedule'>再日程調整</a>"
                    f"</div></div>"
                )

    detail_cls = "detail-head available" if available else "detail-head"
    available_label = (
        "<div class='detail-available-label'>● 開催可能な日程があります</div>"
        if available and not schedule_pending else ""
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

    r = get_recruitment(rid)
    simple_open_answer = bool(
        r
        and is_simple_schedule(r)
        and str(uid) != str(r["gm_discord_id"])
    )
    if not is_active_member(rid, uid, "participant") and not simple_open_answer:
        raise HTTPException(403, "参加者のみ回答できます")

    with db() as c:
        if simple_open_answer and not is_active_member(rid, uid, "participant"):
            c.execute(
                "INSERT OR REPLACE INTO members(recruitment_id,discord_id,member_type,active,joined_at) VALUES(?,?,?,?,?)",
                (rid, str(uid), "participant", 1, iso_now()),
            )
        c.execute("DELETE FROM answers WHERE recruitment_id=? AND discord_id=?",(rid,uid))
        c.execute("DELETE FROM slot_answers WHERE recruitment_id=? AND discord_id=?",(rid,uid))
        c.execute("INSERT INTO answer_submissions(recruitment_id,discord_id,submitted_at) VALUES(?,?,?) ON CONFLICT(recruitment_id,discord_id) DO UPDATE SET submitted_at=excluded.submitted_at",(rid,uid,iso_now()))
    return RedirectResponse(f"/r/{rid}", status_code=303)


@app.post("/r/{rid}/answer")
async def save_answer(rid: int, request: Request, answers: str = Form("{}"), comment: str = Form("")):
    uid = require_login(request)
    await require_csrf(request)
    r = get_recruitment(rid)
    simple_open_answer = bool(
        r
        and is_simple_schedule(r)
        and str(uid) != str(r["gm_discord_id"])
    )
    if not is_active_member(rid, uid, "participant") and not simple_open_answer:
        raise HTTPException(403, "参加者のみ回答できます")
    allowed_dates = set(get_gm_dates(rid))
    per_day_time, _schedule_slots = recruitment_schedule_slots(rid)
    try:
        obj = json.loads(answers)
    except json.JSONDecodeError:
        obj = {}
    with db() as c:
        if simple_open_answer and not is_active_member(rid, uid, "participant"):
            c.execute(
                "INSERT OR REPLACE INTO members(recruitment_id,discord_id,member_type,active,joined_at) VALUES(?,?,?,?,?)",
                (rid, str(uid), "participant", 1, iso_now()),
            )
        c.execute("DELETE FROM answers WHERE recruitment_id=? AND discord_id=?", (rid, uid))
        if not per_day_time:
            for d, a in obj.items():
                if d in allowed_dates and a in {"yes","maybe"}:
                    c.execute("INSERT INTO answers(recruitment_id,discord_id,event_date,answer,updated_at) VALUES(?,?,?,?,?)", (rid, uid, d, a, iso_now()))
        c.execute("INSERT INTO comments(recruitment_id,discord_id,comment,updated_at) VALUES(?,?,?,?) ON CONFLICT(recruitment_id,discord_id) DO UPDATE SET comment=excluded.comment,updated_at=excluded.updated_at", (rid, uid, comment.strip(), iso_now()))
        c.execute("INSERT INTO answer_submissions(recruitment_id,discord_id,submitted_at) VALUES(?,?,?) ON CONFLICT(recruitment_id,discord_id) DO UPDATE SET submitted_at=excluded.submitted_at",(rid,uid,iso_now()))

    if per_day_time:
        save_slot_answers(rid, uid, obj if isinstance(obj,dict) else {}, iso_now())

    # 回答保存後、初めて必要人数を満たした瞬間だけ通知
    try:
        await notify_availability_if_needed(rid)
    except Exception as e:
        log_error(f"availability_notify rid={rid}", e)

    return RedirectResponse(f"/r/{rid}", status_code=303)


@app.post("/r/{rid}/nudge")
async def nudge_unanswered(rid:int,request:Request):
    uid=require_login(request); await require_csrf(request); r=get_recruitment(rid)
    if not r or str(uid)!=str(r["gm_discord_id"]) or not is_simple_schedule(r): raise HTTPException(403)
    done=submitted_user_ids(rid)
    with db() as c: rows=c.execute("SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='participant' AND active=1",(rid,)).fetchall()
    pending=[str(x["discord_id"]) for x in rows if str(x["discord_id"]) not in done]
    if not pending: return RedirectResponse(f"/r/{rid}",status_code=303)
    guild=bot.get_guild(GUILD_ID); ch=guild.get_channel(int(r["waiting_channel_id"])) if guild and r["waiting_channel_id"] else None
    if not ch: raise HTTPException(404,"送信先チャンネルが見つかりません")
    await ch.send(
        "**日程調整のリマインド**\n"
        + " ".join(f"<@{x}>" for x in pending)
        + f"\nまだ回答が完了していません😢\nお時間ある際に以下のリンクよりご回答をお願いします！\n\n{BASE_URL}/r/{rid}",
        silent=in_quiet_hours(),
    )
    return RedirectResponse(f"/r/{rid}",status_code=303)


@app.get("/r/{rid}/decide", response_class=HTMLResponse)
async def decide_form(rid: int, request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/r/{rid}/decide")
    r = get_recruitment(rid)
    if not r:
        raise HTTPException(404)
    if str(uid) != str(r["gm_discord_id"]):
        return page("日程決定", f"""
            <a class='back-link' href='/r/{rid}'>‹ 戻る</a>
            <div class='card' style='text-align:center'><h2>GMの方のみ表示できます</h2>
            <p class='muted'>日程の決定はGMのみ行えます。</p></div>""", request)

    per_day_time, _slots = recruitment_schedule_slots(rid)
    if per_day_time:
        raw = candidate_slot_rows(rid)
    else:
        raw = []
        default_time = str(r["start_time"] or "未定")
        for x in candidate_rows(rid):
            raw.append({**x, "time": default_time, "key": f"{x['date']}|{default_time}"})
    candidates=[x for x in raw if len(x['yes']) >= int(r['min_players'])]
    if not candidates:
        return page("開催日決定", f"""<a class='back-link' href='/r/{rid}'>‹ 戻る</a>
        <div class='card'><p>現在、最小人数{r['min_players']}人を満たす候補がありません。</p>
        <a class='btn' href='/r/{rid}/reschedule'>再日程調整</a></div>""", request)

    data=[]; cards=[]
    for x in candidates:
        key=str(x['key']); d=str(x['date']); t=str(x['time'])
        yes=[str(u) for u in x['yes']]; maybe=[str(u) for u in x['maybe']]
        data.append({'key':key,'date':d,'time':t,'yes':yes})
        yes_names=', '.join(esc(user_display(u)) for u in yes) or 'なし'
        maybe_names=', '.join(esc(user_display(u)) for u in maybe) or 'なし'
        cards.append(f"""
        <label class='candidate' style='display:block'>
          <div style='display:flex;align-items:flex-start;gap:10px'>
            <input class='slot-choice' style='width:auto;margin-top:4px' type='checkbox' name='selected_slot' value='{esc(key)}' onchange='onSlotChoice(this)'>
            <div style='flex:1'><b>{esc(d)} {esc(t)}〜</b><div style='margin-top:6px'>○{len(yes)}人</div>
            <p class='small' style='margin:8px 0 0'>○：{yes_names}</p>
            <p class='small muted' style='margin:4px 0 0'>△：{maybe_names}</p></div>
          </div>
        </label>""")
    data_json=json.dumps(data,ensure_ascii=False)
    member_names={u:user_display(u) for x in candidates for u in x['yes']}
    member_json=json.dumps(member_names,ensure_ascii=False)

    return page("開催日決定", f"""
    <a class='back-link' href='/r/{rid}'>‹ 戻る</a>
    <form class='card' method='post' action='/r/{rid}/decide' onsubmit='return validateDecision()'>
      {csrf_field(request)}
      <h2>開催日を決定</h2>
      <label class='round-number-row'><input type='number' min='1' name='round_no' value='1' required><span>陣目</span></label>

      <label class='checkbox-row' style='margin:16px 0 4px'>
        <input type='checkbox' id='multiDay' name='multi_day' value='1' onchange='toggleMultiDay()'>
        複数日に分けて開催する
      </label>
      <p class='small muted' style='margin:0 0 12px 4px'>1つの卓を複数日に分けて開催する</p>
      <div id='multiDayMismatch' style='display:none;margin:0 0 14px;padding:12px 14px;border:1px solid #6b2a31;border-radius:12px;background:#2a1519'>
        <div style='font-weight:800;color:#ff8b82'>選択した日程の参加者が一致していません</div>
        <div class='small' style='margin-top:4px;color:#c9a8a8'>1卓を複数日に分けて開催するため、同じ参加者となるように選択してください</div>
      </div>
      <div id='candidateList'>{''.join(cards)}</div>
      <div id='commonMembers' class='field-box no-icon' style='display:none;margin-top:16px'>
        <div class='field-stack'><span class='field-label'>参加者</span><div id='commonMemberList'></div>
        <p id='commonMemberNote' class='muted small' style='margin:8px 0 0'></p></div>
      </div>
      <div id='multiDayMemberInputs' style='display:none'></div>
      {("<label class='checkbox-row'><input type='checkbox' name='create_session_channel' value='1'> 参加メンバーだけのDiscordチャンネルを作成する</label>" if is_simple_schedule(r) else "")}
      <div style='margin-top:26px;display:flex;justify-content:center'>
        <button style='width:auto;min-width:280px;text-align:center'>{"この内容で日程を決定する" if is_simple_schedule(r) else "この内容で卓を成立させる"}</button>
      </div>
    </form>
    <script>
    const candidateData={data_json};
    const memberNames={member_json};
    const minPlayers={int(r['min_players'])};
    const maxPlayers={int(r['max_players'])};
    function choices(){{ return [...document.querySelectorAll('.slot-choice:checked')]; }}
    function onSlotChoice(el){{
      if(!document.getElementById('multiDay').checked && el.checked){{
        document.querySelectorAll('.slot-choice').forEach(x=>{{if(x!==el)x.checked=false;}});
      }}
      updateCommon();
    }}
    function toggleMultiDay(){{
      if(!document.getElementById('multiDay').checked){{
        const xs=choices(); xs.slice(1).forEach(x=>x.checked=false);
      }}
      updateCommon();
    }}
    function sameMembers(a,b){{
      if(a.size!==b.size)return false;
      for(const x of a)if(!b.has(x))return false;
      return true;
    }}
    function hasMultiDayMismatch(){{
      const keys=choices().map(x=>x.value);
      if(!document.getElementById('multiDay').checked||keys.length<2)return false;
      const sets=keys.map(k=>{{const row=candidateData.find(x=>x.key===k);return new Set(row?row.yes:[]);}});
      return sets.slice(1).some(s=>!sameMembers(sets[0],s));
    }}
    function updateCommon(){{
      const keys=choices().map(x=>x.value);
      const box=document.getElementById('commonMembers'); const list=document.getElementById('commonMemberList');
      const mismatch=document.getElementById('multiDayMismatch');
      const isMismatch=hasMultiDayMismatch();
      mismatch.style.display=isMismatch?'block':'none';
      if(!keys.length){{box.style.display='none';list.innerHTML='';return;}}
      let common=null;
      keys.forEach(k=>{{ const row=candidateData.find(x=>x.key===k); const ys=new Set(row?row.yes:[]); common=common===null?ys:new Set([...common].filter(x=>ys.has(x))); }});
      const ids=[...(common||new Set())];
      const multi=document.getElementById('multiDay').checked;
      const hidden=document.getElementById('multiDayMemberInputs');
      if(multi){{
        box.style.display='none';
        list.innerHTML='';
        hidden.innerHTML=ids.map(id=>`<input type="hidden" name="member_id" value="${{id}}">`).join('');
      }}else{{
        hidden.innerHTML='';
        box.style.display='block';
        list.innerHTML=ids.map(id=>`<label style="display:flex;gap:9px;align-items:center;padding:5px 0"><input style="width:auto" type="checkbox" name="member_id" value="${{id}}" checked> ${{memberNames[id]||id}}</label>`).join('') || '<div class="warn">○の参加者がいません。</div>';
        document.getElementById('commonMemberNote').textContent=`○の参加者：${{ids.length}}人`;
      }}
    }}
    function validateDecision(){{
      const n=choices().length; const multi=document.getElementById('multiDay').checked;
      if((!multi&&n!==1)||(multi&&n<2)){{alert(multi?'開催日を2つ以上選択してください。':'開催日を1つ選択してください。');return false;}}
      if(multi&&hasMultiDayMismatch()){{
        const e=document.getElementById('multiDayMismatch');e.style.display='block';e.scrollIntoView({{behavior:'smooth',block:'center'}});return false;
      }}
      const m=document.querySelectorAll('[name="member_id"]:checked').length;
      if(m<minPlayers||m>maxPlayers){{alert(`参加者を${{minPlayers}}〜${{maxPlayers}}人選択してください。`);return false;}}
      return true;
    }}
    </script>
    """, request)


@app.post("/r/{rid}/decide")
async def decide_submit(request: Request, rid: int):
    uid = require_login(request)
    await require_csrf(request)
    r=get_recruitment(rid)
    if not r or str(uid)!=str(r['gm_discord_id']): raise HTTPException(403)
    form=await request.form()
    try: round_no=int(form.get('round_no') or 1)
    except Exception: round_no=1
    multi_day=str(form.get('multi_day') or '')=='1'
    selected_keys=[str(x) for x in form.getlist('selected_slot')]
    per_day_time,_=recruitment_schedule_slots(rid)
    if per_day_time:
        raw=candidate_slot_rows(rid)
    else:
        default_time=str(r['start_time'] or '未定')
        raw=[{**x,'time':default_time,'key':f"{x['date']}|{default_time}"} for x in candidate_rows(rid)]
    cmap={str(x['key']):x for x in raw if len(x['yes'])>=int(r['min_players'])}
    selected_keys=list(dict.fromkeys(k for k in selected_keys if k in cmap))
    if (not multi_day and len(selected_keys)!=1) or (multi_day and len(selected_keys)<2):
        raise HTTPException(400,'開催日の選択を確認してください')
    if multi_day:
        member_sets=[set(str(u) for u in cmap[k]['yes']) for k in selected_keys]
        if any(x != member_sets[0] for x in member_sets[1:]):
            raise HTTPException(400,'選択した日程の参加者が一致していません。1卓を複数日に分けて開催するため、同じ参加者となるように選択してください')
    common=None
    for k in selected_keys:
        ys=set(str(u) for u in cmap[k]['yes'])
        common=ys if common is None else common & ys
    common=common or set()
    selected=list(dict.fromkeys(str(x) for x in form.getlist('member_id') if str(x) in common))
    min_sel,max_sel=int(r['min_players']),int(r['max_players'])
    if is_simple_schedule(r) and not r['target_players']: min_sel,max_sel=1,max(1,len(common))
    if not (min_sel<=len(selected)<=max_sel):
        raise HTTPException(400,f'参加者を{min_sel}〜{max_sel}人選択してください')
    slots=[(str(cmap[k]['date']),str(cmap[k]['time'])) for k in selected_keys]
    slots.sort()
    event_date,start_time=slots[0]

    with db() as c:
        if c.execute('SELECT 1 FROM sessions WHERE recruitment_id=? AND round_no=?',(rid,round_no)).fetchone():
            raise HTTPException(400,'その陣数はすでに使われています')
        cur=c.execute('INSERT INTO sessions(recruitment_id,round_no,event_date,start_time,created_at) VALUES(?,?,?,?,?)',(rid,round_no,event_date,start_time,iso_now()))
        sid=int(cur.lastrowid)
        c.executemany('INSERT INTO session_members(session_id,discord_id) VALUES(?,?)',[(sid,u) for u in selected])
    set_session_slots(sid,slots,iso_now())

    try:
        guild=bot.get_guild(GUILD_ID)
        if not guild: raise RuntimeError('Guildが見つかりません')
        ch=None; should_create=(not is_simple_schedule(r)) or bool(form.get('create_session_channel'))
        if should_create:
            category=guild.get_channel(SESSION_CATEGORY_ID); gm=await fetch_member(guild,uid)
            overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True),gm:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
            for puid in selected:
                member=await fetch_member(guild,puid)
                if member: overwrites[member]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
            if not is_simple_schedule(r):
                with db() as c: spectators=c.execute("SELECT discord_id FROM members WHERE recruitment_id=? AND member_type='spectator' AND active=1",(rid,)).fetchall()
                for sp in spectators:
                    member=await fetch_member(guild,sp[0])
                    if member: overwrites[member]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
            ch=await guild.create_text_channel(safe_channel_name(f'{r["scenario_name"]}-{round_no}陣'),category=category,overwrites=overwrites,topic=f'つぶ卓 成立 ID:{sid}')
            mentions='\n'.join(f'・<@{x}>' for x in selected)
            gm_label=r['gm_name_override'] if is_simple_schedule(r) and r['gm_name_override'] else f'<@{uid}>'
            role_label='主催' if r['game_type']=='EVENT' else 'GM'
            slot_lines='\n'.join(f'・{d} {t}〜' for d,t in slots)
            manage_text='' if is_simple_schedule(r) else f'\n\n日程変更・開催中止は以下のリンクよりお願いします！（GM専用）\n{BASE_URL}/session/{sid}/manage'
            await send_long(ch,f'## 『{r["scenario_name"]}』\n**{round_no}陣が成立しました🎉**\n\n開催日時：\n{slot_lines}\n\n{role_label}：{gm_label}\n参加者：\n{mentions}{manage_text}')

        waiting_ch=None
        if r['waiting_channel_id']:
            waiting_ch=guild.get_channel(int(r['waiting_channel_id']))
            if not waiting_ch:
                try: waiting_ch=await guild.fetch_channel(int(r['waiting_channel_id']))
                except Exception: waiting_ch=None
        if waiting_ch:
            member_mentions='、'.join(f'<@{x}>' for x in selected)
            slot_lines='\n'.join(f'・**{d} {t}〜**' for d,t in slots)
            label=f"✅ **{r['scenario_name']} の開催日時が決定しました**" if is_simple_schedule(r) else f'✅ **{round_no}陣の開催日時が決定しました**'
            await waiting_ch.send(f'{label}\n{slot_lines}\n参加者：{member_mentions}',silent=True)

        reminder_channel_id = str(ch.id) if ch else (str(waiting_ch.id) if waiting_ch else None)
        with db() as c:
            c.execute('UPDATE sessions SET channel_id=? WHERE id=?',(reminder_channel_id,sid))
            c.execute("UPDATE recruitments SET status='CONFIRMED' WHERE id=?",(rid,))
        cal_id=archive_confirmed_session(
            source_session_id=sid,source_recruitment_id=rid,game_type=r['game_type'],scenario_name=r['scenario_name'],
            event_date=event_date,start_time=start_time,gm_discord_id=uid,participant_ids=selected,created_at=iso_now(),
            calendar_visible=(bool(int(r['calendar_visible'] or 0)) if r['game_type']=='EVENT' else True),slots=slots,
        )
        if reminder_channel_id: schedule_session_reminder(sid)
    except Exception as e:
        log_error(f'decide_submit rid={rid}',e)
        return page('Discordエラー',"<div class='card'><p class='warn'>Discord側でエラーが発生し、卓の作成に失敗しました。権限やチャンネル設定を確認してください。</p></div>",request)

    summary='<br>'.join(f'{esc(d)} {esc(t)}〜' for d,t in slots)
    title='日程決定' if is_simple_schedule(r) else '卓成立'
    return page(title,f"<div class='card'><h2>🎉 {esc(r['scenario_name'])} {'' if is_simple_schedule(r) else str(round_no)+'陣'}が成立しました！</h2><p>{summary}</p><a class='btn' href='/r/{rid}'>日程ページへ戻る</a></div>",request)


def _session_change_needs_reconcile(old_event_date: str) -> bool:
    """20時差分に既に入った可能性がある卓だけ、変更時に一度整合性を取り直す。"""
    try:
        old_day = date.fromisoformat(str(old_event_date))
    except Exception:
        return False
    now = now_jst()
    return old_day < now.date() or (old_day == now.date() and now.hour >= 20)


def _session_change_reconcile_if_needed(old_event_date: str):
    if not _session_change_needs_reconcile(old_event_date):
        return
    # 開催後変更は例外処理。普段は20時当日差分だけで、ここでは現在日までを一度だけ再同期する。
    cutoff_resync_v83(now_jst().date().isoformat(), iso_now())


def _session_feature_silent() -> bool:
    # 成立後の変更通知だけは指定どおり 0:00〜9:00 をサイレントにする。
    return now_jst().hour < 9


async def _session_channel(session_id: int):
    detail, _ = session_management_detail(session_id)
    if not detail or not detail.get("channel_id"):
        return None
    try:
        channel_id = int(detail["channel_id"])
    except Exception:
        return None
    ch = bot.get_channel(channel_id)
    if ch:
        return ch
    try:
        return await bot.fetch_channel(channel_id)
    except Exception:
        return None


@app.get("/session/{session_id}/manage", response_class=HTMLResponse)
async def session_manage(session_id: int, request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/session/{session_id}/manage")
    detail, members = session_management_detail(session_id)
    if not detail:
        raise HTTPException(404)
    if str(uid) != str(detail["gm_discord_id"]):
        raise HTTPException(403, "GM専用ページです")
    if detail.get("cancelled_at"):
        return page("開催管理", "<div class='card'><h2>開催中止済みです</h2></div>", request)
    member_html = "".join(f"<li>{esc(x['display_name'])}</li>" for x in members)
    current_slots=get_session_slots(session_id)
    slot_html='<br>'.join(f"・{esc(x['event_date'])} {esc(x['start_time'])}〜" for x in current_slots)
    body=f"""
    <div class='card'>
      <h2>『{esc(detail['scenario_name'])}』{int(detail['round_no'])}陣</h2>
      <p class='muted'>現在：<br>{slot_html}</p>
      <ul>{member_html}</ul>
      <div style='display:grid;gap:10px;margin-top:16px'>
        <a class='btn' style='display:flex;align-items:center;justify-content:center;text-align:center' href='/session/{session_id}/reschedule/new'>再日程調整</a>
        <a class='btn danger' style='display:flex;align-items:center;justify-content:center;text-align:center' href='/session/{session_id}/cancel'>開催中止</a>
      </div>
    </div>
    """
    return page("開催管理", body, request)


@app.get("/session/{session_id}/reschedule/new", response_class=HTMLResponse)
async def session_reschedule_new(session_id: int, request: Request):
    uid=request.session.get('user_id')
    if not uid: return RedirectResponse(f'/login?next=/session/{session_id}/reschedule/new')
    detail,_members=session_management_detail(session_id)
    if not detail: raise HTTPException(404)
    if str(uid)!=str(detail['gm_discord_id']): raise HTTPException(403)
    default_deadline=(now_jst().date()+timedelta(days=7)).isoformat()
    weekday_jp=['月','火','水','木','金','土','日']
    cards=[]
    for ds in month_dates():
        d=date.fromisoformat(ds); label=f'{d.month}/{d.day}({weekday_jp[d.weekday()]})'
        cards.append(f'<div class="day" data-date="{ds}" onclick="toggleSessionGM(this)"><span>{label}</span><span class="state">-</span></div>')
    return page('再日程調整',f"""
      <a class='back-link' href='/session/{session_id}/manage'>‹ 戻る</a>
      <div class='section-title'>再日程調整</div>
      <form class='form-shell' method='post'>
        {csrf_field(request)}
        <div class='form-section compact'><div class='field-row'>
          <label><div class='field-box no-icon'><div class='field-stack'><span class='field-label'>開始時間</span><input type='time' name='start_time' value='{esc(detail['start_time'] if detail['start_time']!='未定' else '21:00')}' required></div></div></label>
          <label><div class='field-box no-icon'><div class='field-stack'><span class='field-label'>回答期限</span><input type='date' name='deadline' value='{default_deadline}' required></div></div></label>
        </div></div>
        <div class='create-date-heading'>開催候補日を選択（今月と来月末まで）</div>
        <input type='hidden' id='session_gm_dates' name='gm_dates'>
        <div class='date-scroll'><div class='grid'>{''.join(cards)}</div></div>
        <div class='legend'><span><b style='color:#22c55e'>○</b> 開催できる</span><span><b>-</b> 開催できない</span></div>
        {advanced_schedule_controls_html()}
        <button class='submit-btn' type='submit'>再日程調整を開始する</button>
      </form>
      <script>
      let selected=[];
      function toggleSessionGM(el){{
        const d=el.dataset.date, state=el.querySelector('.state');
        if(selected.includes(d)){{selected=selected.filter(x=>x!==d);el.classList.remove('yes');state.textContent='-';}}
        else{{selected.push(d);el.classList.add('yes');state.textContent='○';}}
        document.getElementById('session_gm_dates').value=selected.join(',');
        refreshAdvancedSlots();
      }}
      </script>
    """,request)


@app.post("/session/{session_id}/reschedule/new")
async def session_reschedule_new_submit(session_id:int,request:Request):
    uid=require_login(request); await require_csrf(request)
    detail,_=session_management_detail(session_id)
    if not detail or str(uid)!=str(detail['gm_discord_id']): raise HTTPException(403)
    form=await request.form()
    start_time=str(form.get('start_time') or '21:00')
    deadline=str(form.get('deadline') or '')
    dates=sorted({d for d in str(form.get('gm_dates') or '').split(',') if re.fullmatch(r'\d{4}-\d{2}-\d{2}',d)})
    if not dates: raise HTTPException(400,'開催可能日を選択してください')
    per_day_time=str(form.get('per_day_time') or '')=='1'
    slots=parse_schedule_slots(dates,start_time,per_day_time,str(form.get('schedule_slots_json') or ''))
    reschedule_id=create_session_reschedule(session_id,start_time,deadline,dates,iso_now(),candidate_slots=slots,per_day_time=per_day_time)
    ch=await _session_channel(session_id)
    if ch:
        await ch.send(f'開催日の再調整を開始しました。\n以下から回答してください。\n{BASE_URL}/session-reschedule/{reschedule_id}',silent=_session_feature_silent())
    return RedirectResponse(f'/session-reschedule/{reschedule_id}',303)


@app.get("/session-reschedule/{reschedule_id}", response_class=HTMLResponse)
async def session_reschedule_answer(reschedule_id:int,request:Request):
    uid=str(request.session.get('user_id') or '')
    if not uid:return RedirectResponse(f'/login?next=/session-reschedule/{reschedule_id}')
    rs,slots,members,answers,_per=session_reschedule_slot_detail(reschedule_id)
    if not rs: raise HTTPException(404)
    member_ids={str(x['discord_id']) for x in members}
    is_gm=uid==str(rs['gm_discord_id'])
    if uid not in member_ids and not is_gm: raise HTTPException(403,'この日程調整の参加者ではありません')
    dates=sorted({str(x['event_date']) for x in slots})
    user_conflicts=calendar_conflicts_for_users([str(x['discord_id']) for x in members],dates)
    own={f"{d}|{t}":a for (puid,d,t),a in answers.items() if puid==uid}
    cards=[]; weekday_jp=['月','火','水','木','金','土','日']
    for sl in slots:
        ds,tm=str(sl['event_date']),str(sl['start_time']); key=f'{ds}|{tm}'
        d=date.fromisoformat(ds); label=f'{d.month}/{d.day}({weekday_jp[d.weekday()]}) {tm}〜'
        current=own.get(key,''); cls='yes' if current=='YES' else 'maybe' if current=='MAYBE' else ''; symbol='○' if current=='YES' else '△' if current=='MAYBE' else '-'
        lines=[]
        for m in members:
            puid=str(m['discord_id']); a=answers.get((puid,ds,tm),''); mark='○' if a=='YES' else '△' if a=='MAYBE' else '-'; mcls='yes' if a=='YES' else 'maybe' if a=='MAYBE' else 'no'
            conflict="<span class='calendar-conflict-badge'>!</span>" if (puid,ds) in user_conflicts else ''
            lines.append(f"<div class='answer-member'><span class='answer-member-name'>{esc(m['display_name'])}</span><span class='answer-member-result'>{conflict}<span class='answer-member-symbol {mcls}'>{mark}</span></span></div>")
        onclick=" onclick='togglePL(this)'" if uid in member_ids and rs['status']=='OPEN' else ''
        cards.append(f"<div class='answer-day {cls}{' clickable' if onclick else ''}' data-key='{esc(key)}'{onclick}><div class='answer-day-head'>{label}</div><div class='answer-day-state'>{symbol}</div><div class='answer-members'>{''.join(lines)}</div></div>")
    js=json.dumps(own,ensure_ascii=False)
    block=f"<div class='answer-grid status-grid'>{''.join(cards)}</div>"
    if uid in member_ids and rs['status']=='OPEN':
        block=f"""
        <div class='answer-title'>日程回答</div><div class='answer-legend'><span><b class='yes-mark'>○</b>：参加可能</span><span><b class='maybe-mark'>△</b>：未定</span><span><b class='no-mark'>-</b>：無理</span><span><b class='conflict-legend-mark'>!</b>：すでに開催予定あり</span></div>
        <form method='post' action='/session-reschedule/{reschedule_id}'>{csrf_field(request)}<input type='hidden' name='answers' id='answers'><div class='answer-grid status-grid'>{''.join(cards)}</div><button class='save-answer' type='submit'>回答を保存</button></form>
        <script>let ans={js};function refreshHidden(){{document.getElementById('answers').value=JSON.stringify(ans);}}function togglePL(el){{const k=el.dataset.key;let v=ans[k]||'';v=v===''?'YES':(v==='YES'?'MAYBE':'');if(v)ans[k]=v;else delete ans[k];el.classList.remove('yes','maybe');if(v==='YES')el.classList.add('yes');if(v==='MAYBE')el.classList.add('maybe');el.querySelector('.answer-day-state').textContent=v==='YES'?'○':(v==='MAYBE'?'△':'-');refreshHidden();}}refreshHidden();</script>"""
    minp=int(rs.get('min_players') or 1)
    available=any(sum(1 for m in members if answers.get((str(m['discord_id']),str(sl['event_date']),str(sl['start_time'])))=='YES')>=minp for sl in slots)
    gm_action=f"<div style='margin-top:18px'><a class='btn green' style='display:flex;justify-content:center;text-align:center' href='/session-reschedule/{reschedule_id}/decide'>開催日を決定</a></div>" if is_gm and rs['status']=='OPEN' and available else ''
    return page('再日程調整',f"<a class='back-link' href='/session/{int(rs['session_id'])}/manage'>‹ 戻る</a><div class='section-title'>再日程調整</div><div class='card'><h2>『{esc(rs['scenario_name'])}』{int(rs['round_no'])}陣</h2>{block}{gm_action}</div>",request)


@app.post("/session-reschedule/{reschedule_id}")
async def session_reschedule_answer_submit(reschedule_id:int,request:Request):
    uid=str(require_login(request)); await require_csrf(request)
    rs,slots,members,_answers,_per=session_reschedule_slot_detail(reschedule_id)
    if not rs or uid not in {str(x['discord_id']) for x in members}: raise HTTPException(403)
    form=await request.form()
    try: raw=json.loads(str(form.get('answers') or '{}'))
    except Exception: raw={}
    save_session_reschedule_slot_answers(reschedule_id,uid,raw if isinstance(raw,dict) else {},iso_now())
    return RedirectResponse(f'/session-reschedule/{reschedule_id}',303)


@app.get("/session-reschedule/{reschedule_id}/decide", response_class=HTMLResponse)
async def session_reschedule_decide(reschedule_id:int,request:Request):
    uid=str(request.session.get('user_id') or '')
    if not uid:return RedirectResponse(f'/login?next=/session-reschedule/{reschedule_id}/decide')
    rs,slots,members,answers,_per=session_reschedule_slot_detail(reschedule_id)
    if not rs or uid!=str(rs['gm_discord_id']): raise HTTPException(403)
    minp=int(rs.get('min_players') or 1); maxp=int(rs.get('max_players') or len(members) or 1)
    data=[]; cards=[]; names={str(m['discord_id']):str(m['display_name']) for m in members}
    for sl in slots:
        d,t=str(sl['event_date']),str(sl['start_time']); key=f'{d}|{t}'; yes=[str(m['discord_id']) for m in members if answers.get((str(m['discord_id']),d,t))=='YES']
        if len(yes)<minp: continue
        data.append({'key':key,'date':d,'time':t,'yes':yes})
        yes_names=', '.join(esc(names.get(u,u)) for u in yes) or 'なし'
        cards.append(f"<label class='candidate' style='display:block'><div style='display:flex;gap:10px;align-items:flex-start'><input class='slot-choice' style='width:auto;margin-top:4px' type='checkbox' name='selected_slot' value='{esc(key)}' onchange='onSlotChoice(this)'><div style='flex:1'><b>{d} {t}〜</b><div style='margin-top:6px'>○{len(yes)}人</div><p class='small' style='margin:8px 0 0'>○：{yes_names}</p></div></div></label>")
    if not data:return page('開催日決定',f"<a class='back-link' href='/session-reschedule/{reschedule_id}'>‹ 戻る</a><div class='card'><p>現在、最小人数{minp}人を満たす候補がありません。</p></div>",request)
    return page('開催日決定',f"""
      <a class='back-link' href='/session-reschedule/{reschedule_id}'>‹ 戻る</a>
      <form class='card' method='post' action='/session-reschedule/{reschedule_id}/confirm' onsubmit='return validateDecision()'>
        {csrf_field(request)}
        <h2>開催日を決定</h2>
        <label class='checkbox-row' style='margin-bottom:4px'>
          <input type='checkbox' id='multiDay' name='multi_day' value='1' onchange='toggleMultiDay()'> 複数日に分けて開催する
        </label>
        <p class='small muted' style='margin:0 0 12px 4px'>1つの卓を複数日に分けて開催する</p>
        <div id='multiDayMismatch' style='display:none;margin:0 0 14px;padding:12px 14px;border:1px solid #6b2a31;border-radius:12px;background:#2a1519'>
          <div style='font-weight:800;color:#ff8b82'>選択した日程の参加者が一致していません</div>
          <div class='small' style='margin-top:4px;color:#c9a8a8'>1卓を複数日に分けて開催するため、同じ参加者となるように選択してください</div>
        </div>
        {''.join(cards)}
        <div id='commonMembers' class='field-box no-icon' style='display:none;margin-top:16px'>
          <div class='field-stack'><span class='field-label'>参加者</span><div id='commonMemberList'></div><p id='commonMemberNote' class='muted small'></p></div>
        </div>
        <div id='multiDayMemberInputs' style='display:none'></div>
        <div style='margin-top:26px;display:flex;justify-content:center'><button style='width:auto;min-width:280px'>この内容で卓を成立させる</button></div>
      </form>
      <script>
      const candidateData={json.dumps(data,ensure_ascii=False)};
      const memberNames={json.dumps(names,ensure_ascii=False)};
      const minPlayers={minp},maxPlayers={maxp};
      function choices(){{return [...document.querySelectorAll('.slot-choice:checked')];}}
      function sameMembers(a,b){{if(a.size!==b.size)return false;for(const x of a)if(!b.has(x))return false;return true;}}
      function hasMultiDayMismatch(){{
        const keys=choices().map(x=>x.value);
        if(!document.getElementById('multiDay').checked||keys.length<2)return false;
        const sets=keys.map(k=>{{const row=candidateData.find(x=>x.key===k);return new Set(row?row.yes:[]);}});
        return sets.slice(1).some(s=>!sameMembers(sets[0],s));
      }}
      function onSlotChoice(el){{
        if(!document.getElementById('multiDay').checked&&el.checked)document.querySelectorAll('.slot-choice').forEach(x=>{{if(x!==el)x.checked=false;}});
        updateCommon();
      }}
      function toggleMultiDay(){{
        if(!document.getElementById('multiDay').checked)choices().slice(1).forEach(x=>x.checked=false);
        updateCommon();
      }}
      function updateCommon(){{
        const keys=choices().map(x=>x.value),box=document.getElementById('commonMembers'),list=document.getElementById('commonMemberList');
        const mismatch=document.getElementById('multiDayMismatch');
        mismatch.style.display=hasMultiDayMismatch()?'block':'none';
        if(!keys.length){{box.style.display='none';list.innerHTML='';return;}}
        let common=null;
        keys.forEach(k=>{{const row=candidateData.find(x=>x.key===k),ys=new Set(row?row.yes:[]);common=common===null?ys:new Set([...common].filter(x=>ys.has(x)));}});
        const ids=[...(common||new Set())];
        const multi=document.getElementById('multiDay').checked;
        const hidden=document.getElementById('multiDayMemberInputs');
        if(multi){{
          box.style.display='none';
          list.innerHTML='';
          hidden.innerHTML=ids.map(id=>`<input type="hidden" name="member_id" value="${{id}}">`).join('');
        }}else{{
          hidden.innerHTML='';
          box.style.display='block';
          list.innerHTML=ids.map(id=>`<label style="display:flex;gap:9px;padding:5px 0"><input style="width:auto" type="checkbox" name="member_id" value="${{id}}" checked> ${{memberNames[id]||id}}</label>`).join('');
          document.getElementById('commonMemberNote').textContent=`○の参加者：${{ids.length}}人`;
        }}
      }}
      function validateDecision(){{
        const n=choices().length,multi=document.getElementById('multiDay').checked,m=document.querySelectorAll('[name="member_id"]:checked').length;
        if((!multi&&n!==1)||(multi&&n<2)){{alert(multi?'2日程以上選択してください':'1日程選択してください');return false;}}
        if(multi&&hasMultiDayMismatch()){{const e=document.getElementById('multiDayMismatch');e.style.display='block';e.scrollIntoView({{behavior:'smooth',block:'center'}});return false;}}
        if(m<minPlayers||m>maxPlayers){{alert(`参加者を${{minPlayers}}〜${{maxPlayers}}人選択してください`);return false;}}
        return true;
      }}
      </script>
    """,request)


@app.post("/session-reschedule/{reschedule_id}/confirm")
async def session_reschedule_confirm(reschedule_id:int,request:Request):
    uid=str(require_login(request)); await require_csrf(request)
    rs,slots,members,answers,_per=session_reschedule_slot_detail(reschedule_id)
    if not rs or uid!=str(rs['gm_discord_id']): raise HTTPException(403)
    form=await request.form(); multi=str(form.get('multi_day') or '')=='1'; keys=[str(x) for x in form.getlist('selected_slot')]
    smap={f"{x['event_date']}|{x['start_time']}":(str(x['event_date']),str(x['start_time'])) for x in slots}
    chosen=[smap[k] for k in dict.fromkeys(keys) if k in smap]
    if (not multi and len(chosen)!=1) or (multi and len(chosen)<2): raise HTTPException(400,'開催日時の選択を確認してください')
    if multi:
        answer_sets=[]
        for d,t in chosen:
            answer_sets.append({str(m['discord_id']) for m in members if answers.get((str(m['discord_id']),d,t))=='YES'})
        if any(x != answer_sets[0] for x in answer_sets[1:]):
            raise HTTPException(400,'選択した日程の参加者が一致していません。1卓を複数日に分けて開催するため、同じ参加者となるように選択してください')
    selected=[str(x) for x in form.getlist('member_id')]
    result=confirm_session_reschedule_slots(reschedule_id,chosen,selected)
    if not result: raise HTTPException(400,'参加人数または選択内容を確認してください')
    try:
        old_dates=list(dict.fromkeys(d for d,_ in result['old_slots']))
        target=next((d for d in old_dates if _session_change_needs_reconcile(d)),None)
        if target:_session_change_reconcile_if_needed(target)
    except Exception as e: log_error(f'session_reschedule_reconcile id={reschedule_id}',e)
    ch=await _session_channel(int(rs['session_id']))
    if ch:
        old='\n'.join(f'・{d} {t}〜' for d,t in result['old_slots']); new='\n'.join(f'・{d} {t}〜' for d,t in result['new_slots'])
        await ch.send(f'開催日が変更されました！\n\n変更前：\n{old}\n\n変更後：\n{new}',silent=_session_feature_silent())
    schedule_session_reminder(int(rs['session_id']))
    return RedirectResponse(f"/session/{int(rs['session_id'])}/manage",303)


@app.get("/session/{session_id}/cancel", response_class=HTMLResponse)
async def session_cancel_form(session_id: int, request: Request):
    uid=request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/session/{session_id}/cancel")
    detail,_=session_management_detail(session_id)
    if not detail:
        raise HTTPException(404)
    if str(uid)!=str(detail['gm_discord_id']):
        raise HTTPException(403)
    return page("開催中止",f"""
      <div class='card' style='text-align:center'>
        <h2 style='text-align:center'>本当に開催中止にしますか？</h2>
        <form method='post' style='text-align:center'>
          {csrf_field(request)}
          <label style='display:flex;gap:9px;align-items:center;justify-content:center;margin:22px 0'>
            <input id='cancel-confirm' type='checkbox' name='confirmed' value='1' onchange="document.getElementById('cancel-submit').disabled=!this.checked">
            <span>確認しました</span>
          </label>
          <button id='cancel-submit' class='btn danger' style='display:flex;width:100%;min-height:58px;align-items:center;justify-content:center;text-align:center' type='submit' disabled>開催中止</button>
        </form>
      </div>
    """,request)


@app.post("/session/{session_id}/cancel")
async def session_cancel_submit(session_id: int, request: Request):
    uid=require_login(request)
    await require_csrf(request)
    detail,_=session_management_detail(session_id)
    if not detail or str(uid)!=str(detail['gm_discord_id']):
        raise HTTPException(403)
    form=await request.form()
    if str(form.get('confirmed') or '')!='1':
        raise HTTPException(400,"確認チェックが必要です")
    old_date=str(detail['event_date'])
    cancelled=cancel_confirmed_session(session_id,iso_now())
    if not cancelled:
        raise HTTPException(404)
    try:
        _session_change_reconcile_if_needed(old_date)
    except Exception as e:
        log_error(f"session_cancel_reconcile id={session_id}",e)
    ch=await _session_channel(session_id)
    if ch:
        await ch.send(
            f"『{detail['scenario_name']}』{int(detail['round_no'])}陣は開催中止となりました。\nGMはチャンネルの削除をお願いします。",
            silent=_session_feature_silent(),
        )
    return page("開催中止", "<div class='card'><h2>開催中止にしました。</h2><p class='muted'>チャンネルは削除していません。</p></div>", request)


@app.get("/r/{rid}/schedule/start", response_class=HTMLResponse)
async def schedule_start_form(rid: int, request: Request):
    uid = request.session.get("user_id")

    if not uid:
        return RedirectResponse(
            f"/login?next=/r/{rid}/schedule/start"
        )

    r = get_recruitment(rid)

    if not r:
        raise HTTPException(404)

    if str(uid) != str(r["gm_discord_id"]):
        return page(
            "日程調整",
            f"""
            <a class='back-link' href='/r/{rid}'>‹ 戻る</a>
            <div class='card'>
              <h2 style='font-size:1.2rem'>日程調整をお待ちください</h2>
              <p class='muted'>
                日程調整はまだ開始されていません。<br>
                GMからの案内をお待ちください。
              </p>
            </div>
            """,
            request,
        )

    if not int(r["schedule_pending"] or 0):
        return RedirectResponse(
            f"/r/{rid}/reschedule",
            status_code=303,
        )

    default_deadline = (
        now_jst().date() + timedelta(days=7)
    ).isoformat()

    weekday_jp = [
        "月", "火", "水", "木", "金", "土", "日"
    ]

    cards = []

    for ds in month_dates():
        d = date.fromisoformat(ds)
        label = (
            f"{d.month}/{d.day}"
            f"({weekday_jp[d.weekday()]})"
        )

        cards.append(
            f'<div class="day" '
            f'data-date="{ds}" '
            f'onclick="toggleGM(this)">'
            f'<span>{label}</span>'
            f'<span class="state">-</span>'
            f'</div>'
        )

    return page(
        "日程調整を開始",
        f"""
        <a class='back-link' href='/r/{rid}'>‹ 戻る</a>
        <div class='section-title'>日程調整を開始</div>

        <form class='form-shell'
              method='post'
              action='/r/{rid}/schedule/start'>
          {csrf_field(request)}

          <div class='form-section compact'>
            <div class='field-row'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>開始時間</span>
                    <input type='time'
                           name='start_time'
                           value='{esc(r["start_time"] or "21:00")}'
                           required>
                  </div>
                </div>
              </label>

              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>回答期限</span>
                    <input type='date'
                           name='deadline_date'
                           value='{default_deadline}'
                           required>
                  </div>
                </div>
              </label>
            </div>
          </div>

          <div class='create-date-heading'>
            開催候補日を選択（今月と来月末まで）
          </div>

          <input type='hidden'
                 id='gm_dates'
                 name='gm_dates'>

          <div class='date-scroll'>
            <div class='grid'>
              {''.join(cards)}
            </div>
          </div>

          <div class='legend'>
            <span>
              <b style='color:#22c55e'>○</b>
              開催できる
            </span>
            <span>
              <b>-</b>
              開催できない
            </span>
          </div>
          {advanced_schedule_controls_html()}

          <button class='submit-btn' type='submit'>
            日程調整を開始する
          </button>
        </form>

        <script>
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

          document.getElementById('gm_dates').value=
            selected.join(',');
          refreshAdvancedSlots();
        }}
        </script>
        """,
        request,
    )


@app.post("/r/{rid}/schedule/start")
async def schedule_start_submit(
    rid: int,
    request: Request,
    start_time: str = Form(...),
    deadline_date: str = Form(...),
    gm_dates: str = Form(...),
):
    uid = require_login(request)
    await require_csrf(request)

    r = get_recruitment(rid)

    if (
        not r
        or str(uid) != str(r["gm_discord_id"])
    ):
        raise HTTPException(403)

    if not int(r["schedule_pending"] or 0):
        raise HTTPException(
            400,
            "この卓の日程調整は既に開始されています"
        )

    dates = sorted({
        d
        for d in gm_dates.split(",")
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            d,
        )
    })

    if not dates:
        raise HTTPException(
            400,
            "開催可能日を1日以上選択してください"
        )
    adv_form = await request.form()
    per_day_time = str(adv_form.get('per_day_time') or '') == '1'
    schedule_slots = parse_schedule_slots(dates, start_time, per_day_time, str(adv_form.get('schedule_slots_json') or ''))

    deadline = datetime.fromisoformat(
        deadline_date + "T21:00:00"
    ).replace(tzinfo=JST)

    with db() as c:
        c.execute(
            "DELETE FROM gm_dates "
            "WHERE recruitment_id=?",
            (rid,),
        )

        c.executemany(
            "INSERT INTO gm_dates("
            "recruitment_id,event_date"
            ") VALUES(?,?)",
            [(rid, d) for d in dates],
        )

        c.execute(
            """UPDATE recruitments
               SET start_time=?,
                   deadline=?,
                   status='RECRUITING',
                   schedule_pending=0,
                   deadline_notified=0,
                   predeadline_notified=0,
                   availability_notified=0
               WHERE id=?""",
            (
                start_time,
                deadline.isoformat(),
                rid,
            ),
        )
    set_recruitment_schedule_slots(rid, schedule_slots, per_day_time)

    # 既存の日程調整チャンネルへ案内
    guild = bot.get_guild(GUILD_ID)
    channel = None

    updated = get_recruitment(rid)

    if guild and updated["waiting_channel_id"]:
        channel_id = int(
            updated["waiting_channel_id"]
        )

        channel = guild.get_channel(channel_id)

        if not channel:
            try:
                channel = await guild.fetch_channel(
                    channel_id
                )
            except Exception:
                channel = None

    # 万一チャンネルが消えていた場合だけ再作成
    if channel is None:
        channel = await create_waiting_channel(rid)

    await channel.send(
        "## 📅 日程調整を開始しました\n\n"
        f"開始時間：**{start_time}〜**\n"
        f"回答期限：**{deadline_date} 21:00**\n\n"
        "以下のリンクから日程を回答してください。\n"
        f"{BASE_URL}/r/{rid}"
    )

    print(
        f"[SCHEDULE START] rid={rid} "
        f"dates={len(dates)}",
        flush=True,
    )

    return RedirectResponse(
        f"/r/{rid}",
        status_code=303,
    )


@app.get("/r/{rid}/reschedule", response_class=HTMLResponse)
async def reschedule_form(rid: int, request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse(f"/login?next=/r/{rid}/reschedule")

    r = get_recruitment(rid)
    if not r or str(uid) != r["gm_discord_id"]:
        raise HTTPException(403)

    default_deadline = (now_jst().date() + timedelta(days=7)).isoformat()
    weekday_jp = ["月","火","水","木","金","土","日"]

    cards = []
    for ds in month_dates():
        d = date.fromisoformat(ds)
        label = f"{d.month}/{d.day}({weekday_jp[d.weekday()]})"
        cards.append(
            f'<div class="day" data-date="{ds}" onclick="toggleGM(this)">'
            f'<span>{label}</span><span class="state">-</span></div>'
        )

    return page(
        "再日程調整",
        f"""
        <a class='back-link' href='/r/{rid}'>‹ 戻る</a>
        <div class='section-title'>再日程調整</div>

        <form class='form-shell' method='post' action='/r/{rid}/reschedule'>
          {csrf_field(request)}

          <div class='form-section compact'>
            <div class='field-row'>
              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>開始時間</span>
                    <input type='time' name='start_time'
                           value='{esc(r["start_time"])}' required>
                  </div>
                </div>
              </label>

              <label>
                <div class='field-box no-icon'>
                  <div class='field-stack'>
                    <span class='field-label'>回答期限</span>
                    <input type='date' name='deadline_date'
                           value='{default_deadline}' required>
                  </div>
                </div>
              </label>
            </div>
          </div>

          <div class='create-date-heading'>
            開催候補日を選択（今月と来月末まで）
          </div>

          <input type='hidden' id='gm_dates' name='gm_dates'>
          <div class='date-scroll'>
            <div class='grid'>
              {''.join(cards)}
            </div>
          </div>

          <div class='legend'>
            <span><b style='color:#22c55e'>○</b> 開催できる</span>
            <span><b>-</b> 開催できない</span>
          </div>
          {advanced_schedule_controls_html()}

          <button class='submit-btn' type='submit'>
            再日程調整を開始する
          </button>
        </form>

        <script>
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
          refreshAdvancedSlots();
        }}
        </script>
        """,
        request,
    )


@app.post("/r/{rid}/reschedule")
async def reschedule_submit(
    rid: int,
    request: Request,
    start_time: str = Form(...),
    deadline_date: str = Form(...),
    gm_dates: str = Form(...),
):
    uid = require_login(request)
    await require_csrf(request)

    r = get_recruitment(rid)
    if not r or uid != r["gm_discord_id"]:
        raise HTTPException(403)

    dates = sorted({
        d for d in gm_dates.split(",")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
    })
    if not dates:
        raise HTTPException(400, "開催可能日を選択してください")
    adv_form = await request.form()
    per_day_time = str(adv_form.get('per_day_time') or '') == '1'
    schedule_slots = parse_schedule_slots(dates, start_time, per_day_time, str(adv_form.get('schedule_slots_json') or ''))

    deadline = datetime.fromisoformat(
        deadline_date + "T21:00:00"
    ).replace(tzinfo=JST)

    # 再日程調整時は、DBだけでなくDiscord上の「現在のリアクション」を
    # 元の募集投稿から読み直して新しい募集IDのmembersを作る。
    # Discord取得に失敗した場合だけ、既存DBをフォールバックとして使う。
    discord_reaction_members = await fetch_current_reaction_members(rid)

    with db() as c:
        old_members = c.execute(
            """SELECT discord_id, member_type, active, joined_at
               FROM members
               WHERE recruitment_id=? AND active=1""",
            (rid,),
        ).fetchall()

        cur = c.execute(
            """INSERT INTO recruitments(
                parent_id,
                game_type,
                scenario_name,
                gm_discord_id,
                min_players,
                max_players,
                variable_players,
                play_time,
                description,
                guide_message,
                image_path,
                start_time,
                deadline,
                status,
                created_at,
                waiting_channel_id,
                simple_schedule,
                target_players,
                gm_name_override,
                target_channel_id,
                calendar_visible
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                r["game_type"],
                r["scenario_name"],
                uid,
                r["min_players"],
                r["max_players"],
                r["variable_players"],
                r["play_time"],
                r["description"],
                r["guide_message"],
                r["image_path"],
                start_time,
                deadline.isoformat(),
                "RECRUITING",
                iso_now(),
                r["waiting_channel_id"],
                r["simple_schedule"],
                r["target_players"],
                r["gm_name_override"],
                r["target_channel_id"],
                r["calendar_visible"],
            ),
        )
        new_id = cur.lastrowid

        c.executemany(
            "INSERT INTO gm_dates(recruitment_id,event_date) VALUES(?,?)",
            [(new_id, d) for d in dates],
        )

        old_images = c.execute(
            """SELECT image_path, sort_order
               FROM recruitment_images
               WHERE recruitment_id=?
               ORDER BY sort_order""",
            (rid,),
        ).fetchall()

        if old_images:
            c.executemany(
                """INSERT OR IGNORE INTO recruitment_images(
                       recruitment_id,image_path,sort_order
                   ) VALUES(?,?,?)""",
                [
                    (new_id, x["image_path"], x["sort_order"])
                    for x in old_images
                ],
            )
        elif r["image_path"]:
            c.execute(
                """INSERT OR IGNORE INTO recruitment_images(
                       recruitment_id,image_path,sort_order
                   ) VALUES(?,?,0)""",
                (new_id, r["image_path"]),
            )

        # 再日程調整では、まずDBに既にいる参加者・観戦者を必ず引き継ぐ。
        # そのうえでDiscordの現在のリアクションから取得できた人を追加する。
        # これにより、Discord取得が0件/不完全でも既存参加者を失わない。
        for m in old_members:
            c.execute(
                """INSERT INTO members(
                    recruitment_id,
                    discord_id,
                    member_type,
                    active,
                    joined_at
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(recruitment_id,discord_id,member_type)
                DO UPDATE SET active=1""",
                (
                    new_id,
                    m["discord_id"],
                    m["member_type"],
                    1,
                    m["joined_at"] or iso_now(),
                ),
            )

        if discord_reaction_members is not None:
            for member_type in ("participant", "spectator"):
                for discord_id in sorted(discord_reaction_members[member_type]):
                    c.execute(
                        """INSERT INTO members(
                            recruitment_id,
                            discord_id,
                            member_type,
                            active,
                            joined_at
                        )
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(recruitment_id,discord_id,member_type)
                        DO UPDATE SET active=1,joined_at=excluded.joined_at""",
                        (
                            new_id,
                            discord_id,
                            member_type,
                            1,
                            iso_now(),
                        ),
                    )

        c.execute(
            "UPDATE recruitments SET status='RESCHEDULED' WHERE id=?",
            (rid,),
        )
    set_recruitment_schedule_slots(new_id, schedule_slots, per_day_time)

    guild = bot.get_guild(GUILD_ID)
    channel = None

    # 元シナリオの日程調整チャンネルをそのまま使う
    if guild and r["waiting_channel_id"]:
        channel_id = int(r["waiting_channel_id"])
        channel = guild.get_channel(channel_id)

        if not channel:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

    # 元の日程調整チャンネルが消えていた場合のみ再作成
    if channel is None:
        await create_waiting_channel(new_id)
        nr = get_recruitment(new_id)

        if guild and nr and nr["waiting_channel_id"]:
            channel_id = int(nr["waiting_channel_id"])
            channel = guild.get_channel(channel_id)

            if not channel:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except Exception:
                    channel = None

    if not channel:
        raise HTTPException(
            500,
            "日程調整チャンネルを取得できませんでした",
        )

    try:
        await send_long(
            channel,
            (
                "## 🔄 再日程調整\n\n"
                f"『{r['scenario_name']}』の再日程調整を開始しました。\n\n"
                f"開始時間：**{start_time}〜**\n"
                f"回答期限：**{deadline_date} 21:00**\n\n"
                "以下のリンクから新しい日程を回答してください。\n"
                f"{BASE_URL}/r/{new_id}"
            ),
        )
    except Exception as e:
        log_error(f"reschedule_message rid={new_id}", e)
        raise HTTPException(
            500,
            "日程調整チャンネルへの投稿に失敗しました",
        )

    source_label = (
        "db+discord-reactions"
        if discord_reaction_members is not None
        else "db-only"
    )
    print(
        f"[RESCHEDULE] old_rid={rid} new_rid={new_id} "
        f"channel={channel.id} members_source={source_label}",
        flush=True,
    )

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

# v62 note: UI CSS override is injected above at source-generation time.
