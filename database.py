from __future__ import annotations

import os
import sqlite3
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
                                    game_type: str | None = None):
    guest_participant_names = guest_participant_names or []
    with db() as c:
        row = c.execute("SELECT game_type FROM calendar_sessions WHERE id=?", (calendar_session_id,)).fetchone()
        if not row: return False
        normalized_game_type = game_type or str(row["game_type"] or "TRPG")
        if normalized_game_type == "マダミス": normalized_game_type = "MADMIS"
        if normalized_game_type not in {"TRPG", "MADMIS", "EVENT"}: return False
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
    ensure_calendar_columns()
    with db() as c:
        c.execute(
            """INSERT OR IGNORE INTO registered_members(
                   discord_id,username,display_name,avatar_url,created_at,updated_at
               )
               SELECT discord_id,username,display_name,avatar_url,updated_at,updated_at
               FROM users"""
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

    {"key":"pair_25","name":"ズッ卓","rarity":"bronze","secret":0,"kind":"pair","target":25,"condition":"同じ人と25卓同卓する"},
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
                "gm": str(r["gm_discord_id"] or ""), "members": members,
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
        "total_roles": len(gm_records) + len(pl_records), "pair_counts": pair_counts,
        "max_pair": max(pair_counts.values(), default=0), "max_streak": max_streak,
        "max_day": max_day, "christmas": christmas, "active_years": active_years,
        "gm_records": gm_records, "pl_records": pl_records, "all_records": all_records,
    }


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
                     discord_id,gm_count,pl_count,trpg_count,madamis_count,total_roles,
                     max_pair,active_years,max_day,max_streak,christmas,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid,m["gm"],m["pl"],m["trpg_pl"],m["madamis_pl"],m["total_roles"],
                 m["max_pair"],m["active_years"],m["max_day"],m["max_streak"],
                 1 if m["christmas"] else 0,str(updated_at)),
            )

            for y in range(first_year, current_ay + 1):
                yr = [r for r in records if _activity_year_for_date(r["date"]) == y]
                mm = _member_metrics(c, uid, yr)
                c.execute(
                    """INSERT INTO profile_year_stats_cache(
                         discord_id,activity_year,term,gm_count,pl_count,trpg_count,madamis_count,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (uid,y,y-2023,mm["gm"],mm["pl"],mm["trpg_pl"],mm["madamis_pl"],str(updated_at)),
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

        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_as_of',?)",
            (str(as_of_date),),
        )
        c.execute(
            "INSERT OR REPLACE INTO achievement_meta(meta_key,meta_value) VALUES('profile_cache_updated_at',?)",
            (str(updated_at),),
        )


def profile_cache_initialized() -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM achievement_meta WHERE meta_key='profile_cache_as_of'"
        ).fetchone() is not None


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
    """as_of_date までのカレンダーを参照して未取得称号だけ解除し、新規解除行を返す。"""
    new_rows=[]
    with db() as c:
        records=_unique_table_records(c, as_of_date)
        users=[str(r["discord_id"]) for r in c.execute("SELECT discord_id FROM registered_members").fetchall()]
        metrics={uid:_member_metrics(c,uid,records) for uid in users}
        defs={x["key"]:x for x in FIXED_ACHIEVEMENTS}

        for uid,m in metrics.items():
            values={
                "pl":m["pl"], "gm":m["gm"], "trpg_pl":m["trpg_pl"], "madamis_pl":m["madamis_pl"],
                "pair":m["max_pair"], "active_years":m["active_years"], "max_day":m["max_day"],
                "total_roles":m["total_roles"], "streak":m["max_streak"], "christmas":1 if m["christmas"] else 0,
            }
            for d in FIXED_ACHIEVEMENTS:
                if d["kind"] in ("pair_dynamic",):
                    continue
                if d["kind"] not in values: continue
                if values[d["kind"]] >= d["target"]:
                    _insert_unlock(c,uid,d["key"],"",d["name"],d["rarity"],d["secret"],None,unlocked_at,new_rows)

            # 生き別れの兄弟は達成相手ごとに別解除。装備時に「〇〇と250卓同卓」を表示。
            d=defs["pair_250"]
            for other,n in m["pair_counts"].items():
                if n >= d["target"]:
                    label=f"{_display_name(c,other)}と{d['target']}卓同卓"
                    _insert_unlock(c,uid,d["key"],str(other),d["name"],d["rarity"],1,label,unlocked_at,new_rows)

            # 同じシナリオを5回GM → シナリオ名そのものを黒シークレット称号として解除。
            scenario_counts={}
            for r in m["gm_records"]:
                if r["scenario"]:
                    k=(r["game_type"],r["scenario"])
                    scenario_counts[k]=scenario_counts.get(k,0)+1
            for (gt,name),n in scenario_counts.items():
                if n>=5:
                    _insert_unlock(c,uid,"scenario_5",f"{gt}\x1f{name}",name,"black",1,None,unlocked_at,new_rows)

        # つぶぐみ年度ごとに、PL+GM合計50卓へ最も早く到達した1人だけMVP。
        years=sorted(set(_activity_year_for_date(r["date"]) for r in records))
        for y in years:
            year_records=[r for r in records if _activity_year_for_date(r["date"])==y]
            candidates=[]
            for uid in users:
                appearances=[]
                for r in year_records:
                    if r["gm"]==uid or uid in r["members"]:
                        appearances.append((r["date"],r["id"]))
                appearances.sort()
                if len(appearances)>=50:
                    reach=appearances[49]
                    candidates.append((reach[0],reach[1],uid))
            if candidates:
                _,_,winner=min(candidates)
                _insert_unlock(c,winner,"year_mvp",str(y),f"{y}年のMVP","black",1,None,unlocked_at,new_rows)
    return new_rows


def achievement_collection(discord_id: str):
    """v68: 称号進捗も20時更新のプロフィールキャッシュから表示する。"""
    uid=str(discord_id)
    with db() as c:
        stat=c.execute("SELECT * FROM profile_stats_cache WHERE discord_id=?",(uid,)).fetchone()
        values={
            "pl":int(stat["pl_count"]) if stat else 0,
            "gm":int(stat["gm_count"]) if stat else 0,
            "trpg_pl":int(stat["trpg_count"]) if stat else 0,
            "madamis_pl":int(stat["madamis_count"]) if stat else 0,
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

        scenario_rows=unlock_by_key.get("scenario_5",[])
        if scenario_rows:
            for r in scenario_rows:
                cards.append({"definition":{"key":"scenario_5","name":r["title_name"],"rarity":"black","secret":1,"kind":"dynamic","target":5,"condition":"同じシナリオを5回回す"},"unlock":r,"value":5})
        else:
            cards.append({"definition":{"key":"scenario_5","name":"？？？？？？？","rarity":"black","secret":1,"kind":"dynamic","target":5,"condition":""},"unlock":None,"value":0})
        mvp_rows=unlock_by_key.get("year_mvp",[])
        if mvp_rows:
            for r in mvp_rows:
                cards.append({"definition":{"key":"year_mvp","name":r["title_name"],"rarity":"black","secret":1,"kind":"dynamic","target":1,"condition":""},"unlock":r,"value":1})
        else:
            cards.append({"definition":{"key":"year_mvp","name":"？？？？？？？","rarity":"black","secret":1,"kind":"dynamic","target":1,"condition":""},"unlock":None,"value":0})
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
