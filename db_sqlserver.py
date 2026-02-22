import os
import logging
from decimal import Decimal
from datetime import datetime, timedelta
import math

try:
    import pyodbc
except Exception:
    pyodbc = None
from werkzeug.security import check_password_hash, generate_password_hash


def get_connection():
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed; install with `pip install pyodbc`')
    dsn = os.getenv('ODBC_DSN')
    conn_env = os.getenv('ODBC_CONN')
    user = os.getenv('DB_USER')
    pwd = os.getenv('DB_PASS')
    # If a credentials file is present in the project, prefer it for direct SQL Server connections.
    # This file is optional; environment variables still override when set.
    try:
        import credential as cred  # local credentials module (optional)
    except Exception:
        cred = None

    if dsn:
        parts = [f"DSN={dsn}"]
        if user:
            parts.append(f"UID={user}")
        if pwd:
            parts.append(f"PWD={pwd}")
        return pyodbc.connect(';'.join(parts))

    # If credential.py is present, construct a SQL Server connection string and **always prefer it**.
    if cred:
        try:
            server = getattr(cred, 'server', None)
            database = getattr(cred, 'database', None)
            username = getattr(cred, 'username', None)
            password = getattr(cred, 'password', None)
            # Build a driver-based connection string; allow override via DB_DRIVER env var.
            driver = os.getenv('DB_DRIVER') or '{ODBC Driver 17 for SQL Server}'
            if server and database and username is not None and password is not None:
                conn_str = (
                    f"DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password};"
                    "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
                )
                return pyodbc.connect(conn_str)
        except Exception:
            # If credential.py exists but connection fails, we do NOT fall back to ODBC_CONN by design.
            # Raise to surface the real issue to the caller.
            raise

    # Only use a raw ODBC_CONN if no credential.py was present.
    if conn_env:
        return pyodbc.connect(conn_env)
    raise RuntimeError('Set ODBC_DSN or ODBC_CONN (and optional DB_USER/DB_PASS)')


def _format_money(val):
    if val is None:
        return None
    # Use env var to allow centralized currency symbol configuration
    symbol = os.getenv('CURRENCY_SYMBOL', 'HK$')
    if isinstance(val, Decimal):
        return f"{symbol}{val:.2f}"
    try:
        return f"{symbol}{float(val):.2f}"
    except Exception:
        return str(val)


def _row_to_dict(cursor, row):
    """Map a pyodbc row + cursor.description to a dict of column->value."""
    cols = [col[0] for col in cursor.description]
    return dict(zip(cols, row))


def _pick_first(candidate_keys, data):
    for k in candidate_keys:
        for key in data.keys():
            if key.lower() == k.lower():
                return data[key]
    # substring fallback
    for k in candidate_keys:
        for key in data.keys():
            if k.lower() in key.lower():
                return data[key]
    return None



