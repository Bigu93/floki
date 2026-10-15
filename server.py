#!/usr/bin/env python3
"""Floki - local reverse-shell / command payload templating utility.

Assembles publicly-documented payload syntax from templates.json, applies the
template's encoding, and serves a single-page UI on localhost. It only formats
and copies text: it never executes a payload and makes no outbound calls.

Run:  python3 server.py [port]   (default 127.0.0.1:8000)

Bind host/port can also be set with the FLOKI_HOST / FLOKI_PORT environment
variables (used by the container image, which binds 0.0.0.0 so the port can be
published). A CLI port argument overrides FLOKI_PORT.
"""
import base64
import ipaddress
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES_FILE = HERE / "templates.json"
INDEX_FILE = HERE / "index.html"
REFERENCE_SEED = HERE / "reference_seed.json"
METHODOLOGY_FILE = HERE / "methodology.json"
DB_FILE = HERE / "floki.db"

MAX_BODY = 64 * 1024  # cap on POST body size; the tool only ever gets small JSON

# The brand mark (same glyph as the UI header), served so the browser's
# automatic /favicon.ico request doesn't 404. Rounded-square accent background.
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#0f1217"/>'
    '<g fill="none" stroke="#46b8c6" stroke-width="2.4" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M9 10l5 6-5 6"/><path d="M18 22h6"/></g></svg>'
).encode("utf-8")

# The runtime store is floki.db, rebuilt on startup from the editable seed files.
# `options` holds each template's toggleable flags (JSON list, stored as text).
TEMPLATE_COLS = ("id", "group", "category", "name", "type", "encoding", "template", "notes", "options")

# Reference tables: table name -> (columns, order-by). Every column is searched
# except those in NO_SEARCH (too noisy to LIKE-match against).
REF_TABLES = {
    "ports": (("port", "proto", "service", "notes"), "port"),
    "creds": (("product", "username", "password", "notes"), "product"),
    "hashmodes": (("mode", "name", "example"), "mode"),
    "gtfobins": (("binary", "function", "command"), "binary"),
    "wordlists": (("name", "path", "use"), "name"),
    "modules": (("name", "protocol", "description"), "protocol"),
    "lolbas": (("binary", "function", "command"), "binary"),
    "nse": (("script", "category", "description"), "category"),
}
NO_SEARCH = {"ports": ("proto",)}  # proto is tcp/udp — matches everything, useless as a filter

# Templates may use any {VAR}. These few get special handling; all others are
# substituted as-is free text.
VAR_RE = re.compile(r"\{([A-Z_]+)\}")
HOST_VARS = {"LHOST", "RHOST", "DC_IP"}   # validated as IP/hostname
PORT_VARS = {"LPORT", "RPORT"}              # validated as 1-65535
DEFAULTS = {"SHELL": "/bin/bash"}           # used when left blank
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


# --- validation -------------------------------------------------------------

def valid_host(value):
    """True if value is a valid IPv4/IPv6 address or hostname."""
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(_HOSTNAME_RE.match(value))


def valid_port(value):
    try:
        return 1 <= int(value) <= 65535
    except (TypeError, ValueError):
        return False


# --- encoding ---------------------------------------------------------------

def _b64_powershell(cmd):
    # PowerShell -EncodedCommand expects UTF-16LE base64.
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def _b64_utf8(cmd):
    # Plain UTF-8 base64 (distinct from the PowerShell UTF-16LE 'base64' above).
    return base64.b64encode(cmd.encode("utf-8")).decode("ascii")


def _url_twice(cmd):
    # Percent-encode twice: safe='' both times so reserved chars are also escaped.
    return urllib.parse.quote(urllib.parse.quote(cmd, safe=""), safe="")


def _hex(cmd):
    # Every UTF-8 byte as a literal \xHH escape.
    return "".join(f"\\x{b:02x}" for b in cmd.encode("utf-8"))


def _unicode(cmd):
    # Every character as a literal \uXXXX escape.
    return "".join(f"\\u{ord(ch):04x}" for ch in cmd)


ENCODERS = {
    "raw": lambda c: c,
    "url": lambda c: urllib.parse.quote(c, safe=""),
    "base64": _b64_powershell,
    "b64": _b64_utf8,
    "url2": _url_twice,
    "hex": _hex,
    "unicode": _unicode,
}


# --- render -----------------------------------------------------------------

def load_templates():
    if not DB_FILE.exists():
        build_db()
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM templates ORDER BY rowid").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d.get("options") or "[]")
        out.append(d)
    return out


def template_vars(body):
    """Ordered, de-duplicated list of {VAR} names used in a template body."""
    return list(dict.fromkeys(VAR_RE.findall(body)))


