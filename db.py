import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    count = conn.execute("SELECT COUNT(*) FROM category").fetchone()[0]
    if count == 0:
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
    return dict(row) if row else {}


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


def _compute_duration(start, end) -> Optional[int]:
    if not start or not end:
        return None
    try:
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        diff = end - start
        return max(0, int(diff.total_seconds() // 86400))
    except Exception:
        return None


def get_auctions(limit: int = 50) -> List[dict]:
    conn = get_connection()
    sql = """
        SELECT a.a_id,
               a.a_item_id,
               a.a_c_price,
               a.a_s_price,
               a.a_status,
               a.a_s_date,
               a.a_e_date,
               i.i_title,
               i.i_desc,
               i.i_image,
               i.i_m_id
        FROM auction a
        JOIN item i ON i.i_id = a.a_item_id
        ORDER BY a.a_s_date DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    conn.close()
    results = []
    for row in rows:
        data = _row_to_dict(row)
        price = data.get("a_c_price") or data.get("a_s_price")
        image = data.get("i_image") or url_for_static_placeholder()
        results.append({
            "id": data.get("a_id"),
            "item_id": data.get("a_item_id"),
            "title": data.get("i_title"),
            "description": data.get("i_desc"),
            "image_url": image,
            "current_bid": _format_money(price),
            "seller_id": data.get("i_m_id"),
            "start_date": data.get("a_s_date"),
            "end_time": data.get("a_e_date"),
            "duration": _compute_duration(data.get("a_s_date"), data.get("a_e_date")),
            "url": f"/auction/{data.get('a_id')}",
            "status": data.get("a_status", "open"),
        })
    return results


def get_auction(auction_id: int) -> Optional[dict]:
    conn = get_connection()
    sql = """
        SELECT a.*, i.i_title, i.i_desc, i.i_image, i.i_m_id
        FROM auction a
        JOIN item i ON i.i_id = a.a_item_id
        WHERE a.a_id = ?
    """
    row = conn.execute(sql, (auction_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = _row_to_dict(row)
    price = data.get("a_c_price") or data.get("a_s_price")
    image = data.get("i_image") or url_for_static_placeholder()
    return {
        "id": data.get("a_id"),
        "item_id": data.get("a_item_id"),
        "title": data.get("i_title"),
        "description": data.get("i_desc"),
        "image_url": image,
        "current_bid": _format_money(price),
        "seller_id": data.get("i_m_id"),
        "start_date": data.get("a_s_date"),
        "end_time": data.get("a_e_date"),
        "duration": _compute_duration(data.get("a_s_date"), data.get("a_e_date")),
        "url": f"/auction/{data.get('a_id')}",
        "status": data.get("a_status", "open"),
    }


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM member WHERE m_login_id = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    data = _row_to_dict(row)
    return {
        "id": data.get("m_id"),
        "username": data.get("m_login_id"),
        "m_login_id": data.get("m_login_id"),
        "password": data.get("m_pass"),
        "email": data.get("m_email"),
        "is_admin": bool(data.get("m_is_admin")),
        "m_is_admin": bool(data.get("m_is_admin")),
        "m_role": data.get("m_role") or ("admin" if data.get("m_is_admin") else "user"),
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


def create_member(login_id: str, plain_password: str,
                  email: Optional[str] = None,
                  role: Optional[str] = None) -> int:
    conn = get_connection()
    exists = conn.execute("SELECT 1 FROM member WHERE m_login_id = ?", (login_id,)).fetchone()
    if exists:
        conn.close()
        raise ValueError("login_id already exists")
    hashed = generate_password_hash(plain_password, method='pbkdf2:sha256', salt_length=16)
    cur = conn.execute(
        "INSERT INTO member(m_login_id, m_pass, m_email, m_role) VALUES (?, ?, ?, ?)",
        (login_id, hashed, email, role)
    )
    conn.commit()
    member_id = cur.lastrowid
    conn.close()
    return member_id


def confirm_member(m_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("UPDATE member SET m_status = 'A' WHERE m_id = ?", (m_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_member_by_id(m_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM member WHERE m_id = ?", (m_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_all_members() -> List[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT m_id, m_login_id, m_email, m_status, m_is_admin, m_role FROM member ORDER BY m_id").fetchall()
    conn.close()
    return [
        {
            "id": row["m_id"],
            "username": row["m_login_id"],
            "email": row["m_email"],
            "status": row["m_status"],
            "m_is_admin": bool(row["m_is_admin"]),
            "is_admin": bool(row["m_is_admin"]),
            "m_role": row["m_role"] or ("admin" if row["m_is_admin"] else "user"),
        }
        for row in rows
    ]


def set_member_admin(m_id: int, is_admin: bool = True) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE member SET m_is_admin = ?, m_role = COALESCE(m_role, ?) WHERE m_id = ?",
        (1 if is_admin else 0, 'admin' if is_admin else 'user', m_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_auction_and_bids(auction_id: int) -> tuple[int, int]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM bid WHERE b_a_id = ?", (auction_id,))
        deleted_bids = cur.rowcount or 0
        cur.execute("DELETE FROM auction WHERE a_id = ?", (auction_id,))
        deleted_auctions = cur.rowcount or 0
        conn.commit()
        return deleted_auctions, deleted_bids
    finally:
        conn.close()


def place_bid(auction_id: int, bidder_m_id: int, amount) -> bool:
    try:
        bid_amount = float(amount)
    except Exception:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT a_status, a_e_date, COALESCE(a_c_price, a_s_price) AS current_price FROM auction WHERE a_id = ?",
            (auction_id,)
        ).fetchone()
        if not row:
            return False
        if row["a_status"] and row["a_status"].lower() in ("closed", "cancelled"):
            return False
        end_date = row["a_e_date"]
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date) if isinstance(end_date, str) else end_date
                if end_dt <= datetime.utcnow():
                    return False
            except Exception:
                pass
        current_price = float(row["current_price"] or 0)
        if bid_amount <= current_price:
            return False
        conn.execute("INSERT INTO bid(b_a_id, b_m_id, b_amount) VALUES (?, ?, ?)", (auction_id, bidder_m_id, bid_amount))
        conn.execute(
            "UPDATE auction SET a_c_price = ?, updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
            (bid_amount, auction_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def create_item(title: str, description: Optional[str] = None, owner_id: Optional[int] = None,
                starting_price: float = 0.0, duration: int = 7, status: str = 'A',
                image_path: Optional[str] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO item(i_m_id, i_title, i_desc, i_b_price, i_duration, i_status, i_image) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, title, description, starting_price, duration, status, image_path)
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return item_id


def create_auction(item_id: int, seller_id: Optional[int] = None, starting_price: float = 0.0,
                   start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO auction(a_item_id, a_m_id, a_s_price, a_c_price, a_s_date, a_e_date) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, seller_id, starting_price, starting_price, start_date or datetime.utcnow(), end_date)
    )
    conn.commit()
    auction_id = cur.lastrowid
    conn.close()
    return auction_id


def create_item_and_auction(title: str, description: Optional[str], seller_id: Optional[int] = None,
                             starting_price: float = 0.0, end_date: Optional[datetime] = None,
                             duration: int = 7, status: str = 'P') -> Tuple[int, int]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO item(i_m_id, i_title, i_desc, i_b_price, i_duration, i_status) VALUES (?, ?, ?, ?, ?, ?)",
            (seller_id, title, description, starting_price, duration, status)
        )
        item_id = cur.lastrowid
        cur.execute(
            "INSERT INTO auction(a_item_id, a_m_id, a_s_price, a_c_price, a_s_date, a_e_date) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, seller_id, starting_price, starting_price, datetime.utcnow(), end_date)
        )
        auction_id = cur.lastrowid
        conn.commit()
        return auction_id, item_id
    finally:
        conn.close()


def get_categories() -> List[tuple]:
    conn = get_connection()
    rows = conn.execute("SELECT cat_id, name FROM category ORDER BY name").fetchall()
    conn.close()
    return [(str(row["cat_id"]), row["name"]) for row in rows]


def set_item_image(item_id: int, image_path: str) -> bool:
    conn = get_connection()
    cur = conn.execute("UPDATE item SET i_image = ? WHERE i_id = ?", (image_path, item_id))
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
        "SELECT img_id, image_url, thumb_url, sort_order FROM item_image WHERE item_id = ? ORDER BY sort_order, img_id",
        (item_id,)
    ).fetchall()
    conn.close()
    uploads_dir = BASE_DIR / "static" / "uploads"
    results = []
    for row in rows:
        variants = {}
        image_url = row["image_url"]
        thumb_url = row["thumb_url"]
        try:
            if image_url and image_url.startswith("/static/uploads/"):
                fname = os.path.basename(image_url)
                stem, ext = os.path.splitext(fname)
                webp = uploads_dir / f"{stem}.webp"
                if webp.exists():
                    variants["webp"] = f"/static/uploads/{stem}.webp"
                for size in ("small", "medium", "large"):
                    candidate = uploads_dir / f"{stem}_thumb_{size}{ext}"
                    if candidate.exists():
                        variants[f"thumb_{size}"] = f"/static/uploads/{stem}_thumb_{size}{ext}"
        except Exception:
            variants = {}
        results.append({
            "img_id": row["img_id"],
            "image_url": image_url,
            "thumb_url": thumb_url,
            "sort_order": row["sort_order"],
            "variants": variants,
        })
    return results


def delete_item_image(img_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT image_url, thumb_url FROM item_image WHERE img_id = ?", (img_id,)).fetchone()
    if not row:
        conn.close()
        return False
    _delete_image_files([row["image_url"], row["thumb_url"]])
    cur = conn.execute("DELETE FROM item_image WHERE img_id = ?", (img_id,))
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
            conn.execute("UPDATE item_image SET sort_order = ? WHERE img_id = ? AND item_id = ?",
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
            cur.execute("UPDATE auction SET a_status = 'closed', a_e_date = COALESCE(a_e_date, ?), updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (now, a_id))
        elif action == "reopen":
            cur.execute("UPDATE auction SET a_status = 'open', a_e_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (a_id,))
        elif action == "set_end_date":
            end_date = params.get("end_date")
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            cur.execute("UPDATE auction SET a_e_date = ?, updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (end_date, a_id))
        elif action == "extend_days":
            days = int(params.get("days", 0))
            row = cur.execute("SELECT a_e_date FROM auction WHERE a_id = ?", (a_id,)).fetchone()
            if not row:
                return False
            end_date = row["a_e_date"]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            base = end_date or now
            new_end = base + timedelta(days=days)
            cur.execute("UPDATE auction SET a_e_date = ?, updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (new_end, a_id))
        elif action == "cancel":
            cur.execute("UPDATE auction SET a_status = 'cancelled', a_e_date = COALESCE(a_e_date, ?), updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (now, a_id))
        elif action == "set_status":
            status = params.get("status") or "open"
            cur.execute("UPDATE auction SET a_status = ?, updated_at = CURRENT_TIMESTAMP WHERE a_id = ?",
                        (status, a_id))
        conn.commit()
        return cur.rowcount and cur.rowcount > 0
    finally:
        conn.close()


def close_expired_auctions(now: Optional[datetime] = None) -> int:
    ref = now or datetime.utcnow()
    ref_text = ref.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE auction
            SET a_status = 'closed', updated_at = CURRENT_TIMESTAMP
            WHERE a_e_date IS NOT NULL
              AND a_e_date <= ?
              AND (a_status IS NULL OR LOWER(a_status) = 'open')
            """,
            (ref_text,)
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def bootstrap_sqlite_db(reset: bool = False) -> Path:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection()
    conn.close()
    return DB_PATH