def get_auctions(limit=50):
    """
    Return a list of normalized auction dicts suitable for templates.
    Uses dbo.auction and tries to join dbo.item if available.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Try first join shape (common): i.item_id = a.a_item_id
    queries = [
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.item_id = a.a_item_id ORDER BY a.a_s_date DESC", ()),
        # fallback: try common item id column names
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.i_id = a.a_item_id ORDER BY a.a_s_date DESC", ()),
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.id = a.a_item_id ORDER BY a.a_s_date DESC", ()),
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.i_id = a.item_id ORDER BY a.a_s_date DESC", ()),
        # fallback: no join
        ("SELECT a.* FROM dbo.auction a ORDER BY a.a_s_date DESC", ()),
    ]

    rows = None
    used_query = None
    for q, params in queries:
        try:
            if limit and 'ORDER BY' in q:
                # Use simple fetchall and slice to avoid SQL dialect dependencies
                cur.execute(q)
                fetched = cur.fetchall()
                rows = (q, fetched)
            else:
                cur.execute(q)
                rows = (q, cur.fetchall())
            used_query = q
            break
        except Exception:
            # try next
            continue

    if rows is None:
        conn.close()
        return []

    q_text, fetched = rows
    out = []
    now = datetime.utcnow()
    # Re-execute cursor for each fetch to have description accessible
    # We'll iterate using the original query again to get cursor.description for each row batch
    cur.execute(q_text)
    all_rows = cur.fetchall()
    for row in all_rows[:limit]:
        data = _row_to_dict(cur, row)

        # Best guesses for item fields
        title = _pick_first(['title', 'name', 'item_title'], data) or f"Item {data.get('a_item_id') or data.get('item_id') or data.get('a_id')}"
        description = _pick_first(['description', 'desc', 'details'], data) or ''
        image = _pick_first(['image_url', 'image', 'img', 'picture', 'photo', 'imagepath'], data) or url_for_static_placeholder()
        # Try to pick a thumbnail from item_image if present
        try:
            item_id_val = data.get('a_item_id') or data.get('item_id') or data.get('a_id')
            if item_id_val is not None:
                imgs = get_item_images(item_id_val)
                if imgs:
                    # prefer thumb_url then image_url
                    first = imgs[0]
                    image = first.get('thumb_url') or first.get('image_url') or image
        except Exception:
            pass

        current_bid = _pick_first(['a_s_price', 'current_bid', 'price', 'starting_price'], data)
        # Determine the current highest bid for this auction when possible.
        # Prefer the bid table's MAX(b_amount) when an auction id is available.
        highest_bid_val = None
        try:
            aid = data.get('a_id') or data.get('a_item_id')
            if aid is not None:
                try:
                    # Use a fresh cursor to avoid disturbing the outer cursor state
                    bid_cur = conn.cursor()
                    bid_cur.execute("SELECT MAX(b_amount) AS maxb FROM dbo.bid WHERE b_a_id = ?", (aid,))
                    br = bid_cur.fetchone()
                    if br:
                        try:
                            highest_bid_val = float(getattr(br, 'maxb') or 0)
                        except Exception:
                            try:
                                highest_bid_val = float(br[0] or 0)
                            except Exception:
                                highest_bid_val = None
                except Exception:
                    highest_bid_val = None
        except Exception:
            highest_bid_val = None

        if highest_bid_val is not None and highest_bid_val > 0:
            current_bid = _format_money(highest_bid_val)
        else:
            current_bid = _format_money(current_bid)

        seller = data.get('a_m_id') or data.get('seller_id') or None

        start_date = data.get('a_s_date') or data.get('start_date')

        # Attempt to discover an end-time or a duration value from the row using
        # common candidate column names. Compute a friendly duration in days
        # when both start and end datetimes are available.
        end_time = _pick_first(['a_e_date', 'end_date', 'a_end', 'a_e'], data)
        # duration may be stored explicitly in some schemas
        duration_raw = _pick_first(['duration', 'a_duration', 'i_duration', 'length', 'days'], data)
        duration = None

        # If both datetimes are present prefer computing duration from them (authoritative)
        try:
            if end_time is not None and start_date is not None:
                if isinstance(end_time, datetime) and isinstance(start_date, datetime):
                    delta = end_time - start_date
                    # Round up partial days so a remaining 1 hour counts as 1 day
                    duration = max(0, int(math.ceil(delta.total_seconds() / 86400)))
        except Exception:
            duration = None

        # Fallback: use explicit duration value from the row when present
        try:
            if duration is None and duration_raw is not None:
                # normalize numeric-like durations to integer days
                duration = int(duration_raw)
        except Exception:
            duration = None

        # determine status: prefer explicit status column when present,
        # otherwise derive from end_time.
        raw_status = _pick_first(['a_status', 'status', 'state'], data)
        status = None
        try:
            if raw_status is not None:
                s = str(raw_status).strip()
                ls = s.lower()
                if ls in ('closed', 'c'):
                    status = 'closed'
                elif ls in ('cancelled', 'cancel'):
                    status = 'cancelled'
                elif ls in ('open', 'o'):
                    status = 'open'
                else:
                    status = s
            else:
                status = 'open'
                if end_time is not None and isinstance(end_time, datetime):
                    if end_time <= now:
                        status = 'closed'
        except Exception:
            status = 'open'

        out.append({
                'id': int(data.get('a_id')) if data.get('a_id') is not None else None,
                'item_id': int(data.get('a_item_id')) if data.get('a_item_id') is not None else None,
            'title': title,
            'description': description,
            'image_url': image,
            'current_bid': current_bid,
            'seller_id': int(seller) if seller is not None else None,
            'start_date': start_date,
                'end_time': end_time,
                'duration': duration,
                # Friendly URL for the item view route; avoid importing Flask here.
                'url': f"/auction/{int(data.get('a_id')) if data.get('a_id') is not None else (data.get('a_item_id') or '')}",
            'status': status,
        })

    conn.close()
    return out


def get_auction(auction_id):
    conn = get_connection()
    cur = conn.cursor()

    queries = [
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.item_id = a.a_item_id WHERE a.a_id = ?", (auction_id,)),
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.i_id = a.a_item_id WHERE a.a_id = ?", (auction_id,)),
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.id = a.a_item_id WHERE a.a_id = ?", (auction_id,)),
        ("SELECT a.*, i.* FROM dbo.auction a LEFT JOIN dbo.item i ON i.i_id = a.item_id WHERE a.a_id = ?", (auction_id,)),
        ("SELECT a.* FROM dbo.auction a WHERE a.a_id = ?", (auction_id,)),
    ]

    data = None
    used_q = None
    for q, params in queries:
        try:
            cur.execute(q, params)
            row = cur.fetchone()
            if row:
                data = _row_to_dict(cur, row)
                used_q = q
                break
        except Exception:
            continue

    conn.close()
    if not data:
        return None

    title = _pick_first(['title', 'name', 'item_title'], data) or f"Item {data.get('a_item_id') or data.get('item_id') or data.get('a_id')}"
    description = _pick_first(['description', 'desc', 'details'], data) or ''
    image = _pick_first(['image_url', 'image', 'img', 'picture', 'photo', 'imagepath'], data) or url_for_static_placeholder()
    # Prefer image from item_image table for detailed view
    try:
        candidate_item_id = data.get('a_item_id') or data.get('item_id') or data.get('a_id')
        if candidate_item_id is not None:
            imgs = get_item_images(candidate_item_id)
            if imgs:
                # pick full image if available
                first = imgs[0]
                image = first.get('image_url') or first.get('thumb_url') or image
    except Exception:
        pass
    # Default current bid value from the auction row (starting/current price)
    current_bid = _pick_first(['a_s_price', 'current_bid', 'price', 'starting_price'], data)
    # Try to pick up the current highest bid from the bid table
    try:
        aid = data.get('a_id')
        highest_bid_val = None
        if aid is not None:
            cur.execute("SELECT MAX(b_amount) AS maxb FROM dbo.bid WHERE b_a_id = ?", (aid,))
            r = cur.fetchone()
            if r:
                try:
                    highest_bid_val = float(getattr(r, 'maxb') or 0)
                except Exception:
                    try:
                        highest_bid_val = float(r[0] or 0)
                    except Exception:
                        highest_bid_val = None
        if highest_bid_val is not None and highest_bid_val > 0:
            current_bid = _format_money(highest_bid_val)
        else:
            current_bid = _format_money(current_bid)
    except Exception:
        # fallback to formatting whatever we found in the row
        current_bid = _format_money(current_bid)
    seller = data.get('a_m_id') or data.get('seller_id') or None

    start_date = data.get('a_s_date') or data.get('start_date')
    end_time = _pick_first(['a_e_date', 'end_date', 'a_end', 'a_e'], data)
    duration_raw = _pick_first(['duration', 'a_duration', 'i_duration', 'length', 'days'], data)
    duration = None

    # Prefer computing duration from datetimes when available
    try:
        if end_time is not None and start_date is not None:
            if isinstance(end_time, datetime) and isinstance(start_date, datetime):
                delta = end_time - start_date
                duration = max(0, int(math.ceil(delta.total_seconds() / 86400)))
    except Exception:
        duration = None

    # Fallback to explicit duration field
    try:
        if duration is None and duration_raw is not None:
            duration = int(duration_raw)
    except Exception:
        duration = None

    # determine status: prefer explicit status column when present,
    # otherwise derive from end_time.
    raw_status = _pick_first(['a_status', 'status', 'state'], data)
    status = None
    try:
        if raw_status is not None:
            s = str(raw_status).strip()
            ls = s.lower()
            if ls in ('closed', 'c'):
                status = 'closed'
            elif ls in ('cancelled', 'cancel'):
                status = 'cancelled'
            elif ls in ('open', 'o'):
                status = 'open'
            else:
                status = s
        else:
            status = 'open'
            now = datetime.utcnow()
            if end_time is not None and isinstance(end_time, datetime):
                if end_time <= now:
                    status = 'closed'
    except Exception:
        status = 'open'

    return {
        'id': int(data.get('a_id')) if data.get('a_id') is not None else None,
        'item_id': int(data.get('a_item_id')) if data.get('a_item_id') is not None else None,
        'title': title,
        'description': description,
        'image_url': image,
        'current_bid': current_bid,
        'seller_id': int(seller) if seller is not None else None,
        'start_date': start_date,
        'end_time': end_time,
        'duration': duration,
        'url': f"/auction/{int(data.get('a_id')) if data.get('a_id') is not None else (data.get('a_item_id') or '')}",
        'status': status,
    }


def delete_auction_and_bids(auction_id: int):
    """Delete bids for an auction and the auction row itself.

    Returns a tuple: (deleted_auctions_count, deleted_bids_count).
    Raises on unexpected errors.
    """
    conn = get_connection()
    cur = conn.cursor()
    deleted_bids = 0
    deleted_auctions = 0
    try:
        try:
            cur.execute('BEGIN TRANSACTION')
        except Exception:
            pass

        # Delete bids (best-effort). Support common column variants.
        for bid_where in ('b_a_id', 'auction_id', 'b_auction_id', 'a_id'):
            try:
                cur.execute(f'DELETE FROM dbo.bid WHERE {bid_where} = ?', (auction_id,))
                # Use first successful delete result
                deleted_bids = cur.rowcount
                break
            except Exception:
                deleted_bids = 0

        # Delete auction row: try several common id column names and sum results
        deleted_auctions = 0
        auction_columns = ('a_id', 'id', 'auction_id', 'aA_id')
        for col in auction_columns:
            try:
                cur.execute(f'DELETE FROM dbo.auction WHERE {col} = ?', (auction_id,))
                # Some drivers return -1 for rowcount when unknown; treat negatives as unknown
                rc = cur.rowcount if isinstance(cur.rowcount, int) and cur.rowcount >= 0 else 0
                deleted_auctions += rc
            except Exception:
                # ignore and try next
                continue

        if deleted_auctions == 0:
            try:
                conn.rollback()
            except Exception:
                pass
            return (0, deleted_bids)

        conn.commit()
        return (deleted_auctions, deleted_bids)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def url_for_static_placeholder():
    # Avoid importing Flask here; templates expect '/static/placeholder.png'
    return '/static/placeholder.png'


def get_user_by_username(username):
    """Lookup a user row by username.

    Preference order:
      1. `dbo.member` using `m_login_id` / `m_pass` (common in this schema)
      2. `dbo.users` with heuristic column names

    Returns a normalized dict with at least `id`, `username`, and `password` keys when found.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')

    conn = get_connection()
    cur = conn.cursor()

    # 1) Try dbo.member (common schema discovered)
    try:
        cur.execute("SELECT * FROM dbo.member WHERE m_login_id = ?", (username,))
        row = cur.fetchone()
        if row:
            data = _row_to_dict(cur, row)
            norm = {
                'id': data.get('m_id'),
                'username': data.get('m_login_id'),
                'password': data.get('m_pass'),
            }
            # include useful convenience fields
            norm['email'] = data.get('m_email')
            norm['first_name'] = data.get('m_f_name')
            norm['last_name'] = data.get('m_l_name')
            norm.update(data)
            conn.close()
            return norm
    except Exception:
        # ignore and fallback to users table heuristics
        pass

    # 2) Fallback: try dbo.users with common column names
    username_cols = ['username', 'user_name', 'u_name', 'member_name', 'm_name', 'name', 'email', 'user']
    try:
        for col in username_cols:
            try:
                sql = f"SELECT * FROM dbo.users WHERE {col} = ?"
                cur.execute(sql, (username,))
                row = cur.fetchone()
                if row:
                    data = _row_to_dict(cur, row)
                    norm = {}
                    # pick id
                    for idcand in ['id', 'user_id', 'u_id', 'm_id', 'member_id']:
                        if idcand in data:
                            norm['id'] = data[idcand]
                            break
                    norm['username'] = data.get(col)
                    for pc in ['password', 'passwd', 'pwd', 'pass', 'pword']:
                        if pc in data:
                            norm['password'] = data[pc]
                            break
                    norm.update(data)
                    conn.close()
                    return norm
            except Exception:
                continue
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return None


