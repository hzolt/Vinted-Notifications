import sqlite3
import sys

from logger import get_logger

logger = get_logger(__name__)

DB_PATH = "./data/vinted_notifications.db"

# How long a connection waits for another process to finish with the database
# before giving up with "database is locked". The scraper, item extractor, web
# UI and plugins all use the file at once, and SQLite's default of 5 seconds is
# not always enough on a slow or busy disk.
DB_TIMEOUT_SECONDS = 30


def _log_db_error():
    """Log the exception being handled, with its traceback, to logs/vinted.log.

    Most functions here return None or an empty value when the database fails,
    which the caller cannot tell apart from a missing value, so this is where
    the real cause shows up (it used to be printed to stderr only).
    """
    caller = sys._getframe(1).f_code.co_name
    logger.error(f"Database error in {caller}(): {sys.exc_info()[1]}", exc_info=True)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_or_update_sqlite_db(db_path):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Using the sql script
        with open(db_path, "r", encoding="utf-8") as sql_file:
            sql_script = sql_file.read()
            cursor.executescript(sql_script)

        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def is_item_in_db_by_id(id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT() FROM items WHERE item=?", (id,))
        if cursor.fetchone()[0]:
            return True
        return False
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_last_timestamp(query_id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT last_item FROM queries WHERE id=?", (query_id,))
        result = cursor.fetchone()
        if result:
            return result[0]
        return None
    except Exception:
        _log_db_error()
        return None
    finally:
        if conn:
            conn.close()


def update_last_timestamp(query_id, timestamp):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queries SET last_item=? WHERE id=?", (timestamp, query_id)
        )
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def add_item_to_db(id, title, query_id, price, timestamp, photo_url, currency="EUR"):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Insert into db the id and the query_id related to the item
        cursor.execute(
            "INSERT INTO items (item, title, price, currency, timestamp, photo_url, query_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, title, price, currency, timestamp, photo_url, query_id),
        )
        # Update the last item for the query
        cursor.execute(
            "UPDATE queries SET last_item=? WHERE id=?", (timestamp, query_id)
        )
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_queries():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT id, query, last_item, query_name FROM queries")
        return cursor.fetchall()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def is_query_in_db(processed_query):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        # replace spaces in searched_text by % to match any query containing the searched text

        cursor.execute(
            "SELECT COUNT() FROM queries WHERE query = ?", (processed_query,)
        )
        if cursor.fetchone()[0]:
            return True
        return False
    except Exception:
        _log_db_error()
        return False
    finally:
        if conn:
            conn.close()


def add_query_to_db(query, name=None, ai_intent=None, ai_vision_intent=None):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        if name:
            cursor.execute(
                "INSERT INTO queries (query, last_item, query_name) VALUES (?, NULL, ?)",
                (query, name),
            )
        else:
            cursor.execute(
                "INSERT INTO queries (query, last_item) VALUES (?, NULL)", (query,)
            )
        query_id = cursor.lastrowid
        if ai_intent:
            cursor.execute(
                "UPDATE queries SET ai_intent=? WHERE id=?",
                (ai_intent, query_id),
            )
        if ai_vision_intent:
            cursor.execute(
                "UPDATE queries SET ai_vision_intent=? WHERE id=?",
                (ai_vision_intent, query_id),
            )
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_query_id_by_rowid(rowid):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        query = f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY ROWID) rn FROM queries) t WHERE rn={rowid}"
        cursor.execute(query)
        result = cursor.fetchone()
        if result:
            return result[0]
        return None
    except Exception:
        _log_db_error()
        return None
    finally:
        if conn:
            conn.close()


