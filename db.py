import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, List, Optional, Sequence

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SQLITE_PATH", BASE_DIR / "iom.db"))
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "HK$")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS member (
    m_id INTEGER PRIMARY KEY AUTOINCREMENT,
    m_login_id TEXT NOT NULL UNIQUE,
    m_pass TEXT NOT NULL,
    m_email TEXT,
    m_status TEXT NOT NULL DEFAULT 'P',
    m_is_admin INTEGER NOT NULL DEFAULT 0,
    m_role TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS item (
    i_id INTEGER PRIMARY KEY AUTOINCREMENT,
    i_m_id INTEGER,
    i_title TEXT NOT NULL,
    i_desc TEXT,
    i_b_price REAL NOT NULL DEFAULT 0,
    i_duration INTEGER NOT NULL DEFAULT 7,
    i_cat TEXT,
    i_s_cat TEXT,
    i_s_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    i_status TEXT NOT NULL DEFAULT 'A',
    i_image TEXT,
    FOREIGN KEY(i_m_id) REFERENCES member(m_id)
);

CREATE TABLE IF NOT EXISTS auction (
    a_id INTEGER PRIMARY KEY AUTOINCREMENT,
    a_item_id INTEGER NOT NULL,
    a_m_id INTEGER,
    a_s_price REAL NOT NULL DEFAULT 0,
    a_c_price REAL NOT NULL DEFAULT 0,
    a_s_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    a_e_date TIMESTAMP,
    a_status TEXT NOT NULL DEFAULT 'open',
    FOREIGN KEY(a_item_id) REFERENCES item(i_id) ON DELETE CASCADE,
    FOREIGN KEY(a_m_id) REFERENCES member(m_id)
);

CREATE TABLE IF NOT EXISTS bid (
    b_id INTEGER PRIMARY KEY AUTOINCREMENT,
    b_a_id INTEGER NOT NULL,
    b_m_id INTEGER NOT NULL,
    b_amount REAL NOT NULL,
    b_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(b_a_id) REFERENCES auction(a_id) ON DELETE CASCADE,
    FOREIGN KEY(b_m_id) REFERENCES member(m_id)
);

CREATE TABLE IF NOT EXISTS item_image (
    img_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    thumb_url TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(item_id) REFERENCES item(i_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS category (
    cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
"""

_DEFAULT_CATEGORIES = [
    "Antiques",
    "Books",
    "Collectibles",
    "Electronics",
    "Fashion",
    "Home & Living",
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "category" in existing:
        rows = conn.execute("SELECT COUNT(*) FROM category").fetchone()
        if rows and rows[0] == 0:
            conn.executemany("INSERT INTO category(name) VALUES (?)", [(c,) for c in _DEFAULT_CATEGORIES])
            conn.commit()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if isinstance(row, sqlite3.Row) else dict(row or {})


def _format_money(val) -> Optional[str]:
    if val is None:
        return None
    try:
        amount = float(val)
    except Exception:
        return str(val)
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def url_for_static_placeholder() -> str:
    return "/static/placeholder.png"


def get_auctions(limit: int = 50) -> List[dict]:
    conn = get_connection()
    sql = """
        SELECT a.id AS auction_id,
               a.item_id,
               COALESCE(a.current_price, a.starting_price) AS current_price,
               a.status,
               a.start_date,
               a.end_date,
               i.title,
               i.description,
               i.image_url,
               i.owner_id AS seller_id
        FROM auction a
        JOIN item i ON i.id = a.item_id
        ORDER BY a.start_date DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    out = []
    for row in rows:
        data = _row_to_dict(row)
        image = data.get("image_url") or url_for_static_placeholder()
        out.append({
            "id": data.get("auction_id"),
            "item_id": data.get("item_id"),
            "title": data.get("title"),
            "description": data.get("description"),
            "image_url": image,
            "current_bid": _format_money(data.get("current_price")),
            "seller_id": data.get("seller_id"),
            "start_date": data.get("start_date"),
            "end_time": data.get("end_date"),
            "duration": _compute_duration(data.get("start_date"), data.get("end_date")),
            "url": f"/auction/{data.get('auction_id')}",
            "status": data.get("status", "open"),
        })
    conn.close()
    return out


def _compute_duration(start_date, end_date) -> Optional[int]:
    try:
        if start_date and end_date:
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date)
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            delta = end_date - start_date
            return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return None
    return None


def get_auction(auction_id: int) -> Optional[dict]:
    conn = get_connection()
    sql = """
        SELECT a.*, i.title, i.description, i.image_url, i.owner_id AS seller_id
        FROM auction a
        JOIN item i ON i.id = a.item_id
        WHERE a.id = ?
    """
    row = conn.execute(sql, (auction_id,)).fetchone()
    if not row:
        conn.close()
        return None
    data = _row_to_dict(row)
    image = data.get("image_url") or url_for_static_placeholder()
    result = {
        "id": data.get("id"),
        "item_id": data.get("item_id"),
        "title": data.get("title"),
        "description": data.get("description"),
        "image_url": image,
        "current_bid": _format_money(data.get("current_price") or data.get("starting_price")),
        "seller_id": data.get("seller_id"),
        "start_date": data.get("start_date"),
        "end_time": data.get("end_date"),
        "duration": _compute_duration(data.get("start_date"), data.get("end_date")),
        "url": f"/auction/{data.get('id')}",
        "status": data.get("status", "open"),
    }
    conn.close()
    return result


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM member WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    data = _row_to_dict(row)
    return {
        "id": data.get("id"),
        "username": data.get("username"),
        "password": data.get("password"),
        "email": data.get("email"),
        "first_name": data.get("first_name"),
        "last_name": data.get("last_name"),
        "is_admin": bool(data.get("is_admin")),
        "m_is_admin": bool(data.get("is_admin")),
        "m_role": "admin" if data.get("is_admin") else "user",
    }


def verify_password(stored_password, provided_password) -> bool:
    if stored_password is None:
        return False
    try:
        if check_password_hash(stored_password, provided_password):
            return True
    except Exception:
        pass
    return str(stored_password) == str(provided_password)


def create_member(login_id: str, plain_password: str, first_name: Optional[str] = None,
                  last_name: Optional[str] = None, email: Optional[str] = None) -> int:
    conn = get_connection()
    existing = conn.execute("SELECT 1 FROM member WHERE username = ?", (login_id,)).fetchone()
    if existing:
        conn.close()
        raise ValueError("login_id already exists")
    hashed = generate_password_hash(plain_password)
    cur = conn.execute(
        "INSERT INTO member(username, password, first_name, last_name, email) VALUES (?, ?, ?, ?, ?)",
        (login_id, hashed, first_name, last_name, email)
    )
    conn.commit()
    member_id = cur.lastrowid
    conn.close()
    return member_id


def confirm_member(m_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("UPDATE member SET status = 'A' WHERE id = ?", (m_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_member_by_id(m_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM member WHERE id = ?", (m_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = _row_to_dict(row)
    data["username"] = data.get("username")
    data["password"] = data.get("password")
    return data


def get_all_members() -> List[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT id, username, email, status, is_admin FROM member ORDER BY id ASC").fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "status": row["status"],
            "m_is_admin": bool(row["is_admin"]),
            "is_admin": bool(row["is_admin"]),
            "m_role": "admin" if row["is_admin"] else "user",
        }
        for row in rows
    ]


def set_member_admin(m_id: int, is_admin: bool = True) -> bool:
    conn = get_connection()
    cur = conn.execute("UPDATE member SET is_admin = ?, status = COALESCE(status, 'A') WHERE id = ?",
                       (1 if is_admin else 0, m_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_auction_and_bids(auction_id: int) -> tuple[int, int]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM bid WHERE auction_id = ?", (auction_id,))
        deleted_bids = cur.rowcount or 0
        cur.execute("DELETE FROM auction WHERE id = ?", (auction_id,))
        deleted_auctions = cur.rowcount or 0
        conn.commit()
    finally:
        conn.close()
    return deleted_auctions, deleted_bids


def place_bid(auction_id: int, bidder_m_id: int, amount) -> bool:
    try:
        bid_val = float(amount)
    except Exception:
        return False
    conn = get_connection()
    try:
        auction = conn.execute("SELECT status, end_date, COALESCE(current_price, starting_price) AS current_price FROM auction WHERE id = ?",
                               (auction_id,)).fetchone()
        if not auction:
            return False
        status = auction["status"]
        end_date = auction["end_date"]
        if status and status.lower() in ("closed", "cancelled"):
            return False
        if end_date:
            try:
                if isinstance(end_date, str):
                    end_dt = datetime.fromisoformat(end_date)
                else:
                    end_dt = end_date
                if end_dt <= datetime.utcnow():
                    return False
            except Exception:
                pass
        current = float(auction["current_price"] or 0)
        if bid_val <= max(current, 0):
            return False
        conn.execute("INSERT INTO bid(auction_id, bidder_id, amount) VALUES (?, ?, ?)",
                     (auction_id, bidder_m_id, bid_val))
        conn.execute(
            "UPDATE auction SET current_price = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (bid_val, auction_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def create_item(title: str, description: Optional[str] = None, owner_id: Optional[int] = None,
                category: Optional[str] = None, sub_category: Optional[str] = None,
                image_path: Optional[str] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO item(title, description, owner_id, category, sub_category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
        (title, description, owner_id, category, sub_category, image_path)
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return item_id


def create_auction(item_id: int, seller_id: Optional[int] = None, starting_price: float = 0.0,
                   start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO auction(item_id, seller_id, starting_price, current_price, start_date, end_date) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, seller_id, starting_price, starting_price, start_date or datetime.utcnow(), end_date)
    )
    conn.commit()
    auction_id = cur.lastrowid
    conn.close()
    return auction_id


def create_item_and_auction(title: str, description: Optional[str], seller_id: Optional[int] = None,
                             starting_price: float = 0.0, end_date: Optional[datetime] = None,
                             category: Optional[str] = None, sub_category: Optional[str] = None,
                             image_path: Optional[str] = None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO item(title, description, owner_id, category, sub_category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
            (title, description, seller_id, category, sub_category, image_path)
        )
        item_id = cur.lastrowid
        cur.execute(
            "INSERT INTO auction(item_id, seller_id, starting_price, current_price, start_date, end_date) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, seller_id, starting_price, starting_price, datetime.utcnow(), end_date)
        )
        auction_id = cur.lastrowid
        conn.commit()
        return auction_id, item_id
    finally:
        conn.close()


def get_categories() -> List[tuple]:
    conn = get_connection()
    rows = conn.execute("SELECT id, name FROM category ORDER BY name ASC").fetchall()
    conn.close()
    return [(str(row["id"]), row["name"]) for row in rows]


def set_item_image(item_id: int, image_path: str) -> bool:
    conn = get_connection()
    cur = conn.execute("UPDATE item SET image_url = ? WHERE id = ?", (image_path, item_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def add_item_image(item_id: int, image_url: str, thumb_url: Optional[str] = None, sort_order: int = 0) -> Optional[int]:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO item_image(item_id, image_url, thumb_url, sort_order) VALUES (?, ?, ?, ?)",
        (item_id, image_url, thumb_url, sort_order)
    )
    conn.commit()
    img_id = cur.lastrowid
    conn.close()
    return img_id


def get_item_images(item_id: int) -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, image_url, thumb_url, sort_order FROM item_image WHERE item_id = ? ORDER BY sort_order ASC, id ASC",
        (item_id,)
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        image_url = row["image_url"]
        thumb_url = row["thumb_url"]
        variants = {}
        try:
            if image_url and image_url.startswith("/static/uploads/"):
                uploads_dir = BASE_DIR / "static" / "uploads"
                fname = os.path.basename(image_url)
                name_noext, ext = os.path.splitext(fname)
                webp = uploads_dir / f"{name_noext}.webp"
                if webp.exists():
                    variants["webp"] = f"/static/uploads/{name_noext}.webp"
                for size in ("small", "medium", "large"):
                    thumb_name = uploads_dir / f"{name_noext}_thumb_{size}{ext}"
                    if thumb_name.exists():
                        variants[f"thumb_{size}"] = f"/static/uploads/{name_noext}_thumb_{size}{ext}"
        except Exception:
            variants = {}
        out.append({
            "img_id": row["id"],
            "image_url": image_url,
            "thumb_url": thumb_url,
            "sort_order": row["sort_order"],
            "variants": variants,
        })
    return out


def delete_item_image(img_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT image_url, thumb_url FROM item_image WHERE id = ?", (img_id,)).fetchone()
    if not row:
        conn.close()
        return False
    image_url = row["image_url"]
    thumb_url = row["thumb_url"]
    _delete_image_files([image_url, thumb_url])
    cur = conn.execute("DELETE FROM item_image WHERE id = ?", (img_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def _delete_image_files(paths: Sequence[Optional[str]]) -> None:
    uploads_dir = BASE_DIR / "static" / "uploads"
    for p in paths:
        if not p or not isinstance(p, str):
            continue
        if not p.startswith("/static/uploads/"):
            continue
        file_path = uploads_dir / os.path.basename(p)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass


def reorder_item_images(item_id: int, ordered_img_ids: Iterable[int]) -> bool:
    conn = get_connection()
    try:
        for idx, img_id in enumerate(ordered_img_ids, start=1):
            conn.execute("UPDATE item_image SET sort_order = ? WHERE id = ? AND item_id = ?",
                         (idx, img_id, item_id))
        conn.commit()
        return True
    finally:
        conn.close()


def update_auction_housekeeping(a_id: int, action: str, params: Optional[dict] = None) -> bool:
    params = params or {}
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow()
    try:
        if action == "close":
            cur.execute("UPDATE auction SET status = 'closed', end_date = COALESCE(end_date, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (now, a_id))
        elif action == "reopen":
            cur.execute("UPDATE auction SET status = 'open', end_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (a_id,))
        elif action == "set_end_date":
            end_date = params.get("end_date")
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            cur.execute("UPDATE auction SET end_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (end_date, a_id))
        elif action == "extend_days":
            days = int(params.get("days", 0))
            row = cur.execute("SELECT end_date FROM auction WHERE id = ?", (a_id,)).fetchone()
            if not row:
                conn.close()
                return False
            end_date = row["end_date"]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            base = end_date or now
            new_end = base + timedelta(days=days)
            cur.execute("UPDATE auction SET end_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_end, a_id))
        elif action == "cancel":
            cur.execute("UPDATE auction SET status = 'cancelled', end_date = COALESCE(end_date, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (now, a_id))
        elif action == "set_status":
            status = params.get("status") or "open"
            cur.execute("UPDATE auction SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (status, a_id))
        conn.commit()
        return cur.rowcount and cur.rowcount > 0
    finally:
        conn.close()


def bootstrap_sqlite_db(reset: bool = False) -> Path:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection()
    conn.close()
    return DB_PATH