def verify_password(stored_password, provided_password):
    """Verify password against stored value.

    - If stored looks like a werkzeug hash (pbkdf2:...), `check_password_hash` will verify.
    - If stored equals the provided password (legacy plaintext), we'll accept it.
    - Returns True/False.
    """
    if stored_password is None:
        return False
    try:
        # try werkzeug check first
        if check_password_hash(stored_password, provided_password):
            return True
    except Exception:
        # not a werkzeug hash or bcrypt not installed — fallthrough
        pass

    # plain equality fallback (legacy insecure)
    try:
        if str(stored_password) == str(provided_password):
            return True
    except Exception:
        pass
    return False


def create_member(login_id, plain_password, first_name=None, last_name=None, email=None):
    """Create a member in dbo.member. Returns new m_id on success.

    Raises RuntimeError on connection issues or ValueError if username exists.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')

    conn = get_connection()
    cur = conn.cursor()

    # Check for existing login
    cur.execute("SELECT m_id FROM dbo.member WHERE m_login_id = ?", (login_id,))
    if cur.fetchone():
        conn.close()
        raise ValueError('login_id already exists')

    hashed = generate_password_hash(plain_password)

    # Insert row; rely on DB defaults for created_at and m_status
    # Try to insert with m_status='P' (pending) if the column exists; otherwise omit it.
    try:
        cur.execute(
            "INSERT INTO dbo.member (m_login_id, m_pass, m_f_name, m_l_name, m_email, m_status) VALUES (?, ?, ?, ?, ?, 'P')",
            (login_id, hashed, first_name, last_name, email)
        )
    except Exception:
        # fallback: insert without m_status
        cur.execute(
            "INSERT INTO dbo.member (m_login_id, m_pass, m_f_name, m_l_name, m_email) VALUES (?, ?, ?, ?, ?)",
            (login_id, hashed, first_name, last_name, email)
        )
    try:
        # Get the new identity value in a SQL Server-friendly way
        cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
        r = cur.fetchone()
        new_id = None
        if r:
            # Try attribute access then index access
            try:
                new_id = int(getattr(r, 'new_id'))
            except Exception:
                try:
                    new_id = int(r[0])
                except Exception:
                    new_id = None
    except Exception:
        # Fallback: try cursor.rowcount or lastrowid (may be None)
        new_id = None

    conn.commit()
    # If we couldn't determine new_id from SCOPE_IDENTITY, try to look it up by login_id as a reliable fallback.
    if not new_id:
        try:
            cur.execute("SELECT m_id FROM dbo.member WHERE m_login_id = ?", (login_id,))
            r = cur.fetchone()
            if r:
                try:
                    new_id = int(getattr(r, 'm_id'))
                except Exception:
                    try:
                        new_id = int(r[0])
                    except Exception:
                        new_id = None
        except Exception:
            # ignore and return whatever we have (possibly None)
            new_id = new_id

    conn.close()
    return new_id


def confirm_member(m_id):
    """Mark a member as active (m_status = 'A'). Returns True if updated."""
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE dbo.member SET m_status = 'A' WHERE m_id = ?", (m_id,))
        conn.commit()
        updated = cur.rowcount if hasattr(cur, 'rowcount') else None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return (updated is None) or (updated > 0)


def get_member_by_id(m_id):
    """Return a normalized member dict for a given m_id, or None if not found."""
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM dbo.member WHERE m_id = ?", (m_id,))
        row = cur.fetchone()
        if not row:
            return None
        data = _row_to_dict(cur, row)
        norm = {
            'id': data.get('m_id'),
            'username': data.get('m_login_id'),
            'password': data.get('m_pass'),
            'email': data.get('m_email')
        }
        norm.update(data)
        return norm
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_all_members():
    """Return a list of simple member dicts (id, username, email, status).

    This is a convenience helper used by administrative UIs.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    out = []
    try:
        # Discover which optional columns exist so we build a safe SELECT
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='member'")
        existing = {r[0].lower() for r in cur.fetchall()}
        cols = ['m_id', 'm_login_id', 'm_email', 'm_status']
        if 'm_role' in existing:
            cols.append('m_role')
        if 'm_is_admin' in existing:
            cols.append('m_is_admin')
        if 'is_admin' in existing and 'is_admin' not in cols:
            cols.append('is_admin')

        sql = "SELECT " + ", ".join(cols) + " FROM dbo.member"
        cur.execute(sql)
        rows = cur.fetchall()

        def _to_bool_raw(val):
            if val is None:
                return False
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                try:
                    return int(val) != 0
                except Exception:
                    return False
            if isinstance(val, bytes):
                try:
                    return int.from_bytes(val, 'little') != 0
                except Exception:
                    return bool(val)
            s = str(val).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y')

        for row in rows:
            d = _row_to_dict(cur, row)
            m_is_admin_raw = d.get('m_is_admin') if 'm_is_admin' in d else None
            is_admin_raw = d.get('is_admin') if 'is_admin' in d else None
            role_raw = d.get('m_role') if 'm_role' in d else None
            out.append({
                'id': d.get('m_id'),
                'username': d.get('m_login_id'),
                'email': d.get('m_email'),
                'status': d.get('m_status'),
                # normalized boolean for convenience
                'm_is_admin': _to_bool_raw(m_is_admin_raw),
                'is_admin': _to_bool_raw(is_admin_raw),
                # keep raw role value if present
                'm_role': role_raw
            })
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def set_member_admin(m_id, is_admin=True):
    """Attempt to grant or revoke admin privileges for a member.

    Tries multiple common schema columns and returns True if any update affected rows.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    updated = 0
    try:
        # Ensure an explicit boolean admin column exists. Many schemas lack it,
        # so detect `m_is_admin` and add it if possible. Failures here are
        # non-fatal: we'll still try other update shapes below.
        try:
            cur.execute(
                "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='member' AND COLUMN_NAME='m_is_admin'"
            )
            exists = cur.fetchone() is not None
        except Exception:
            exists = False

        if not exists:
            try:
                # Add a NOT NULL bit column with default 0 so existing rows are non-admin.
                # Add an explicit default constraint name to make it idempotent on some SQL Server setups.
                cur.execute(
                    "ALTER TABLE dbo.member ADD m_is_admin bit NOT NULL CONSTRAINT DF_member_m_is_admin DEFAULT 0"
                )
                # commit the DDL so subsequent updates can use the column
                conn.commit()
                # Recreate cursor reference after DDL (pyodbc may reuse objects)
                cur = conn.cursor()
            except Exception:
                # If we can't alter the table (permissions, locking, etc.), ignore and continue.
                pass

        # Try boolean flag columns first
        try:
            cur.execute("UPDATE dbo.member SET m_is_admin = ? WHERE m_id = ?", (1 if is_admin else 0, m_id))
            updated = cur.rowcount or updated
        except Exception:
            pass
        try:
            cur.execute("UPDATE dbo.member SET is_admin = ? WHERE m_id = ?", (1 if is_admin else 0, m_id))
            updated = cur.rowcount or updated
        except Exception:
            pass
        # Try role column (set to 'admin' or NULL/'user')
        try:
            if is_admin:
                cur.execute("UPDATE dbo.member SET m_role = ? WHERE m_id = ?", ('admin', m_id))
            else:
                cur.execute("UPDATE dbo.member SET m_role = NULL WHERE m_id = ?", (m_id,))
            updated = cur.rowcount or updated
        except Exception:
            pass
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return (updated is None) or (updated > 0)


def place_bid(auction_id, bidder_m_id, amount):
    """Place a bid on an auction.

    Returns True on success, False if bid was too low or operation failed.
    This helper tries common table/column names across schemas and is defensive.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Check auction status / end date first to prevent bids on closed auctions.
        try:
            cur.execute("SELECT * FROM dbo.auction WHERE a_id = ?", (auction_id,))
            arow = cur.fetchone()
            if arow:
                adata = _row_to_dict(cur, arow)
                # pick common end-date and status column names
                end_time = _pick_first(['a_e_date', 'end_date', 'a_end', 'a_e'], adata)
                status = _pick_first(['a_status', 'status', 'state'], adata)
                try:
                    now = datetime.utcnow()
                    if end_time is not None and isinstance(end_time, datetime) and end_time <= now:
                        # auction already ended
                        return False
                except Exception:
                    pass
                try:
                    if status is not None and str(status).lower() in ('closed', 'c', 'cancelled', 'cancel'):
                        return False
                except Exception:
                    pass
            else:
                # no such auction
                return False
        except Exception:
            # if this check fails, continue defensively to attempt normal bid flow
            pass
        # Determine current highest bid; try dbo.bid then fallback to dbo.auction starting price
        current = 0.0
        try:
            cur.execute("SELECT MAX(b_amount) AS maxb FROM dbo.bid WHERE b_a_id = ?", (auction_id,))
            row = cur.fetchone()
            if row:
                try:
                    current = float(getattr(row, 'maxb') or 0)
                except Exception:
                    current = float(row[0] or 0)
        except Exception:
            # try alternate column names
            try:
                cur.execute("SELECT MAX(amount) AS maxb FROM dbo.bid WHERE auction_id = ?", (auction_id,))
                row = cur.fetchone()
                if row:
                    try:
                        current = float(getattr(row, 'maxb') or 0)
                    except Exception:
                        current = float(row[0] or 0)
            except Exception:
                current = 0.0

        # final fallback: auction starting/current price
        if current == 0.0:
            try:
                cur.execute("SELECT a_s_price FROM dbo.auction WHERE a_id = ?", (auction_id,))
                row = cur.fetchone()
                if row:
                    try:
                        current = float(getattr(row, 'a_s_price') or 0)
                    except Exception:
                        current = float(row[0] or 0)
            except Exception:
                # give up and assume 0
                current = 0.0

        # Validate bid amount
        try:
            bid_val = float(amount)
        except Exception:
            return False
        if bid_val <= current:
            return False

        # Try inserting into a bid table; adapt to common schemas
        inserted = False
        try:
            cur.execute(
                "INSERT INTO dbo.bid (b_a_id, b_m_id, b_amount, b_time) VALUES (?, ?, ?, GETUTCDATE())",
                (auction_id, bidder_m_id, bid_val)
            )
            inserted = True
        except Exception:
            try:
                cur.execute(
                    "INSERT INTO dbo.bid (auction_id, member_id, amount, created_at) VALUES (?, ?, ?, GETUTCDATE())",
                    (auction_id, bidder_m_id, bid_val)
                )
                inserted = True
            except Exception:
                # As a last resort try to update auction current price
                try:
                    cur.execute("UPDATE dbo.auction SET a_s_price = ? WHERE a_id = ?", (bid_val, auction_id))
                    inserted = (cur.rowcount or 0) > 0
                except Exception:
                    inserted = False

        if not inserted:
            conn.rollback()
            return False
        conn.commit()
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


