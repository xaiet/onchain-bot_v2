import sqlite3
from datetime import datetime, timedelta

DB_PATH = "onchain.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Protocols watchlist
    c.execute("""
        CREATE TABLE IF NOT EXISTS protocols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Wallets seguides
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL UNIQUE,
            label TEXT,
            chain TEXT DEFAULT 'evm',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked TIMESTAMP,
            notes TEXT
        )
    """)

    # Historial d'alertes — evita duplicats
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL UNIQUE,
            sent_at TIMESTAMP NOT NULL
        )
    """)

    # Historial de transaccions notables de wallets seguides
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallet_txns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            tx_hash TEXT NOT NULL UNIQUE,
            value_usd REAL,
            direction TEXT,
            token TEXT,
            noted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

# ── Protocols ──────────────────────────────────────────

def get_protocols():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, slug FROM protocols ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [{"name": r[0], "defillama_slug": r[1]} for r in rows]

def add_protocol(name, slug):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO protocols (name, slug) VALUES (?, ?)", (name, slug))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_protocol(slug):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM protocols WHERE slug = ?", (slug,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

# ── Wallets ────────────────────────────────────────────

def get_wallets(chain=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if chain:
        c.execute("SELECT address, label, chain, notes FROM wallets WHERE chain = ? ORDER BY added_at DESC", (chain,))
    else:
        c.execute("SELECT address, label, chain, notes FROM wallets ORDER BY added_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"address": r[0], "label": r[1], "chain": r[2], "notes": r[3]} for r in rows]

def add_wallet(address, label=None, chain="evm", notes=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO wallets (address, label, chain, notes) VALUES (?, ?, ?, ?)",
            (address, label, chain, notes)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_wallet(address):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM wallets WHERE address = ?", (address,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def update_wallet_label(address, label):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE wallets SET label = ? WHERE address = ?", (label, address))
    conn.commit()
    conn.close()

def log_wallet_txn(address, tx_hash, value_usd, direction, token):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO wallet_txns (address, tx_hash, value_usd, direction, token)
            VALUES (?, ?, ?, ?, ?)
        """, (address, tx_hash, value_usd, direction, token))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        pass

# ── Alertes ────────────────────────────────────────────

def should_send_alert(alert_key, cooldown_hours=6):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sent_at FROM alerts_sent WHERE alert_key = ?", (alert_key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return True
    last_sent = datetime.fromisoformat(row[0])
    return datetime.utcnow() - last_sent > timedelta(hours=cooldown_hours)

def mark_alert_sent(alert_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO alerts_sent (alert_key, sent_at)
        VALUES (?, ?)
        ON CONFLICT(alert_key) DO UPDATE SET sent_at = excluded.sent_at
    """, (alert_key, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()