def render(template, values, options=None, extra="", encoding=None):
    """Substitute every {VAR}, append any toggled flags + extra args, then encode.

    values: dict of VAR name -> string. Each var present in the template is
    required (except those in DEFAULTS). HOST_VARS are validated as IP/hostname
    and PORT_VARS as 1-65535. options: flag strings the user toggled on (only
    those declared in the template's own `options` are honored). extra: free
    trailing arguments appended verbatim. encoding: optional per-request override;
    when given it must be a known ENCODERS key and takes precedence over the
    template's own encoding (None means use the template's). Raises ValueError on
    bad/missing input or an unknown encoding.
    """
    body = template["template"]
    resolved = {}
    for var in template_vars(body):
        raw = (values.get(var) or "").strip()
        if not raw:
            if var in DEFAULTS:
                raw = DEFAULTS[var]
            else:
                raise ValueError(f"{var} is required for this template.")
        if var in HOST_VARS and not valid_host(raw):
            raise ValueError(f"{var} must be a valid IP or hostname.")
        if var in PORT_VARS and not valid_port(raw):
            raise ValueError(f"{var} must be a number between 1 and 65535.")
        resolved[var] = str(int(raw)) if var in PORT_VARS else raw

    # Single-pass substitution: replace every {VAR} in one sweep so a value
    # that happens to contain a literal {OTHER_VAR} token is never re-expanded.
    cmd = VAR_RE.sub(lambda m: resolved.get(m.group(1), m.group(0)), body)

    # Append toggled flags (whitelisted to the template's declared options),
    # then any free-form extra arguments.
    allowed = set(template.get("options") or [])
    parts = [cmd] + [f for f in (options or []) if f in allowed]
    if (extra or "").strip():
        parts.append(extra.strip())
    cmd = " ".join(parts)

    chosen = encoding if encoding is not None else template.get("encoding", "raw")
    encoder = ENCODERS.get(chosen)
    if encoder is None:
        raise ValueError(f"Unknown encoding '{chosen}'.")
    return encoder(cmd)


# --- sqlite store -----------------------------------------------------------
# floki.db is the runtime store, rebuilt on startup from the editable seed
# files: templates.json (payload/command templates) and reference_seed.json
# (ports, creds, hashcat modes, gtfobins, wordlists). Add seed rows or new
# tables to fit more data. Column names are trusted (from the code); only
# values are user-supplied, and those go through bound parameters.

def _cols_ddl(cols):
    return ", ".join(f'"{c}"' for c in cols)


def _tpl_cell(tpl, col):
    # options is a JSON list -> store as text; everything else stored as-is.
    if col == "options":
        return json.dumps(tpl.get("options") or [])
    return tpl.get(col)


def build_db():
    con = sqlite3.connect(DB_FILE)
    if TEMPLATES_FILE.exists():
        tpls = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
        con.execute("DROP TABLE IF EXISTS templates")
        con.execute(f"CREATE TABLE templates ({_cols_ddl(TEMPLATE_COLS)})")
        con.executemany(
            f"INSERT INTO templates VALUES ({', '.join('?' for _ in TEMPLATE_COLS)})",
            [tuple(_tpl_cell(t, c) for c in TEMPLATE_COLS) for t in tpls])
    if REFERENCE_SEED.exists():
        seed = json.loads(REFERENCE_SEED.read_text(encoding="utf-8"))
        for table, (cols, _order) in REF_TABLES.items():
            con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute(f"CREATE TABLE {table} ({_cols_ddl(cols)})")
            con.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})",
                [tuple(r.get(c) for c in cols) for r in seed.get(table, [])])
    con.commit()
    con.close()


def _match_tier(row, search_cols, ql):
    """Rank one row against a lowercased query.

    Returns 0 for an exact cell match, 1 for a prefix match, 2 for a substring
    match, or None when no searchable cell matches. The best (lowest) tier wins.
    """
    best = None
    for col in search_cols:
        value = row[col]
        if value is None:
            continue
        text = str(value).lower()
        if text == ql:
            return 0
        if text.startswith(ql):
            best = 1 if best is None else min(best, 1)
        elif ql in text:
            best = 2 if best is None else min(best, 2)
    return best


def search_reference(query):
    result = {t: [] for t in REF_TABLES}
    if not DB_FILE.exists():
        return result
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    q = (query or "").strip()
    ql = q.lower()
    for table, (cols, order) in REF_TABLES.items():
        search = [c for c in cols if c not in NO_SEARCH.get(table, ())]
        # Fetch in the table's natural order first; that order is preserved inside
        # each rank tier, so ranking only ever promotes closer matches.
        cur = con.execute(f'SELECT * FROM {table} ORDER BY "{order}"')
        rows = cur.fetchall()
        if q:
            ranked = []
            for idx, row in enumerate(rows):
                tier = _match_tier(row, search, ql)
                if tier is not None:
                    ranked.append((tier, idx, dict(row)))
            ranked.sort(key=lambda item: (item[0], item[1]))
            result[table] = [r for _, _, r in ranked]
        else:
            result[table] = [dict(row) for row in rows]
    con.close()
    return result


# --- methodology ------------------------------------------------------------

def load_methodology():
    if not METHODOLOGY_FILE.exists():
        return {"phases": []}
    try:
        return json.loads(METHODOLOGY_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"phases": []}


# --- http -------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Floki"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
        elif self.path in ("/favicon.ico", "/favicon.svg"):
            self._send(200, FAVICON_SVG, "image/svg+xml")
        elif self.path == "/api/templates":
            self._json(200, load_templates())
        elif self.path == "/api/methodology":
            self._json(200, load_methodology())
        elif self.path.startswith("/api/reference"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0]
            self._json(200, search_reference(q))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/render":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self._json(413, {"error": "request body too large"})
                return
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"error": "invalid request body"})
            return

        tpl = next((t for t in load_templates()
                    if t["id"] == req.get("template_id")), None)
        if tpl is None:
            self._json(404, {"error": "unknown template_id"})
            return
        override = req.get("encoding")
        try:
            command = render(tpl, req.get("vars") or {},
                             req.get("options") or [], req.get("extra") or "",
                             encoding=override)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        used = override if override is not None else tpl.get("encoding", "raw")
        self._json(200, {"command": command, "notes": tpl.get("notes", ""),
                         "type": tpl["type"], "encoding": used})

    def log_message(self, *args):
        pass  # quiet; local tool


def main():
    host = os.environ.get("FLOKI_HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("FLOKI_PORT", 8000))
    build_db()
    httpd = ThreadingHTTPServer((host, port), Handler)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"Floki running at http://{shown}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