def remove_query_from_db(query_number):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        # Delete items associated with this query using query_id
        cursor.execute("DELETE FROM items WHERE query_id=?", (query_number,))
        # Delete the query
        cursor.execute("DELETE FROM queries WHERE id=?", (query_number,))
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def remove_all_queries_from_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        # Delete all items first to maintain foreign key integrity
        cursor.execute("DELETE FROM items")
        # Then delete all queries
        cursor.execute("DELETE FROM queries")
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def update_query_in_db(query_id, query, name):
    """
    Update an existing query in the database.

    Args:
        query_id (int): The ID of the query to update
        query (str): The new query URL
        name (str, optional): The new name for the query

    Returns:
        bool: True if the query was updated successfully, False otherwise
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queries SET query=?, query_name=? WHERE id=?",
            (query, name, query_id),
        )
        conn.commit()
        return True
    except Exception:
        _log_db_error()
        return False
    finally:
        if conn:
            conn.close()


# ---- AI filter (fork addition) -------------------------------------------
# Kept out of the versioned migrations on purpose: bumping the "version"
# parameter would make the Telegram bot report an upstream update every day.


AI_VERDICT_COLUMNS = [
    "item_id", "query_id", "checked_at", "intent",
    "final_decision", "final_reason", "notified",
    "vision_model", "vision_decision", "vision_reason", "vision_tokens",
    "text_model", "text_decision", "text_reason", "text_tokens",
    "details_source", "seconds", "vision_intent",
]


def ensure_ai_schema():
    """Create the AI filter columns and table if missing. Safe to run every start.

    queries.ai_intent         the plain-language filter text
    queries.ai_vision_intent  extra instructions for the vision model only
                              (AI Vision filter)
    ai_verdicts               one row per checked item and query: both
                              reviewers' decisions, the final outcome and
                              whether it was sent
    """
    conn = None
    try:
        conn = get_db_connection()
        added = False
        columns = [row[1] for row in conn.execute("PRAGMA table_info(queries)")]
        for column in ("ai_intent", "ai_vision_intent"):
            if column not in columns:
                conn.execute(f"ALTER TABLE queries ADD COLUMN {column} TEXT")
                added = True
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_verdicts'"
        ).fetchone()
        if not exists:
            conn.execute(
                """CREATE TABLE ai_verdicts (
                    item_id         NUMERIC NOT NULL,
                    query_id        INTEGER NOT NULL,
                    checked_at      INTEGER,
                    intent          TEXT,
                    final_decision  TEXT,
                    final_reason    TEXT,
                    notified        INTEGER,
                    vision_model    TEXT,
                    vision_decision TEXT,
                    vision_reason   TEXT,
                    vision_tokens   INTEGER,
                    text_model      TEXT,
                    text_decision   TEXT,
                    text_reason     TEXT,
                    text_tokens     INTEGER,
                    details_source  TEXT,
                    seconds         REAL,
                    vision_intent   TEXT,
                    PRIMARY KEY (item_id, query_id)
                )"""
            )
            added = True
        verdict_columns = [row[1] for row in conn.execute("PRAGMA table_info(ai_verdicts)")]
        if "vision_intent" not in verdict_columns:
            conn.execute("ALTER TABLE ai_verdicts ADD COLUMN vision_intent TEXT")
            added = True
        conn.commit()
        return added
    except Exception:
        _log_db_error()
        return False
    finally:
        if conn:
            conn.close()


def ensure_indexes():
    """Create the indexes the scraping loop needs. Safe to run every start.

    items(item): is_item_in_db_by_id() runs for every scraped item of every
    query on each refresh. Without an index each call reads the whole items
    table, which only grows, and keeps the database busy for the other
    processes. Returns True if an index was added.
    """
    conn = None
    try:
        conn = get_db_connection()
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_items_item'"
        ).fetchone()
        if not exists:
            conn.execute("CREATE INDEX idx_items_item ON items (item)")
            conn.commit()
        return not exists
    except Exception:
        _log_db_error()
        return False
    finally:
        if conn:
            conn.close()


def save_ai_verdict(item_id, query_id, intent, row):
    """Store one verdict. `row` comes from ai_filter.Evaluation.to_row()."""
    import time as _time

    values = {"item_id": item_id, "query_id": query_id,
              "checked_at": int(_time.time()), "intent": intent, **row}
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        conn.execute(
            f"INSERT OR REPLACE INTO ai_verdicts ({', '.join(AI_VERDICT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(AI_VERDICT_COLUMNS))})",
            [values.get(c) for c in AI_VERDICT_COLUMNS],
        )
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_ai_verdicts(item_ids):
    """Return {item_id: verdict dict} (latest per item) for the given ids."""
    if not item_ids:
        return {}
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(item_ids))
        rows = conn.execute(
            f"SELECT * FROM ai_verdicts WHERE item_id IN ({placeholders}) ORDER BY checked_at",
            list(item_ids),
        ).fetchall()
        return {row["item_id"]: dict(row) for row in rows}
    except Exception:
        _log_db_error()
        return {}
    finally:
        if conn:
            conn.close()


def get_query_ai_intent(query_id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        row = conn.execute(
            "SELECT ai_intent FROM queries WHERE id=?", (query_id,)
        ).fetchone()
        return row[0].strip() if row and row[0] and row[0].strip() else None
    except Exception:
        _log_db_error()
        return None
    finally:
        if conn:
            conn.close()


def get_query_ai_intents():
    """Return {query_id: ai_intent} for queries that have one."""
    return _get_query_texts("ai_intent")


def set_query_ai_intent(query_id, ai_intent):
    return _set_query_text(query_id, "ai_intent", ai_intent)


def get_query_ai_vision_intent(query_id):
    """The query's AI Vision filter instructions, or None."""
    return _get_query_texts("ai_vision_intent").get(query_id)


