from __future__ import annotations

import os
import sqlite3
import hashlib
import json
import unicodedata
from contextlib import contextmanager

# ============================================================
# つぶ卓 Database
# SQLite / Railway Volume 管理
# ============================================================

DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/tsubutaku.db")

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

            CREATE TABLE IF NOT EXISTS registered_members (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                avatar_url TEXT,
                created_at TEXT NOT NULL,
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
                schedule_pending INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES recruitments(id)
            );

            CREATE TABLE IF NOT EXISTS recruitment_images (
                recruitment_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(recruitment_id, image_path),
                FOREIGN KEY(recruitment_id) REFERENCES recruitments(id) ON DELETE CASCADE
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

            CREATE TABLE IF NOT EXISTS answer_submissions (
                recruitment_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
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

            -- v85: 成立後の日程変更・開催中止管理
            CREATE TABLE IF NOT EXISTS session_reschedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                deadline TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL,
                confirmed_date TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS session_reschedule_dates (
                reschedule_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                PRIMARY KEY(reschedule_id,event_date),
                FOREIGN KEY(reschedule_id) REFERENCES session_reschedules(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS session_reschedule_answers (
                reschedule_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                answer TEXT NOT NULL CHECK(answer IN ('YES','MAYBE','NO')),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(reschedule_id,discord_id,event_date),
                FOREIGN KEY(reschedule_id) REFERENCES session_reschedules(id) ON DELETE CASCADE
            );


            -- 成立卓の永久保存用。recruitments/sessionsが90日後に消えても残す。
            CREATE TABLE IF NOT EXISTS calendar_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_session_id INTEGER UNIQUE,
                source_recruitment_id INTEGER,
                game_type TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT,
                gm_discord_id TEXT NOT NULL,
                calendar_visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_session_members (
                calendar_session_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                PRIMARY KEY(calendar_session_id, discord_id),
                FOREIGN KEY(calendar_session_id) REFERENCES calendar_sessions(id) ON DELETE CASCADE
            );

            -- カレンダー表示専用の一時メンバー。users/registered_membersには追加しない。
            CREATE TABLE IF NOT EXISTS calendar_guest_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calendar_session_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                FOREIGN KEY(calendar_session_id) REFERENCES calendar_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS calendar_deleted_sources (
                source_session_id INTEGER PRIMARY KEY,
                deleted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scenario_progress (
                game_type TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PASSED','WATCHED')),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(game_type, scenario_name, discord_id)
            );

            CREATE INDEX IF NOT EXISTS idx_scenario_progress_type_name
                ON scenario_progress(game_type, scenario_name);

            -- v65: プロフィール / 称号
            CREATE TABLE IF NOT EXISTS achievement_unlocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                achievement_key TEXT NOT NULL,
                context_key TEXT NOT NULL DEFAULT '',
                title_name TEXT NOT NULL,
                rarity TEXT NOT NULL,
                secret INTEGER NOT NULL DEFAULT 0,
                context_label TEXT,
                unlocked_at TEXT NOT NULL,
                UNIQUE(discord_id, achievement_key, context_key)
            );

            CREATE TABLE IF NOT EXISTS equipped_titles (
                discord_id TEXT PRIMARY KEY,
                unlock_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(unlock_id) REFERENCES achievement_unlocks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS achievement_daily_runs (
                run_date TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS achievement_meta (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_achievement_unlocks_user
                ON achievement_unlocks(discord_id);

            -- v68: 20時更新の表示専用プロフィールキャッシュ
            CREATE TABLE IF NOT EXISTS profile_stats_cache (
                discord_id TEXT PRIMARY KEY,
                gm_count INTEGER NOT NULL DEFAULT 0,
                pl_count INTEGER NOT NULL DEFAULT 0,
                trpg_count INTEGER NOT NULL DEFAULT 0,
                madamis_count INTEGER NOT NULL DEFAULT 0,
                trpg_pl_count INTEGER NOT NULL DEFAULT 0,
                madamis_pl_count INTEGER NOT NULL DEFAULT 0,
                total_roles INTEGER NOT NULL DEFAULT 0,
                max_pair INTEGER NOT NULL DEFAULT 0,
                active_years INTEGER NOT NULL DEFAULT 0,
                max_day INTEGER NOT NULL DEFAULT 0,
                max_streak INTEGER NOT NULL DEFAULT 0,
                christmas INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_year_stats_cache (
                discord_id TEXT NOT NULL,
                activity_year INTEGER NOT NULL,
                term INTEGER NOT NULL,
                gm_count INTEGER NOT NULL DEFAULT 0,
                pl_count INTEGER NOT NULL DEFAULT 0,
                trpg_count INTEGER NOT NULL DEFAULT 0,
                madamis_count INTEGER NOT NULL DEFAULT 0,
                trpg_pl_count INTEGER NOT NULL DEFAULT 0,
                madamis_pl_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(discord_id, activity_year)
            );

            CREATE TABLE IF NOT EXISTS profile_pair_cache (
                discord_id TEXT NOT NULL,
                partner_discord_id TEXT NOT NULL,
                table_count INTEGER NOT NULL DEFAULT 0,
                rank_no INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(discord_id, partner_discord_id)
            );

            CREATE INDEX IF NOT EXISTS idx_profile_pair_cache_user_rank
                ON profile_pair_cache(discord_id, rank_no);

            -- v69: 日次差分更新用。過去履歴を毎日再走査しないための小さな集計テーブル
            CREATE TABLE IF NOT EXISTS profile_processed_tables (
                table_key TEXT PRIMARY KEY,
                event_date TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_pair_totals (
                discord_id TEXT NOT NULL,
                partner_discord_id TEXT NOT NULL,
                table_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(discord_id, partner_discord_id)
            );

            CREATE TABLE IF NOT EXISTS profile_active_days (
                discord_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                table_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(discord_id, event_date)
            );

            CREATE TABLE IF NOT EXISTS profile_active_years (
                discord_id TEXT NOT NULL,
                activity_year INTEGER NOT NULL,
                PRIMARY KEY(discord_id, activity_year)
            );

            CREATE INDEX IF NOT EXISTS idx_profile_processed_tables_date
                ON profile_processed_tables(event_date);
            CREATE INDEX IF NOT EXISTS idx_profile_pair_totals_user_count
                ON profile_pair_totals(discord_id, table_count DESC);
            CREATE INDEX IF NOT EXISTS idx_profile_active_days_user_date
                ON profile_active_days(discord_id, event_date);

            -- v70: 同じシナリオをGMした回数を差分で保持。毎日の全履歴走査は不要。
            CREATE TABLE IF NOT EXISTS achievement_scenario_gm_totals (
                discord_id TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                gm_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(discord_id, scenario_name)
            );
            CREATE INDEX IF NOT EXISTS idx_achievement_scenario_gm_user_count
                ON achievement_scenario_gm_totals(discord_id, gm_count DESC);

            CREATE INDEX IF NOT EXISTS idx_calendar_sessions_event_date
                ON calendar_sessions(event_date);
            CREATE INDEX IF NOT EXISTS idx_calendar_session_members_session
                ON calendar_session_members(calendar_session_id);
            CREATE INDEX IF NOT EXISTS idx_calendar_session_members_user
                ON calendar_session_members(discord_id);
            CREATE INDEX IF NOT EXISTS idx_calendar_guest_members_session
                ON calendar_guest_members(calendar_session_id);
            CREATE INDEX IF NOT EXISTS idx_calendar_sessions_gm
                ON calendar_sessions(gm_discord_id);
            """
        )

        # 旧バージョンで保存済みの単一画像を複数画像テーブルへ自動移行
        c.execute(
            """INSERT OR IGNORE INTO recruitment_images(recruitment_id,image_path,sort_order)
               SELECT id,image_path,0
               FROM recruitments
               WHERE image_path IS NOT NULL AND image_path<>''"""
        )



def ensure_recruitment_columns():
    with db() as c:
        cols = {
            r["name"]
            for r in c.execute(
                "PRAGMA table_info(recruitments)"
            ).fetchall()
        }

        if "availability_notified" not in cols:
            c.execute(
                "ALTER TABLE recruitments "
                "ADD COLUMN availability_notified INTEGER NOT NULL DEFAULT 0"
            )

        if "schedule_pending" not in cols:
            c.execute(
                "ALTER TABLE recruitments "
                "ADD COLUMN schedule_pending INTEGER NOT NULL DEFAULT 0"
            )
        if "simple_schedule" not in cols:
            c.execute("ALTER TABLE recruitments ADD COLUMN simple_schedule INTEGER NOT NULL DEFAULT 0")
        if "target_players" not in cols:
            c.execute("ALTER TABLE recruitments ADD COLUMN target_players INTEGER")
        if "gm_name_override" not in cols:
            c.execute("ALTER TABLE recruitments ADD COLUMN gm_name_override TEXT")
        if "target_channel_id" not in cols:
            c.execute("ALTER TABLE recruitments ADD COLUMN target_channel_id TEXT")
        if "target_message_id" not in cols:
            c.execute("ALTER TABLE recruitments ADD COLUMN target_message_id TEXT")
        if "calendar_visible" not in cols:
            c.execute(
                "ALTER TABLE recruitments "
                "ADD COLUMN calendar_visible INTEGER NOT NULL DEFAULT 0"
            )


def ensure_session_columns():
    """v85: 既存sessionsへ開催中止状態を安全に追加する。"""
    with db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
        if "cancelled_at" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN cancelled_at TEXT")


def ensure_profile_cache_columns():
    """v73: 既存の表示キャッシュへPL専用内訳列を安全に追加する。

    trpg_count / madamis_count は v73 以降「GM+PLで参加した卓数」。
    称号判定用のPL専用値は別列に保持する。
    """
    with db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(profile_stats_cache)").fetchall()}
        if "trpg_pl_count" not in cols:
            c.execute("ALTER TABLE profile_stats_cache ADD COLUMN trpg_pl_count INTEGER NOT NULL DEFAULT 0")
        if "madamis_pl_count" not in cols:
            c.execute("ALTER TABLE profile_stats_cache ADD COLUMN madamis_pl_count INTEGER NOT NULL DEFAULT 0")

        ycols = {r["name"] for r in c.execute("PRAGMA table_info(profile_year_stats_cache)").fetchall()}
        if "trpg_pl_count" not in ycols:
            c.execute("ALTER TABLE profile_year_stats_cache ADD COLUMN trpg_pl_count INTEGER NOT NULL DEFAULT 0")
        if "madamis_pl_count" not in ycols:
            c.execute("ALTER TABLE profile_year_stats_cache ADD COLUMN madamis_pl_count INTEGER NOT NULL DEFAULT 0")


def ensure_calendar_columns():
    """既存のカレンダー履歴テーブルへ追加列を安全に足す。"""
    with db() as c:
        cols = {
            r["name"]
            for r in c.execute(
                "PRAGMA table_info(calendar_sessions)"
            ).fetchall()
        }
        if "start_time" not in cols:
            c.execute(
                "ALTER TABLE calendar_sessions "
                "ADD COLUMN start_time TEXT"
            )
        if "gm_guest_name" not in cols:
            c.execute(
                "ALTER TABLE calendar_sessions "
                "ADD COLUMN gm_guest_name TEXT NOT NULL DEFAULT ''"
            )


def backfill_calendar_history():
    """現在残っている成立卓を永久保存テーブルへ一度だけ移す。"""
    with db() as c:
        rows = c.execute(
            """SELECT s.id AS session_id,
                      s.recruitment_id,
                      s.event_date,
                      s.start_time,
                      s.created_at,
                      r.game_type,
                      r.scenario_name,
                      r.gm_discord_id
               FROM sessions s
               JOIN recruitments r ON r.id=s.recruitment_id
               LEFT JOIN calendar_deleted_sources cds
                 ON cds.source_session_id=s.id
               WHERE cds.source_session_id IS NULL"""
        ).fetchall()

        for row in rows:
            c.execute(
                """INSERT OR IGNORE INTO calendar_sessions(
                       source_session_id,source_recruitment_id,game_type,
                       scenario_name,event_date,start_time,gm_discord_id,calendar_visible,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    row["session_id"], row["recruitment_id"],
                    row["game_type"], row["scenario_name"],
                    row["event_date"], row["start_time"], row["gm_discord_id"],
                    0 if row["game_type"] == "EVENT" else 1,
                    row["created_at"],
                ),
            )
            cal = c.execute(
                "SELECT id FROM calendar_sessions WHERE source_session_id=?",
                (row["session_id"],),
            ).fetchone()
            if not cal:
                continue
            members = c.execute(
                "SELECT discord_id FROM session_members WHERE session_id=?",
                (row["session_id"],),
            ).fetchall()
            c.executemany(
                "INSERT OR IGNORE INTO calendar_session_members(calendar_session_id,discord_id) VALUES(?,?)",
                [(cal["id"], m["discord_id"]) for m in members],
            )

        # v43-v45ですでに保存済みの履歴にも、元sessionsが残っていれば開始時間を補完。
        c.execute(
            """UPDATE calendar_sessions
               SET start_time=(
                   SELECT s.start_time
                   FROM sessions s
                   WHERE s.id=calendar_sessions.source_session_id
               )
               WHERE (start_time IS NULL OR start_time='')
                 AND source_session_id IS NOT NULL"""
        )


def archive_confirmed_session(
    source_session_id: int,
    source_recruitment_id: int,
    game_type: str,
    scenario_name: str,
    event_date: str,
    start_time: str,
    gm_discord_id: str,
    participant_ids: list[str],
    created_at: str,
    calendar_visible: bool | None = None,
) -> int:
    """成立卓をカレンダー/履歴用DBへ永久保存する。"""
    with db() as c:
        deleted = c.execute(
            "SELECT 1 FROM calendar_deleted_sources WHERE source_session_id=?",
            (source_session_id,),
        ).fetchone()
        if deleted:
            return 0

        visible = (
            bool(calendar_visible)
            if calendar_visible is not None
            else game_type != "EVENT"
        )
        c.execute(
            """INSERT INTO calendar_sessions(
                   source_session_id,source_recruitment_id,game_type,scenario_name,
                   event_date,start_time,gm_discord_id,calendar_visible,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_session_id) DO UPDATE SET
                 game_type=excluded.game_type,
                 scenario_name=excluded.scenario_name,
                 event_date=excluded.event_date,
                 start_time=excluded.start_time,
                 gm_discord_id=excluded.gm_discord_id,
                 calendar_visible=excluded.calendar_visible""",
            (
                source_session_id, source_recruitment_id, game_type, scenario_name,
                event_date, start_time, gm_discord_id, 1 if visible else 0, created_at,
            ),
        )
        cal = c.execute(
            "SELECT id FROM calendar_sessions WHERE source_session_id=?",
            (source_session_id,),
        ).fetchone()
        calendar_session_id = int(cal["id"])
        c.execute(
            "DELETE FROM calendar_session_members WHERE calendar_session_id=?",
            (calendar_session_id,),
        )
        c.executemany(
            "INSERT OR IGNORE INTO calendar_session_members(calendar_session_id,discord_id) VALUES(?,?)",
            [(calendar_session_id, str(uid)) for uid in dict.fromkeys(participant_ids)],
        )

        gt = normalize_progress_game_type(game_type)
        if gt in {"TRPG", "MADMIS"} and str(scenario_name or "").strip():
            for participant_id in dict.fromkeys(participant_ids):
                c.execute(
                    """INSERT INTO scenario_progress(
                           game_type,scenario_name,discord_id,status,updated_at
                       ) VALUES(?,?,?,?,?)
                       ON CONFLICT(game_type,scenario_name,discord_id)
                       DO UPDATE SET status='PASSED',updated_at=excluded.updated_at""",
                    (
                        gt,
                        scenario_name,
                        str(participant_id),
                        "PASSED",
                        created_at,
                    ),
                )
        return calendar_session_id


def calendar_entries(start_date: str, end_date: str):
    """指定期間の公開カレンダー用成立卓を取得する。"""
    with db() as c:
        rows = c.execute(
            """SELECT cs.*,
                      CASE WHEN COALESCE(cs.gm_guest_name,'')<>'' THEN cs.gm_guest_name
                           ELSE COALESCE(u.display_name,u.username,cs.gm_discord_id,'') END AS gm_name
               FROM calendar_sessions cs
               LEFT JOIN users u ON u.discord_id=cs.gm_discord_id
               WHERE cs.calendar_visible=1
                 AND cs.event_date>=? AND cs.event_date<?
               ORDER BY cs.event_date, cs.id""",
            (start_date, end_date),
        ).fetchall()
        out = []
        for row in rows:
            members = [dict(x) for x in c.execute(
                """SELECT csm.discord_id,
                          COALESCE(u.display_name,u.username,csm.discord_id) AS display_name,
                          0 AS is_guest
                   FROM calendar_session_members csm
                   LEFT JOIN users u ON u.discord_id=csm.discord_id
                   WHERE csm.calendar_session_id=?
                   ORDER BY display_name""", (row["id"],)).fetchall()]
            guests = c.execute(
                "SELECT display_name FROM calendar_guest_members WHERE calendar_session_id=? ORDER BY id",
                (row["id"],)).fetchall()
            members.extend({"discord_id":"", "display_name":str(g["display_name"]), "is_guest":1} for g in guests)
            out.append((row, members))
        return out

def calendar_conflicts_for_users(discord_ids: list[str], event_dates: list[str]) -> set[tuple[str, str]]:
    """複数ユーザーについて、(discord_id, 日付) の重複予定セットを返す。"""
    users = [str(x) for x in dict.fromkeys(discord_ids) if x]
    dates = [str(x) for x in dict.fromkeys(event_dates) if x]
    if not users or not dates:
        return set()

    user_ph = ",".join("?" for _ in users)
    date_ph = ",".join("?" for _ in dates)

    with db() as c:
        rows = c.execute(
            f"""SELECT DISTINCT cs.gm_discord_id AS discord_id, cs.event_date
                FROM calendar_sessions cs
                WHERE cs.gm_discord_id IN ({user_ph})
                  AND cs.event_date IN ({date_ph})
                UNION
                SELECT DISTINCT csm.discord_id AS discord_id, cs.event_date
                FROM calendar_sessions cs
                JOIN calendar_session_members csm
                  ON csm.calendar_session_id=cs.id
                WHERE csm.discord_id IN ({user_ph})
                  AND cs.event_date IN ({date_ph})""",
            [*users, *dates, *users, *dates],
        ).fetchall()

    return {(str(x["discord_id"]), str(x["event_date"])) for x in rows}


def calendar_conflict_dates(discord_id: str, event_dates: list[str]) -> set[str]:
    """ユーザーがGMまたはPLとして既に成立卓を持つ日付を返す。"""
    clean_dates = [str(x) for x in dict.fromkeys(event_dates) if x]
    if not clean_dates:
        return set()

    placeholders = ",".join("?" for _ in clean_dates)
    params = [str(discord_id), str(discord_id), *clean_dates]

    with db() as c:
        rows = c.execute(
            f"""SELECT DISTINCT cs.event_date
                FROM calendar_sessions cs
                LEFT JOIN calendar_session_members csm
                  ON csm.calendar_session_id=cs.id
                WHERE (cs.gm_discord_id=? OR csm.discord_id=?)
                  AND cs.event_date IN ({placeholders})""",
            params,
        ).fetchall()

    return {str(x["event_date"]) for x in rows}




def registered_member(discord_id: str):
    with db() as c:
        return c.execute("SELECT * FROM registered_members WHERE discord_id=?", (str(discord_id),)).fetchone()

def registered_members():
    with db() as c:
        return c.execute("""SELECT * FROM registered_members
            WHERE LOWER(COALESCE(display_name,'')) <> 'okuyama'
              AND LOWER(COALESCE(username,'')) <> 'okuyama'
            ORDER BY display_name COLLATE NOCASE""").fetchall()

def upsert_registered_member(discord_id, username, display_name, avatar_url, now_iso):
    uid=str(discord_id)
    with db() as c:
        c.execute("""INSERT INTO registered_members(discord_id,username,display_name,avatar_url,created_at,updated_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,
                     display_name=excluded.display_name,avatar_url=excluded.avatar_url,updated_at=excluded.updated_at""",
                  (uid,username,display_name,avatar_url,now_iso,now_iso))
        c.execute("""INSERT INTO users(discord_id,username,display_name,avatar_url,updated_at) VALUES(?,?,?,?,?)
                     ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,
                     display_name=excluded.display_name,avatar_url=excluded.avatar_url,updated_at=excluded.updated_at""",
                  (uid,username,display_name,avatar_url,now_iso))

def refresh_registered_member_profile(discord_id, username, display_name, avatar_url, now_iso):
    uid=str(discord_id)
    with db() as c:
        if not c.execute("SELECT 1 FROM registered_members WHERE discord_id=?",(uid,)).fetchone():
            return False
        c.execute("UPDATE registered_members SET username=?,display_name=?,avatar_url=?,updated_at=? WHERE discord_id=?",
                  (username,display_name,avatar_url,now_iso,uid))
        c.execute("""INSERT INTO users(discord_id,username,display_name,avatar_url,updated_at) VALUES(?,?,?,?,?)
                     ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username,
                     display_name=excluded.display_name,avatar_url=excluded.avatar_url,updated_at=excluded.updated_at""",
                  (uid,username,display_name,avatar_url,now_iso))
    return True

def calendar_manual_options():
    """手動追加フォーム用のシナリオ候補・ユーザー候補。"""
    with db() as c:
        scenarios = c.execute(
            """SELECT DISTINCT game_type, scenario_name
               FROM calendar_sessions
               WHERE game_type IN ('TRPG','MADMIS','マダミス')
                 AND scenario_name<>''
               ORDER BY game_type, scenario_name"""
        ).fetchall()
        users = c.execute(
            """SELECT discord_id,
                      COALESCE(display_name, username, discord_id) AS display_name
               FROM registered_members
               WHERE LOWER(COALESCE(display_name,'')) <> 'okuyama'
                 AND LOWER(COALESCE(username,'')) <> 'okuyama'
               ORDER BY display_name"""
        ).fetchall()
    return scenarios, users


def add_manual_calendar_session(
    game_type: str, scenario_name: str, event_date: str, start_time: str,
    gm_discord_id: str, participant_ids: list[str], created_at: str,
    gm_guest_name: str = "", guest_participant_names: list[str] | None = None,
) -> int:
    """Discordへ送らずカレンダーへ追加。guestは表示専用で集計対象外。"""
    guest_participant_names = guest_participant_names or []
    with db() as c:
        cur = c.execute(
            """INSERT INTO calendar_sessions(
                   source_session_id,source_recruitment_id,game_type,scenario_name,event_date,start_time,
                   gm_discord_id,gm_guest_name,calendar_visible,created_at
               ) VALUES(NULL,NULL,?,?,?,?,?,?,?,?)""",
            (game_type, scenario_name.strip(), event_date, start_time or "未定",
             str(gm_discord_id or ""), gm_guest_name.strip(), 1, created_at),
        )
        cal_id = int(cur.lastrowid)
        member_ids = [str(uid) for uid in dict.fromkeys(participant_ids)
                      if uid and str(uid) != str(gm_discord_id)]
        if member_ids:
            c.executemany("INSERT OR IGNORE INTO calendar_session_members(calendar_session_id,discord_id) VALUES(?,?)",
                          [(cal_id, uid) for uid in member_ids])
            gt = normalize_progress_game_type(game_type)
            if gt in {"TRPG", "MADMIS"} and scenario_name.strip():
                for uid in member_ids:
                    c.execute(
                        """INSERT INTO scenario_progress(game_type,scenario_name,discord_id,status,updated_at)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(game_type,scenario_name,discord_id)
                           DO UPDATE SET status='PASSED',updated_at=excluded.updated_at""",
                        (gt, scenario_name.strip(), uid, "PASSED", created_at),
                    )
        guest_names = [x.strip() for x in dict.fromkeys(guest_participant_names) if x.strip()]
        if guest_names:
            c.executemany("INSERT INTO calendar_guest_members(calendar_session_id,display_name) VALUES(?,?)",
                          [(cal_id, name) for name in guest_names])
        return cal_id

def calendar_session_detail(calendar_session_id: int):
    with db() as c:
        row = c.execute(
            """SELECT cs.*, CASE WHEN COALESCE(cs.gm_guest_name,'')<>'' THEN cs.gm_guest_name
                      ELSE COALESCE(u.display_name,u.username,cs.gm_discord_id,'') END AS gm_name
               FROM calendar_sessions cs LEFT JOIN users u ON u.discord_id=cs.gm_discord_id WHERE cs.id=?""",
            (calendar_session_id,),).fetchone()
        if not row: return None, []
        members = [dict(x) for x in c.execute(
            """SELECT csm.discord_id, COALESCE(u.display_name,u.username,csm.discord_id) AS display_name, 0 AS is_guest
               FROM calendar_session_members csm LEFT JOIN users u ON u.discord_id=csm.discord_id
               WHERE csm.calendar_session_id=? ORDER BY display_name""", (calendar_session_id,)).fetchall()]
        guests = c.execute("SELECT display_name FROM calendar_guest_members WHERE calendar_session_id=? ORDER BY id",
                           (calendar_session_id,)).fetchall()
        members.extend({"discord_id":"", "display_name":str(g["display_name"]), "is_guest":1} for g in guests)
    return row, members

def update_calendar_session_details(calendar_session_id: int, scenario_name: str, gm_discord_id: str,
                                    participant_ids: list[str], gm_guest_name: str = "",
                                    guest_participant_names: list[str] | None = None,
                                    game_type: str | None = None, event_date: str | None = None):
    guest_participant_names = guest_participant_names or []
    with db() as c:
        row = c.execute("SELECT game_type FROM calendar_sessions WHERE id=?", (calendar_session_id,)).fetchone()
        if not row: return False
        normalized_game_type = game_type or str(row["game_type"] or "TRPG")
        if normalized_game_type == "マダミス": normalized_game_type = "MADMIS"
        if normalized_game_type not in {"TRPG", "MADMIS", "EVENT"}: return False
        if event_date is not None:
            c.execute("UPDATE calendar_sessions SET game_type=?, scenario_name=?, gm_discord_id=?, gm_guest_name=?, event_date=? WHERE id=?",
                      (normalized_game_type, scenario_name.strip(), str(gm_discord_id or ""), gm_guest_name.strip(), str(event_date), calendar_session_id))
        else:
            c.execute("UPDATE calendar_sessions SET game_type=?, scenario_name=?, gm_discord_id=?, gm_guest_name=? WHERE id=?",
                      (normalized_game_type, scenario_name.strip(), str(gm_discord_id or ""), gm_guest_name.strip(), calendar_session_id))
        c.execute("DELETE FROM calendar_session_members WHERE calendar_session_id=?", (calendar_session_id,))
        c.execute("DELETE FROM calendar_guest_members WHERE calendar_session_id=?", (calendar_session_id,))
        cleaned=[str(uid) for uid in dict.fromkeys(participant_ids) if uid and str(uid)!=str(gm_discord_id)]
        if cleaned:
            c.executemany("INSERT OR IGNORE INTO calendar_session_members(calendar_session_id,discord_id) VALUES(?,?)",
                          [(calendar_session_id,uid) for uid in cleaned])
            gt = normalize_progress_game_type(normalized_game_type)
            if gt in {"TRPG", "MADMIS"} and scenario_name.strip():
                info = c.execute("SELECT created_at FROM calendar_sessions WHERE id=?", (calendar_session_id,)).fetchone()
                stamp = str(info["created_at"] or "") if info else ""
                for uid in cleaned:
                    c.execute(
                        """INSERT INTO scenario_progress(game_type,scenario_name,discord_id,status,updated_at)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(game_type,scenario_name,discord_id)
                           DO UPDATE SET status='PASSED',updated_at=excluded.updated_at""",
                        (gt, scenario_name.strip(), uid, "PASSED", stamp),
                    )
        guests=[x.strip() for x in dict.fromkeys(guest_participant_names) if x.strip()]
        if guests:
            c.executemany("INSERT INTO calendar_guest_members(calendar_session_id,display_name) VALUES(?,?)",
                          [(calendar_session_id,name) for name in guests])
    return True

def update_calendar_session_members(calendar_session_id: int, participant_ids: list[str]):
    # 旧呼び出し互換
    row, members = calendar_session_detail(calendar_session_id)
    if not row: return False
    return update_calendar_session_details(calendar_session_id, str(row["scenario_name"]),
        str(row["gm_discord_id"] or ""), participant_ids, str(row["gm_guest_name"] or ""),
        [str(m["display_name"]) for m in members if m.get("is_guest")])

def hide_calendar_session(calendar_session_id: int):
    with db() as c:
        cur = c.execute(
            "UPDATE calendar_sessions SET calendar_visible=0 WHERE id=?",
            (calendar_session_id,),
        )
    return cur.rowcount > 0


def permanently_delete_calendar_session(calendar_session_id: int, deleted_at: str):
    """
    カレンダー履歴を完全削除する。

    - source session が残っていても backfill で復活しないよう tombstone を残す。
    - 削除卓だけを根拠に自動付与されていた PASSED は、同じ人・同じシナリオの
      別カレンダー卓が残っていない場合に scenario_progress から除外する。
    - calendar_stats は calendar_sessions を直接集計するため、削除後は累計からも外れる。
    """
    with db() as c:
        row = c.execute(
            """SELECT source_session_id, game_type, scenario_name
               FROM calendar_sessions WHERE id=?""",
            (calendar_session_id,),
        ).fetchone()
        if not row:
            return False

        source_session_id = row["source_session_id"]
        gt = normalize_progress_game_type(row["game_type"])
        scenario_name = str(row["scenario_name"] or "").strip()
        member_rows = c.execute(
            "SELECT discord_id FROM calendar_session_members WHERE calendar_session_id=?",
            (calendar_session_id,),
        ).fetchall()
        deleted_member_ids = [str(x["discord_id"]) for x in member_rows if x["discord_id"]]

        if source_session_id is not None:
            c.execute(
                """INSERT INTO calendar_deleted_sources(source_session_id,deleted_at)
                   VALUES(?,?)
                   ON CONFLICT(source_session_id) DO UPDATE SET
                     deleted_at=excluded.deleted_at""",
                (int(source_session_id), deleted_at),
            )

        c.execute(
            "DELETE FROM calendar_session_members WHERE calendar_session_id=?",
            (calendar_session_id,),
        )
        c.execute(
            "DELETE FROM calendar_guest_members WHERE calendar_session_id=?",
            (calendar_session_id,),
        )
        c.execute(
            "DELETE FROM calendar_sessions WHERE id=?",
            (calendar_session_id,),
        )

        # 通過済みリストはカレンダー履歴と連動させる。
        # 同じシナリオの別開催が1件でも残っている間は、そのシナリオの進捗を維持する。
        # 最後の1件を削除した時点で、古いバージョン由来の残骸も含めて進捗を全削除する。
        if gt in {"TRPG", "MADMIS"} and scenario_name:
            scenario_still_exists = c.execute(
                """SELECT 1
                   FROM calendar_sessions cs
                   WHERE (CASE WHEN cs.game_type='マダミス' THEN 'MADMIS' ELSE cs.game_type END)=?
                     AND TRIM(cs.scenario_name)=?
                   LIMIT 1""",
                (gt, scenario_name),
            ).fetchone()

            if not scenario_still_exists:
                c.execute(
                    """DELETE FROM scenario_progress
                       WHERE game_type=? AND TRIM(scenario_name)=?""",
                    (gt, scenario_name),
                )
            else:
                # シナリオ自体は残っているが、このPLの別参加履歴が無ければ自動PASSEDだけ外す。
                for uid in deleted_member_ids:
                    still_has_session = c.execute(
                        """SELECT 1
                           FROM calendar_sessions cs
                           JOIN calendar_session_members csm
                             ON csm.calendar_session_id=cs.id
                           WHERE (CASE WHEN cs.game_type='マダミス' THEN 'MADMIS' ELSE cs.game_type END)=?
                             AND TRIM(cs.scenario_name)=?
                             AND csm.discord_id=?
                           LIMIT 1""",
                        (gt, scenario_name, uid),
                    ).fetchone()
                    if not still_has_session:
                        c.execute(
                            """DELETE FROM scenario_progress
                               WHERE game_type=? AND TRIM(scenario_name)=? AND discord_id=?
                                 AND status='PASSED'""",
                            (gt, scenario_name, uid),
                        )
    return True





def session_management_detail(session_id: int):
    """成立卓の管理画面用。元卓・GM・参加者・カレンダーIDをまとめて返す。"""
    with db() as c:
        row = c.execute(
            """SELECT s.*,r.scenario_name,r.game_type,r.gm_discord_id,r.gm_name_override,
                      cs.id AS calendar_session_id,cs.event_date AS calendar_event_date,
                      cs.start_time AS calendar_start_time
                 FROM sessions s
                 JOIN recruitments r ON r.id=s.recruitment_id
                 LEFT JOIN calendar_sessions cs ON cs.source_session_id=s.id
                WHERE s.id=?""",
            (int(session_id),),
        ).fetchone()
        if not row:
            return None, []
        members = c.execute(
            """SELECT sm.discord_id,
                      COALESCE(rm.display_name,rm.username,u.display_name,u.username,sm.discord_id) AS display_name
                 FROM session_members sm
                 LEFT JOIN registered_members rm ON rm.discord_id=sm.discord_id
                 LEFT JOIN users u ON u.discord_id=sm.discord_id
                WHERE sm.session_id=?
                ORDER BY display_name COLLATE NOCASE""",
            (int(session_id),),
        ).fetchall()
    return dict(row), [dict(x) for x in members]


def create_session_reschedule(session_id: int, start_time: str, deadline: str, candidate_dates: list[str], created_at: str) -> int:
    dates = list(dict.fromkeys(str(x).strip() for x in candidate_dates if str(x).strip()))
    if not dates:
        raise ValueError("候補日がありません")
    with db() as c:
        # 同じ成立卓で開いている古い再調整は閉じる。元の開催日は変更しない。
        c.execute("UPDATE session_reschedules SET status='CLOSED' WHERE session_id=? AND status='OPEN'", (int(session_id),))
        cur = c.execute(
            "INSERT INTO session_reschedules(session_id,start_time,deadline,status,created_at) VALUES(?,?,?,'OPEN',?)",
            (int(session_id), str(start_time), str(deadline or ''), str(created_at)),
        )
        reschedule_id = int(cur.lastrowid)
        c.executemany(
            "INSERT INTO session_reschedule_dates(reschedule_id,event_date) VALUES(?,?)",
            [(reschedule_id, d) for d in dates],
        )
    return reschedule_id


def session_reschedule_detail(reschedule_id: int):
    with db() as c:
        rs = c.execute(
            """SELECT sr.*,s.recruitment_id,s.round_no,s.event_date AS original_event_date,
                      s.start_time AS original_start_time,s.channel_id,s.cancelled_at,
                      r.scenario_name,r.game_type,r.gm_discord_id,r.gm_name_override,
                      r.min_players,r.max_players
                 FROM session_reschedules sr
                 JOIN sessions s ON s.id=sr.session_id
                 JOIN recruitments r ON r.id=s.recruitment_id
                WHERE sr.id=?""",
            (int(reschedule_id),),
        ).fetchone()
        if not rs:
            return None, [], [], {}
        dates = [str(r["event_date"]) for r in c.execute(
            "SELECT event_date FROM session_reschedule_dates WHERE reschedule_id=? ORDER BY event_date",
            (int(reschedule_id),),
        ).fetchall()]
        members = [dict(r) for r in c.execute(
            """SELECT sm.discord_id,
                      COALESCE(rm.display_name,rm.username,u.display_name,u.username,sm.discord_id) AS display_name
                 FROM session_members sm
                 LEFT JOIN registered_members rm ON rm.discord_id=sm.discord_id
                 LEFT JOIN users u ON u.discord_id=sm.discord_id
                WHERE sm.session_id=? ORDER BY display_name COLLATE NOCASE""",
            (int(rs["session_id"]),),
        ).fetchall()]
        answers = {}
        for a in c.execute(
            "SELECT discord_id,event_date,answer FROM session_reschedule_answers WHERE reschedule_id=?",
            (int(reschedule_id),),
        ).fetchall():
            answers.setdefault(str(a["discord_id"]), {})[str(a["event_date"])] = str(a["answer"])
    return dict(rs), dates, members, answers


def save_session_reschedule_answers(reschedule_id: int, discord_id: str, answers: dict[str,str], updated_at: str):
    rs, dates, members, _ = session_reschedule_detail(reschedule_id)
    if not rs or str(rs.get("status")) != "OPEN":
        return False
    allowed_users = {str(m["discord_id"]) for m in members}
    if str(discord_id) not in allowed_users:
        return False
    allowed_dates = set(dates)
    with db() as c:
        # 元の再日程調整と同じく「-」は未回答扱い。保存時に一度自分の回答を消してから○/△だけ入れ直す。
        c.execute(
            "DELETE FROM session_reschedule_answers WHERE reschedule_id=? AND discord_id=?",
            (int(reschedule_id), str(discord_id)),
        )
        for d, a in answers.items():
            if d not in allowed_dates or a not in {"YES","MAYBE"}:
                continue
            c.execute(
                """INSERT INTO session_reschedule_answers(reschedule_id,discord_id,event_date,answer,updated_at)
                   VALUES(?,?,?,?,?)""",
                (int(reschedule_id), str(discord_id), str(d), str(a), str(updated_at)),
            )
    return True


def confirm_session_reschedule(reschedule_id: int, new_event_date: str, new_start_time: str, selected_member_ids: list[str] | None = None):
    """成立卓IDをキーに1卓だけ移動する。同日の別卓には一切触れない。参加者も選択内容へ同期する。"""
    rs, dates, members, answers = session_reschedule_detail(reschedule_id)
    if not rs or str(rs.get("status")) != "OPEN" or str(new_event_date) not in set(dates):
        return None
    session_id = int(rs["session_id"])
    old_date = str(rs["original_event_date"])
    old_time = str(rs["original_start_time"] or "")

    allowed_member_ids = {str(m["discord_id"]) for m in members}
    yes_member_ids = {uid for uid in allowed_member_ids if answers.get(uid, {}).get(str(new_event_date)) == "YES"}
    if selected_member_ids is None:
        selected = sorted(yes_member_ids)
    else:
        selected = list(dict.fromkeys(str(x) for x in selected_member_ids if str(x) in yes_member_ids))
    if len(selected) < int(rs.get("min_players") or 1):
        return None
    if len(selected) > int(rs.get("max_players") or len(selected)):
        return None

    with db() as c:
        c.execute(
            "UPDATE sessions SET event_date=?,start_time=?,reminder_sent=0 WHERE id=?",
            (str(new_event_date), str(new_start_time), session_id),
        )
        # source_session_id が一致する1レコードだけ更新する。同日の別卓は触らない。
        c.execute(
            "UPDATE calendar_sessions SET event_date=?,start_time=? WHERE source_session_id=?",
            (str(new_event_date), str(new_start_time), session_id),
        )
        # 再日程調整でGMが選んだPLへ、成立卓と永久カレンダーの参加者を同期。
        c.execute("DELETE FROM session_members WHERE session_id=?", (session_id,))
        if selected:
            c.executemany(
                "INSERT INTO session_members(session_id,discord_id) VALUES(?,?)",
                [(session_id, uid) for uid in selected],
            )
        cs = c.execute("SELECT id FROM calendar_sessions WHERE source_session_id=?", (session_id,)).fetchone()
        if cs:
            calendar_session_id = int(cs["id"])
            c.execute("DELETE FROM calendar_session_members WHERE calendar_session_id=?", (calendar_session_id,))
            if selected:
                c.executemany(
                    "INSERT INTO calendar_session_members(calendar_session_id,discord_id) VALUES(?,?)",
                    [(calendar_session_id, uid) for uid in selected],
                )
        c.execute(
            "UPDATE session_reschedules SET status='CONFIRMED',confirmed_date=? WHERE id=?",
            (str(new_event_date), int(reschedule_id)),
        )
    return {"session_id":session_id,"old_date":old_date,"old_time":old_time,"new_date":str(new_event_date),"new_time":str(new_start_time),"member_ids":selected}


def cancel_confirmed_session(session_id: int, cancelled_at: str):
    """成立卓を開催中止にする。Discordチャンネルは削除せず、カレンダーだけ完全削除する。"""
    detail, _ = session_management_detail(session_id)
    if not detail:
        return None
    calendar_session_id = detail.get("calendar_session_id")
    with db() as c:
        c.execute("UPDATE sessions SET cancelled_at=? WHERE id=?", (str(cancelled_at), int(session_id)))
        c.execute("UPDATE session_reschedules SET status='CANCELLED' WHERE session_id=? AND status='OPEN'", (int(session_id),))
    if calendar_session_id:
        permanently_delete_calendar_session(int(calendar_session_id), str(cancelled_at))
    return detail

def normalize_progress_game_type(game_type: str) -> str:
    gt = str(game_type or "").strip()
    return "MADMIS" if gt in {"MADMIS", "マダミス"} else gt


def backfill_scenario_progress():
    """既存の成立卓PLを通過済みとして補完する。手動設定済みは上書きしない。"""
    with db() as c:
        rows = c.execute(
            """SELECT cs.id, cs.game_type, cs.scenario_name, cs.created_at
               FROM calendar_sessions cs
               WHERE cs.game_type IN ('TRPG','MADMIS','マダミス')
                 AND cs.scenario_name<>''"""
        ).fetchall()

        for row in rows:
            gt = normalize_progress_game_type(row["game_type"])
            members = c.execute(
                """SELECT discord_id
                   FROM calendar_session_members
                   WHERE calendar_session_id=?""",
                (row["id"],),
            ).fetchall()

            for member in members:
                uid = str(member["discord_id"])
                exists = c.execute(
                    """SELECT 1 FROM scenario_progress
                       WHERE game_type=? AND scenario_name=? AND discord_id=?""",
                    (gt, row["scenario_name"], uid),
                ).fetchone()
                if not exists:
                    c.execute(
                        """INSERT INTO scenario_progress(
                               game_type,scenario_name,discord_id,status,updated_at
                           ) VALUES(?,?,?,?,?)""",
                        (
                            gt,
                            row["scenario_name"],
                            uid,
                            "PASSED",
                            row["created_at"] or "",
                        ),
                    )


def scenario_progress_data():
    """通過表表示用データ。"""
    with db() as c:
        users = c.execute(
            """SELECT discord_id,
                      COALESCE(display_name,username,discord_id) AS display_name
               FROM registered_members
               WHERE LOWER(COALESCE(display_name,'')) <> 'okuyama'
                 AND LOWER(COALESCE(username,'')) <> 'okuyama'
               ORDER BY display_name"""
        ).fetchall()

        # 一覧に出すシナリオは「現在カレンダー履歴に存在するもの」だけ。
        # 旧バージョンで残った orphan な scenario_progress 行が一覧へ復活するのを防ぐ。
        scenarios = c.execute(
            """SELECT
                   CASE WHEN game_type='マダミス' THEN 'MADMIS' ELSE game_type END AS game_type,
                   TRIM(scenario_name) AS scenario_name
               FROM calendar_sessions
               WHERE game_type IN ('TRPG','MADMIS','マダミス')
                 AND TRIM(scenario_name)<>''
               GROUP BY game_type,TRIM(scenario_name)
               ORDER BY scenario_name"""
        ).fetchall()

        statuses = c.execute(
            """SELECT game_type,scenario_name,discord_id,status
               FROM scenario_progress"""
        ).fetchall()

    status_map = {
        (
            str(r["game_type"]),
            str(r["scenario_name"]),
            str(r["discord_id"]),
        ): str(r["status"])
        for r in statuses
    }
    return users, scenarios, status_map


def set_scenario_progress_status(
    game_type: str,
    scenario_name: str,
    discord_id: str,
    status: str,
    updated_at: str,
):
    gt = normalize_progress_game_type(game_type)
    name = str(scenario_name or "").strip()
    uid = str(discord_id or "").strip()

    if gt not in {"TRPG", "MADMIS"} or not name or not uid:
        return False

    with db() as c:
        if status == "":
            c.execute(
                """DELETE FROM scenario_progress
                   WHERE game_type=? AND scenario_name=? AND discord_id=?""",
                (gt, name, uid),
            )
        elif status in {"PASSED", "WATCHED"}:
            c.execute(
                """INSERT INTO scenario_progress(
                       game_type,scenario_name,discord_id,status,updated_at
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(game_type,scenario_name,discord_id)
                   DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at""",
                (gt, name, uid, status, updated_at),
            )
        else:
            return False

    return True


def scenario_detail(game_type: str, scenario_name: str):
    gt = normalize_progress_game_type(game_type)
    name = str(scenario_name or "").strip()

    with db() as c:
        played = c.execute(
            """SELECT COUNT(*) AS n
               FROM calendar_sessions
               WHERE (CASE WHEN game_type='マダミス' THEN 'MADMIS' ELSE game_type END)=?
                 AND scenario_name=?""",
            (gt, name),
        ).fetchone()["n"]

        rows = c.execute(
            """SELECT sp.status,
                      COALESCE(u.display_name,u.username,sp.discord_id) AS display_name
               FROM scenario_progress sp
               LEFT JOIN registered_members u ON u.discord_id=sp.discord_id
               WHERE sp.game_type=? AND sp.scenario_name=?
               ORDER BY display_name""",
            (gt, name),
        ).fetchall()

    return int(played), rows


def calendar_stats(period_start: str | None = None, period_end: str | None = None):
    """イベントを除外し、同一シナリオ+GM+PL構成の複数日開催を1卓として集計。"""
    with db() as c:
        clauses=["cs.game_type IN ('TRPG','MADMIS','マダミス')"]
        params=[]
        if period_start and period_end:
            clauses += ["cs.event_date>=?", "cs.event_date<?"]
            params += [period_start, period_end]
        where="WHERE "+" AND ".join(clauses)
        rows=c.execute(f"SELECT cs.* FROM calendar_sessions cs {where} ORDER BY cs.event_date,cs.id",params).fetchall()
        unique={}
        for r in rows:
            mid=tuple(sorted(str(x["discord_id"]) for x in c.execute(
                "SELECT discord_id FROM calendar_session_members WHERE calendar_session_id=?",(r["id"],)).fetchall()))
            guests=tuple(sorted(str(x["display_name"]).strip() for x in c.execute(
                "SELECT display_name FROM calendar_guest_members WHERE calendar_session_id=?",(r["id"],)).fetchall()))
            gt='MADMIS' if str(r['game_type'])=='マダミス' else str(r['game_type'])
            gm_key=('guest:'+str(r['gm_guest_name']).strip()) if str(r['gm_guest_name'] or '').strip() else ('id:'+str(r['gm_discord_id'] or ''))
            key=(gt,str(r['scenario_name']).strip(),gm_key,mid,guests)
            unique.setdefault(key,r)
        vals=list(unique.items())
        total=len(vals)
        trpg=sum(1 for k,_ in vals if k[0]=='TRPG')
        madamis=sum(1 for k,_ in vals if k[0]=='MADMIS')
        scenario_sets={'TRPG':set(),'MADMIS':set()}
        for k,_ in vals: scenario_sets[k[0]].add(k[1])

        # ランキングは表示専用guestを除外し、重複卓も1回だけ。
        gm_counts={}; pl_counts={}
        for k,r in vals:
            gid=str(r['gm_discord_id'] or '')
            if gid and not str(r['gm_guest_name'] or '').strip(): gm_counts[gid]=gm_counts.get(gid,0)+1
            for uid in k[3]: pl_counts[uid]=pl_counts.get(uid,0)+1
        def top_rows(counts):
            out=[]
            for uid,n in sorted(counts.items(), key=lambda z:(-z[1],z[0]))[:3]:
                u=c.execute("SELECT COALESCE(display_name,username,discord_id) AS display_name FROM users WHERE discord_id=?",(uid,)).fetchone()
                out.append({'discord_id':uid,'display_name':str(u['display_name']) if u else uid,'n':n})
            return out
        return {'total':total,'trpg':trpg,'madamis':madamis,'event':0,
                'scenario_count':len(scenario_sets['TRPG']|scenario_sets['MADMIS']),
                'trpg_scenarios':len(scenario_sets['TRPG']),
                'madamis_scenarios':len(scenario_sets['MADMIS']),
                'gm_top':top_rows(gm_counts),'pl_top':top_rows(pl_counts)}

def new_scenario_count(period_start: str, period_end: str) -> int:
    """その年度に初めて履歴へ登場したTRPG/マダミスのシナリオ数。"""
    with db() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n
               FROM (
                 SELECT
                   CASE WHEN cs.game_type='マダミス' THEN 'MADMIS' ELSE cs.game_type END AS gt,
                   cs.scenario_name
                 FROM calendar_sessions cs
                 WHERE cs.event_date>=?
                   AND cs.event_date<?
                   AND cs.game_type IN ('TRPG','MADMIS','マダミス')
                   AND cs.scenario_name<>''
                 GROUP BY gt, cs.scenario_name
                 HAVING NOT EXISTS (
                   SELECT 1
                   FROM calendar_sessions old
                   WHERE
                     (CASE WHEN old.game_type='マダミス' THEN 'MADMIS' ELSE old.game_type END)
                       =
                     (CASE WHEN cs.game_type='マダミス' THEN 'MADMIS' ELSE cs.game_type END)
                     AND old.scenario_name=cs.scenario_name
                     AND old.event_date<?
                 )
               )""",
            (period_start, period_end, period_start),
        ).fetchone()
    return int(row["n"])


def initialize_database():
    """既存DBを保持したまま、必要なテーブル・列だけ追加する。"""
    init_db()
    ensure_recruitment_columns()
    ensure_session_columns()
    ensure_calendar_columns()
    ensure_profile_cache_columns()
    with db() as c:
        # v97: 手動補正値を履歴再集計とは独立して保持する。
        # 一度登録した補正はキャッシュ再構築でも消えない。
        c.execute(
            """CREATE TABLE IF NOT EXISTS profile_year_adjustments(
                   discord_id TEXT NOT NULL,
                   activity_year INTEGER NOT NULL,
                   gm_delta INTEGER NOT NULL DEFAULT 0,
                   pl_delta INTEGER NOT NULL DEFAULT 0,
                   trpg_delta INTEGER NOT NULL DEFAULT 0,
                   madamis_delta INTEGER NOT NULL DEFAULT 0,
                   note TEXT,
                   created_at TEXT NOT NULL,
                   PRIMARY KEY(discord_id, activity_year, note)
               )"""
        )
        c.execute(
            """INSERT OR IGNORE INTO registered_members(
                   discord_id,username,display_name,avatar_url,created_at,updated_at
               )
               SELECT discord_id,username,display_name,avatar_url,updated_at,updated_at
               FROM users"""
        )

        # v79: 「ズッ卓」→「ズッ友」の表示名変更。
        # 全履歴や称号条件は再計算せず、既存の pair_25 レコードだけを
        # 一度だけ更新する軽量マイグレーション。
        migration_key = "migration_v79_pair25_zuttomo"
        migrated = c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key=?", (migration_key,)
        ).fetchone()
        if not migrated:
            c.execute(
                "UPDATE achievement_unlocks SET title_name='ズッ友' WHERE achievement_key='pair_25'"
            )
            c.execute(
                "INSERT INTO achievement_meta(meta_key, meta_value) VALUES(?, '1')",
                (migration_key,),
            )
    backfill_calendar_history()
    backfill_scenario_progress()


# main.py から import された時点で従来どおり初期化する。
initialize_database()


# ============================================================
# v65: プロフィール / 称号
# ============================================================

FIXED_ACHIEVEMENTS = [
    {"key":"pl_10","name":"ひよっこ","rarity":"bronze","secret":0,"kind":"pl","target":10,"condition":"PLとして10卓参加する"},
    {"key":"pl_50","name":"常連","rarity":"silver","secret":0,"kind":"pl","target":50,"condition":"PLとして50卓参加する"},
    {"key":"pl_100","name":"百戦錬磨","rarity":"gold","secret":0,"kind":"pl","target":100,"condition":"PLとして100卓参加する"},
    {"key":"pl_250","name":"廃人","rarity":"black","secret":1,"kind":"pl","target":250,"condition":"PLとして250卓参加する"},

    {"key":"gm_10","name":"駆け出しGM","rarity":"bronze","secret":0,"kind":"gm","target":10,"condition":"GMを10回する"},
    {"key":"gm_50","name":"熟練GM","rarity":"silver","secret":0,"kind":"gm","target":50,"condition":"GMを50回する"},
    {"key":"gm_100","name":"観測者","rarity":"gold","secret":0,"kind":"gm","target":100,"condition":"GMを100回する"},
    {"key":"gm_250","name":"悠久の語り部","rarity":"black","secret":1,"kind":"gm","target":250,"condition":"GMを250回する"},

    {"key":"trpg_pl_250","name":"這い寄る混沌","rarity":"black","secret":1,"kind":"trpg_pl","target":250,"condition":"TRPGに250卓参加する"},
    {"key":"madamis_pl_250","name":"マダミスの家畜","rarity":"black","secret":1,"kind":"madamis_pl","target":250,"condition":"マダミスに250卓参加する"},

    {"key":"pair_25","name":"ズッ友","rarity":"bronze","secret":0,"kind":"pair","target":25,"condition":"同じ人と25卓同卓する"},
    {"key":"pair_50","name":"腐れ縁","rarity":"silver","secret":0,"kind":"pair","target":50,"condition":"同じ人と50卓同卓する"},
    {"key":"pair_100","name":"運命共同体","rarity":"gold","secret":0,"kind":"pair","target":100,"condition":"同じ人と100卓同卓する"},
    {"key":"pair_250","name":"生き別れの兄弟","rarity":"black","secret":1,"kind":"pair_dynamic","target":250,"condition":"同じ人と250卓同卓する"},

    {"key":"active_year_1","name":"新人","rarity":"bronze","secret":0,"kind":"active_years","target":1,"condition":"つぶぐみに1年在籍する（毎年1卓以上参加）"},
    {"key":"active_year_3","name":"暇人","rarity":"silver","secret":0,"kind":"active_years","target":3,"condition":"つぶぐみに3年在籍する（毎年1卓以上参加）"},
    {"key":"active_year_5","name":"古参勢","rarity":"gold","secret":0,"kind":"active_years","target":5,"condition":"つぶぐみに5年在籍する（毎年1卓以上参加）"},
    {"key":"active_year_10","name":"三葉虫","rarity":"black","secret":1,"kind":"active_years","target":10,"condition":"つぶぐみに10年在籍する"},

    {"key":"day_3","name":"3度の飯より暇つぶし","rarity":"gold","secret":1,"kind":"max_day","target":3,"condition":"1日に3卓参加する"},
    {"key":"christmas","name":"聖夜のサン卓ロース","rarity":"gold","secret":1,"kind":"christmas","target":1,"condition":"12月24日または25日に卓へ参加する"},
    {"key":"total_500","name":"卓修羅","rarity":"black","secret":1,"kind":"total_roles","target":500,"condition":"PL・GMの累計が500卓に達する"},

    {"key":"streak_3","name":"ひまんちゅ","rarity":"silver","secret":0,"kind":"streak","target":3,"condition":"3日連続で卓に参加する"},
    {"key":"streak_5","name":"暇仙人","rarity":"gold","secret":0,"kind":"streak","target":5,"condition":"5日連続で卓に参加する"},
    {"key":"streak_7","name":"暇かよ働け","rarity":"black","secret":1,"kind":"streak","target":7,"condition":"1週間連続で卓に参加する"},
]

RARITY_LABELS = {"bronze":"🥉", "silver":"🥈", "gold":"🥇", "black":"🏆"}


def _achievement_gt(value: str) -> str:
    return "MADMIS" if str(value or "") == "マダミス" else str(value or "")


def _activity_year_for_date(d: str) -> int:
    y, m, _ = map(int, str(d).split("-"))
    return y if m >= 6 else y - 1


def _unique_table_records(c, as_of_date: str | None = None):
    """卓履歴を3クエリで一括取得する高速版。

    旧実装は卓ごとに participants / guests を個別SELECTしていたため、
    履歴が増えるほど 1 + 2N クエリになっていた。ここでは全メンバーを
    一括取得してPython側でまとめ、常にほぼ3クエリで完結させる。
    """
    clauses = ["cs.game_type IN ('TRPG','MADMIS','マダミス')"]
    params = []
    if as_of_date:
        clauses.append("cs.event_date<=?")
        params.append(str(as_of_date))
    rows = c.execute(
        "SELECT cs.id,cs.event_date,cs.game_type,cs.scenario_name,cs.gm_discord_id "
        "FROM calendar_sessions cs WHERE " + " AND ".join(clauses) + " ORDER BY cs.event_date,cs.id",
        params,
    ).fetchall()
    if not rows:
        return []

    ids = [int(r["id"]) for r in rows]
    member_map = {sid: [] for sid in ids}
    guest_map = {sid: [] for sid in ids}
    # SQLiteの変数上限を避けるため分割取得。
    for start in range(0, len(ids), 800):
        chunk = ids[start:start+800]
        ph = ",".join("?" for _ in chunk)
        for x in c.execute(
            f"SELECT calendar_session_id,discord_id FROM calendar_session_members WHERE calendar_session_id IN ({ph})",
            chunk,
        ).fetchall():
            member_map[int(x["calendar_session_id"])].append(str(x["discord_id"]))
        for x in c.execute(
            f"SELECT calendar_session_id,display_name FROM calendar_guest_members WHERE calendar_session_id IN ({ph})",
            chunk,
        ).fetchall():
            guest_map[int(x["calendar_session_id"])].append(str(x["display_name"]))

    unique = {}
    for r in rows:
        sid = int(r["id"])
        members = tuple(sorted(member_map.get(sid, ())))
        guests = tuple(sorted(guest_map.get(sid, ())))
        gt = _achievement_gt(r["game_type"])
        key = (gt, str(r["scenario_name"] or "").strip(), str(r["gm_discord_id"] or ""), members, guests)
        if key not in unique:
            unique[key] = {
                "id": sid, "date": str(r["event_date"]), "game_type": gt,
                "scenario": str(r["scenario_name"] or "").strip(),
                "gm": str(r["gm_discord_id"] or ""), "members": members, "guests": guests,
            }
    return list(unique.values())


def _display_name(c, uid: str) -> str:
    row = c.execute(
        "SELECT COALESCE(display_name,username,discord_id) AS n FROM registered_members WHERE discord_id=?",
        (str(uid),),
    ).fetchone()
    if not row:
        row = c.execute(
            "SELECT COALESCE(display_name,username,discord_id) AS n FROM users WHERE discord_id=?",
            (str(uid),),
        ).fetchone()
    return str(row["n"]) if row else str(uid)


def _member_metrics(c, uid: str, records):
    uid = str(uid)
    gm_records = [r for r in records if r["gm"] == uid]
    pl_records = [r for r in records if uid in r["members"]]
    all_records = [r for r in records if r["gm"] == uid or uid in r["members"]]
    trpg_pl = sum(1 for r in pl_records if r["game_type"] == "TRPG")
    madamis_pl = sum(1 for r in pl_records if r["game_type"] == "MADMIS")
    # v73: プロフィールのTRPG/マダミス数は、GM・PLどちらの参加も1卓として数える。
    trpg_total = sum(1 for r in records if r["game_type"] == "TRPG" and (r["gm"] == uid or uid in r["members"]))
    madamis_total = sum(1 for r in records if r["game_type"] == "MADMIS" and (r["gm"] == uid or uid in r["members"]))

    pair_counts = {}
    for r in all_records:
        people = set(r["members"])
        if r["gm"]:
            people.add(r["gm"])
        people.discard(uid)
        for other in people:
            if other:
                pair_counts[other] = pair_counts.get(other, 0) + 1

    dates = sorted(set(r["date"] for r in all_records))
    max_streak = 0
    streak = 0
    prev = None
    from datetime import date as _date, timedelta as _td
    for ds in dates:
        d = _date.fromisoformat(ds)
        if prev is not None and d == prev + _td(days=1):
            streak += 1
        else:
            streak = 1
        max_streak = max(max_streak, streak)
        prev = d

    per_day = {}
    for r in all_records:
        per_day[r["date"]] = per_day.get(r["date"], 0) + 1
    max_day = max(per_day.values(), default=0)
    christmas = any(r["date"][5:] in ("12-24", "12-25") for r in all_records)
    active_years = len(set(_activity_year_for_date(r["date"]) for r in all_records))
    return {
        "gm": len(gm_records), "pl": len(pl_records), "trpg_pl": trpg_pl, "madamis_pl": madamis_pl,
        "trpg_total": trpg_total, "madamis_total": madamis_total,
        "total_roles": len(gm_records) + len(pl_records), "pair_counts": pair_counts,
        "max_pair": max(pair_counts.values(), default=0), "max_streak": max_streak,
        "max_day": max_day, "christmas": christmas, "active_years": active_years,
        "gm_records": gm_records, "pl_records": pl_records, "all_records": all_records,
    }


def _record_table_key(r) -> str:
    payload = [
        str(r.get("game_type") or ""),
        str(r.get("scenario") or "").strip(),
        str(r.get("gm") or ""),
        sorted(str(x) for x in (r.get("members") or ()) if str(x)),
        sorted(str(x) for x in (r.get("guests") or ()) if str(x)),
    ]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _seed_incremental_tracking(c, records, updated_at: str):
    """v69初回だけ、既存キャッシュと同じ履歴から差分更新用の基準を作る。"""
    c.execute("DELETE FROM profile_processed_tables")
    c.execute("DELETE FROM profile_pair_totals")
    c.execute("DELETE FROM profile_active_days")
    c.execute("DELETE FROM profile_active_years")

    pair_counts = {}
    day_counts = {}
    active_years = set()
    for r in records:
        key = _record_table_key(r)
        c.execute(
            "INSERT OR IGNORE INTO profile_processed_tables(table_key,event_date,processed_at) VALUES(?,?,?)",
            (key, str(r["date"]), str(updated_at)),
        )
        people = set(str(x) for x in r.get("members", ()) if str(x))
        if str(r.get("gm") or ""):
            people.add(str(r["gm"]))
        for uid in people:
            dk = (uid, str(r["date"]))
            day_counts[dk] = day_counts.get(dk, 0) + 1
            active_years.add((uid, _activity_year_for_date(str(r["date"]))))
            for other in people:
                if other != uid:
                    pk = (uid, other)
                    pair_counts[pk] = pair_counts.get(pk, 0) + 1

    if pair_counts:
        c.executemany(
            "INSERT INTO profile_pair_totals(discord_id,partner_discord_id,table_count) VALUES(?,?,?)",
            [(a,b,n) for (a,b),n in pair_counts.items()],
        )
    if day_counts:
        c.executemany(
            "INSERT INTO profile_active_days(discord_id,event_date,table_count) VALUES(?,?,?)",
            [(u,d,n) for (u,d),n in day_counts.items()],
        )
    if active_years:
        c.executemany(
            "INSERT INTO profile_active_years(discord_id,activity_year) VALUES(?,?)",
            list(active_years),
        )


def refresh_profile_caches(as_of_date: str, updated_at: str):
    """プロフィール/称号表示用の集計結果を一括更新する。

    通常のページ表示では履歴を再集計せず、このキャッシュだけを読む。
    毎日20:00の称号判定と同じタイミングで呼び出す想定。
    """
    with db() as c:
        records = _unique_table_records(c, str(as_of_date))
        users = [str(r["discord_id"]) for r in c.execute(
            "SELECT discord_id FROM registered_members ORDER BY discord_id"
        ).fetchall()]

        # 表示名は全登録メンバー分を一括取得。
        name_map = {}
        for r in c.execute(
            "SELECT discord_id,COALESCE(display_name,username,discord_id) AS n FROM registered_members"
        ).fetchall():
            name_map[str(r["discord_id"])] = str(r["n"])
        for r in c.execute(
            "SELECT discord_id,COALESCE(display_name,username,discord_id) AS n FROM users"
        ).fetchall():
            name_map.setdefault(str(r["discord_id"]), str(r["n"]))

        from datetime import date as _date
        cutoff = _date.fromisoformat(str(as_of_date))
        current_ay = cutoff.year if cutoff.month >= 6 else cutoff.year - 1
        first_year = min(2024, min((_activity_year_for_date(r["date"]) for r in records), default=2024))

        c.execute("DELETE FROM profile_stats_cache")
        c.execute("DELETE FROM profile_year_stats_cache")
        c.execute("DELETE FROM profile_pair_cache")

        for uid in users:
            m = _member_metrics(c, uid, records)
            c.execute(
                """INSERT INTO profile_stats_cache(
                     discord_id,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,total_roles,
                     max_pair,active_years,max_day,max_streak,christmas,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid,m["gm"],m["pl"],m["trpg_total"],m["madamis_total"],m["trpg_pl"],m["madamis_pl"],m["total_roles"],
                 m["max_pair"],m["active_years"],m["max_day"],m["max_streak"],
                 1 if m["christmas"] else 0,str(updated_at)),
            )

            for y in range(first_year, current_ay + 1):
                yr = [r for r in records if _activity_year_for_date(r["date"]) == y]
                mm = _member_metrics(c, uid, yr)
                c.execute(
                    """INSERT INTO profile_year_stats_cache(
                         discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (uid,y,y-2023,mm["gm"],mm["pl"],mm["trpg_total"],mm["madamis_total"],mm["trpg_pl"],mm["madamis_pl"],str(updated_at)),
                )

            pairs = sorted(
                m["pair_counts"].items(),
                key=lambda z: (-z[1], name_map.get(z[0], z[0]).lower()),
            )[:3]
            for rank_no, (other, n) in enumerate(pairs, 1):
                c.execute(
                    """INSERT INTO profile_pair_cache(
                         discord_id,partner_discord_id,table_count,rank_no,updated_at
                       ) VALUES(?,?,?,?,?)""",
                    (uid,str(other),int(n),rank_no,str(updated_at)),
                )

        _seed_incremental_tracking(c, records, str(updated_at))

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_as_of',?)",
            (str(as_of_date),),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_updated_at',?)",
            (str(updated_at),),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_delta_v69_seeded','1')"
        )




def full_derived_rebuild_v75_done() -> bool:
    """v75の一度きりの完全再構築が正常完了しているか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='full_derived_rebuild_v75_done' AND meta_value='1'"
        ).fetchone() is not None


def full_derived_rebuild_v75(as_of_date: str, updated_at: str):
    """現在のカレンダー履歴を唯一の正として、プロフィール・称号派生データを一度だけ完全再構築する。

    カレンダー本体、募集、登録メンバー、通過状況には触れない。
    称号解除・装備は全消去してから現在の実績だけで再判定する。
    完了マーカーは最後に書くため、途中失敗時は次回起動で安全に再試行できる。
    """
    # 1) 称号だけ先に完全リセット。今回はまだ誰も装備していない前提だが、
    #    FKや古いunlock_idを残さないため装備も明示的に空にする。
    with db() as c:
        c.execute("DELETE FROM equipped_titles")
        c.execute("DELETE FROM achievement_unlocks")
        c.execute("DELETE FROM achievement_daily_runs")
        c.execute("DELETE FROM achievement_scenario_gm_totals")
        c.execute("DELETE FROM achievement_meta WHERE meta_key='full_derived_rebuild_v75_done'")

    # 2) 全カレンダー履歴からプロフィール表示キャッシュと、以後の差分更新基準を再生成。
    refresh_profile_caches(str(as_of_date), str(updated_at))

    # 3) シナリオ別GM回数も同じカレンダー履歴からゼロベースで再生成。
    with db() as c:
        records = _unique_table_records(c, str(as_of_date))
        counts = {}
        for r in records:
            gm = str(r.get("gm") or "")
            scenario = str(r.get("scenario") or "").strip()
            if gm and scenario:
                counts[(gm, scenario)] = counts.get((gm, scenario), 0) + 1
        if counts:
            c.executemany(
                "INSERT INTO achievement_scenario_gm_totals(discord_id,scenario_name,gm_count) VALUES(?,?,?)",
                [(uid, scenario, n) for (uid, scenario), n in counts.items()],
            )

    # 4) 再生成したキャッシュだけを使って全称号を静かに再判定。
    #    呼び出し側では通知しない。
    rebuilt_unlocks = evaluate_achievements(str(as_of_date), str(updated_at))

    # 5) すべて成功してから移行完了を記録。旧移行マーカーも現在状態に揃える。
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('full_derived_rebuild_v75_done','1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('full_derived_rebuild_v75_done_at',?)",
            (str(updated_at),),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_v74_resynced','1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('scenario_gm_delta_v73_seeded','1')"
        )
    return rebuilt_unlocks

def cutoff_resync_v83_done() -> bool:
    """v83: 未来卓を除外した一度きりの派生データ再同期が完了済みか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='cutoff_resync_v83_done' AND meta_value='1'"
        ).fetchone() is not None


def cutoff_resync_v83(as_of_date: str, updated_at: str):
    """v83: as_of_date までの卓だけを正としてプロフィール・称号派生データを再同期する。

    未来卓は profile_processed_tables にも入れないため、その開催日の20時に通常の
    日次差分として一度だけ加算される。既存の装備称号は、再判定後も同じ称号が
    有効なら復元する。
    """
    cutoff = str(as_of_date)

    # 装備中称号があっても安全に移行できるよう、unlock_idではなく安定キーで退避。
    with db() as c:
        equipped = [dict(r) for r in c.execute(
            """SELECT et.discord_id,au.achievement_key,au.context_key
                 FROM equipped_titles et
                 JOIN achievement_unlocks au ON au.id=et.unlock_id"""
        ).fetchall()]

        # 称号は現在の有効な履歴だけから再判定する。
        c.execute("DELETE FROM equipped_titles")
        c.execute("DELETE FROM achievement_unlocks")
        c.execute("DELETE FROM achievement_scenario_gm_totals")
        # cutoffより後の日付が処理済み扱いになっていた場合は解除する。
        c.execute("DELETE FROM achievement_daily_runs WHERE run_date>?", (cutoff,))

    # 表示キャッシュ + processed_tables / 同卓 / 活動日等を cutoff までで作り直す。
    refresh_profile_caches(cutoff, str(updated_at))

    # シナリオGM回数も cutoff までだけで再生成。
    with db() as c:
        records = _unique_table_records(c, cutoff)
        counts = {}
        for r in records:
            gm = str(r.get("gm") or "")
            scenario = str(r.get("scenario") or "").strip()
            if gm and scenario:
                counts[(gm, scenario)] = counts.get((gm, scenario), 0) + 1
        if counts:
            c.executemany(
                "INSERT INTO achievement_scenario_gm_totals(discord_id,scenario_name,gm_count) VALUES(?,?,?)",
                [(uid, scenario, n) for (uid, scenario), n in counts.items()],
            )

    # cutoff時点で成立している称号だけ静かに再付与。
    evaluate_achievements(cutoff, str(updated_at))

    # 以前装備していた称号が現在も取得条件を満たすなら復元。
    with db() as c:
        for old in equipped:
            row = c.execute(
                """SELECT id FROM achievement_unlocks
                    WHERE discord_id=? AND achievement_key=? AND context_key=?""",
                (str(old["discord_id"]), str(old["achievement_key"]), str(old["context_key"] or "")),
            ).fetchone()
            if row:
                c.execute(
                    "INSERT OR REPLACE INTO equipped_titles(discord_id,unlock_id,updated_at) VALUES(?,?,?)",
                    (str(old["discord_id"]), int(row["id"]), str(updated_at)),
                )

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('cutoff_resync_v83_done','1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('cutoff_resync_v83_as_of',?)",
            (cutoff,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('cutoff_resync_v83_done_at',?)",
            (str(updated_at),),
        )
    return True


def calendar_resync_v90_done() -> bool:
    """v90: テスト後の一度きりのカレンダー再取得が完了済みか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='calendar_resync_v90_done' AND meta_value='1'"
        ).fetchone() is not None


def calendar_resync_v90(as_of_date: str, updated_at: str):
    """v90: カレンダーを正として as_of_date までの派生集計を一度だけ再構築する。"""
    cutoff_resync_v83(str(as_of_date), str(updated_at))
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('calendar_resync_v90_done','1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('calendar_resync_v90_as_of',?)",
            (str(as_of_date),),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('calendar_resync_v90_done_at',?)",
            (str(updated_at),),
        )
    return True



def temporary_v92_madamis_year_fix_done() -> bool:
    """v92: テスト中に減った今年度1卓分の臨時補正を適用済みか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='temporary_v92_madamis_year_fix_done' AND meta_value='1'"
        ).fetchone() is not None


def apply_temporary_v92_madamis_year_fix(updated_at: str) -> dict:
    """
    v92限定の一度きり補正。
    2026年度（2026-06-01〜2027-05-31）の年別プロフィールキャッシュだけを、
    まげたん / すぅちぃ / ニック は PL+1・マダミス+1、
    yuzuky は GM+1・マダミス+1 する。
    累計値・カレンダー・processed tables・称号には触れない。
    """
    marker = 'temporary_v92_madamis_year_fix_done'
    activity_year = 2026
    term = activity_year - 2023
    targets = [
        ('まげたん', 'PL', None),
        ('すぅちぃ', 'PL', None),
        ('ニック', 'PL', None),
        ('yuzuky', 'GM', '804350794371039272'),
    ]
    result = {}
    with db() as c:
        if c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key=? AND meta_value='1'",
            (marker,),
        ).fetchone():
            return {'already_done': True}

        # 4人全員を特定できた場合だけ補正する。途中まで加算される事故を防ぐ。
        resolved = []
        for name, role, fixed_uid in targets:
            row = None
            if fixed_uid:
                row = c.execute(
                    "SELECT discord_id,display_name,username FROM registered_members WHERE discord_id=?",
                    (fixed_uid,),
                ).fetchone()
            if row is None:
                row = c.execute(
                    """SELECT discord_id,display_name,username
                       FROM registered_members
                       WHERE lower(trim(display_name))=lower(?) OR lower(trim(username))=lower(?)
                       ORDER BY CASE WHEN trim(display_name)=? THEN 0 ELSE 1 END
                       LIMIT 1""",
                    (name, name, name),
                ).fetchone()
            if row is None:
                raise RuntimeError(f'v92 temporary fix: registered member not found: {name}')
            resolved.append((name, role, str(row['discord_id'])))

        for name, role, uid in resolved:
            c.execute(
                """INSERT OR IGNORE INTO profile_year_stats_cache(
                     discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                   ) VALUES(?,?,?,0,0,0,0,0,0,?)""",
                (uid, activity_year, term, str(updated_at)),
            )
            if role == 'GM':
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET gm_count=gm_count+1, madamis_count=madamis_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            else:
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET pl_count=pl_count+1, madamis_count=madamis_count+1, madamis_pl_count=madamis_pl_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            result[name] = uid

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES(?, '1')",
            (marker,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('temporary_v92_madamis_year_fix_done_at',?)",
            (str(updated_at),),
        )
    return result



def temporary_v93_madamis_year_fix_done() -> bool:
    """v93: v92補正が後続再集計で上書きされたケース向けの最終一度きり補正。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='temporary_v93_madamis_year_fix_done' AND meta_value='1'"
        ).fetchone() is not None


def apply_temporary_v93_madamis_year_fix(updated_at: str) -> dict:
    """
    v93限定の一度きり最終補正。
    通常の起動時再集計がすべて終わった後に、2026年度の年別プロフィールキャッシュへ
    まげたん / すぅちぃ / ニック は PL+1・マダミス+1、
    yuzuky は GM+1・マダミス+1 を加える。
    v92とは別マーカーを使い、Railway再起動時の二重加算を防ぐ。
    """
    marker = 'temporary_v93_madamis_year_fix_done'
    activity_year = 2026
    term = activity_year - 2023
    targets = [
        ('まげたん', 'PL', None),
        ('すぅちぃ', 'PL', None),
        ('ニック', 'PL', None),
        ('yuzuky', 'GM', '804350794371039272'),
    ]
    result = {}
    with db() as c:
        if c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key=? AND meta_value='1'",
            (marker,),
        ).fetchone():
            return {'already_done': True}

        resolved = []
        for name, role, fixed_uid in targets:
            row = None
            if fixed_uid:
                row = c.execute(
                    "SELECT discord_id,display_name,username FROM registered_members WHERE discord_id=?",
                    (fixed_uid,),
                ).fetchone()
            if row is None:
                row = c.execute(
                    """SELECT discord_id,display_name,username
                       FROM registered_members
                       WHERE lower(trim(display_name))=lower(?) OR lower(trim(username))=lower(?)
                       ORDER BY CASE WHEN trim(display_name)=? THEN 0 ELSE 1 END
                       LIMIT 1""",
                    (name, name, name),
                ).fetchone()
            if row is None:
                raise RuntimeError(f'v93 temporary fix: registered member not found: {name}')
            resolved.append((name, role, str(row['discord_id'])))

        for name, role, uid in resolved:
            c.execute(
                """INSERT OR IGNORE INTO profile_year_stats_cache(
                     discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                   ) VALUES(?,?,?,0,0,0,0,0,0,?)""",
                (uid, activity_year, term, str(updated_at)),
            )
            if role == 'GM':
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET gm_count=gm_count+1, madamis_count=madamis_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            else:
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET pl_count=pl_count+1, madamis_count=madamis_count+1,
                           madamis_pl_count=madamis_pl_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            result[name] = uid

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES(?, '1')",
            (marker,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('temporary_v93_madamis_year_fix_done_at',?)",
            (str(updated_at),),
        )
    return result


def temporary_v94_madamis_year_fix_done() -> bool:
    """v94: v93が既に成功済み、またはv94補正済みならTrue。"""
    with db() as c:
        return c.execute(
            """SELECT 1 FROM achievement_meta
               WHERE (meta_key='temporary_v93_madamis_year_fix_done' OR meta_key='temporary_v94_madamis_year_fix_done')
                 AND meta_value='1'
               LIMIT 1"""
        ).fetchone() is not None


def apply_temporary_v94_madamis_year_fix(updated_at: str) -> dict:
    """
    v94限定。on_readyでbootstrap_achievements()が完了/returnした後に必ず呼ばれる補正。
    v93が成功済みなら加算しない。未適用の場合のみ2026年度の1卓分を戻す。
    """
    marker = 'temporary_v94_madamis_year_fix_done'
    activity_year = 2026
    term = activity_year - 2023
    targets = [
        ('まげたん', 'PL', None),
        ('すぅちぃ', 'PL', None),
        ('ニック', 'PL', None),
        ('yuzuky', 'GM', '804350794371039272'),
    ]
    result = {}
    with db() as c:
        # v93が既に本当に適用済みなら二重加算しない。
        if c.execute(
            """SELECT 1 FROM achievement_meta
               WHERE (meta_key='temporary_v93_madamis_year_fix_done' OR meta_key=?)
                 AND meta_value='1' LIMIT 1""",
            (marker,),
        ).fetchone():
            return {'already_done': True}

        resolved = []
        for name, role, fixed_uid in targets:
            row = None
            if fixed_uid:
                row = c.execute(
                    "SELECT discord_id,display_name,username FROM registered_members WHERE discord_id=?",
                    (fixed_uid,),
                ).fetchone()
            if row is None:
                row = c.execute(
                    """SELECT discord_id,display_name,username FROM registered_members
                       WHERE lower(trim(display_name))=lower(?) OR lower(trim(username))=lower(?)
                       ORDER BY CASE WHEN trim(display_name)=? THEN 0 ELSE 1 END LIMIT 1""",
                    (name, name, name),
                ).fetchone()
            if row is None:
                raise RuntimeError(f'v94 temporary fix: registered member not found: {name}')
            resolved.append((name, role, str(row['discord_id'])))

        for name, role, uid in resolved:
            c.execute(
                """INSERT OR IGNORE INTO profile_year_stats_cache(
                     discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                   ) VALUES(?,?,?,0,0,0,0,0,0,?)""",
                (uid, activity_year, term, str(updated_at)),
            )
            if role == 'GM':
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET gm_count=gm_count+1, madamis_count=madamis_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            else:
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET pl_count=pl_count+1, madamis_count=madamis_count+1,
                           madamis_pl_count=madamis_pl_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            result[name] = uid

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES(?, '1')",
            (marker,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('temporary_v94_madamis_year_fix_done_at',?)",
            (str(updated_at),),
        )
    return result


def temporary_v95_madamis_year_fix_done() -> bool:
    """v95限定: 今回の誤減算1卓分の補正が実行済みか。過去版のマーカーは見ない。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='temporary_v95_madamis_year_fix_done' AND meta_value='1'"
        ).fetchone() is not None


def apply_temporary_v95_madamis_year_fix(updated_at: str) -> dict:
    """
    v95限定の一度きり補正。
    テスト中に誤って-1された2026年度のマダミス1卓分を、対象4人の年別表示キャッシュへ+1する。
    過去版(v92-v94)のマーカー状態には依存しない。
    """
    marker = 'temporary_v95_madamis_year_fix_done'
    activity_year = 2026
    term = 3
    targets = [
        ('まげたん', 'PL', None),
        ('すぅちぃ', 'PL', None),
        ('ニック', 'PL', None),
        ('yuzuky', 'GM', '804350794371039272'),
    ]
    result = {}
    with db() as c:
        if c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key=? AND meta_value='1'",
            (marker,),
        ).fetchone():
            return {'already_done': True}

        resolved = []
        for name, role, fixed_uid in targets:
            row = None
            if fixed_uid:
                row = c.execute(
                    "SELECT discord_id,display_name,username FROM registered_members WHERE discord_id=?",
                    (fixed_uid,),
                ).fetchone()
            if row is None:
                row = c.execute(
                    """SELECT discord_id,display_name,username FROM registered_members
                       WHERE lower(trim(display_name))=lower(?) OR lower(trim(username))=lower(?)
                       ORDER BY CASE WHEN trim(display_name)=? THEN 0 ELSE 1 END LIMIT 1""",
                    (name, name, name),
                ).fetchone()
            if row is None:
                raise RuntimeError(f'v95 temporary fix: registered member not found: {name}')
            resolved.append((name, role, str(row['discord_id'])))

        for name, role, uid in resolved:
            c.execute(
                """INSERT OR IGNORE INTO profile_year_stats_cache(
                     discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                   ) VALUES(?,?,?,0,0,0,0,0,0,?)""",
                (uid, activity_year, term, str(updated_at)),
            )
            if role == 'GM':
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET gm_count=gm_count+1, madamis_count=madamis_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            else:
                c.execute(
                    """UPDATE profile_year_stats_cache
                       SET pl_count=pl_count+1, madamis_count=madamis_count+1,
                           madamis_pl_count=madamis_pl_count+1, updated_at=?
                       WHERE discord_id=? AND activity_year=?""",
                    (str(updated_at), uid, activity_year),
                )
            result[name] = uid

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES(?, '1')",
            (marker,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('temporary_v95_madamis_year_fix_done_at',?)",
            (str(updated_at),),
        )
    return result



def _temporary_v96_norm_name(value: str) -> str:
    """v96臨時補正用: Discord表示名の表記ゆれを吸収して比較する。"""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold().replace(" ", "").replace("　", "")


def temporary_v96_madamis_year_fix_done() -> bool:
    """v97: 今回の補正レコードが4人分DBへ永続登録済みか。"""
    with db() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM profile_year_adjustments
               WHERE activity_year=2026 AND note='testplay_madamis_minus1_restore_20260830'"""
        ).fetchone()
        return int(row['n'] or 0) >= 4


def apply_temporary_v96_madamis_year_fix(updated_at: str) -> dict:
    """
    v97で使用する今回だけの補正登録。
    キャッシュそのものではなく profile_year_adjustments に1卓分を記録するため、
    後から再集計されても消えない。noteのUNIQUE性で二重登録もしない。
    """
    activity_year = 2026
    note = 'testplay_madamis_minus1_restore_20260830'
    targets = [
        ('magetann', 'まげたん', 'PL', None),
        ('suuchii', 'すぅちぃ', 'PL', None),
        ('nick', 'ニック', 'PL', None),
        ('yuzuky', 'yuzuky', 'GM', '804350794371039272'),
    ]
    result = {}
    with db() as c:
        rows = c.execute(
            """SELECT discord_id, username, display_name FROM registered_members
               UNION ALL
               SELECT discord_id, username, display_name FROM users"""
        ).fetchall()
        by_norm = {}
        for r in rows:
            uid = str(r['discord_id'])
            for raw in (r['display_name'], r['username']):
                key = _temporary_v96_norm_name(raw)
                if key and key not in by_norm:
                    by_norm[key] = uid

        for slug, name, role, fixed_uid in targets:
            uid = str(fixed_uid) if fixed_uid else by_norm.get(_temporary_v96_norm_name(name))
            if not uid:
                result[name] = 'not_found'
                continue
            gm_delta = 1 if role == 'GM' else 0
            pl_delta = 1 if role == 'PL' else 0
            cur = c.execute(
                """INSERT OR IGNORE INTO profile_year_adjustments(
                       discord_id,activity_year,gm_delta,pl_delta,trpg_delta,madamis_delta,note,created_at
                   ) VALUES(?,?,?,?,0,1,?,?)""",
                (uid, activity_year, gm_delta, pl_delta, note, str(updated_at)),
            )
            result[name] = 'added' if cur.rowcount else 'already_done'

            marker = f'temporary_v97_madamis_year_fix_{slug}'
            c.execute(
                "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES(?, '1')", (marker,)
            )
    return result


def profile_cache_v74_resynced() -> bool:
    """v74でプロフィール集計キャッシュをカレンダー履歴から再同期済みか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='profile_cache_v74_resynced' AND meta_value='1'"
        ).fetchone() is not None


def mark_profile_cache_v74_resynced(updated_at: str):
    """v74の一度きりのプロフィールキャッシュ再同期を完了扱いにする。"""
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_v74_resynced','1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_v74_resynced_at',?)",
            (str(updated_at),),
        )

def scenario_gm_counter_initialized() -> bool:
    """v73のシナリオGMカウンター再同期が完了しているか。"""
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='scenario_gm_delta_v73_seeded' AND meta_value='1'"
        ).fetchone() is not None


def ensure_scenario_gm_counter_initialized(as_of_date: str, updated_at: str):
    """v73初回だけ、カレンダー履歴を正としてシナリオ5回称号を再構築する。

    ここで一度だけ全履歴からGM回数を作り直し、重複していた scenario_5 の解除行も
    正規化する。以後は apply_profile_daily_delta() が当日分だけ +1 する。
    """
    with db() as c:
        seeded = c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='scenario_gm_delta_v73_seeded' AND meta_value='1'"
        ).fetchone()
        if seeded:
            return

        records = _unique_table_records(c, str(as_of_date))
        counts = {}
        for r in records:
            gm = str(r.get("gm") or "")
            scenario = str(r.get("scenario") or "").strip()
            if not gm or not scenario:
                continue
            counts[(gm, scenario)] = counts.get((gm, scenario), 0) + 1

        # 現在 scenario_5 を装備している人は、再生成後の新しいunlock_idへ付け替える。
        equipped_scenarios = {}
        for row in c.execute(
            """SELECT et.discord_id,au.context_key,au.title_name
                 FROM equipped_titles et
                 JOIN achievement_unlocks au ON au.id=et.unlock_id
                WHERE au.achievement_key='scenario_5'"""
        ).fetchall():
            equipped_scenarios[str(row["discord_id"])] = str(row["context_key"] or row["title_name"] or "").strip()

        c.execute("DELETE FROM achievement_scenario_gm_totals")
        if counts:
            c.executemany(
                "INSERT INTO achievement_scenario_gm_totals(discord_id,scenario_name,gm_count) VALUES(?,?,?)",
                [(uid, scenario, n) for (uid, scenario), n in counts.items()],
            )

        # v70以前に発生した重複・不整合を一度リセットし、カレンダー実績から正しく再付与。
        c.execute("DELETE FROM achievement_unlocks WHERE achievement_key='scenario_5'")
        for (uid, scenario), n in counts.items():
            if int(n) < 5:
                continue
            c.execute(
                """INSERT OR IGNORE INTO achievement_unlocks(
                     discord_id,achievement_key,context_key,title_name,rarity,secret,context_label,unlocked_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (uid, "scenario_5", scenario, scenario, "black", 1, f"{scenario}を5回回す", str(updated_at)),
            )

        for uid, scenario in equipped_scenarios.items():
            row = c.execute(
                """SELECT id FROM achievement_unlocks
                    WHERE discord_id=? AND achievement_key='scenario_5' AND context_key=?""",
                (uid, scenario),
            ).fetchone()
            if row:
                c.execute(
                    "INSERT OR REPLACE INTO equipped_titles(discord_id,unlock_id,updated_at) VALUES(?,?,?)",
                    (uid, int(row["id"]), str(updated_at)),
                )
            else:
                c.execute("DELETE FROM equipped_titles WHERE discord_id=?", (uid,))

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('scenario_gm_delta_v73_seeded','1')"
        )


def profile_cache_initialized() -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='profile_cache_as_of'"
        ).fetchone() is not None


def profile_delta_initialized() -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='profile_delta_v69_seeded' AND meta_value='1'"
        ).fetchone() is not None