def create_item(title, description=None, owner_id=None, category=None, sub_category=None, image_path=None):
    """Insert an item into dbo.item using common column names. Returns new item_id or None."""
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Discover actual columns for dbo.item to pick appropriate insert column names
        try:
            cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='item'")
            fetched = [(r[0].lower(), (r[1].lower() if len(r) > 1 and r[1] is not None else None)) for r in cur.fetchall()]
            existing = {name for name, _ in fetched}
            col_types = {name: dtype for name, dtype in fetched}
        except Exception:
            existing = set()
            col_types = {}

        # Preferred candidate names
        title_candidates = ['title', 'item_title', 'name', 'i_title', 'i_id']
        desc_candidates = ['description', 'desc', 'details', 'i_desc', 'i_desc']
        owner_candidates = ['i_m_id', 'm_id', 'owner_id', 'm_member_id', 'm_m_id']
        cat_candidates = ['i_cat', 'cat', 'category']
        subcat_candidates = ['i_s_cat', 's_cat', 'sub_category', 'subcategory']
        image_candidates = ['image_url', 'image', 'img', 'picture', 'photo', 'imagepath', 'i_image']

        title_col = None
        desc_col = None
        for c in title_candidates:
            if c in existing:
                title_col = c
                break
        for c in desc_candidates:
            if c in existing:
                desc_col = c
                break

        # If we didn't detect reasonable columns, try common 'i_' prefixed columns
        if not title_col:
            for c in existing:
                if c.startswith('i_') and ('title' in c or 'name' in c or 't_' in c):
                    title_col = c
                    break
        if not desc_col:
            for c in existing:
                if c.startswith('i_') and ('desc' in c or 'descr' in c or 'detail' in c):
                    desc_col = c
                    break

        # If owner_id not provided but schema has an owner column that likely disallows NULL,
        # try to pick an existing member as a fallback owner.
        if owner_id is None:
            for oc in owner_candidates:
                if oc in existing:
                    try:
                        cur.execute("SELECT TOP 1 m_id FROM dbo.member")
                        r = cur.fetchone()
                        if r:
                            try:
                                owner_id = int(getattr(r, 'm_id'))
                            except Exception:
                                try:
                                    owner_id = int(r[0])
                                except Exception:
                                    owner_id = None
                    except Exception:
                        owner_id = None
                    break

        # Build insert statement dynamically
        cols = []
        vals = []
        if title_col:
            cols.append(title_col)
            vals.append(title)
        # include owner column if present and we have an owner_id
        # prefer schema-specific owner column names
        included_owner_col = None
        if owner_id is not None:
            for oc in owner_candidates:
                if oc in existing:
                    cols.append(oc)
                    vals.append(owner_id)
                    included_owner_col = oc
                    break
        if desc_col:
            cols.append(desc_col)
            vals.append(description)
        # include category/sub-category when present and provided
        if category is not None:
            for cc in cat_candidates:
                if cc in existing and cc not in cols:
                    # if DB column is integer-like, coerce value to int or skip
                    dtype = col_types.get(cc)
                    if dtype in ('int', 'bigint', 'smallint', 'tinyint'):
                        try:
                            vals.append(int(category))
                            cols.append(cc)
                        except Exception:
                            # couldn't coerce category to int; skip this column
                            pass
                    else:
                        cols.append(cc)
                        vals.append(category)
                    break
        if sub_category is not None:
            for sc in subcat_candidates:
                if sc in existing and sc not in cols:
                    dtype = col_types.get(sc)
                    if dtype in ('int', 'bigint', 'smallint', 'tinyint'):
                        try:
                            vals.append(int(sub_category))
                            cols.append(sc)
                        except Exception:
                            pass
                    else:
                        cols.append(sc)
                        vals.append(sub_category)
                    break
        # include image path when provided and DB has an image column
        if image_path is not None:
            for ic in image_candidates:
                if ic in existing:
                    cols.append(ic)
                    vals.append(image_path)
                    break
        if cols:
            # Deduplicate columns while preserving order to avoid accidental duplicate-column INSERTs.
            seen = set()
            dedup_cols = []
            dedup_vals = []
            for c, v in zip(cols, vals):
                if c not in seen:
                    seen.add(c)
                    dedup_cols.append(c)
                    dedup_vals.append(v)
            cols = dedup_cols
            vals = dedup_vals

            placeholders = ', '.join(['?'] * len(cols))
            col_list = ', '.join(cols)
            sql = f"INSERT INTO dbo.item ({col_list}) VALUES ({placeholders})"
            cur.execute(sql, tuple(vals))
        else:
            # Last resort: try a generic insert without specifying columns
            cur.execute("INSERT INTO dbo.item DEFAULT VALUES")

        # Try to read identity
        item_id = None
        try:
            cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
            r = cur.fetchone()
            if r:
                try:
                    item_id = int(getattr(r, 'new_id'))
                except Exception:
                    try:
                        item_id = int(r[0])
                    except Exception:
                        item_id = None
        except Exception:
            item_id = None

        if not item_id:
            try:
                # Determine id column and title column to use in the lookup
                id_candidates = ['item_id', 'id', 'i_id', 'a_item_id']
                id_col = None
                for c in id_candidates:
                    if c in existing:
                        id_col = c
                        break
                # Fallback to any column that ends with '_id'
                if not id_col:
                    for c in existing:
                        if c.endswith('_id'):
                            id_col = c
                            break

                where_title_col = title_col or 'title'
                if id_col:
                    sql = f"SELECT TOP 1 {id_col} FROM dbo.item WHERE {where_title_col} = ? ORDER BY {id_col} DESC"
                    cur.execute(sql, (title,))
                    r = cur.fetchone()
                    if r:
                        try:
                            item_id = int(getattr(r, id_col))
                        except Exception:
                            try:
                                item_id = int(r[0])
                            except Exception:
                                item_id = None
                else:
                    # No id column detected; attempt a generic lookup
                    cur.execute("SELECT TOP 1 * FROM dbo.item WHERE " + where_title_col + " = ?", (title,))
                    r = cur.fetchone()
                    if r:
                        try:
                            # try to infer id from common positions
                            item_id = int(r[0])
                        except Exception:
                            item_id = None
            except Exception:
                item_id = None

        conn.commit()
        return item_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def create_auction(item_id, seller_id=None, starting_price=0.0, start_date=None, end_date=None):
    """Insert an auction row referencing an existing item. Returns new auction_id or None."""
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    conn = get_connection()
    cur = conn.cursor()
    try:
        auction_id = None
        try:
            if end_date:
                cur.execute(
                    "INSERT INTO dbo.auction (a_item_id, a_m_id, a_s_price, a_s_date, a_e_date) VALUES (?, ?, ?, ?, ?)",
                    (item_id, seller_id, starting_price, start_date or datetime.utcnow(), end_date)
                )
            else:
                cur.execute(
                    "INSERT INTO dbo.auction (a_item_id, a_m_id, a_s_price, a_s_date) VALUES (?, ?, ?, ?)",
                    (item_id, seller_id, starting_price, start_date or datetime.utcnow())
                )
            try:
                cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
                r = cur.fetchone()
                if r:
                    try:
                        auction_id = int(getattr(r, 'new_id'))
                    except Exception:
                        try:
                            auction_id = int(r[0])
                        except Exception:
                            auction_id = None
            except Exception:
                auction_id = None
        except Exception:
            # Try alternate column names
            try:
                if end_date:
                    cur.execute(
                        "INSERT INTO dbo.auction (a_item_id, seller_id, starting_price, a_s_date, a_e_date) VALUES (?, ?, ?, ?, ?)",
                        (item_id, seller_id, starting_price, start_date or datetime.utcnow(), end_date)
                    )
                else:
                    cur.execute(
                        "INSERT INTO dbo.auction (a_item_id, seller_id, starting_price, a_s_date) VALUES (?, ?, ?, ?)",
                        (item_id, seller_id, starting_price, start_date or datetime.utcnow())
                    )
                try:
                    cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
                    r = cur.fetchone()
                    if r:
                        try:
                            auction_id = int(getattr(r, 'new_id'))
                        except Exception:
                            try:
                                auction_id = int(r[0])
                            except Exception:
                                auction_id = None
                except Exception:
                    auction_id = None
            except Exception:
                auction_id = None

        # If identity wasn't retrieved via SCOPE_IDENTITY, try a lookup by item_id
        if not auction_id:
            try:
                cur.execute("SELECT TOP 1 a_id FROM dbo.auction WHERE a_item_id = ? ORDER BY a_id DESC", (item_id,))
                r = cur.fetchone()
                if r:
                    try:
                        auction_id = int(getattr(r, 'a_id'))
                    except Exception:
                        try:
                            auction_id = int(r[0])
                        except Exception:
                            auction_id = None
            except Exception:
                auction_id = None

        conn.commit()
        return auction_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def create_item_and_auction(title, description, seller_id=None, starting_price=0.0, end_date=None, category=None, sub_category=None, image_path=None):
    """Create an item and auction together. Returns auction_id or None."""
    # Perform both inserts in a single DB transaction to avoid orphan items.
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        # DEV-DEBUG: log invocation inputs for troubleshooting route failures
        try:
            logger = logging.getLogger('auth')
            logger.debug('create_item_and_auction called with: %r', {'title': title, 'description': description, 'seller_id': seller_id, 'starting_price': starting_price, 'end_date': end_date, 'category': category, 'sub_category': sub_category, 'image_path': image_path})
        except Exception:
            pass

        # === Insert item (adapted from create_item) ===
        try:
            cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='item'")
            fetched = [(r[0].lower(), (r[1].lower() if len(r) > 1 and r[1] is not None else None)) for r in cur.fetchall()]
            existing = {name for name, _ in fetched}
            col_types = {name: dtype for name, dtype in fetched}
        except Exception:
            existing = set()
            col_types = {}

        title_candidates = ['title', 'item_title', 'name', 'i_title', 'i_id']
        desc_candidates = ['description', 'desc', 'details', 'i_desc', 'i_desc']
        owner_candidates = ['i_m_id', 'm_id', 'owner_id', 'm_member_id', 'm_m_id']
        cat_candidates = ['i_cat', 'cat', 'category']
        subcat_candidates = ['i_s_cat', 's_cat', 'sub_category', 'subcategory']
        image_candidates = ['image_url', 'image', 'img', 'picture', 'photo', 'imagepath', 'i_image']

        title_col = None
        desc_col = None
        for c in title_candidates:
            if c in existing:
                title_col = c
                break
        for c in desc_candidates:
            if c in existing:
                desc_col = c
                break

        if not title_col:
            for c in existing:
                if c.startswith('i_') and ('title' in c or 'name' in c or 't_' in c):
                    title_col = c
                    break
        if not desc_col:
            for c in existing:
                if c.startswith('i_') and ('desc' in c or 'descr' in c or 'detail' in c):
                    desc_col = c
                    break

        if seller_id is None:
            for oc in owner_candidates:
                if oc in existing:
                    try:
                        cur.execute("SELECT TOP 1 m_id FROM dbo.member")
                        r = cur.fetchone()
                        if r:
                            try:
                                seller_id = int(getattr(r, 'm_id'))
                            except Exception:
                                try:
                                    seller_id = int(r[0])
                                except Exception:
                                    seller_id = None
                    except Exception:
                        seller_id = None
                    break

        cols = []
        vals = []
        if title_col:
            cols.append(title_col)
            vals.append(title)
        included_owner_col = None
        if seller_id is not None:
            for oc in owner_candidates:
                if oc in existing:
                    cols.append(oc)
                    vals.append(seller_id)
                    included_owner_col = oc
                    break
        if desc_col:
            cols.append(desc_col)
            vals.append(description)
        if category is not None:
            for cc in cat_candidates:
                if cc in existing and cc not in cols:
                    dtype = col_types.get(cc)
                    if dtype in ('int', 'bigint', 'smallint', 'tinyint'):
                        try:
                            vals.append(int(category))
                            cols.append(cc)
                        except Exception:
                            # skip if cannot coerce
                            pass
                    else:
                        cols.append(cc)
                        vals.append(category)
                    break
        if sub_category is not None:
            for sc in subcat_candidates:
                if sc in existing and sc not in cols:
                    dtype = col_types.get(sc)
                    if dtype in ('int', 'bigint', 'smallint', 'tinyint'):
                        try:
                            vals.append(int(sub_category))
                            cols.append(sc)
                        except Exception:
                            pass
                    else:
                        cols.append(sc)
                        vals.append(sub_category)
                    break
        if image_path is not None:
            for ic in image_candidates:
                if ic in existing:
                    cols.append(ic)
                    vals.append(image_path)
                    break

        if cols:
            # Deduplicate columns while preserving order to avoid accidental duplicate-column INSERTs.
            seen = set()
            dedup_cols = []
            dedup_vals = []
            for c, v in zip(cols, vals):
                if c not in seen:
                    seen.add(c)
                    dedup_cols.append(c)
                    dedup_vals.append(v)
            cols = dedup_cols
            vals = dedup_vals

            placeholders = ', '.join(['?'] * len(cols))
            col_list = ', '.join(cols)
            sql = f"INSERT INTO dbo.item ({col_list}) VALUES ({placeholders})"
            cur.execute(sql, tuple(vals))
        else:
            cur.execute("INSERT INTO dbo.item DEFAULT VALUES")

        # retrieve item id
        item_id = None
        try:
            cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
            r = cur.fetchone()
            if r:
                try:
                    item_id = int(getattr(r, 'new_id'))
                except Exception:
                    try:
                        item_id = int(r[0])
                    except Exception:
                        item_id = None
        except Exception:
            item_id = None

        if not item_id:
            try:
                id_candidates = ['item_id', 'id', 'i_id', 'a_item_id']
                id_col = None
                for c in id_candidates:
                    if c in existing:
                        id_col = c
                        break
                if not id_col:
                    for c in existing:
                        if c.endswith('_id'):
                            id_col = c
                            break
                where_title_col = title_col or 'title'
                if id_col:
                    sql = f"SELECT TOP 1 {id_col} FROM dbo.item WHERE {where_title_col} = ? ORDER BY {id_col} DESC"
                    cur.execute(sql, (title,))
                    r = cur.fetchone()
                    if r:
                        try:
                            item_id = int(getattr(r, id_col))
                        except Exception:
                            try:
                                item_id = int(r[0])
                            except Exception:
                                item_id = None
                else:
                    cur.execute("SELECT TOP 1 * FROM dbo.item WHERE " + where_title_col + " = ?", (title,))
                    r = cur.fetchone()
                    if r:
                        try:
                            item_id = int(r[0])
                        except Exception:
                            item_id = None
            except Exception:
                item_id = None

        if not item_id:
            # abort if we couldn't determine item id
            try:
                logger = logging.getLogger('auth')
                logger.error('create_item_and_auction: failed to determine item_id. cols=%r vals=%r existing=%r', cols, vals, sorted(list(existing)))
            except Exception:
                pass
            conn.rollback()
            return None

        # === Insert auction (schema-aware) ===
        auction_id = None
        try:
            try:
                cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='auction'")
                fetched = [(r[0].lower(), (r[1].lower() if len(r) > 1 and r[1] is not None else None)) for r in cur.fetchall()]
                auction_existing = {name for name, _ in fetched}
                auction_col_types = {name: dtype for name, dtype in fetched}
            except Exception:
                auction_existing = set()
                auction_col_types = {}

            # Candidate column lists
            a_item_candidates = ['a_item_id', 'item_id', 'a_item', 'itemid']
            a_member_candidates = ['a_m_id', 'm_id', 'seller_id', 'member_id']
            a_startprice_candidates = ['a_s_price', 'starting_price', 'start_price', 's_price']
            a_s_date_candidates = ['a_s_date', 'start_date', 's_date', 'a_start']
            a_e_date_candidates = ['a_e_date', 'end_date', 'a_end', 'a_e']

            cols = []
            vals = []

            # helper to pick first existing col from candidates
            def pick(cands):
                for c in cands:
                    if c in auction_existing:
                        return c
                return None

            ai_col = pick(a_item_candidates)
            if ai_col:
                cols.append(ai_col)
                vals.append(item_id)
            am_col = pick(a_member_candidates)
            if am_col:
                cols.append(am_col)
                vals.append(seller_id)
            sp_col = pick(a_startprice_candidates)
            if sp_col:
                cols.append(sp_col)
                vals.append(starting_price)
            sd_col = pick(a_s_date_candidates)
            if sd_col:
                cols.append(sd_col)
                vals.append(datetime.utcnow())

            # include end-date only if column exists and end_date provided
            if end_date is not None:
                ae_col = pick(a_e_date_candidates)
                if ae_col:
                    cols.append(ae_col)
                    vals.append(end_date)

            # If probing failed (no auction_existing) fall back to conventional column names
            if not auction_existing:
                cols = ['a_item_id', 'a_m_id', 'a_s_price', 'a_s_date']
                vals = [item_id, seller_id, starting_price, datetime.utcnow()]
                if end_date is not None:
                    cols.append('a_e_date')
                    vals.append(end_date)

            if cols:
                placeholders = ', '.join(['?'] * len(cols))
                col_list = ', '.join(cols)
                sql = f"INSERT INTO dbo.auction ({col_list}) OUTPUT INSERTED.a_id VALUES ({placeholders})"
                try:
                    logger = logging.getLogger('auth')
                    logger.debug('Executing auction INSERT; cols=%r vals=%r sql=%r', cols, vals, sql)
                except Exception:
                    pass
                try:
                    cur.execute(sql, tuple(vals))
                    try:
                        r = cur.fetchone()
                        if r:
                            try:
                                auction_id = int(getattr(r, 'a_id'))
                            except Exception:
                                try:
                                    auction_id = int(r[0])
                                except Exception:
                                    auction_id = None
                    except Exception:
                        auction_id = None
                except Exception as e:
                    try:
                        logger = logging.getLogger('auth')
                        logger.exception('auction INSERT failed (schema-aware path) exception: %s', e)
                    except Exception:
                        pass
                    auction_id = None
            else:
                auction_id = None
        except Exception:
            auction_id = None

        # fallback: try lookup by item id
        if not auction_id:
            try:
                cur.execute("SELECT TOP 1 a_id FROM dbo.auction WHERE a_item_id = ? ORDER BY a_id DESC", (item_id,))
                r = cur.fetchone()
                if r:
                    try:
                        auction_id = int(getattr(r, 'a_id'))
                    except Exception:
                        try:
                            auction_id = int(r[0])
                        except Exception:
                            auction_id = None
            except Exception:
                auction_id = None

        if not auction_id:
            try:
                logger = logging.getLogger('auth')
                logger.error('create_item_and_auction: failed to determine auction_id for item_id=%r.', item_id)
            except Exception:
                pass
            conn.rollback()
            return None

        conn.commit()
        # Return both ids so callers can name uploaded files using the item id
        return auction_id, item_id
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def get_categories():
    """Return a list of (id, name) tuples for categories found in the DB.

    This is best-effort and tolerant of different schema names. It will try
    common table names (`category`, `categories`, `item_category`) and return
    a list of string pairs. On any failure it returns an empty list.
    """
    if pyodbc is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        # try a few likely table names and column layouts
        candidates = [
            ("dbo.category", ["id", "name"], ["id", "category_name", "name"]),
            ("dbo.categories", ["id", "name"], ["id", "category_name", "name"]),
            ("dbo.item_category", ["cat_id", "cat_name"], ["cat_id", "cat_name", "name"]),
            ("dbo.cat", ["id", "name"], ["id", "name"]),
        ]
        for table, prefer_cols, alt_cols in candidates:
            try:
                # Build a select that attempts to pick common id/name column names
                # We'll probe available columns from INFORMATION_SCHEMA first
                schema, tbl = table.split('.') if '.' in table else ('dbo', table)
                cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=?", (schema.replace('dbo', ''), tbl.replace('dbo', '')))
                fetched = [r[0].lower() for r in cur.fetchall()]
                # find id col
                id_col = None
                name_col = None
                for col in prefer_cols + (alt_cols or []):
                    if col.lower() in fetched and id_col is None and 'id' in col.lower():
                        id_col = col
                for col in prefer_cols + (alt_cols or []):
                    if col.lower() in fetched and name_col is None and ('name' in col.lower() or 'title' in col.lower()):
                        name_col = col
                if not id_col or not name_col:
                    # try some heuristics
                    for c in fetched:
                        if c.endswith('_id') and not id_col:
                            id_col = c
                        if ('name' in c or 'title' in c) and not name_col:
                            name_col = c
                if not id_col or not name_col:
                    continue
                sql = f"SELECT {id_col}, {name_col} FROM {table} ORDER BY {name_col}"
                cur.execute(sql)
                rows = cur.fetchall()
                out = []
                for r in rows:
                    try:
                        cid = getattr(r, id_col)
                    except Exception:
                        try:
                            cid = r[0]
                        except Exception:
                            cid = None
                    try:
                        cname = getattr(r, name_col)
                    except Exception:
                        try:
                            cname = r[1]
                        except Exception:
                            cname = None
                    if cid is None or cname is None:
                        continue
                    out.append((str(cid), str(cname)))
                if out:
                    return out
            except Exception:
                # try next candidate
                continue
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return []


