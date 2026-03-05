import os
import logging
import time
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Flask, render_template, session, redirect, url_for, request, flash
from flask import abort
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "replace-with-a-secure-secret"

get_auctions = None
get_auction = None
get_user_by_username = None
verify_password = None
create_member = None
get_member_by_id = None
get_all_members = None

# Optional DB-backed mode. Set USE_DB=1 or USE_DB=true to enable.
USE_DB = os.getenv('USE_DB', '').lower() in ('1', 'true', 'yes')
if USE_DB:
    try:
        from db import get_auctions, get_auction, get_user_by_username, verify_password, create_member, get_member_by_id, get_all_members
    except Exception:
        # Keep demo mode if DB helpers are not available
        get_auctions = None
        get_auction = None
        get_user_by_username = None
        verify_password = None
        create_member = None
        get_member_by_id = None
        get_all_members = None

# --- Auth hardening: login attempt tracking and logging ---
AUTH_LOG = os.getenv('AUTH_LOG', 'auth.log')
if AUTH_LOG.startswith('/'):
    log_dir = os.path.dirname(AUTH_LOG) or '/'
    try:
        os.makedirs(log_dir, exist_ok=True)
    except PermissionError:
        fallback = os.path.join(os.getcwd(), os.path.basename(AUTH_LOG))
        AUTH_LOG = fallback
        log_dir = os.path.dirname(AUTH_LOG) or os.getcwd()
        os.makedirs(log_dir, exist_ok=True)
logger = logging.getLogger('auth')
if not logger.handlers:
    h = logging.FileHandler(AUTH_LOG)
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

LOGIN_ATTEMPTS = {}  # username -> {'fail_count': int, 'lockout_until': timestamp}
MAX_FAILED = int(os.getenv('LOGIN_MAX_FAILED', '5'))
LOCKOUT_SECONDS = int(os.getenv('LOGIN_LOCKOUT_SECONDS', '300'))


def _client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip() or 'unknown'
    return request.remote_addr or 'unknown'


def _attempt_keys(username, ip_addr=None):
    uname = (username or '').strip().lower()
    ip = (ip_addr or 'unknown').strip().lower()
    keys = [f'ip:{ip}']
    if uname:
        keys.append(f'user:{uname}')
    return keys


def is_locked(username, ip_addr=None):
    for key in _attempt_keys(username, ip_addr):
        info = LOGIN_ATTEMPTS.get(key)
        if not info:
            continue
        until = info.get('lockout_until')
        if until and time.time() < until:
            return True
    return False


def _user_dict_from_session():
    """Return a normalized user dict for the currently logged-in session, or None."""
    if not session.get('u_name'):
        return None
    uname = session.get('u_name')
    if USE_DB and get_user_by_username:
        try:
            return get_user_by_username(uname)
        except Exception:
            return None
    # If DB mode is not enabled, provide a demo admin user for 'admin' only
    if uname == 'admin':
        return {
            'id': 1,
            'username': 'admin',
            'm_login_id': 'admin',
            'm_is_admin': True,
            'is_admin': True,
            'm_role': 'admin',
        }
    return None


def user_is_admin(user_dict):
    """Heuristic to determine if a user dict represents an admin.

    Checks common admin/role flags in DB rows and falls back to ADMIN_USERNAME env var.
    """
    if not user_dict:
        return False
    # explicit role fields
    role_keys = ['role', 'm_role', 'is_admin', 'm_is_admin', 'admin', 'isadmin']
    for k in role_keys:
        v = user_dict.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.lower() in ('1', 't', 'true', 'yes', 'admin'):
            return True
        if isinstance(v, (int, bool)) and bool(v):
            return True
    # No username fallback — require DB role fields for admin privileges
    return False

def record_failed(username, ip_addr=None):
    keys = _attempt_keys(username, ip_addr)
    for key in keys:
        info = LOGIN_ATTEMPTS.setdefault(key, {'fail_count': 0, 'lockout_until': None})
        info['fail_count'] += 1
        if info['fail_count'] >= MAX_FAILED:
            info['lockout_until'] = time.time() + LOCKOUT_SECONDS
            logger.warning(f"Login lockout: {key}")
        else:
            logger.info(f"Failed login {info['fail_count']} for {key}")


def record_success(username, ip_addr=None):
    for key in _attempt_keys(username, ip_addr):
        if key in LOGIN_ATTEMPTS:
            LOGIN_ATTEMPTS.pop(key, None)
    logger.info(f"Successful login for {(username or '').strip().lower() or 'unknown-user'} from {(ip_addr or 'unknown')}")

# --- Email confirmation helpers ---
TS_SECRET = os.getenv('TS_SECRET') or app.secret_key or 'replace-with-a-secure-secret'
ts = URLSafeTimedSerializer(TS_SECRET)

def generate_confirmation_token(member_id):
    return ts.dumps({'m_id': member_id})

def confirm_token(token, max_age=60*60*24):
    try:
        data = ts.loads(token, max_age=max_age)
        return data
    except SignatureExpired:
        return None
    except BadSignature:
        return None

def send_confirmation_email(to_email, token):
    subject = 'Confirm your registration'
    confirm_url = url_for('confirm_registration', token=token, _external=True)
    # Try to render a prettier email using templates (both text and HTML). Fallback to plain text.
    try:
        text = render_template('email/confirmation_email.txt', confirm_url=confirm_url)
        html = render_template('email/confirmation_email.html', confirm_url=confirm_url)
    except Exception:
        text = f"Please confirm your registration by clicking: {confirm_url}\n\nIf you didn't request this, ignore."
        html = None

    return _send_email(to_email=to_email, subject=subject, text=text, html=html)