def _unique_table_records_for_date(c, event_date: str):
    rows = c.execute(
        """SELECT id,event_date,game_type,scenario_name,gm_discord_id
             FROM calendar_sessions
            WHERE event_date=? AND game_type IN ('TRPG','MADMIS','マダミス')
            ORDER BY id""",
        (str(event_date),),
    ).fetchall()
    if not rows:
        return []
    ids = [int(r["id"]) for r in rows]
    member_map = {sid: [] for sid in ids}
    guest_map = {sid: [] for sid in ids}
    for start in range(0, len(ids), 800):
        chunk = ids[start:start+800]
        ph = ",".join("?" for _ in chunk)
        for x in c.execute(
            f"SELECT calendar_session_id,discord_id FROM calendar_session_members WHERE calendar_session_id IN ({ph})",
            chunk,
        ).fetchall():
            member_map[int(x["calendar_session_id"])].append(str(x["discord_id"]))
        for x in c.execute(
            f"SELECT calendar_session_id,display_name FROM calendar_guest_members WHERE calendar_session_id IN ({ph})",
            chunk,
        ).fetchall():
            guest_map[int(x["calendar_session_id"])].append(str(x["display_name"]))
    unique = {}
    for r in rows:
        sid = int(r["id"])
        rec = {
            "id": sid,
            "date": str(r["event_date"]),
            "game_type": _achievement_gt(r["game_type"]),
            "scenario": str(r["scenario_name"] or "").strip(),
            "gm": str(r["gm_discord_id"] or ""),
            "members": tuple(sorted(member_map.get(sid, ()))),
            "guests": tuple(sorted(guest_map.get(sid, ()))),
        }
        unique.setdefault(_record_table_key(rec), rec)
    return list(unique.values())


