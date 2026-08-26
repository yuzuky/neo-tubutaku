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

            CREATE INDEX IF NOT EXISTS idx_calendar_sessions_event_date
                ON calendar_sessions(event_date);
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
                      COALESCE(u.display_name,u.username,cs.gm_discord_id) AS gm_name
               FROM calendar_sessions cs
               LEFT JOIN users u ON u.discord_id=cs.gm_discord_id
               WHERE cs.calendar_visible=1
                 AND cs.event_date>=?
                 AND cs.event_date<?
               ORDER BY cs.event_date, cs.id""",
            (start_date, end_date),
        ).fetchall()
        out = []
        for row in rows:
            members = c.execute(
                """SELECT csm.discord_id,
                          COALESCE(u.display_name,u.username,csm.discord_id) AS display_name
                   FROM calendar_session_members csm
                   LEFT JOIN users u ON u.discord_id=csm.discord_id
                   WHERE csm.calendar_session_id=?
                   ORDER BY display_name""",
                (row["id"],),
            ).fetchall()
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
               FROM users
               WHERE LOWER(COALESCE(display_name,'')) <> 'okuyama'
                 AND LOWER(COALESCE(username,'')) <> 'okuyama'
               ORDER BY display_name"""
        ).fetchall()
    return scenarios, users


def add_manual_calendar_session(
    game_type: str,
    scenario_name: str,
    event_date: str,
    start_time: str,
    gm_discord_id: str,
    participant_ids: list[str],
    created_at: str,
) -> int:
    """Discordへ何も送らず、履歴・カレンダーDBだけへ卓を追加する。"""
    with db() as c:
        cur = c.execute(
            """INSERT INTO calendar_sessions(
                   source_session_id,source_recruitment_id,
                   game_type,scenario_name,event_date,start_time,
                   gm_discord_id,calendar_visible,created_at
               ) VALUES(NULL,NULL,?,?,?,?,?,?,?)""",
            (
                game_type,
                scenario_name.strip(),
                event_date,
                start_time or "未定",
                str(gm_discord_id),
                1,
                created_at,
            ),
        )
        cal_id = int(cur.lastrowid)

        member_ids = [
            str(uid)
            for uid in dict.fromkeys(participant_ids)
            if str(uid) != str(gm_discord_id)
        ]
        if member_ids:
            c.executemany(
                """INSERT OR IGNORE INTO calendar_session_members(
                       calendar_session_id,discord_id
                   ) VALUES(?,?)""",
                [(cal_id, uid) for uid in member_ids],
            )

        gt = normalize_progress_game_type(game_type)
        if gt in {"TRPG", "MADMIS"} and scenario_name.strip():
            for uid in member_ids:
                c.execute(
                    """INSERT INTO scenario_progress(
                           game_type,scenario_name,discord_id,status,updated_at
                       ) VALUES(?,?,?,?,?)
                       ON CONFLICT(game_type,scenario_name,discord_id)
                       DO UPDATE SET status='PASSED',updated_at=excluded.updated_at""",
                    (gt, scenario_name.strip(), uid, "PASSED", created_at),
                )

        return cal_id


def calendar_session_detail(calendar_session_id: int):
    with db() as c:
        row = c.execute(
            """SELECT cs.*,
                      COALESCE(u.display_name,u.username,cs.gm_discord_id) AS gm_name
               FROM calendar_sessions cs
               LEFT JOIN users u ON u.discord_id=cs.gm_discord_id
               WHERE cs.id=?""",
            (calendar_session_id,),
        ).fetchone()
        if not row:
            return None, []
        members = c.execute(
            """SELECT csm.discord_id,
                      COALESCE(u.display_name,u.username,csm.discord_id) AS display_name
               FROM calendar_session_members csm
               LEFT JOIN users u ON u.discord_id=csm.discord_id
               WHERE csm.calendar_session_id=?
               ORDER BY display_name""",
            (calendar_session_id,),
        ).fetchall()
    return row, members


def update_calendar_session_members(calendar_session_id: int, participant_ids: list[str]):
    with db() as c:
        row = c.execute(
            "SELECT gm_discord_id, game_type FROM calendar_sessions WHERE id=?",
            (calendar_session_id,),
        ).fetchone()
        if not row:
            return False
        if str(row["game_type"]) == "EVENT":
            return False

        c.execute(
            "DELETE FROM calendar_session_members WHERE calendar_session_id=?",
            (calendar_session_id,),
        )
        cleaned = [
            str(uid)
            for uid in dict.fromkeys(participant_ids)
            if uid and str(uid) != str(row["gm_discord_id"])
        ]
        if cleaned:
            c.executemany(
                """INSERT OR IGNORE INTO calendar_session_members(
                       calendar_session_id,discord_id
                   ) VALUES(?,?)""",
                [(calendar_session_id, uid) for uid in cleaned],
            )

            info = c.execute(
                """SELECT game_type,scenario_name,created_at
                   FROM calendar_sessions WHERE id=?""",
                (calendar_session_id,),
            ).fetchone()
            if info:
                gt = normalize_progress_game_type(info["game_type"])
                if gt in {"TRPG", "MADMIS"}:
                    for uid in cleaned:
                        c.execute(
                            """INSERT INTO scenario_progress(
                                   game_type,scenario_name,discord_id,status,updated_at
                               ) VALUES(?,?,?,?,?)
                               ON CONFLICT(game_type,scenario_name,discord_id)
                               DO UPDATE SET status='PASSED',updated_at=excluded.updated_at""",
                            (
                                gt,
                                info["scenario_name"],
                                uid,
                                "PASSED",
                                info["created_at"] or "",
                            ),
                        )
    return True


