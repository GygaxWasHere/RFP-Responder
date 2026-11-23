"""Microsoft SQL Server as a knowledge source.

A source is a server + database + one query (or a table/view name). Every result row becomes one
document whose text is "Column: value" lines, so Claude can quote it and the quote can be checked.

Safety:
  * Only a single SELECT (or WITH ... SELECT) is accepted; data-changing keywords are refused.
  * The connection asks for read-only intent, and the transaction is always rolled back.
  * Use a database login that can only read (ideally your Windows account with read rights).
  * Passwords for SQL logins never go to the shared folder: they are kept on this computer only
    (Windows: encrypted for the user with DPAPI; macOS: the login Keychain).
"""
import datetime
import decimal
import hashlib
import re

from . import osdep

MAX_ROWS = 100000
_FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|grant|revoke|deny|"
                        r"backup|restore|dbcc|shutdown|reconfigure|openrowset|opendatasource|openquery|into|"
                        r"waitfor|bulk|kill|use|sp_\w+|xp_\w+)\b", re.I)
_IDENT = re.compile(r"^\s*(\[[^\]]+\]|\w+)(\s*\.\s*(\[[^\]]+\]|\w+)){0,2}\s*$")


# ------------------------------------------------------------------ passwords (this computer only)

def _secret_key(src):
    return f"sql|{src.get('server', '').lower()}|{src.get('database', '').lower()}|{src.get('user', '').lower()}"


def save_password(src, password):
    osdep.save_secret(_secret_key(src), password)


def get_password(src):
    return osdep.get_secret(_secret_key(src))


# ------------------------------------------------------------------ connection

def _pyodbc():
    try:
        import pyodbc
    except ImportError:
        raise RuntimeError("Database sources need the pyodbc package and Microsoft's ODBC driver for SQL Server. "
                           "On a Mac: brew install unixodbc msodbcsql18, then pip install pyodbc "
                           "(see the README). Areas built by colleagues work without it.") from None
    return pyodbc


def _driver():
    have = _pyodbc().drivers()
    for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"):
        if d in have:
            return d
    raise RuntimeError("No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server' from Microsoft.")


def connect(src, password=None):
    pyodbc = _pyodbc()
    drv = _driver()
    parts = [f"DRIVER={{{drv}}}", f"SERVER={src['server']}", f"DATABASE={src['database']}"]
    if src.get("auth") == "sql":
        pw = password if password is not None else get_password(src)
        if pw is None:
            raise RuntimeError(f"No password for {src.get('user')} on {src['server']} is stored on this PC. "
                               "Open the source on the Knowledge base tab and enter it.")
        parts += [f"UID={src['user']}", "PWD={" + pw.replace("}", "}}") + "}"]
    else:
        parts.append("Trusted_Connection=yes")
    if drv.startswith("ODBC Driver"):
        parts.append("ApplicationIntent=ReadOnly")
        parts.append("Encrypt=yes")
        if src.get("trust_cert", True):
            parts.append("TrustServerCertificate=yes")
    c = pyodbc.connect(";".join(parts), timeout=15, autocommit=False, readonly=True)
    c.timeout = 300
    return c


def build_sql(query):
    """A table/view name becomes SELECT * FROM it; otherwise the query must be a single SELECT."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        raise ValueError("Enter a table or view name, or a SELECT query")
    if _IDENT.match(q):
        return f"SELECT * FROM {q}"
    bare = re.sub(r"--[^\n]*|/\*.*?\*/", " ", q, flags=re.S)
    bare = re.sub(r"'(?:[^']|'')*'", "''", bare)  # ignore words inside string literals
    bare = re.sub(r'\[[^\]]*\]|"[^"]*"', "x", bare)  # ...and inside quoted names like [Update]
    if not re.match(r"^\s*(select|with)\b", bare, re.I):
        raise ValueError("Only SELECT queries are allowed")
    if ";" in bare:
        raise ValueError("Only one statement is allowed")
    m = _FORBIDDEN.search(bare)
    if m:
        raise ValueError(f"'{m.group(0)}' is not allowed in a knowledge source query (read only)")
    return q


def _val(v):
    if v is None:
        return ""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else v.isoformat()
    if isinstance(v, decimal.Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, (bytes, bytearray, memoryview)):
        return ""
    return str(v).strip()


def _rows(src, password=None, limit=None):
    c = connect(src, password)
    try:
        cur = c.cursor()
        cur.execute(build_sql(src["query"]))
        cols = [d[0] for d in cur.description]
        n = 0
        while True:
            batch = cur.fetchmany(500)
            if not batch:
                break
            for r in batch:
                yield cols, r
                n += 1
                if n >= (limit or MAX_ROWS):
                    return
    finally:
        try:
            c.rollback()
        finally:
            c.close()


def preview(src, password=None, n=8):
    cols, rows = [], []
    for cols, r in _rows(src, password, n):
        rows.append([_val(v)[:200] for v in r])
    return {"columns": cols, "rows": rows}


def list_tables(src, password=None):
    s = dict(src, query="SELECT TABLE_SCHEMA + '.' + TABLE_NAME AS name, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
                        "ORDER BY TABLE_SCHEMA, TABLE_NAME")
    return [{"name": r[0], "type": r[1]} for _, r in _rows(s, password, 2000)]


def collection_of(src):
    return f"mssql://{src['server']}/{src['database']}/{src.get('name') or src['query'][:60]}"


def ingest_sql(kb, src, user="", job=None):
    """Load every row as a document. Unchanged rows are skipped; rows that disappeared are removed."""
    coll = collection_of(src)
    name = src.get("name") or "rows"
    keep, stored, unchanged = [], 0, 0
    for i, (cols, r) in enumerate(_rows(src), 1):
        if job and job.cancelled:
            break
        rec = dict(zip(cols, r))
        key = _val(rec.get(src.get("key_col"))) if src.get("key_col") in rec else str(i)
        title = _val(rec.get(src.get("title_col"))) if src.get("title_col") in rec else ""
        title = title or f"{name} {key}"
        text = "\n".join(f"{c}: {_val(v)}" for c, v in rec.items() if _val(v))
        if not text:
            continue
        loc = f"{coll}/{key}"
        keep.append(loc)
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        old = kb.doc_state(loc)
        if old and old["hash"] == h:
            unchanged += 1
        else:
            kb.put_document("sql", loc, title, coll, [(title, text)], user=user, content_hash=h)
            stored += 1
        if job and i % 200 == 0:
            job.update(message=f"{name}: {i} rows read")
    removed = 0 if (job and job.cancelled) else kb.delete_missing(coll, keep, kind="sql")
    if job:
        job.log(f"{name}: {stored} rows stored, {unchanged} unchanged, {removed} removed")
    return {"stored": stored, "unchanged": unchanged, "removed": removed, "collection": coll}