def _streak_ending_on(c, uid: str, event_date: str) -> int:
    from datetime import date as _date, timedelta as _td
    d = _date.fromisoformat(str(event_date))
    n = 0
    while True:
        row = c.execute(
            "SELECT 1 FROM profile_active_days WHERE discord_id=? AND event_date=?",
            (str(uid), d.isoformat()),
        ).fetchone()
        if not row:
            break
        n += 1
        d -= _td(days=1)
    return n


def _refresh_pair_top_for_user(c, uid: str, updated_at: str):
    c.execute("DELETE FROM profile_pair_cache WHERE discord_id=?", (str(uid),))
    rows = c.execute(
        """SELECT pt.partner_discord_id,pt.table_count,
                  COALESCE(rm.display_name,rm.username,u.display_name,u.username,pt.partner_discord_id) AS n
             FROM profile_pair_totals pt
             LEFT JOIN registered_members rm ON rm.discord_id=pt.partner_discord_id
             LEFT JOIN users u ON u.discord_id=pt.partner_discord_id
            WHERE pt.discord_id=?
            ORDER BY pt.table_count DESC, n COLLATE NOCASE ASC
            LIMIT 3""",
        (str(uid),),
    ).fetchall()
    for rank_no, r in enumerate(rows, 1):
        c.execute(
            """INSERT INTO profile_pair_cache(discord_id,partner_discord_id,table_count,rank_no,updated_at)
               VALUES(?,?,?,?,?)""",
            (str(uid), str(r["partner_discord_id"]), int(r["table_count"]), rank_no, str(updated_at)),
        )