def hide_calendar_session(calendar_session_id: int):
    with db() as c:
        cur = c.execute(
            "UPDATE calendar_sessions SET calendar_visible=0 WHERE id=?",
            (calendar_session_id,),
        )
    return cur.rowcount > 0


def permanently_delete_calendar_session(calendar_session_id: int, deleted_at: str):
    """履歴を削除。source sessionが残っていてもbackfillで復活させない。"""
    with db() as c:
        row = c.execute(
            "SELECT source_session_id FROM calendar_sessions WHERE id=?",
            (calendar_session_id,),
        ).fetchone()
        if not row:
            return False

        source_session_id = row["source_session_id"]
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
            "DELETE FROM calendar_sessions WHERE id=?",
            (calendar_session_id,),
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
               FROM users
               WHERE LOWER(COALESCE(display_name,'')) <> 'okuyama'
                 AND LOWER(COALESCE(username,'')) <> 'okuyama'
               ORDER BY display_name"""
        ).fetchall()

        scenarios = c.execute(
            """SELECT game_type,scenario_name FROM (
                 SELECT
                   CASE WHEN game_type='マダミス' THEN 'MADMIS' ELSE game_type END AS game_type,
                   scenario_name
                 FROM calendar_sessions
                 WHERE game_type IN ('TRPG','MADMIS','マダミス')
                   AND scenario_name<>''
                 UNION
                 SELECT game_type,scenario_name
                 FROM scenario_progress
               )
               GROUP BY game_type,scenario_name
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
               LEFT JOIN users u ON u.discord_id=sp.discord_id
               WHERE sp.game_type=? AND sp.scenario_name=?
               ORDER BY display_name""",
            (gt, name),
        ).fetchall()

    return int(played), rows


def calendar_stats(period_start: str | None = None, period_end: str | None = None):
    """指定期間（未指定なら累計）の卓数・種類別件数・GM/PL Top3等。"""
    with db() as c:
        if period_start and period_end:
            where = "WHERE cs.event_date>=? AND cs.event_date<?"
            params = [period_start, period_end]
        else:
            where = ""
            params = []

        total = int(c.execute(
            f"SELECT COUNT(*) AS n FROM calendar_sessions cs {where}",
            params,
        ).fetchone()["n"])

        type_rows = c.execute(
            f"""SELECT
                    CASE WHEN game_type='マダミス' THEN 'MADMIS' ELSE game_type END AS gt,
                    COUNT(*) AS n
                FROM calendar_sessions cs
                {where}
                GROUP BY gt""",
            params,
        ).fetchall()
        by_type = {str(x["gt"]): int(x["n"]) for x in type_rows}

        gm_top = c.execute(
            f"""SELECT cs.gm_discord_id AS discord_id,
                       COALESCE(u.display_name,u.username,cs.gm_discord_id) AS display_name,
                       COUNT(*) AS n
                FROM calendar_sessions cs
                LEFT JOIN users u ON u.discord_id=cs.gm_discord_id
                {'WHERE' if not where else where + ' AND'} cs.game_type<>'EVENT'
                GROUP BY cs.gm_discord_id
                ORDER BY n DESC, display_name
                LIMIT 3""",
            params,
        ).fetchall()

        pl_where = ""
        pl_params = []
        if period_start and period_end:
            pl_where = "WHERE cs.event_date>=? AND cs.event_date<?"
            pl_params = [period_start, period_end]

        pl_top = c.execute(
            f"""SELECT csm.discord_id AS discord_id,
                       COALESCE(u.display_name,u.username,csm.discord_id) AS display_name,
                       COUNT(*) AS n
                FROM calendar_session_members csm
                JOIN calendar_sessions cs ON cs.id=csm.calendar_session_id
                LEFT JOIN users u ON u.discord_id=csm.discord_id
                {'WHERE' if not pl_where else pl_where + ' AND'} cs.game_type<>'EVENT'
                GROUP BY csm.discord_id
                ORDER BY n DESC, display_name
                LIMIT 3""",
            pl_params,
        ).fetchall()

        scenario_where = ""
        scenario_params = []
        if period_start and period_end:
            scenario_where = (
                "WHERE cs.event_date>=? AND cs.event_date<? "
                "AND cs.game_type IN ('TRPG','MADMIS','マダミス') "
                "AND cs.scenario_name<>''"
            )
            scenario_params = [period_start, period_end]
        else:
            scenario_where = (
                "WHERE cs.game_type IN ('TRPG','MADMIS','マダミス') "
                "AND cs.scenario_name<>''"
            )

        scenario_count = int(c.execute(
            f"""SELECT COUNT(*) AS n FROM (
                    SELECT
                      CASE WHEN cs.game_type='マダミス' THEN 'MADMIS' ELSE cs.game_type END AS gt,
                      cs.scenario_name
                    FROM calendar_sessions cs
                    {scenario_where}
                    GROUP BY gt, cs.scenario_name
                )""",
            scenario_params,
        ).fetchone()["n"])

    return {
        "total": total,
        "trpg": int(by_type.get("TRPG", 0)),
        "madamis": int(by_type.get("MADMIS", 0)),
        "event": int(by_type.get("EVENT", 0)),
        "scenario_count": scenario_count,
        "gm_top": gm_top,
        "pl_top": pl_top,
    }


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
    backfill_calendar_history()
    backfill_scenario_progress()


# main.py から import された時点で従来どおり初期化する。
initialize_database()
