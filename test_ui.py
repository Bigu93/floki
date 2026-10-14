#!/usr/bin/env python3
"""Acceptance entrypoint for the Floki UI regression checks.

The regressions these fixes target are DOM-orchestration behaviours (render
invalidation on edit/template/target change, superseded in-flight responses,
truthful manual-copy recovery, fetch error-vs-empty + Retry, focus retention),
which are only meaningful against a real browser. So this runner drives the
live index.html script in headless Chrome with `fetch`, `navigator.clipboard`
and `document.execCommand` stubbed — no network, no dependencies.

When Chrome is available it runs the full-page suite at a desktop and a mobile
viewport, then (if present) the pure-logic helper suite under Node. When Chrome
is missing but Node exists, only the helper suite runs and the run says so. If
neither runtime exists the run FAILS rather than reporting a hollow pass.

Run: python3 test_ui.py
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
JS_TEST = HERE / "test_ui.js"

CHROME_NAMES = (
    "google-chrome", "google-chrome-stable", "chromium",
    "chromium-browser", "chrome", "headless-chrome",
)


# --- pure-logic helper suite (Node) -----------------------------------------

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
    print("test_ui: pure-logic helper suite passed (node)")
    return 0


# --- full-page browser suite (Chrome) ---------------------------------------

def find_chrome():
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


# Runs before the app script: replaces all network + clipboard side effects with
# deterministic, inspectable stubs. Render responses are handed back one by one
# through window.__floki.renderDeferred so out-of-order completion is testable.
BOOTSTRAP_JS = r"""
window.__floki = {
  renderDeferred: [],
  refMode: 'empty',
  tplMode: 'ok',
  templates: [
    {id:'t-var', group:'Regression', category:'Core', name:'With Input', type:'tool', encoding:'raw', template:'echo {TARGET}', notes:'a note', options:['--flag']},
    {id:'t-novar', group:'Regression', category:'Core', name:'No Inputs', type:'tool', encoding:'raw', template:'echo ready', notes:'', options:[]}
  ],
  methodology: {phases:[{phase:'Phase One', items:['first step']}]},
  refData: {ports:[{port:22,proto:'tcp',service:'ssh',notes:'secure shell'}], creds:[], hashmodes:[], gtfobins:[], wordlists:[], modules:[], lolbas:[], nse:[]},
  emptyRef: {ports:[], creds:[], hashmodes:[], gtfobins:[], wordlists:[], modules:[], lolbas:[], nse:[]}
};
function __flokiResp(obj){ return {ok:true, status:200, json:function(){ return Promise.resolve(obj); }}; }
function __flokiErr(code, msg){ return {ok:false, status:code, json:function(){ return Promise.resolve({error:msg}); }}; }
window.fetch = function(url){
  url = String(url);
  if (url.indexOf('/api/templates') === 0) {
    if (window.__floki.tplMode === 'error') return Promise.resolve(__flokiErr(500, 'templates unavailable'));
    return Promise.resolve(__flokiResp(window.__floki.templates));
  }
  if (url.indexOf('/api/methodology') === 0) return Promise.resolve(__flokiResp(window.__floki.methodology));
  if (url.indexOf('/api/reference') === 0) {
    if (window.__floki.refMode === 'error') return Promise.resolve(__flokiErr(500, 'reference unavailable'));
    if (window.__floki.refMode === 'empty') return Promise.resolve(__flokiResp(window.__floki.emptyRef));
    return Promise.resolve(__flokiResp(window.__floki.refData));
  }
  if (url.indexOf('/api/render') === 0) {
    return new Promise(function(resolve){ window.__floki.renderDeferred.push(resolve); });
  }
  return Promise.resolve(__flokiErr(404, 'not found'));
};
try {
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:function(){ return Promise.reject(new Error('clipboard blocked')); }}});
} catch (e) {}
try { document.execCommand = function(){ return false; }; } catch (e) {}
"""

# Runs after the app script: drives the real UI and reports PASS/FAIL lines
# between FLOKI_UI_BEGIN/END so the Python side can aggregate them.
BROWSER_TESTS = r"""
(function(){
  var out = [], checks = 0, failures = 0;
  function ck(name, cond, detail){
    checks++;
    out.push((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : ' | ' + (detail == null ? '' : detail)));
    if (!cond) failures++;
  }
  function $(s){ return document.querySelector(s); }
  function tick(){ return new Promise(function(r){ setTimeout(r, 0); }); }
  function until(fn, ms){
    return new Promise(function(res, rej){
      var t0 = Date.now();
      (function loop(){
        try { if (fn()) return res(); } catch (e) {}
        if (Date.now() - t0 > (ms || 5000)) return rej(new Error('timeout'));
        setTimeout(loop, 15);
      })();
    });
  }
  function resp(obj){ return {ok:true, status:200, json:function(){ return Promise.resolve(obj); }}; }
  function tpl(id){ return document.querySelector('#tplList button[data-id="' + id + '"]'); }
  function pick(id){ var b = tpl(id); if (b) b.click(); return b; }
  var isMobile = window.matchMedia('(max-width: 820px)').matches;
  var F = window.__floki;

  (async function(){
    try {
      await until(function(){ return tpl('t-var') && tpl('t-novar'); });

      // (1) render, then invalidate on edit and on template change
      pick('t-var');
      ck('select-reveals-form', $('#genForm').hidden === false);
      ck('select-clears-copy', $('#copyBtn').disabled === true);
      $('#var-TARGET').value = '10.0.0.1';
      $('#genForm').requestSubmit();
      await tick();
      ck('render-busy-while-pending', $('#outputWrap').getAttribute('aria-busy') === 'true');
      F.renderDeferred[F.renderDeferred.length - 1](resp({command:'CMD-1', notes:'n', type:'tool', encoding:'raw'}));
      await until(function(){ return $('#cmd').textContent === 'CMD-1'; });
      ck('render-shows-command', $('#cmd').textContent === 'CMD-1');
      ck('render-clears-busy', !$('#outputWrap').hasAttribute('aria-busy'));
      ck('render-enables-copy', $('#copyBtn').disabled === false);
      ck('render-writes-history', $('#histList').textContent.indexOf('CMD-1') !== -1);

      var inp = $('#var-TARGET');
      inp.value = '10.0.0.2';
      inp.dispatchEvent(new Event('input', {bubbles:true}));
      ck('edit-disables-copy', $('#copyBtn').disabled === true);
      ck('edit-marks-stale', $('#cmd').classList.contains('stale'));
      ck('edit-prompts-rerender', /render again/i.test($('#msg').textContent));

      pick('t-novar');
      ck('template-change-disables-copy', $('#copyBtn').disabled === true);
      ck('template-change-clears-output', $('#cmd').textContent.indexOf('CMD-1') === -1);

      // (2) superseded in-flight responses are dropped, newest wins
      pick('t-var');
      $('#var-TARGET').value = '10.0.0.3';
      $('#genForm').requestSubmit();
      await tick();
      $('#genForm').requestSubmit();
      await tick();
      var n = F.renderDeferred.length;
      F.renderDeferred[n - 1](resp({command:'CMD-B', notes:'', type:'tool', encoding:'raw'}));
      await until(function(){ return $('#cmd').textContent === 'CMD-B'; });
      F.renderDeferred[n - 2](resp({command:'CMD-A', notes:'', type:'tool', encoding:'raw'}));
      await tick(); await tick();
      ck('out-of-order-latest-wins', $('#cmd').textContent === 'CMD-B', $('#cmd').textContent);
      ck('out-of-order-stale-not-history', $('#histList').textContent.indexOf('CMD-A') === -1);
      ck('out-of-order-stale-keeps-copy', $('#copyBtn').disabled === false);

      // (3) an older response must not clear the pending newer render's status
      $('#genForm').requestSubmit();
      await tick();
      $('#genForm').requestSubmit();
      await tick();
      var m = F.renderDeferred.length;
      F.renderDeferred[m - 2](resp({command:'CMD-C', notes:'', type:'tool', encoding:'raw'}));
      await tick(); await tick();
      ck('older-response-keeps-pending-busy', $('#outputWrap').getAttribute('aria-busy') === 'true');
      ck('older-response-no-output', $('#cmd').textContent !== 'CMD-C');
      F.renderDeferred[m - 1](resp({command:'CMD-D', notes:'', type:'tool', encoding:'raw'}));
      await until(function(){ return $('#cmd').textContent === 'CMD-D'; });
      ck('newer-response-clears-busy', !$('#outputWrap').hasAttribute('aria-busy'));

      // (4) arbitrary values (quotes/tags) round-trip with no attribute break-out
      try { window.localStorage.setItem('floki_globals', JSON.stringify({TARGET:'p"w<&>'})); } catch (e) {}
      pick('t-novar');
      pick('t-var');
      ck('quoted-global-roundtrip', $('#var-TARGET').value === 'p"w<&>', JSON.stringify($('#var-TARGET').value));

      // (5) reference: empty success is not an error; failure shows Retry that works
      F.refMode = 'empty';
      await window.loadReference('');
      ck('ref-empty-not-error', $('#refStatus').hidden === true && /No reference entries/.test($('#refResults').textContent));
      F.refMode = 'error';
      await window.loadReference('zzz');
      ck('ref-error-visible', $('#refStatus').hidden === false && $('#refStatus').classList.contains('err'), $('#refStatus').className);
      ck('ref-error-retry', !!$('#refStatus .retry'));
      F.refMode = 'data';
      $('#refStatus .retry').click();
      await until(function(){ return $('#refResults').textContent.indexOf('ssh') !== -1; });
      ck('ref-retry-loads-data', $('#refResults').textContent.indexOf('ssh') !== -1 && $('#refStatus').hidden === true);

      // templates failure is visible (not swallowed inside a hidden form) and recoverable
      F.tplMode = 'error';
      await window.loadTemplates();
      ck('templates-error-visible', $('#tplStatus').hidden === false && $('#tplStatus').classList.contains('err') && !!$('#tplStatus .retry'));
      F.tplMode = 'ok';
      $('#tplStatus .retry').click();
      await until(function(){ return $('#tplStatus').hidden === true && !!tpl('t-var'); });
      ck('templates-retry-loads', $('#tplStatus').hidden === true && !!tpl('t-var'));

      // (6) manual-copy recovery exposes the exact text for builder/reference/history
      pick('t-var');
      $('#var-TARGET').value = '10.0.0.4';
      $('#genForm').requestSubmit();
      await tick();
      var k = F.renderDeferred.length;
      F.renderDeferred[k - 1](resp({command:'CMD-BUILDER', notes:'', type:'tool', encoding:'raw'}));
      await until(function(){ return $('#cmd').textContent === 'CMD-BUILDER'; });
      $('#copyBtn').click();
      await until(function(){ return $('#manualCopy').hidden === false; });
      ck('builder-manual-copy-exact', $('#manualCopyText').value === 'CMD-BUILDER', $('#manualCopyText').value);
      ck('builder-manual-copy-focus', document.activeElement === $('#manualCopyText'));

      var refRow = $('#refResults .ref-row');
      ck('reference-row-present', !!refRow);
      if (refRow) refRow.click();
      await until(function(){ return $('#manualCopyText').value === '22'; });
      ck('reference-manual-copy-exact', $('#manualCopyText').value === '22', $('#manualCopyText').value);
      ck('reference-manual-copy-not-builder', $('#manualCopyText').value !== $('#cmd').textContent);

      var histRows = document.querySelectorAll('#histList .hist-row');
      ck('history-row-present', histRows.length > 0);
      if (histRows.length) {
        var histRow = histRows[histRows.length - 1];
        var histText = histRow.title.replace(/^Copy: /, '');
        histRow.click();
        await until(function(){ return $('#manualCopyText').value === histText; });
        ck('history-manual-copy-exact', $('#manualCopyText').value === histText, $('#manualCopyText').value);
      }

      // (7) target stage toggle keeps keyboard focus (no list rebuild)
      $('#tab-targets').click();
      await tick();
      ck('target-tab-reveals-panel', $('#panel-targets').hidden === false);
      $('#tgName').value = 'box01';
      $('#tgIp').value = '10.0.0.9';
      $('#tgPts').value = '10';
      $('#targetForm').requestSubmit();
      await tick();
      var cb = $('#targetList .tg-card input[data-stage="recon"]');
      ck('target-card-created', !!cb);
      if (cb) {
        cb.focus();
        cb.checked = true;
        cb.dispatchEvent(new Event('change', {bubbles:true}));
        await tick();
        ck('target-stage-keeps-focus', document.activeElement === cb);
        ck('target-stage-still-in-dom', document.body.contains(cb));
      }

      // (8) responsive navigation
      if (isMobile) {
        pick('t-var');
        var builderEl = document.querySelector('.builder');
        ck('mobile-focus-builder', document.activeElement === builderEl);
        ck('mobile-input-not-autofocused', document.activeElement !== $('#var-TARGET'));
        ck('mobile-back-visible', $('#backToTpl').hidden === false);
        $('#backToTpl').click();
        await tick();
        ck('mobile-back-restores-template-focus', document.activeElement === tpl('t-var'));
        pick('t-novar');
        ck('mobile-no-input-still-navigates', document.activeElement === document.querySelector('.builder'));
      } else {
        pick('t-var');
        ck('desktop-focuses-first-input', document.activeElement === $('#var-TARGET'));
      }
    } catch (e) {
      ck('harness-exception', false, e && (e.message || String(e)));
    }
    var pre = document.getElementById('FLOKI_UI_RESULT');
    pre.textContent = '\n' + 'FLOKI_UI_' + 'BEGIN' + '\n' + out.join('\n') + '\n' + 'FLOKI_UI_' + 'END' + '\n';
  })();
})();
"""


def _integration_page(html):
    if "</head>" not in html or "</body>" not in html:
        raise ValueError("index.html is missing </head> or </body>")
    page = html.replace("</head>", "<script>" + BOOTSTRAP_JS + "</script>\n</head>", 1)
    page = page.replace(
        "</body>",
        '<pre id="FLOKI_UI_RESULT"></pre>\n<script>' + BROWSER_TESTS + "</script>\n</body>",
        1,
    )
    return page


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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _start_server(directory):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def _run_chrome_once(chrome, url, window_size, profile):
    for headless in ("--headless=new", "--headless"):
        try:
            proc = subprocess.run(
                [chrome, headless, "--disable-gpu", "--no-sandbox",
                 "--disable-dev-shm-usage", "--hide-scrollbars",
                 "--force-device-scale-factor=1",
                 "--window-size=" + window_size,
                 "--user-data-dir=" + profile,
                 "--virtual-time-budget=10000", "--dump-dom", url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, errors="replace", timeout=90,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        result = parse_dump(proc.stdout or "")
        if result is not None:
            return result
    return None


def run_chrome():
    chrome = find_chrome()
    if not chrome:
        return None
    try:
        html = INDEX.read_text(encoding="utf-8")
        page = _integration_page(html)
    except (OSError, ValueError) as exc:
        print("test_ui: FAILED — " + str(exc))
        return 1

    tmpdir = tempfile.mkdtemp(prefix="floki-ui-")
    httpd = None
    try:
        Path(os.path.join(tmpdir, "floki-ui-test.html")).write_text(page, encoding="utf-8")
        httpd, port = _start_server(tmpdir)
        url = "http://127.0.0.1:%d/floki-ui-test.html" % port
        ok = True
        for label, size in (("desktop", "1280,900"), ("mobile", "390,844")):
            profile = os.path.join(tmpdir, "profile-" + label)
            os.makedirs(profile, exist_ok=True)
            code = _run_chrome_once(chrome, url, size, profile)
            if code is None:
                print("test_ui: FAILED — headless chrome produced no parseable output (%s viewport)" % label)
                ok = False
                break
            if code != 0:
                print("test_ui: FAILED (headless chrome, %s viewport)" % label)
                ok = False
                break
        if ok:
            print("test_ui: all passed (headless chrome; desktop + mobile viewports)")
        return 0 if ok else 1
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    chrome = find_chrome()
    node = shutil.which("node") or shutil.which("nodejs")
    if chrome:
        rc = run_chrome()
        if rc != 0:
            return rc
        if node:
            return run_node(node)
        return 0
    if node:
        print("test_ui: note — headless Chrome not found; running the pure-logic helper suite only.")
        print("test_ui: note — full-page UI orchestration is NOT covered in this run.")
        return run_node(node)
    print("test_ui: FAILED — no JavaScript runtime found (need headless Chrome or Node).")
    print("         Install headless Chrome to run the full-page UI regression suite.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
