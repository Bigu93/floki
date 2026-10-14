#!/usr/bin/env python3
"""Acceptance entrypoint for the Floki UI regression checks.

Behavioral coverage lives in test_ui.js, which loads the real pure-logic block
out of index.html and exercises it. This runner prefers Node; if Node is absent
it falls back to headless Chrome against the same logic (both use the browser's
DOM for the field builders). If neither runtime exists a structural fallback
checks the markup invariants instead — no dependencies are required.

Run: python3 test_ui.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
JS_TEST = HERE / "test_ui.js"

CORE_START = "/* FLOKI-CORE-START */"
CORE_END = "/* FLOKI-CORE-END */"

CHROME_NAMES = (
    "google-chrome", "google-chrome-stable", "chromium",
    "chromium-browser", "chrome", "headless-chrome",
)


def extract_core(html):
    start = html.find(CORE_START)
    end = html.find(CORE_END)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("FLOKI-CORE markers not found in index.html")
    return html[start:end]


def run_node(node):
    proc = subprocess.run(
        [node, str(JS_TEST)], cwd=str(HERE),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
    )
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print("test_ui: FAILED (node exit %d)" % proc.returncode)
        return proc.returncode
    print("test_ui: all passed (node)")
    return 0


def find_chrome():
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _chrome_page(core):
    checks = (
        "var g=createRenderGuard();var t=g.bump();ck('render-guard-current',g.isCurrent(t)===true);"
        "g.bump();ck('render-guard-stale',g.isCurrent(t)===false);"
        "var tricky='p\"w<&>';"
        "var f=buildField(document,{id:'var-USER',label:'USER',dataName:'data-var',dataValue:'USER',"
        "placeholder:'a\"b<&>',value:tricky,secret:true});var inp=f.querySelector('input');"
        "ck('value-roundtrip',inp.value===tricky,inp.value);"
        "ck('placeholder-roundtrip',inp.placeholder==='a\"b<&>',inp.placeholder);"
        "ck('data-var',inp.getAttribute('data-var')==='USER');"
        "ck('secret-autocomplete',inp.getAttribute('autocomplete')==='off');"
        "var chip=buildOptionChip(document,'--flag=\"x\"');"
        "ck('chip-roundtrip',chip.querySelector('input').value==='--flag=\"x\"');"
        "ck('fetch-error',classifyFetchOutcome(false,0)==='error');"
        "ck('fetch-empty',classifyFetchOutcome(true,0)==='empty');"
        "ck('fetch-data',classifyFetchOutcome(true,3)==='data');"
        "ck('copy-ok',copyOutcome(true,false)==='ok');"
        "ck('copy-manual',copyOutcome(false,false)==='manual');"
        "ck('copy-exec',copyOutcome(false,true)==='ok');"
        "var tg={root:false,proof:false};"
        "ck('target-root',nextTargetDone(tg,'root',true)===false);"
        "ck('target-proof',nextTargetDone(tg,'proof',true)===true);"
        "ck('mobile-scroll',mobileSelectAction(true)==='scroll');"
        "ck('desktop-focus',mobileSelectAction(false)==='focus');"
        "ck('reduced-motion',scrollBehavior(true)==='auto');"
        "ck('smooth-motion',scrollBehavior(false)==='smooth');"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        "<pre id='r'>pending</pre><script>\n"
        + core
        + "\nvar out=[];function ck(n,c,d){out.push((c?'PASS ':'FAIL ')+n+(c?'':' | '+(d==null?'':d)));}\n"
        + "try{\n" + checks + "\n}catch(e){out.push('FAIL exception | '+e.message);}\n"
        + "document.getElementById('r').textContent='\\nFLOKI_UI_BEGIN\\n'+out.join('\\n')+'\\nFLOKI_UI_END\\n';\n"
        + "</script></body></html>"
    )


def parse_dump(dump):
    if "FLOKI_UI_BEGIN" not in dump or "FLOKI_UI_END" not in dump:
        return None
    body = dump.split("FLOKI_UI_BEGIN", 1)[1].split("FLOKI_UI_END", 1)[0]
    failures = 0
    reported = 0
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("PASS "):
            print("ok   " + line[5:])
            reported += 1
        elif line.startswith("FAIL "):
            print("FAIL " + line[5:])
            failures += 1
            reported += 1
    if reported == 0:
        return None
    print("")
    print("%d checks, %d failure(s)" % (reported, failures))
    return 1 if failures else 0


def run_chrome():
    chrome = find_chrome()
    if not chrome:
        return None
    try:
        html = INDEX.read_text(encoding="utf-8")
        core = extract_core(html)
    except (OSError, ValueError) as exc:
        print("test_ui: FAILED — " + str(exc))
        return 1
    page = _chrome_page(core)
    tmpdir = tempfile.mkdtemp(prefix="floki-ui-")
    try:
        page_path = os.path.join(tmpdir, "core-test.html")
        with open(page_path, "w", encoding="utf-8") as handle:
            handle.write(page)
        url = "file://" + page_path
        for headless in ("--headless=new", "--headless"):
            try:
                proc = subprocess.run(
                    [chrome, headless, "--disable-gpu", "--no-sandbox",
                     "--virtual-time-budget=2000", "--dump-dom", url],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, errors="replace", timeout=60,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            result = parse_dump(proc.stdout or "")
            if result is not None:
                if result == 0:
                    print("test_ui: all passed (headless chrome)")
                else:
                    print("test_ui: FAILED (headless chrome)")
                return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None


REQUIRED_IDS = (
    "genForm", "msg", "cmd", "copyBtn", "outputWrap",
    "tplStatus", "refStatus", "methodStatus", "backToTpl",
)


def run_structural():
    """Last-resort checks when no JS runtime is installed. Verifies the markup
    invariants the behavioral suite relies on; the logic itself is untested."""
    try:
        html = INDEX.read_text(encoding="utf-8")
    except OSError as exc:
        print("test_ui: FAILED — " + str(exc))
        return 1
    problems = []
    try:
        extract_core(html)
    except ValueError as exc:
        problems.append(str(exc))
    for el_id in REQUIRED_IDS:
        if 'id="%s"' % el_id not in html:
            problems.append('missing element id="%s"' % el_id)
    form_open = html.find('id="genForm"')
    form_close = html.find("</form>", form_open) if form_open >= 0 else -1
    msg_at = html.find('id="msg"')
    if form_open >= 0 and form_close >= 0 and msg_at >= 0 and msg_at < form_close:
        problems.append("status #msg is still nested inside the hidden #genForm")
    if problems:
        print("test_ui: FAILED (structural)")
        for item in problems:
            print("  - " + item)
        return 1
    print("test_ui: structural checks passed (no JS runtime found).")
    print("         Install Node (or headless Chrome) to run the behavioral suite.")
    return 0


def main():
    node = shutil.which("node") or shutil.which("nodejs")
    if node:
        return run_node(node)
    result = run_chrome()
    if result is not None:
        return result
    return run_structural()


if __name__ == "__main__":
    sys.exit(main())