def apply_profile_daily_delta(event_date: str, updated_at: str) -> int:
    """v69: 指定日の未処理卓だけをプロフィールキャッシュへ加算する。

    過去の全履歴は読まない。初回v69移行時だけ refresh_profile_caches() で
    基準を作り、それ以降の20時処理はこの関数だけを使う。
    """
    day = str(event_date)
    with db() as c:
        records = _unique_table_records_for_date(c, day)
        if not records:
            c.execute(
                "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_as_of',?)",
                (day,),
            )
            c.execute(
                "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_updated_at',?)",
                (str(updated_at),),
            )
            return 0

        registered = {str(r["discord_id"]) for r in c.execute("SELECT discord_id FROM registered_members").fetchall()}
        affected = set()
        added = 0
        ay = _activity_year_for_date(day)
        term = ay - 2023

        for r in records:
            key = _record_table_key(r)
            cur = c.execute(
                "INSERT OR IGNORE INTO profile_processed_tables(table_key,event_date,processed_at) VALUES(?,?,?)",
                (key, day, str(updated_at)),
            )
            if not cur.rowcount:
                continue
            added += 1

            gm = str(r.get("gm") or "")
            members = [str(x) for x in r.get("members", ()) if str(x)]
            people = set(members)
            if gm:
                people.add(gm)
            people &= registered

            for uid in people:
                c.execute(
                    """INSERT OR IGNORE INTO profile_stats_cache(
                         discord_id,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,total_roles,
                         max_pair,active_years,max_day,max_streak,christmas,updated_at
                       ) VALUES(?,0,0,0,0,0,0,0,0,0,0,0,0,?)""",
                    (uid, str(updated_at)),
                )
                c.execute(
                    """INSERT OR IGNORE INTO profile_year_stats_cache(
                         discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,trpg_pl_count,madamis_pl_count,updated_at
                       ) VALUES(?,?,?,0,0,0,0,0,0,?)""",
                    (uid, ay, term, str(updated_at)),
                )

            if gm in registered:
                gm_type_col = "madamis_count" if str(r.get("game_type")) == "MADMIS" else "trpg_count"
                c.execute(
                    f"UPDATE profile_stats_cache SET gm_count=gm_count+1,{gm_type_col}={gm_type_col}+1,total_roles=total_roles+1,updated_at=? WHERE discord_id=?",
                    (str(updated_at), gm),
                )
                c.execute(
                    f"UPDATE profile_year_stats_cache SET gm_count=gm_count+1,{gm_type_col}={gm_type_col}+1,updated_at=? WHERE discord_id=? AND activity_year=?",
                    (str(updated_at), gm, ay),
                )
                affected.add(gm)
                scenario = str(r.get("scenario") or "").strip()
                if scenario:
                    c.execute(
                        """INSERT INTO achievement_scenario_gm_totals(discord_id,scenario_name,gm_count) VALUES(?,?,1)
                           ON CONFLICT(discord_id,scenario_name) DO UPDATE SET gm_count=gm_count+1""",
                        (gm, scenario),
                    )

            for uid in members:
                if uid not in registered:
                    continue
                is_madamis = str(r.get("game_type")) == "MADMIS"
                type_col = "madamis_count" if is_madamis else "trpg_count"
                pl_type_col = "madamis_pl_count" if is_madamis else "trpg_pl_count"
                c.execute(
                    f"UPDATE profile_stats_cache SET pl_count=pl_count+1,{type_col}={type_col}+1,{pl_type_col}={pl_type_col}+1,total_roles=total_roles+1,updated_at=? WHERE discord_id=?",
                    (str(updated_at), uid),
                )
                c.execute(
                    f"UPDATE profile_year_stats_cache SET pl_count=pl_count+1,{type_col}={type_col}+1,{pl_type_col}={pl_type_col}+1,updated_at=? WHERE discord_id=? AND activity_year=?",
                    (str(updated_at), uid, ay),
                )
                affected.add(uid)

            for uid in people:
                c.execute(
                    """INSERT INTO profile_active_days(discord_id,event_date,table_count) VALUES(?,?,1)
                       ON CONFLICT(discord_id,event_date) DO UPDATE SET table_count=table_count+1""",
                    (uid, day),
                )
                c.execute(
                    "INSERT OR IGNORE INTO profile_active_years(discord_id,activity_year) VALUES(?,?)",
                    (uid, ay),
                )
                for other in people:
                    if other == uid:
                        continue
                    c.execute(
                        """INSERT INTO profile_pair_totals(discord_id,partner_discord_id,table_count) VALUES(?,?,1)
                           ON CONFLICT(discord_id,partner_discord_id) DO UPDATE SET table_count=table_count+1""",
                        (uid, other),
                    )

        for uid in affected:
            day_count_row = c.execute(
                "SELECT table_count FROM profile_active_days WHERE discord_id=? AND event_date=?",
                (uid, day),
            ).fetchone()
            day_count = int(day_count_row["table_count"]) if day_count_row else 0
            active_years = int(c.execute(
                "SELECT COUNT(*) AS n FROM profile_active_years WHERE discord_id=?", (uid,)
            ).fetchone()["n"])
            max_pair = int(c.execute(
                "SELECT COALESCE(MAX(table_count),0) AS n FROM profile_pair_totals WHERE discord_id=?", (uid,)
            ).fetchone()["n"])
            streak = _streak_ending_on(c, uid, day)
            christmas = 1 if day[5:] in ("12-24", "12-25") and day_count else 0
            c.execute(
                """UPDATE profile_stats_cache
                      SET max_pair=?,active_years=?,max_day=MAX(max_day,?),max_streak=MAX(max_streak,?),
                          christmas=MAX(christmas,?),updated_at=?
                    WHERE discord_id=?""",
                (max_pair, active_years, day_count, streak, christmas, str(updated_at), uid),
            )
            _refresh_pair_top_for_user(c, uid, str(updated_at))

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_as_of',?)",
            (day,),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_updated_at',?)",
            (str(updated_at),),
        )
        return added


