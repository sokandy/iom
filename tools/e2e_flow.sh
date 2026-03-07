#!/usr/bin/env bash
set -euo pipefail

# End-to-end flow test:
# register seller -> login seller -> create auction -> register bidder -> bid -> close expired -> verify

ROOT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://127.0.0.1:5000}"
DB_PATH="${SQLITE_PATH:-$ROOT_DIR/iom.e2e.db}"
export USE_DB="${USE_DB:-1}"
export SQLITE_PATH="$DB_PATH"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
	if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
		PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
	else
		PYTHON_BIN="python3"
	fi
fi

RUN_ID="${RUN_ID:-$(date +%Y%m%d%H%M%S)}"
SELLER_USER="${SELLER_USER:-seller_${RUN_ID}}"
SELLER_PASS="${SELLER_PASS:-Secret123!}"
SELLER_EMAIL="${SELLER_EMAIL:-${SELLER_USER}@example.com}"

BIDDER_USER="${BIDDER_USER:-bidder_${RUN_ID}}"
BIDDER_PASS="${BIDDER_PASS:-Secret123!}"
BIDDER_EMAIL="${BIDDER_EMAIL:-${BIDDER_USER}@example.com}"

TITLE="${TITLE:-E2E Auction ${RUN_ID}}"
DESC="${DESC:-End to end auction flow test ${RUN_ID}}"
STARTING_PRICE="${STARTING_PRICE:-10}"
BID_AMOUNT="${BID_AMOUNT:-20}"
DURATION_DAYS="${DURATION_DAYS:-1}"

SELLER_COOKIE="/tmp/iom_e2e_seller_${RUN_ID}.cookie"
BIDDER_COOKIE="/tmp/iom_e2e_bidder_${RUN_ID}.cookie"

cleanup() {
	rm -f "$SELLER_COOKIE" "$BIDDER_COOKIE"
}
trap cleanup EXIT

log() {
	printf '[e2e] %s\n' "$*"
}

fail() {
	printf '[e2e][ERROR] %s\n' "$*" >&2
	exit 1
}

http_post_form() {
	local cookie_file="$1"
	shift
	curl -sS -o /dev/null -w "%{http_code}" -L -c "$cookie_file" -b "$cookie_file" "$@"
}

require_http_okish() {
	local code="$1"
	local step="$2"
	if [[ "$code" != "200" && "$code" != "302" && "$code" != "303" ]]; then
		fail "$step failed with HTTP $code"
	fi
}

log "Root: $ROOT_DIR"
log "Base URL: $BASE_URL"
log "DB path: $SQLITE_PATH"
log "Python: $PYTHON_BIN"

if [[ ! -f "$ROOT_DIR/static/placeholder.png" ]]; then
	log "placeholder.png not found, generating one"
	"$PYTHON_BIN" tools/write_placeholder_png.py
fi

log "Checking app availability"
APP_CODE="$(curl -sS -o /dev/null -w "%{http_code}" "$BASE_URL/")"
if [[ "$APP_CODE" != "200" && "$APP_CODE" != "302" ]]; then
	fail "App not reachable at $BASE_URL (HTTP $APP_CODE). Start app.py first."
fi

log "Resetting SQLite DB"
"$PYTHON_BIN" tools/init_sqlite_db.py --reset --path "$SQLITE_PATH"

log "Register seller: $SELLER_USER"
code="$(http_post_form "$SELLER_COOKIE" -X POST "$BASE_URL/register" \
	-d "username=$SELLER_USER" \
	-d "password=$SELLER_PASS" \
	-d "confirm=$SELLER_PASS" \
	-d "email=$SELLER_EMAIL")"
require_http_okish "$code" "Seller registration"

log "Login seller"
code="$(http_post_form "$SELLER_COOKIE" -X POST "$BASE_URL/user_login" \
	-d "username=$SELLER_USER" \
	-d "password=$SELLER_PASS")"
require_http_okish "$code" "Seller login"

log "Create auction via /auctions/new"
code="$(http_post_form "$SELLER_COOKIE" -X POST "$BASE_URL/auctions/new" \
	-F "title=$TITLE" \
	-F "desc=$DESC" \
	-F "category=1" \
	-F "sub_category=1" \
	-F "starting_price=$STARTING_PRICE" \
	-F "duration=$DURATION_DAYS" \
	-F "images=@$ROOT_DIR/static/placeholder.png;type=image/png")"
require_http_okish "$code" "Create auction"

log "Resolve auction id from DB"
AID="$($PYTHON_BIN - <<'PY'
import os
import sqlite3

db_path = os.environ["SQLITE_PATH"]
title = os.environ["TITLE"]
conn = sqlite3.connect(db_path)
try:
		row = conn.execute(
				"""
				SELECT a.a_id
				FROM auction a
				JOIN item i ON i.i_id = a.a_item_id
				WHERE i.i_title = ?
				ORDER BY a.a_id DESC
				LIMIT 1
				""",
				(title,),
		).fetchone()
		print(row[0] if row else "")
finally:
		conn.close()
PY
)"
[[ -n "$AID" ]] || fail "Could not resolve auction id"
log "Auction ID: $AID"

log "Register bidder: $BIDDER_USER"
code="$(http_post_form "$BIDDER_COOKIE" -X POST "$BASE_URL/register" \
	-d "username=$BIDDER_USER" \
	-d "password=$BIDDER_PASS" \
	-d "confirm=$BIDDER_PASS" \
	-d "email=$BIDDER_EMAIL")"
require_http_okish "$code" "Bidder registration"

log "Login bidder"
code="$(http_post_form "$BIDDER_COOKIE" -X POST "$BASE_URL/user_login" \
	-d "username=$BIDDER_USER" \
	-d "password=$BIDDER_PASS")"
require_http_okish "$code" "Bidder login"

log "Place bid amount=$BID_AMOUNT on auction=$AID"
code="$(http_post_form "$BIDDER_COOKIE" -X POST "$BASE_URL/auction/$AID/bid" \
	-d "amount=$BID_AMOUNT")"
require_http_okish "$code" "Place bid"

log "Verify status before close"
"$PYTHON_BIN" - <<PY
import db
a = db.get_auction(int("$AID"))
print("before_close:", {"id": a.get("id"), "status": a.get("status"), "end_time": str(a.get("end_time")), "current_bid": a.get("current_bid")})
PY

log "Force close expired auctions with a future --at timestamp"
AT="$($PYTHON_BIN - <<'PY'
from datetime import datetime, timedelta
print((datetime.utcnow() + timedelta(days=2)).isoformat())
PY
)"
"$PYTHON_BIN" tools/close_expired_auctions.py --at "$AT"

log "Verify status after close"
"$PYTHON_BIN" - <<PY
import db
a = db.get_auction(int("$AID"))
print("after_close:", {"id": a.get("id"), "status": a.get("status"), "end_time": str(a.get("end_time")), "current_bid": a.get("current_bid")})
if str(a.get("status", "")).lower() != "closed":
		raise SystemExit("Auction did not close as expected")
PY

log "E2E flow completed successfully."
log "Tip: inspect auth.log for email fallback logs if needed."