def set_item_image(item_id, image_path):
    """Attempt to update an item row with an image path. Returns True if updated.

    Tries several common image column names and updates whichever exists.
    """
    if pyodbc is None:
        return False
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        # probe for likely image columns
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='item'")
        existing = {r[0].lower() for r in cur.fetchall()}
        image_candidates = ['image_url', 'image', 'img', 'picture', 'photo', 'imagepath', 'i_image']
        for ic in image_candidates:
            if ic in existing:
                try:
                    sql = f"UPDATE dbo.item SET {ic} = ? WHERE "
                    # choose id column
                    id_col = 'i_id' if 'i_id' in existing else ('item_id' if 'item_id' in existing else None)
                    if not id_col:
                        # try any *_id column
                        for c in existing:
                            if c.endswith('_id'):
                                id_col = c
                                break
                    if not id_col:
                        return False
                    sql += f"{id_col} = ?"
                    cur.execute(sql, (image_path, item_id))
                    conn.commit()
                    return (cur.rowcount or 0) > 0
                except Exception:
                    continue
    except Exception:
        return False
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return False


def update_auction_housekeeping(a_id, action, params=None):
    """Perform admin housekeeping actions on an auction row.

    action: one of 'close', 'reopen', 'set_end_date', 'extend_days', 'cancel', 'set_status'
    params: dict containing required params for the action (e.g., {'end_date': datetime} or {'days': 3})

    Returns True if any row was updated, False otherwise.
    """
    if pyodbc is None:
        raise RuntimeError('pyodbc is not installed')
    params = params or {}
    conn = get_connection()
    cur = conn.cursor()
    updated = 0
    try:
        # discover likely column names for end-date and status
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='auction'")
        cols = [r[0].lower() for r in cur.fetchall()]
        end_candidates = ['a_e_date', 'end_date', 'a_end', 'a_e']
        status_candidates = ['a_status', 'status', 'state']
        end_col = None
        status_col = None
        for c in end_candidates:
            if c in cols:
                end_col = c
                break
        for c in status_candidates:
            if c in cols:
                status_col = c
                break

        # If there's no explicit status column but we're performing an action
        # that requires persisting status (cancel / set_status), attempt to
        # add a durable `a_status` column so admin state is explicit.
        if status_col is None and action in ('cancel', 'set_status'):
            try:
                cur.execute(
                    "ALTER TABLE dbo.auction ADD a_status NVARCHAR(64) NULL"
                )
                conn.commit()
                # refresh columns list and mark status_col
                cur = conn.cursor()
                cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='auction'")
                cols = [r[0].lower() for r in cur.fetchall()]
                if 'a_status' in cols:
                    status_col = 'a_status'
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        now = datetime.utcnow()

        # Helper to run an update safely when column exists
        def _update_col(col, value):
            nonlocal updated
            try:
                cur.execute(f"UPDATE dbo.auction SET {col} = ? WHERE a_id = ?", (value, a_id))
                updated = (cur.rowcount or 0) or updated
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        if action == 'close':
            # set end date to now and optionally set status to closed
            if end_col:
                _update_col(end_col, now)
            if status_col:
                _update_col(status_col, 'closed')

        elif action == 'reopen':
            # clear end date and set status to open
            if end_col:
                _update_col(end_col, None)
            if status_col:
                _update_col(status_col, 'open')

        elif action == 'set_end_date':
            ed = params.get('end_date')
            if isinstance(ed, str):
                try:
                    ed = datetime.fromisoformat(ed)
                except Exception:
                    ed = None
            if ed is None:
                # nothing to do
                return False
            if end_col:
                _update_col(end_col, ed)

        elif action == 'extend_days':
            days = params.get('days')
            try:
                days = int(days)
            except Exception:
                days = None
            if days is None:
                return False
            # fetch current end date; if missing, try start date + days
            cur.execute("SELECT a_s_date, " + (end_col or "NULL") + " FROM dbo.auction WHERE a_id = ?", (a_id,))
            row = cur.fetchone()
            if not row:
                return False
            a_s_date = None
            a_e_date = None
            try:
                a_s_date = row[0]
                if end_col and len(row) > 1:
                    a_e_date = row[1]
            except Exception:
                pass
            base = a_e_date or a_s_date
            if not base:
                return False
            try:
                new_end = base + timedelta(days=days)
            except Exception:
                return False
            if end_col:
                _update_col(end_col, new_end)

        elif action == 'cancel':
            # mark as cancelled and set end date to now
            if status_col:
                _update_col(status_col, 'cancelled')
            if end_col:
                _update_col(end_col, now)

        elif action == 'set_status':
            st = params.get('status')
            if st and status_col:
                _update_col(status_col, st)

    finally:
        try:
            conn.close()
        except Exception:
            pass
    return (updated is None) or (updated > 0)