def profile_data(discord_id: str):
    """v68: 履歴を再計算せず、20時更新の表示専用キャッシュだけを読む。"""
    uid = str(discord_id)
    with db() as c:
        member = c.execute("SELECT * FROM registered_members WHERE discord_id=?", (uid,)).fetchone()
        if not member:
            return None

        stat = c.execute(
            "SELECT * FROM profile_stats_cache WHERE discord_id=?", (uid,)
        ).fetchone()
        if stat:
            total = {
                "gm":int(stat["gm_count"]), "pl":int(stat["pl_count"]),
                "trpg":int(stat["trpg_count"]), "madamis":int(stat["madamis_count"]),
            }
        else:
            total = {"gm":0,"pl":0,"trpg":0,"madamis":0}

        years = [{
            "year":int(r["activity_year"]), "term":int(r["term"]),
            "gm":int(r["gm_count"]), "pl":int(r["pl_count"]),
            "trpg":int(r["trpg_count"]), "madamis":int(r["madamis_count"]),
        } for r in c.execute(
            "SELECT * FROM profile_year_stats_cache WHERE discord_id=? ORDER BY activity_year", (uid,)
        ).fetchall()]

        # v97: テスト等で必要になった明示的な年別補正を最後に重ねる。
        # profile_year_stats_cache が将来再構築されても、この補正値は別テーブルなので消えない。
        adjustments = c.execute(
            """SELECT activity_year,
                      SUM(gm_delta) AS gm_delta,SUM(pl_delta) AS pl_delta,
                      SUM(trpg_delta) AS trpg_delta,SUM(madamis_delta) AS madamis_delta
                 FROM profile_year_adjustments WHERE discord_id=? GROUP BY activity_year""",
            (uid,),
        ).fetchall()
        year_map = {int(y["year"]): y for y in years}
        for a in adjustments:
            ay = int(a["activity_year"])
            y = year_map.get(ay)
            if y is None:
                y = {"year":ay,"term":ay-2023,"gm":0,"pl":0,"trpg":0,"madamis":0}
                years.append(y); year_map[ay] = y
            y["gm"] += int(a["gm_delta"] or 0)
            y["pl"] += int(a["pl_delta"] or 0)
            y["trpg"] += int(a["trpg_delta"] or 0)
            y["madamis"] += int(a["madamis_delta"] or 0)
        years.sort(key=lambda y: y["year"])

        pair_rows = []
        for r in c.execute(
            """SELECT pc.partner_discord_id,pc.table_count,pc.rank_no,
                      COALESCE(rm.display_name,rm.username,u.display_name,u.username,pc.partner_discord_id) AS n
                   FROM profile_pair_cache pc
                   LEFT JOIN registered_members rm ON rm.discord_id=pc.partner_discord_id
                   LEFT JOIN users u ON u.discord_id=pc.partner_discord_id
                   WHERE pc.discord_id=? ORDER BY pc.rank_no LIMIT 3""",
            (uid,),
        ).fetchall():
            pair_rows.append({
                "discord_id":str(r["partner_discord_id"]),
                "display_name":str(r["n"]),
                "n":int(r["table_count"]),
            })

        equipped = equipped_title(uid, c)
        return {
            "member":dict(member), "total":total, "years":years,
            "pair_top":pair_rows, "equipped":equipped,
        }


