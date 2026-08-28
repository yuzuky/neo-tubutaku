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
    backfill_calendar_history()


# main.py から import された時点で従来どおり初期化する。
initialize_database()