def add_item_image(item_id, image_url, thumb_url=None, sort_order=0):
    """Insert a row into dbo.item_image mapping an item to an image.

    Returns the new img_id on success or None.
    """
    if pyodbc is None:
        return None
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO dbo.item_image (item_id, image_url, thumb_url, sort_order) VALUES (?, ?, ?, ?)",
                (item_id, image_url, thumb_url, sort_order)
            )
        except Exception:
            # Try alternate column names
            try:
                cur.execute(
                    "INSERT INTO dbo.item_image (item_id, image_url) VALUES (?, ?)",
                    (item_id, image_url)
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return None

        # commit first so identity/@@IDENTITY is available
        try:
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

        # try to read identity via several fallbacks
        img_id = None
        try:
            cur.execute("SELECT CAST(SCOPE_IDENTITY() AS INT) AS new_id")
            r = cur.fetchone()
            if r:
                try:
                    img_id = int(getattr(r, 'new_id'))
                except Exception:
                    try:
                        img_id = int(r[0])
                    except Exception:
                        img_id = None
        except Exception:
            img_id = None

        if not img_id:
            try:
                cur.execute("SELECT CAST(@@IDENTITY AS INT) AS new_id")
                r = cur.fetchone()
                if r:
                    try:
                        img_id = int(getattr(r, 'new_id'))
                    except Exception:
                        try:
                            img_id = int(r[0])
                        except Exception:
                            img_id = None
            except Exception:
                img_id = None

        # Final fallback: lookup by (item_id, image_url) ordering by img_id desc
        if not img_id:
            try:
                cur.execute("SELECT TOP 1 img_id FROM dbo.item_image WHERE item_id = ? AND image_url = ? ORDER BY img_id DESC", (item_id, image_url))
                r = cur.fetchone()
                if r:
                    try:
                        img_id = int(getattr(r, 'img_id'))
                    except Exception:
                        try:
                            img_id = int(r[0])
                        except Exception:
                            img_id = None
            except Exception:
                img_id = None

        return img_id
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def get_item_images(item_id):
    """Return a list of image dicts for the given item_id.

    Each dict: {'img_id', 'image_url', 'thumb_url', 'sort_order'}
    """
    if pyodbc is None:
        return []
    conn = None
    out = []
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT img_id, item_id, image_url, thumb_url, sort_order FROM dbo.item_image WHERE item_id = ? ORDER BY sort_order ASC, img_id ASC", (item_id,))
        except Exception:
            # Try alternate column layouts
            try:
                cur.execute("SELECT img_id, item_id, image_url FROM dbo.item_image WHERE item_id = ?", (item_id,))
            except Exception:
                return []
        rows = cur.fetchall()
        for r in rows:
            try:
                d = {}
                d['img_id'] = getattr(r, 'img_id') if hasattr(r, 'img_id') else (r[0] if len(r) > 0 else None)
                # attempt to read image_url and thumb_url by name then by index
                try:
                    d['image_url'] = getattr(r, 'image_url')
                except Exception:
                    try:
                        d['image_url'] = r[2]
                    except Exception:
                        d['image_url'] = None
                try:
                    d['thumb_url'] = getattr(r, 'thumb_url')
                except Exception:
                    try:
                        d['thumb_url'] = r[3]
                    except Exception:
                        d['thumb_url'] = None
                try:
                    d['sort_order'] = getattr(r, 'sort_order')
                except Exception:
                    try:
                        d['sort_order'] = r[4]
                    except Exception:
                        d['sort_order'] = None
                # compute on-disk variant URLs (small/medium/large thumbs and webp) if files exist
                variants = {}
                try:
                    if d.get('image_url') and isinstance(d.get('image_url'), str) and d['image_url'].startswith('/static/uploads/'):
                        uploads_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
                        fname = os.path.basename(d['image_url'])
                        name_noext, ext = os.path.splitext(fname)
                        # main webp
                        main_webp = os.path.join(uploads_dir, f"{name_noext}.webp")
                        if os.path.exists(main_webp):
                            variants['webp'] = f"/static/uploads/{name_noext}.webp"
                        # thumbs by size
                        for sz in ('small', 'medium', 'large'):
                            tname = f"{name_noext}_thumb_{sz}{ext}"
                            tpath = os.path.join(uploads_dir, tname)
                            if os.path.exists(tpath):
                                variants[f'thumb_{sz}'] = f"/static/uploads/{tname}"
                            # webp thumb
                            wname = f"{name_noext}_thumb_{sz}.webp"
                            wpath = os.path.join(uploads_dir, wname)
                            if os.path.exists(wpath):
                                variants[f'thumb_{sz}_webp'] = f"/static/uploads/{wname}"
                except Exception:
                    variants = {}
                d['variants'] = variants
                out.append(d)
            except Exception:
                continue
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return out


def delete_item_image(img_id):
    """Delete an item_image row and attempt to remove files on disk. Returns True on success."""
    if pyodbc is None:
        return False
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        # fetch urls
        try:
            cur.execute("SELECT image_url, thumb_url FROM dbo.item_image WHERE img_id = ?", (img_id,))
            row = cur.fetchone()
        except Exception:
            return False
        if not row:
            return False
        try:
            image_url = getattr(row, 'image_url', None) if hasattr(row, 'image_url') else (row[0] if len(row) > 0 else None)
        except Exception:
            image_url = None
        try:
            thumb_url = getattr(row, 'thumb_url', None) if hasattr(row, 'thumb_url') else (row[1] if len(row) > 1 else None)
        except Exception:
            thumb_url = None

        # attempt to remove files under static/uploads
        try:
            uploads_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
            for u in (image_url, thumb_url):
                if not u or not isinstance(u, str):
                    continue
                if u.startswith('/static/uploads/'):
                    p = os.path.join(uploads_dir, os.path.basename(u))
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                    # also try webp / thumb variants
                    base, ext = os.path.splitext(os.path.basename(u))
                    for suffix in ('_thumb_small.webp', '_thumb_medium.webp', '_thumb_large.webp', '.webp', '_thumb_small'+ext, '_thumb_medium'+ext, '_thumb_large'+ext):
                        try:
                            p2 = os.path.join(uploads_dir, base + suffix) if not suffix.startswith('.') else os.path.join(uploads_dir, base + suffix)
                            if os.path.exists(p2):
                                os.remove(p2)
                        except Exception:
                            pass
        except Exception:
            pass

        # delete DB row
        try:
            cur.execute("DELETE FROM dbo.item_image WHERE img_id = ?", (img_id,))
            conn.commit()
            return (cur.rowcount or 0) > 0
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def reorder_item_images(item_id, ordered_img_ids):
    """Set sort_order for images belonging to `item_id` according to ordered_img_ids list."""
    if pyodbc is None:
        return False
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        for idx, img_id in enumerate(ordered_img_ids, start=1):
            try:
                cur.execute("UPDATE dbo.item_image SET sort_order = ? WHERE img_id = ? AND item_id = ?", (idx, img_id, item_id))
            except Exception:
                continue
        conn.commit()
        return True
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