def equipped_title(discord_id: str, conn=None):
    own = conn is None
    cm = db() if own else None
    c = cm.__enter__() if own else conn
    try:
        row = c.execute(
            """SELECT au.* FROM equipped_titles et
               JOIN achievement_unlocks au ON au.id=et.unlock_id
               WHERE et.discord_id=? AND au.discord_id=?""",
            (str(discord_id), str(discord_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            cm.__exit__(None,None,None)


def equipped_titles_map(discord_ids=None):
    with db() as c:
        params=[]; where=""
        if discord_ids is not None:
            ids=[str(x) for x in dict.fromkeys(discord_ids) if str(x)]
            if not ids: return {}
            where="WHERE et.discord_id IN ("+",".join("?" for _ in ids)+")"
            params=ids
        rows=c.execute(
            f"""SELECT et.discord_id,au.* FROM equipped_titles et
                JOIN achievement_unlocks au ON au.id=et.unlock_id {where}""", params
        ).fetchall()
        return {str(r["discord_id"]):dict(r) for r in rows}


def set_equipped_title(discord_id: str, unlock_id: int | None, updated_at: str) -> bool:
    uid=str(discord_id)
    with db() as c:
        if unlock_id is None:
            c.execute("DELETE FROM equipped_titles WHERE discord_id=?",(uid,))
            return True
        row=c.execute("SELECT id FROM achievement_unlocks WHERE id=? AND discord_id=?",(int(unlock_id),uid)).fetchone()
        if not row: return False
        c.execute(
            """INSERT INTO equipped_titles(discord_id,unlock_id,updated_at) VALUES(?,?,?)
               ON CONFLICT(discord_id) DO UPDATE SET unlock_id=excluded.unlock_id,updated_at=excluded.updated_at""",
            (uid,int(unlock_id),str(updated_at)),
        )
    return True


def achievement_unlocks_for_user(discord_id: str):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM achievement_unlocks WHERE discord_id=? ORDER BY id", (str(discord_id),)
        ).fetchall()]


def _insert_unlock(c, uid, key, context_key, name, rarity, secret, context_label, unlocked_at, new_rows):
    cur=c.execute(
        """INSERT OR IGNORE INTO achievement_unlocks(
             discord_id,achievement_key,context_key,title_name,rarity,secret,context_label,unlocked_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (str(uid),str(key),str(context_key or ""),str(name),str(rarity),int(bool(secret)),context_label,str(unlocked_at)),
    )
    if cur.rowcount:
        row=c.execute("SELECT * FROM achievement_unlocks WHERE id=?",(cur.lastrowid,)).fetchone()
        new_rows.append(dict(row))


def evaluate_achievements(as_of_date: str, unlocked_at: str):
    """v69: 表示キャッシュだけで固定称号を判定する軽量版。

    毎日20時に過去履歴を再走査しない。シナリオ5回 / 年間MVPの新規判定は
    いったん停止し、既に解除済みの称号はそのまま保持する。
    """
    new_rows = []
    with db() as c:
        defs = {x["key"]: x for x in FIXED_ACHIEVEMENTS}
        rows = c.execute(
            """SELECT ps.*,rm.discord_id
                 FROM profile_stats_cache ps
                 JOIN registered_members rm ON rm.discord_id=ps.discord_id"""
        ).fetchall()
        for row in rows:
            uid = str(row["discord_id"])
            values = {
                "pl": int(row["pl_count"]),
                "gm": int(row["gm_count"]),
                "trpg_pl": int(row["trpg_pl_count"]),
                "madamis_pl": int(row["madamis_pl_count"]),
                "pair": int(row["max_pair"]),
                "active_years": int(row["active_years"]),
                "max_day": int(row["max_day"]),
                "total_roles": int(row["total_roles"]),
                "streak": int(row["max_streak"]),
                "christmas": int(row["christmas"]),
            }
            for d in FIXED_ACHIEVEMENTS:
                if d["kind"] == "pair_dynamic":
                    continue
                if d["kind"] not in values:
                    continue
                if values[d["kind"]] >= d["target"]:
                    _insert_unlock(c, uid, d["key"], "", d["name"], d["rarity"], d["secret"], None, unlocked_at, new_rows)

            # 生き別れの兄弟だけは相手別の累計キャッシュから判定できるので継続。
            d = defs["pair_250"]
            for pr in c.execute(
                "SELECT partner_discord_id,table_count FROM profile_pair_totals WHERE discord_id=? AND table_count>=?",
                (uid, int(d["target"])),
            ).fetchall():
                other = str(pr["partner_discord_id"])
                label = f"{_display_name(c, other)}と{d['target']}卓同卓"
                _insert_unlock(c, uid, d["key"], other, d["name"], d["rarity"], 1, label, unlocked_at, new_rows)

            # v70: シナリオ5回は専用カウンターだけを見るので軽量。
            # 条件を満たすシナリオごとに別の黒称号として解除できる。
            for sr in c.execute(
                "SELECT scenario_name,gm_count FROM achievement_scenario_gm_totals WHERE discord_id=? AND gm_count>=5",
                (uid,),
            ).fetchall():
                scenario = str(sr["scenario_name"] or "").strip()
                if scenario:
                    _insert_unlock(c, uid, "scenario_5", scenario, scenario, "black", 1,
                                   f"{scenario}を5回回す", unlocked_at, new_rows)

    return new_rows

def achievement_collection(discord_id: str):
    """v68: 称号進捗も20時更新のプロフィールキャッシュから表示する。"""
    uid=str(discord_id)
    with db() as c:
        stat=c.execute("SELECT * FROM profile_stats_cache WHERE discord_id=?",(uid,)).fetchone()
        values={
            "pl":int(stat["pl_count"]) if stat else 0,
            "gm":int(stat["gm_count"]) if stat else 0,
            "trpg_pl":int(stat["trpg_pl_count"]) if stat else 0,
            "madamis_pl":int(stat["madamis_pl_count"]) if stat else 0,
            "pair":int(stat["max_pair"]) if stat else 0,
            "active_years":int(stat["active_years"]) if stat else 0,
            "max_day":int(stat["max_day"]) if stat else 0,
            "total_roles":int(stat["total_roles"]) if stat else 0,
            "streak":int(stat["max_streak"]) if stat else 0,
            "christmas":int(stat["christmas"]) if stat else 0,
        }
        unlocked=[dict(r) for r in c.execute(
            "SELECT * FROM achievement_unlocks WHERE discord_id=? ORDER BY id",(uid,)
        ).fetchall()]
        unlock_by_key={}
        for r in unlocked:
            unlock_by_key.setdefault(r["achievement_key"],[]).append(r)
        cards=[]
        for d in FIXED_ACHIEVEMENTS:
            if d["kind"]=="pair_dynamic":
                rows=unlock_by_key.get(d["key"],[])
                if rows:
                    for r in rows:
                        cards.append({"definition":d,"unlock":r,"value":d["target"]})
                else:
                    cards.append({"definition":d,"unlock":None,"value":0})
                continue
            rows=unlock_by_key.get(d["key"],[])
            cards.append({"definition":d,"unlock":rows[0] if rows else None,"value":values.get(d["kind"],0)})

        # v70: シナリオ5回は差分カウンターで復活。年間MVPは引き続き新規判定を停止。
        for r in unlock_by_key.get("scenario_5",[]):
            cards.append({"definition":{"key":"scenario_5","name":r["title_name"],"rarity":"black","secret":1,"kind":"dynamic","target":5,"condition":"同じシナリオを5回回す"},"unlock":r,"value":5})
        for r in unlock_by_key.get("year_mvp",[]):
            cards.append({"definition":{"key":"year_mvp","name":r["title_name"],"rarity":"black","secret":1,"kind":"dynamic","target":1,"condition":""},"unlock":r,"value":1})
        return cards


def achievement_run_done(run_date: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM achievement_daily_runs WHERE run_date=?",(str(run_date),)).fetchone() is not None


def mark_achievement_run(run_date: str, processed_at: str):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO achievement_daily_runs(run_date,processed_at) VALUES(?,?)",(str(run_date),str(processed_at)))


def achievement_bootstrapped() -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM achievement_meta WHERE meta_key='bootstrapped' AND meta_value='1'").fetchone() is not None


def mark_achievement_bootstrapped():
    with db() as c:
        c.execute("INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('bootstrapped','1')")