def get_query_ai_vision_intents():
    """Return {query_id: ai_vision_intent} for queries that have one."""
    return _get_query_texts("ai_vision_intent")


def set_query_ai_vision_intent(query_id, ai_vision_intent):
    return _set_query_text(query_id, "ai_vision_intent", ai_vision_intent)


_AI_QUERY_COLUMNS = ("ai_intent", "ai_vision_intent")


def _get_query_texts(column):
    """Return {query_id: text} for queries with a non-empty value in `column`."""
    assert column in _AI_QUERY_COLUMNS
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        rows = conn.execute(
            f"SELECT id, {column} FROM queries WHERE {column} IS NOT NULL AND TRIM({column}) != ''"
        ).fetchall()
        return {row[0]: row[1].strip() for row in rows}
    except Exception:
        _log_db_error()
        return {}
    finally:
        if conn:
            conn.close()


def _set_query_text(query_id, column, text):
    """Store a query's text in `column`; empty text is stored as NULL."""
    assert column in _AI_QUERY_COLUMNS
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        value = text.strip() if text and text.strip() else None
        conn.execute(f"UPDATE queries SET {column}=? WHERE id=?", (value, query_id))
        conn.commit()
        return True
    except Exception:
        _log_db_error()
        return False
    finally:
        if conn:
            conn.close()


def add_to_allowlist(country):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO allowlist VALUES (?)", (country,))
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def remove_from_allowlist(country):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allowlist WHERE country=?", (country,))
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_allowlist():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM allowlist")
        # Get list of countries
        countries = [country[0] for country in cursor.fetchall()]
        # Return 0 if there are no countries in the allowlist
        if not countries:
            return 0
        return countries
    finally:
        if conn:
            conn.close()


def clear_allowlist():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allowlist")
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_parameter(key):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM parameters WHERE key=?", (key,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def set_parameter(key, value):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("UPDATE parameters SET value=? WHERE key=?", (value, key))
        conn.commit()
    except Exception:
        _log_db_error()
    finally:
        if conn:
            conn.close()


def get_all_parameters():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM parameters")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        _log_db_error()
        return {}
    finally:
        if conn:
            conn.close()


def get_items(limit=50, query=None):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        if query:
            # Get the query_id for the given query
            cursor.execute("SELECT id FROM queries WHERE query=?", (query,))
            result = cursor.fetchone()
            if result:
                query_id = result[0]
                # Get items with the matching query_id
                cursor.execute(
                    "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url, q.query_name FROM items i JOIN queries q ON i.query_id = q.id WHERE i.query_id=? ORDER BY i.timestamp DESC LIMIT ?",
                    (query_id, limit),
                )
            else:
                return []
        else:
            # Join with queries table to get the query text
            cursor.execute(
                "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url, q.query_name FROM items i JOIN queries q ON i.query_id = q.id ORDER BY i.timestamp DESC LIMIT ?",
                (limit,),
            )
        return cursor.fetchall()
    except Exception:
        _log_db_error()
        return []
    finally:
        if conn:
            conn.close()


def get_total_items_count():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM items")
        return cursor.fetchone()[0]
    except Exception:
        _log_db_error()
        return 0
    finally:
        if conn:
            conn.close()


def get_total_queries_count():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queries")
        return cursor.fetchone()[0]
    except Exception:
        _log_db_error()
        return 0
    finally:
        if conn:
            conn.close()


def get_last_found_item():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url FROM items i JOIN queries q ON i.query_id = q.id ORDER BY i.timestamp DESC LIMIT 1"
        )
        return cursor.fetchone()
    except Exception:
        _log_db_error()
        return None
    finally:
        if conn:
            conn.close()


def get_items_per_day():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        cursor = conn.cursor()

        # Get total items
        cursor.execute("SELECT COUNT(*) FROM items")
        total_items = cursor.fetchone()[0]

        if total_items == 0:
            return 0

        # Get earliest and latest timestamps
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM items")
        min_timestamp, max_timestamp = cursor.fetchone()

        # Calculate number of days (add 1 to include both start and end days)
        import datetime

        min_date = datetime.datetime.fromtimestamp(min_timestamp).date()
        max_date = datetime.datetime.fromtimestamp(max_timestamp).date()
        days_diff = (max_date - min_date).days + 1

        # Ensure at least 1 day to avoid division by zero
        days_diff = max(1, days_diff)

        # Calculate items per day
        return round(total_items / days_diff, 1)
    except Exception:
        _log_db_error()
        return 0
    finally:
        if conn:
            conn.close()