def _send_email(to_email, subject, text, html=None):
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '0')) if os.getenv('SMTP_PORT') else None
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')

    if smtp_host and smtp_port:
        try:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = smtp_user or 'noreply@example.com'
            msg['To'] = to_email
            msg.set_content(text)
            if html:
                msg.add_alternative(html, subtype='html')
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                if smtp_user and smtp_pass:
                    s.starttls()
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            logger.info(f"Sent email to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.exception(f"Failed sending email to {to_email}: {e}")
            return False

    logger.info(f"Email (no SMTP configured) to {to_email}: {subject} | {text}")
    return True

def send_auction_result_email(to_email, auction_title, auction_url, recipient_role, winning_amount=None):
    if not to_email:
        return False

    symbol = os.getenv('CURRENCY_SYMBOL', 'HK$')
    amount_text = None
    if winning_amount is not None:
        try:
            amount_text = f"{symbol}{float(winning_amount):.2f}"
        except Exception:
            amount_text = str(winning_amount)

    subject_map = {
        'winner': 'You won the auction',
        'seller_winner': 'Your auction has a winner',
        'seller_no_sale': 'Your auction ended with no bids',
    }
    subject = subject_map.get(recipient_role, 'Auction result update')

    context = {
        'auction_title': auction_title,
        'auction_url': auction_url,
        'amount_text': amount_text,
    }

    template_map = {
        'winner': ('email/auction_result_winner.txt', 'email/auction_result_winner.html'),
        'seller_winner': ('email/auction_result_seller_winner.txt', 'email/auction_result_seller_winner.html'),
        'seller_no_sale': ('email/auction_result_seller_no_sale.txt', 'email/auction_result_seller_no_sale.html'),
    }
    text_tmpl, html_tmpl = template_map.get(recipient_role, template_map['seller_no_sale'])

    try:
        text = render_template(text_tmpl, **context)
    except Exception:
        link_text = f"\nView auction: {auction_url}" if auction_url else ""
        if recipient_role == 'winner':
            text = f"You won {auction_title}. Winning bid: {amount_text}.{link_text}"
        elif recipient_role == 'seller_winner':
            text = f"Your auction {auction_title} ended with a winning bid of {amount_text}.{link_text}"
        else:
            text = f"Your auction {auction_title} ended with no bids.{link_text}"

    try:
        html = render_template(html_tmpl, **context)
    except Exception:
        html = None

    return _send_email(to_email=to_email, subject=subject, text=text, html=html)


def send_outbid_email(to_email, auction, previous_amount, new_amount):
    if not to_email:
        return False
    subject = 'You have been outbid'
    auction_title = (auction or {}).get('title') or f"Auction #{(auction or {}).get('id') or ''}".strip()
    auction_url = None
    try:
        auction_url = url_for('view_auction', item_id=(auction or {}).get('id'), _external=True)
    except Exception:
        auction_url = (auction or {}).get('url')
    symbol = os.getenv('CURRENCY_SYMBOL', 'HK$')

    context = {
        'auction_title': auction_title,
        'auction_url': auction_url,
        'previous_amount': f"{symbol}{float(previous_amount):.2f}",
        'new_amount': f"{symbol}{float(new_amount):.2f}",
    }

    try:
        text = render_template('email/outbid_email.txt', **context)
    except Exception:
        link_part = f"\nView auction: {auction_url}" if auction_url else ""
        text = (
            f"You've been outbid on {auction_title}.\n"
            f"Your last bid: {context['previous_amount']}\n"
            f"New highest bid: {context['new_amount']}"
            f"{link_part}"
        )

    try:
        html = render_template('email/outbid_email.html', **context)
    except Exception:
        html = None

    return _send_email(to_email=to_email, subject=subject, text=text, html=html)


def actual_date():
    """Return a formatted current date/time similar to the PHP helper."""
    return datetime.now().strftime("%A, %B %d, %Y %I:%M %p")


@app.context_processor
def inject_globals():
    # Make helpers and commonly used variables available in all templates
    data = {
        "actual_date": actual_date,
        "u_name": session.get("u_name")
    }
    # Provide categories globally so header includes can render consistently
    try:
        categories = [("1", "Antiques"), ("2", "Electronics"), ("3", "Books")]
        if USE_DB:
            try:
                from db import get_categories
                cats = get_categories()
                if cats:
                    categories = cats
            except Exception:
                pass
        data['categories'] = categories
    except Exception:
        data['categories'] = [("1", "Antiques"), ("2", "Electronics"), ("3", "Books")]
    # Provide current year for footer copyright
    try:
        data['current_year'] = datetime.utcnow().year
    except Exception:
        data['current_year'] = 2025
    # Currency settings (centralized for templates)
    try:
        # Allow overriding via environment variables if needed
        data['currency_symbol'] = os.getenv('CURRENCY_SYMBOL', 'HK$')
        data['currency_label'] = os.getenv('CURRENCY_LABEL', 'HKD')
    except Exception:
        data['currency_symbol'] = 'HK$'
        data['currency_label'] = 'HKD'
    return data


def parse_int_field(val, name=None, required=False):
    """Safely parse an integer field from form data.

    Returns an int on success or None when empty or unparsable.
    Logs a debug message when coercion fails. If `required` is True and the
    value is invalid, this still returns None (caller may decide to flash/abort).
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == '':
        return None
    try:
        return int(s)
    except Exception:
        try:
            logger.debug('parse_int_field: could not convert %s=%r to int', name, val)
        except Exception:
            pass
        return None


@app.route('/contact')
def contact():
    try:
        return render_template('contact.html')
    except FileNotFoundError:
        logger.warning("contact.html template not found")
        return "Contact page unavailable", 200
    except Exception as e:
        logger.exception("Unexpected error in /contact")
        if app.debug:
            return f"Error: {e}", 500
        return "Internal server error", 500


@app.route('/')
def index():
    recent_auctions = []
    if USE_DB:
        try:
            from db import get_auctions
            recent_auctions = get_auctions(limit=6) or []
        except Exception as e:
            logger.warning(f"get_auctions failed in /: {e}")
            recent_auctions = []
    try:
        return render_template('index.html', recent_auctions=recent_auctions)
    except Exception as e:
        logger.exception(f"Template rendering failed in /: {e}")
        if app.debug:
            return f"Error rendering index.html: {e}", 500
        return "Internal server error", 500



@app.route('/search')
def search():
    filters = _parse_auction_filters(request.args)
    items = []
    if USE_DB and get_auctions:
        try:
            items = get_auctions(
                limit=filters['limit'],
                keyword=filters['key_word'],
                category=filters['category'],
                status=filters['status'],
                min_price=filters['min_price'],
                max_price=filters['max_price'],
            ) or []
        except Exception as e:
            logger.exception(f"Unexpected error in /search DB query: {e}")
            items = []
    try:
        return render_template('auction_browse.html', items=items, filters=filters)
    except Exception as e:
        logger.exception(f"Template rendering failed in /search: {e}")
        if app.debug:
            return f"Error rendering auction_browse.html: {e}", 500
        return render_template('auction_browse.html', items=[], filters=filters)


@app.route('/browse')
def browse():
    # Keep backward compatibility: redirect to the RESTful auctions listing
    return redirect(url_for('auctions'))


@app.route('/auctions')
def auctions():
    filters = _parse_auction_filters(request.args)

    sample_items = []
    if USE_DB and get_auctions:
        try:
            sample_items = get_auctions(
                limit=filters['limit'],
                keyword=filters['key_word'],
                category=filters['category'],
                status=filters['status'],
                min_price=filters['min_price'],
                max_price=filters['max_price'],
            )
        except Exception as e:
            logger.warning(f"get_auctions failed in /auctions: {e}")
            sample_items = []
    try:
        return render_template('auction_browse.html', items=sample_items, filters=filters)
    except Exception as e:
        logger.exception(f"Template rendering failed in /auctions: {e}")
        if app.debug:
            return f"Error rendering auction_browse.html: {e}", 500
        return "Internal server error", 500


def _parse_auction_filters(args):
    key_word = (args.get('key_word') or '').strip()
    category = (args.get('category') or '').strip()
    status = (args.get('status') or '').strip().lower()

    qlimit = (args.get('limit') or '50').strip()
    if qlimit.lower() in ('all', 'none', '0', 'no', 'unlimited'):
        limit = None
    else:
        try:
            limit = int(qlimit)
            if limit <= 0:
                limit = 50
        except Exception:
            limit = 50

    def _as_float(name):
        raw = (args.get(name) or '').strip()
        if raw == '':
            return None
        try:
            return float(raw)
        except Exception:
            return None

    min_price = _as_float('min_price')
    max_price = _as_float('max_price')
    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price

    return {
        'key_word': key_word,
        'category': category,
        'status': status,
        'min_price': min_price,
        'max_price': max_price,
        'limit': limit,
    }


@app.route('/sell')
def sell():
    # Redirect to the RESTful new-auction route for consistency
    return redirect(url_for('new_auction'))


@app.route('/help')
def help_page():
    try:
        return render_template('how_to_bid.html')
    except Exception:
        try:
            return render_template('contact.html')
        except Exception:
            return 'Help page unavailable', 200


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not username or not password:
            return render_template('register.html', message='Username and password are required.')
        if password != confirm:
            return render_template('register.html', message='Passwords do not match.')
        if len(password) < 8:
            return render_template('register.html', message='Password must be at least 8 characters')
        if '@' not in request.form.get('email', '') and request.form.get('email'):
            return render_template('register.html', message='Invalid email address')

        if USE_DB and create_member is not None:
            try:
                first_name = request.form.get('first_name') or request.form.get('fname')
                last_name = request.form.get('last_name') or request.form.get('lname')
                email = request.form.get('email')
                if first_name or last_name:
                    logger.info(f"New member extra fields captured: first={first_name}, last={last_name}")
                new_id = create_member(username, password, email=email, role=request.form.get('role') or 'user')
                if email:
                    token = generate_confirmation_token(new_id)
                    send_confirmation_email(email, token)
                    return render_template('register.html', success=True, username=username, pending=True)
                session['u_name'] = username
                if new_id:
                    session['user_id'] = new_id
                return render_template('register.html', success=True, username=username)
            except ValueError as ve:
                logger.info(f"Registration error for {username}: {ve}")
                return render_template('register.html', message=str(ve))
            except Exception as e:
                logger.exception(f"Registration failed for {username}: {e}")
                if app.debug or os.getenv('SHOW_ERRORS', '').lower() in ('1', 'true', 'yes'):
                    return render_template('register.html', message=f"Registration failed (server error): {e}")
                return render_template('register.html', message='Registration failed (server error).')

        session['u_name'] = username
        return render_template('register.html', success=True, username=username)

    if session.get('u_name'):
        return render_template('register.html', already=True)
    return render_template('register.html')


@app.route('/confirm/<token>')
def confirm_registration(token):
    data = confirm_token(token)
    if not data:
        return render_template('register.html', message='Invalid or expired confirmation link.')
    member_id = data.get('m_id') if isinstance(data, dict) else None
    if USE_DB and member_id:
        try:
            from db import confirm_member
            confirm_member(int(member_id))
        except Exception as e:
            logger.warning(f"confirm_registration failed for m_id={member_id}: {e}")
    return render_template('register.html', confirmed=True)


@app.route('/user_login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        ip_addr = _client_ip()

        if is_locked(username, ip_addr):
            return render_template('user_login.html', message='Too many login attempts. Try again later.')

        if USE_DB and get_user_by_username is not None:
            try:
                user = get_user_by_username(username)
            except Exception as e:
                logger.warning(f"get_user_by_username failed: {e}")
                user = None
            if user:
                stored = user.get('password') or user.get('m_pass') or user.get('passwd') or user.get('pwd')
                if verify_password and verify_password(stored, password):
                    session['u_name'] = username
                    if user.get('id'):
                        session['user_id'] = user.get('id')
                    record_success(username, ip_addr)
                    return redirect(url_for('index'))
                else:
                    record_failed(username, ip_addr)
                    return render_template('user_login.html', message='Invalid login, please try again.')
            else:
                record_failed(username, ip_addr)
                return render_template('user_login.html', message='Invalid login, please try again.')

        if username == 'admin' and password == 'adminpass':
            session['u_name'] = 'admin'
            session['user_id'] = 1
            record_success(username, ip_addr)
            return redirect(url_for('index'))
        record_failed(username, ip_addr)
        return render_template('user_login.html', message='Server not configured for DB-backed authentication.')

    if session.get('u_name'):
        return render_template('user_login.html', already=True)
    return render_template('user_login.html')


@app.route('/logout')
def logout():
    session.pop('u_name', None)
    return redirect(url_for('index'))


@app.route('/user_menu')
def user_menu():
    if not session.get('u_name'):
        return redirect(url_for('user_login'))
    try:
        return render_template('user_menu.html', username=session.get('u_name'))
    except FileNotFoundError:
        logger.warning("user_menu.html template not found")
        return "User menu unavailable", 200
    except Exception as e:
        logger.exception("Unexpected error in /user_menu")
        if app.debug:
            return f"Error: {e}", 500
        return "Internal server error", 500


@app.route('/user_agreement')
def user_agreement():
    if not session.get('u_name'):
        return redirect(url_for('user_login'))
    try:
        return render_template('user_agreement.html')
    except FileNotFoundError:
        logger.warning("user_agreement.html template not found")
        return "User agreement page unavailable", 200
    except Exception as e:
        logger.exception("Unexpected error in /user_agreement")
        if app.debug:
            return f"Error: {e}", 500
        return "Internal server error", 500


@app.route('/admin')
def admin():
    """Simple admin landing page.

    Requires an authenticated admin user. Renders `admin.html` when available
    or returns a minimal HTML page listing admin actions.
    """
    if not session.get('u_name'):
        return redirect(url_for('user_login'))
    user = _user_dict_from_session()
    if not user or not user_is_admin(user):
        # Not authorized
        abort(403)
    # Optionally load member list for the main admin page too
    members = None
    auctions = None
    if USE_DB:
        try:
            from db import get_all_members
            members = get_all_members() or []
        except Exception:
            members = None
        try:
            from db import get_auctions
            auctions = get_auctions(limit=50) or []
        except Exception:
            auctions = None

    try:
        # Prefer the `admin_panel_fixed.html` template if present
        try:
            return render_template('admin_panel_fixed.html', user=user, members=members, auctions=auctions)
        except Exception:
            return render_template('admin_panel.html', user=user, members=members, auctions=auctions)
    except FileNotFoundError:
        logger.warning('admin_panel.html template not found; returning text fallback')
        # Minimal fallback page with links to common admin actions
        html = [
            '<!doctype html>',
            '<html><head><meta charset="utf-8"><title>Admin</title></head><body>',
            '<h1>Admin Console</h1>',
            f'<p>Logged in as: {session.get("u_name")}</p>',
            '<ul>',
            '<li><a href="/admin/resend">Resend confirmation email</a></li>',
            '<li><a href="/admin/unlock">Unlock account</a></li>',
            '<li><a href="/admin/grant">Grant admin role</a></li>',
            '<li><a href="/admin/revoke">Revoke admin role</a></li>',
            '<li><a href="/admin/members">List members</a></li>',
            '<li><a href="/admin/audit">View audit log</a></li>',
            '</ul>',
            '</body></html>'
        ]
        return '\n'.join(html)
    except Exception as e:
        logger.exception('Unexpected error in /admin: %s', e)
        if app.debug:
            return f'Error: {e}', 500
        return 'Internal server error', 500


def _require_admin():
    """Ensure current session is an admin. Returns user dict or aborts/redirects."""
    if not session.get('u_name'):
        return redirect(url_for('user_login'))
    user = _user_dict_from_session()
    if not user or not user_is_admin(user):
        abort(403)
    return user


def _audit_admin_action(user, action, target=None, result='success', detail=None):
    if not USE_DB:
        return
    try:
        from db import log_admin_action
        admin_username = None
        if isinstance(user, dict):
            admin_username = user.get('username') or user.get('m_login_id')
        log_admin_action(
            admin_username=admin_username or session.get('u_name') or 'unknown',
            action=str(action),
            target=(str(target) if target is not None else None),
            result=str(result),
            detail=(str(detail) if detail is not None else None),
            ip_address=request.remote_addr or _client_ip(),
        )
    except Exception as e:
        logger.warning('audit logging failed: %s', e)


def _resolve_member_id(identifier):
    """Resolve a username or id-like identifier to a numeric member id when possible.

    Returns an int member id or None.
    """
    if not identifier:
        return None
    # numeric id
    try:
        if str(identifier).isdigit():
            return int(identifier)
    except Exception:
        pass

    if USE_DB:
        try:
            # try direct helper if available
            if 'get_user_by_username' in globals() and callable(get_user_by_username):
                m = get_user_by_username(identifier)
                if m:
                    return m.get('id') or m.get('m_id')
            # fallback: scan members list if available
            if 'get_all_members' in globals() and callable(get_all_members):
                for mem in get_all_members() or []:
                    uname = (mem.get('username') or mem.get('m_login_id') or '')
                    if uname and uname.lower() == str(identifier).lower():
                        return mem.get('id') or mem.get('m_id')
        except Exception:
            pass
    return None


@app.route('/admin/resend', methods=['GET', 'POST'])
def admin_resend():
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    member_id = request.form.get('member_id') or request.args.get('member_id')
    email = None
    if not member_id:
        flash('member_id is required', 'error')
        return redirect(url_for('admin'))
    if USE_DB:
        try:
            # member_id may be numeric id or username/email
            mid = _resolve_member_id(member_id)
            member = None
            if mid and 'get_member_by_id' in globals() and callable(get_member_by_id):
                member = get_member_by_id(mid)
            # if not found, try lookup by username
            if not member and 'get_user_by_username' in globals() and callable(get_user_by_username):
                member = get_user_by_username(member_id)
            if member:
                email = member.get('email') or member.get('m_email')
        except Exception:
            email = None
    if '@' in (member_id or '') and not email:
        email = member_id

    if email:
        try:
            token = generate_confirmation_token(member_id)
            ok = send_confirmation_email(email, token)
            if ok:
                _audit_admin_action(user, 'resend_confirmation', target=member_id, result='success', detail=f'email={email}')
                flash('Confirmation email sent.', 'success')
            else:
                _audit_admin_action(user, 'resend_confirmation', target=member_id, result='error', detail='send_confirmation_email returned false')
                flash('Failed to send confirmation email.', 'error')
        except Exception as e:
            logger.exception('admin_resend failed: %s', e)
            _audit_admin_action(user, 'resend_confirmation', target=member_id, result='error', detail=str(e))
            flash('Failed to send confirmation email (server error).', 'error')
    else:
        _audit_admin_action(user, 'resend_confirmation', target=member_id, result='error', detail='email not found')
        flash('Member email not found or DB not configured.', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/resend/<member_id>', methods=['GET', 'POST'])
def admin_resend_legacy(member_id):
    code = 307 if request.method == 'POST' else 302
    return redirect(url_for('admin_resend', member_id=member_id), code=code)


@app.route('/admin/auction/<int:a_id>/delete', methods=['POST'])
def admin_delete_auction(a_id):
    """Admin-only endpoint to permanently delete an auction and its bids."""
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    if not USE_DB:
        flash('DB not configured; cannot delete auctions.', 'error')
        return redirect(url_for('admin'))
    try:
        from db import delete_auction_and_bids
        deleted_auctions, deleted_bids = delete_auction_and_bids(a_id)
        if deleted_auctions:
            _audit_admin_action(user, 'delete_auction', target=a_id, result='success', detail=f'bids={deleted_bids}')
            flash(f'Deleted auction {a_id} and {deleted_bids} bids.', 'success')
        else:
            _audit_admin_action(user, 'delete_auction', target=a_id, result='warning', detail='no auction deleted')
            flash(f'No auction row deleted for id {a_id}.', 'warning')
    except Exception as e:
        logger.exception('admin_delete_auction failed: %s', e)
        _audit_admin_action(user, 'delete_auction', target=a_id, result='error', detail=str(e))
        flash('Failed to delete auction (server error).', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/unlock', methods=['POST'])
def admin_unlock():
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    member = request.form.get('member') or request.args.get('member')
    if not member:
        flash('Member identifier required', 'error')
        return redirect(url_for('admin'))
    acted = False
    mid = _resolve_member_id(member)
    if USE_DB:
        try:
            # Use confirm_member as a way to activate/unlock accounts in DB
            from db import confirm_member
            if mid:
                ok = confirm_member(mid)
                acted = bool(ok)
        except Exception:
            acted = False
    # Always clear in-memory lockout as a fallback
    try:
        LOGIN_ATTEMPTS.pop(member, None)
        LOGIN_ATTEMPTS.pop(str(mid), None)
        acted = True
    except Exception:
        pass
    if acted:
        _audit_admin_action(user, 'unlock_member', target=member, result='success', detail=f'mid={mid}')
        flash('Member unlocked (best-effort).', 'success')
    else:
        _audit_admin_action(user, 'unlock_member', target=member, result='error', detail=f'mid={mid}')
        flash('Failed to unlock member.', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/unlock/<member>', methods=['POST'])
def admin_unlock_legacy(member):
    return redirect(url_for('admin_unlock', member=member), code=307)


@app.route('/admin/grant', methods=['POST'])
def admin_grant():
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    member = request.form.get('member') or request.args.get('member')
    if not member:
        flash('Member identifier required', 'error')
        return redirect(url_for('admin'))
    try:
        mid = _resolve_member_id(member)
        if USE_DB and mid:
            try:
                from db import set_member_admin
                ok = set_member_admin(mid, True)
                if ok:
                    _audit_admin_action(user, 'grant_admin', target=member, result='success', detail=f'mid={mid}')
                    flash('Granted admin role.', 'success')
                else:
                    _audit_admin_action(user, 'grant_admin', target=member, result='warning', detail='no rows affected')
                    flash('Grant admin did not affect any rows.', 'error')
            except Exception as e:
                logger.exception('admin_grant (db) failed: %s', e)
                _audit_admin_action(user, 'grant_admin', target=member, result='error', detail=str(e))
                flash('Failed to grant admin role (server error).', 'error')
        else:
            _audit_admin_action(user, 'grant_admin', target=member, result='error', detail='db not configured or member not found')
            flash('DB not configured or member not found; cannot grant admin.', 'error')
    except Exception as e:
        logger.exception('admin_grant failed: %s', e)
        _audit_admin_action(user, 'grant_admin', target=member, result='error', detail=str(e))
        flash('Failed to grant admin role (server error).', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/revoke', methods=['POST'])
def admin_revoke():
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    member = request.form.get('member') or request.args.get('member')
    if not member:
        flash('Member identifier required', 'error')
        return redirect(url_for('admin'))
    try:
        mid = _resolve_member_id(member)
        if USE_DB and mid:
            try:
                from db import set_member_admin
                ok = set_member_admin(mid, False)
                if ok:
                    _audit_admin_action(user, 'revoke_admin', target=member, result='success', detail=f'mid={mid}')
                    flash('Revoked admin role.', 'success')
                else:
                    _audit_admin_action(user, 'revoke_admin', target=member, result='warning', detail='no rows affected')
                    flash('Revoke admin did not affect any rows.', 'error')
            except Exception as e:
                logger.exception('admin_revoke (db) failed: %s', e)
                _audit_admin_action(user, 'revoke_admin', target=member, result='error', detail=str(e))
                flash('Failed to revoke admin role (server error).', 'error')
        else:
            _audit_admin_action(user, 'revoke_admin', target=member, result='error', detail='db not configured or member not found')
            flash('DB not configured or member not found; cannot revoke admin.', 'error')
    except Exception as e:
        logger.exception('admin_revoke failed: %s', e)
        _audit_admin_action(user, 'revoke_admin', target=member, result='error', detail=str(e))
        flash('Failed to revoke admin role (server error).', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/members')
def admin_members():
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    members = None
    if USE_DB:
        try:
            from db import get_all_members
            members = get_all_members() or []
        except Exception:
            members = None
    try:
        return render_template('admin_panel_fixed.html', user=user, members=members)
    except Exception:
        return render_template('admin_panel.html', user=user, members=members)



@app.route('/admin/auction/<int:a_id>/housekeep', methods=['POST'])
def admin_auction_housekeep(a_id):
    """Admin-only endpoint to perform housekeeping on an auction.

    Actions supported (via form field `action`):
      - close: set end date to now and mark closed
      - reopen: clear end date and mark open
      - set_end_date: expects form `end_date` (ISO format)
      - extend_days: expects form `days` (int)
      - cancel: mark cancelled and set end date to now
      - set_status: expects form `status`
    """
    user = _require_admin()
    if isinstance(user, tuple):
        return user
    action = request.form.get('action') or request.args.get('action')
    if not action:
        flash('Action is required for housekeeping.', 'error')
        return redirect(url_for('admin'))
    # collect params
    params = {}
    if 'end_date' in request.form:
        params['end_date'] = request.form.get('end_date')
    if 'days' in request.form:
        try:
            params['days'] = int(request.form.get('days'))
        except Exception:
            params['days'] = None
    if 'status' in request.form:
        params['status'] = request.form.get('status')

    if USE_DB:
        try:
            from db import update_auction_housekeeping
            ok = update_auction_housekeeping(a_id, action, params)
            if ok:
                _audit_admin_action(user, 'auction_housekeep', target=a_id, result='success', detail=f'action={action}, params={params}')
                flash(f'Auction {a_id} updated: {action}', 'success')
            else:
                _audit_admin_action(user, 'auction_housekeep', target=a_id, result='warning', detail=f'action={action}, no changes')
                flash(f'No changes applied for auction {a_id}.', 'warning')
        except Exception as e:
            logger.exception('admin_auction_housekeep failed: %s', e)
            _audit_admin_action(user, 'auction_housekeep', target=a_id, result='error', detail=str(e))
            flash('Failed to perform housekeeping (server error).', 'error')
    else:
        _audit_admin_action(user, 'auction_housekeep', target=a_id, result='error', detail='db not configured')
        flash('DB not configured; cannot perform housekeeping.', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/audit')
def admin_audit():
    user = _require_admin()
    if isinstance(user, tuple):
        return user

    rows = []
    if USE_DB:
        try:
            from db import get_recent_admin_audit_logs
            rows = get_recent_admin_audit_logs(limit=100)
        except Exception as e:
            logger.exception('admin_audit failed: %s', e)
            rows = []

    html = [
        '<!doctype html>',
        '<html><head><meta charset="utf-8"><title>Admin Audit</title></head><body>',
        '<h1>Admin Audit Log (latest 100)</h1>',
        '<p><a href="/admin">Back to Admin</a></p>',
        '<table border="1" cellpadding="6" cellspacing="0">',
        '<tr><th>ID</th><th>Time</th><th>Admin</th><th>Action</th><th>Target</th><th>Result</th><th>Detail</th><th>IP</th></tr>',
    ]
    for r in rows:
        html.append(
            f"<tr><td>{r.get('log_id')}</td><td>{r.get('created_at') or ''}</td><td>{r.get('admin_username') or ''}</td>"
            f"<td>{r.get('action') or ''}</td><td>{r.get('target') or ''}</td><td>{r.get('result') or ''}</td>"
            f"<td>{r.get('detail') or ''}</td><td>{r.get('ip_address') or ''}</td></tr>"
        )
    html.append('</table></body></html>')
    return '\n'.join(html)


@app.route('/auction/<int:item_id>')
@app.route('/auctions/<int:item_id>')
def view_auction(item_id):
    if not (USE_DB and get_auction):
        abort(404)
    try:
        from db import get_connection, get_item_images
        item = get_auction(item_id)
        if not item:
            abort(404)
        # Try to fetch highest bid for display
        highest_bid = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SELECT TOP 1 b_amount AS a_s_price, b_m_id FROM dbo.bid WHERE b_a_id = ? ORDER BY b_amount DESC", (item_id,))
                row = cur.fetchone()
                if row:
                    highest_bid = {'a_s_price': getattr(row, 'a_s_price', None) or (row[0] if len(row) > 0 else None),
                                   'a_m_id': getattr(row, 'b_m_id', None) or (row[1] if len(row) > 1 else None)}
            except Exception:
                try:
                    cur.execute("SELECT TOP 1 amount AS a_s_price, member_id FROM dbo.bid WHERE auction_id = ? ORDER BY amount DESC", (item_id,))
                    row = cur.fetchone()
                    if row:
                        highest_bid = {'a_s_price': getattr(row, 'a_s_price', None) or (row[0] if len(row) > 0 else None),
                                       'a_m_id': getattr(row, 'member_id', None) or (row[1] if len(row) > 1 else None)}
                except Exception:
                    highest_bid = None
        finally:
            try:
                conn.close()
            except Exception:
                pass
        # Fetch image list for gallery if available
        try:
            images = get_item_images(item_id) or []
        except Exception:
            images = []
        return render_template('item.html', item=item, highest_bid=highest_bid, images=images)
    except Exception as e:
        logger.exception(f"Error in /auction/<item_id>: {e}")
        if app.debug:
            return f"Error loading auction: {e}", 500
        return "Internal server error", 500


@app.route('/auction/<int:auction_id>/bid', methods=['POST'])
def place_bid_route(auction_id):
    user = _user_dict_from_session()
    if not user:
        return redirect(url_for('user_login'))
    amount = request.form.get('amount') or request.form.get('bid')
    if not amount:
        flash('No bid amount provided.', 'error')
        return redirect(url_for('view_auction', item_id=auction_id))
    # parse bid amount
    try:
        bid_val = float(amount)
    except Exception:
        flash('Invalid bid amount.', 'error')
        return redirect(url_for('view_auction', item_id=auction_id))

    bidder_id = user.get('id') or user.get('m_id')
    if not bidder_id and USE_DB and get_user_by_username:
        try:
            fresh = get_user_by_username(user.get('username') or user.get('m_login_id'))
            bidder_id = fresh.get('id') or fresh.get('m_id')
        except Exception:
            bidder_id = None
    if not bidder_id:
        return redirect(url_for('user_login'))

    if USE_DB:
        try:
            # Check auction existence and status first for clearer messages
            from db import get_auction, get_connection, place_bid, get_current_highest_bidder
            auc = None
            try:
                auc = get_auction(auction_id)
            except Exception:
                auc = None
            if not auc:
                flash('Auction not found.', 'error')
                return redirect(url_for('auctions'))

            # If auction read helper provides status/end_time, use it
            try:
                if auc.get('status') and str(auc.get('status')).lower() in ('closed', 'c', 'cancelled', 'cancel'):
                    flash('This auction is closed and no longer accepts bids.', 'error')
                    return redirect(url_for('view_auction', item_id=auction_id))
                end_time = auc.get('end_time')
                from datetime import datetime
                if end_time and isinstance(end_time, datetime) and end_time <= datetime.utcnow():
                    flash('This auction has ended and no longer accepts bids.', 'error')
                    return redirect(url_for('view_auction', item_id=auction_id))
            except Exception:
                # ignore and continue
                pass

            # Determine current highest bid numeric value
            current = 0.0
            try:
                conn = get_connection()
                cur = conn.cursor()
                try:
                    cur.execute("SELECT MAX(b_amount) AS maxb FROM dbo.bid WHERE b_a_id = ?", (auction_id,))
                    row = cur.fetchone()
                    if row:
                        try:
                            current = float(getattr(row, 'maxb') or 0)
                        except Exception:
                            current = float(row[0] or 0)
                except Exception:
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
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Exception:
                current = 0.0

            # Fallback to auction starting price when no bids
            if current == 0.0:
                try:
                    # try common auction starting price column
                    from db import get_connection as _gc
                    cconn = _gc()
                    ccur = cconn.cursor()
                    try:
                        ccur.execute("SELECT a_s_price FROM dbo.auction WHERE a_id = ?", (auction_id,))
                        r = ccur.fetchone()
                        if r:
                            try:
                                current = float(getattr(r, 'a_s_price') or 0)
                            except Exception:
                                current = float(r[0] or 0)
                    finally:
                        try:
                            cconn.close()
                        except Exception:
                            pass
                except Exception:
                    current = 0.0

            # If bid is not higher than current, provide a clearer message
            if bid_val <= current:
                symbol = os.getenv('CURRENCY_SYMBOL', 'HK$')
                flash(f'Your bid must be higher than the current highest bid ({symbol}{current:.2f}).', 'error')
                return redirect(url_for('view_auction', item_id=auction_id))

            previous_highest = None
            try:
                previous_highest = get_current_highest_bidder(auction_id)
            except Exception:
                previous_highest = None

            # Attempt to place the bid
            ok = place_bid(auction_id, bidder_id, bid_val)
            if ok:
                try:
                    prev_id = int(previous_highest.get('member_id')) if previous_highest and previous_highest.get('member_id') is not None else None
                except Exception:
                    prev_id = None
                if prev_id and prev_id != int(bidder_id):
                    prev_email = previous_highest.get('email')
                    prev_amount = previous_highest.get('amount')
                    if prev_email:
                        send_outbid_email(
                            to_email=prev_email,
                            auction=auc,
                            previous_amount=prev_amount,
                            new_amount=bid_val,
                        )
                flash('Your bid was placed successfully.', 'success')
            else:
                flash('Your bid was not accepted (it may be too low or the auction is closed).', 'error')
            return redirect(url_for('view_auction', item_id=auction_id))
        except Exception as e:
            logger.exception(f'Place bid failed: {e}')
            if app.debug:
                flash(f'Bid failed: {e}', 'error')
            else:
                flash('Bid failed due to a server error.', 'error')
            return redirect(url_for('view_auction', item_id=auction_id))
    # Non-DB fallback: redirect back to auction page
    return redirect(url_for('view_auction', item_id=auction_id))


@app.route('/auction_page')
def auction_page():
    # Demo auction page removed.
    abort(404)


@app.route('/auction_result')
def auction_result():
    # Demo auction result removed.
    abort(404)


@app.route('/forgotpasswd')
def forgotpasswd():
    try:
        return render_template('forgotpasswd.html')
    except FileNotFoundError:
        logger.warning("forgotpasswd.html template not found")
        return "Forgot password page unavailable", 200
    except Exception as e:
        logger.exception("Unexpected error in /forgotpasswd")
        if app.debug:
            return f"Error: {e}", 500
        return "Internal server error", 500


@app.route('/how_to_bid')
def how_to_bid():
    if not (USE_DB and get_auctions):
        abort(404)
    try:
        return render_template('how_to_bid.html')
    except FileNotFoundError:
        logger.warning("how_to_bid.html template not found")
        return redirect(url_for('new_auction'))
    except Exception as e:
        logger.exception(f"Error rendering how_to_bid: {e}")
        if app.debug:
            return f"Error rendering how_to_bid.html: {e}", 500
        return redirect(url_for('new_auction'))


def validate_form_data(form):
    """Validate and parse form data."""
    title = (form.get('title') or '').strip()
    desc = (form.get('desc') or '').strip()
    category = (form.get('category') or '').strip()
    sub_category = (form.get('sub_category') or '').strip()
    starting_price = form.get('starting_price') or form.get('price') or 0
    duration_raw = form.get('duration') or '0'

    try:
        starting_price = float(starting_price)
    except Exception:
        starting_price = 0.0

    try:
        duration_days = int(duration_raw)
        if duration_days < 0:
            duration_days = 0
    except Exception:
        duration_days = 0

    return title, desc, category, sub_category, starting_price, duration_days


def authenticate_user():
    """Ensure the user is logged in and retrieve their ID."""
    user = _user_dict_from_session()
    if not user:
        flash('You must be logged in to post an item.', 'error')
        return None, redirect(url_for('user_login'))

    seller_id = user.get('id') or user.get('m_id') or session.get('user_id')
    return seller_id, None


def validate_uploaded_images(files):
    """Validate and process uploaded images."""
    allowed_ext = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
    valid_images = []

    for f in files:
        if not f:
            continue
        filename = f.filename or ''
        if filename and any(filename.lower().endswith(ext) for ext in allowed_ext):
            valid_images.append(f)

    if not valid_images:
        flash('Please upload at least one image for the item.', 'error')
        return None

    return valid_images


def create_auction_in_db(title, desc, seller_id, starting_price, end_date, category, sub_category):
    """Insert item and auction into the database."""
    from db import create_item_and_auction

    try:
        result = create_item_and_auction(
            title, desc, seller_id=seller_id, starting_price=starting_price,
            end_date=end_date, category=parse_int_field(category, 'category'),
            sub_category=parse_int_field(sub_category, 'sub_category')
        )
        if not result:
            flash('Failed to create item/auction (database error).', 'error')
            return None, render_template('post_items_for_sale.html', title=title, desc=desc)

        return result, None
    except Exception as e:
        logger.exception('Failed to create item/auction: %s', e)
        flash('Failed to create auction (server error).', 'error')
        return None, render_template('post_items_for_sale.html', title=title, desc=desc)


def save_uploaded_images(valid_images, item_id):
    """Save images to the filesystem and update the database."""
    from db import add_item_image, set_item_image
    upload_dir = os.path.join(app.static_folder or 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    saved_image_path = None
    for idx, f in enumerate(valid_images, start=1):
        try:
            filename = secure_filename(f.filename)
            if not filename:
                continue
            base, ext = os.path.splitext(filename)
            ext = ext or '.jpg'
            stored_name = f"item{item_id}_{idx}{ext}"
            out_path = os.path.join(upload_dir, stored_name)
            f.save(out_path)

            web_image = f"/static/uploads/{stored_name}"
            if add_item_image:
                add_item_image(item_id, web_image, None, sort_order=idx)

            if idx == 1:
                saved_image_path = web_image
                if set_item_image:
                    set_item_image(item_id, saved_image_path)
        except Exception as e:
            logger.exception('Failed saving uploaded image: %s', e)

    return saved_image_path


@app.route('/auctions/new', methods=['GET', 'POST'])
def new_auction():
    categories = [("1", "Antiques"), ("2", "Electronics"), ("3", "Books")]

    if request.method == 'POST':
        title, desc, category, sub_category, starting_price, duration_days = validate_form_data(request.form)

        if not title:
            flash('Title is required to post an item.', 'error')
            return render_template('post_items_for_sale.html', title=title, desc=desc, categories=categories)

        seller_id, redirect_response = authenticate_user()
        if redirect_response:
            return redirect_response

        valid_images = validate_uploaded_images(request.files.getlist('images'))
        if not valid_images:
            return render_template('post_items_for_sale.html', title=title, desc=desc, categories=categories)

        end_date = datetime.utcnow() + timedelta(days=duration_days) if duration_days > 0 else None

        result, error_response = create_auction_in_db(title, desc, seller_id, starting_price, end_date, category, sub_category)
        if error_response:
            return error_response

        auction_id, item_id = result
        save_uploaded_images(valid_images, item_id)

        flash('Item and auction created successfully.', 'success')
        return redirect(url_for('view_auction', item_id=auction_id))

    return render_template('post_items_for_sale.html', categories=categories)


if __name__ == "__main__":
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '5000'))
    app.run(host=host, port=port, debug=True)
