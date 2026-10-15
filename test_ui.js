#!/usr/bin/env node
'use strict';
// Behavioral regression coverage for the Floki UI. Rather than grepping the
// markup, this loads the real pure-logic block out of index.html (delimited by
// the FLOKI-CORE markers) and exercises it, so the shipped UI and the tests
// share one implementation. No dependencies, no build step.
//
// Run: node test_ui.js   (or: python3 test_ui.py)
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const HTML_PATH = path.join(HERE, 'index.html');

const CORE_EXPORTS = [
  'createRenderGuard', 'buildField', 'buildOptionChip', 'copyOutcome',
  'mobileSelectAction', 'scrollBehavior', 'nextTargetDone',
  'methodologyKey', 'pivotQuery',
];

let checks = 0;
let failures = 0;
function check(name, cond, detail) {
  checks += 1;
  if (cond) {
    console.log('ok   ' + name);
  } else {
    failures += 1;
    console.log('FAIL ' + name + (detail ? ' - ' + detail : ''));
  }
}

function extractCore(html) {
  const startMark = '/* FLOKI-CORE-START */';
  const endMark = '/* FLOKI-CORE-END */';
  const start = html.indexOf(startMark);
  const end = html.indexOf(endMark);
  if (start < 0 || end < 0 || end <= start) {
    throw new Error('FLOKI-CORE markers not found in index.html');
  }
  return html.slice(start, end);
}

// Minimal DOM element stub modelling the operations the field builders use,
// so the tests run without a browser while still driving the real functions.
function makeStubDoc() {
  return {
    createElement(tag) {
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        attributes: {},
        dataset: {},
        style: {},
        className: '',
        value: '',
        placeholder: '',
        type: '',
        id: '',
        classList: {
          _s: new Set(),
          add(c) { this._s.add(c); },
          remove(c) { this._s.delete(c); },
          contains(c) { return this._s.has(c); },
          toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
        },
        setAttribute(k, v) { this.attributes[k] = String(v); return v; },
        getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
        appendChild(c) { this.children.push(c); return c; },
        addEventListener() {},
      };
    },
  };
}

function main() {
  let html;
  try {
    html = fs.readFileSync(HTML_PATH, 'utf8');
  } catch (err) {
    console.log('FAIL cannot read index.html: ' + err.message);
    process.exit(1);
    return;
  }

  let coreSrc;
  try {
    coreSrc = extractCore(html);
  } catch (err) {
    console.log('FAIL ' + err.message);
    process.exit(1);
    return;
  }

  let core;
  try {
    core = new Function(coreSrc + '\nreturn {' + CORE_EXPORTS.join(', ') + '};')();
  } catch (err) {
    console.log('FAIL could not evaluate UI logic from index.html: ' + err.message);
    process.exit(1);
    return;
  }

  // (1) stale/async render guard: a superseded token must never be accepted.
  const guard = core.createRenderGuard();
  const first = guard.bump();
  check('render guard accepts its own token', guard.isCurrent(first) === true);
  guard.bump();
  check('render guard rejects a superseded response', guard.isCurrent(first) === false);

  // (2) arbitrary values round-trip through DOM property assignment.
  const doc = makeStubDoc();
  const tricky = 'p"w<&>';
  const field = core.buildField(doc, {
    id: 'var-USER', label: 'USER', dataName: 'data-var', dataValue: 'USER',
    placeholder: 'a"b<&>', value: tricky, numeric: false, secret: true,
  });
  const input = field.children[1];
  check('field value keeps quotes/tags intact', input.value === tricky, 'got ' + JSON.stringify(input.value));
  check('field placeholder keeps quotes intact', input.placeholder === 'a"b<&>', 'got ' + JSON.stringify(input.placeholder));
  check('field retains data-var', input.getAttribute('data-var') === 'USER');
  check('secret field sets autocomplete=off', input.getAttribute('autocomplete') === 'off');
  check('field label is the variable name', field.children[0].textContent === 'USER');

  const chip = core.buildOptionChip(doc, '--flag="x"');
  const chipInput = chip.children[0];
  check('option chip value keeps quotes intact', chipInput.value === '--flag="x"', 'got ' + JSON.stringify(chipInput.value));

  // (3) copy feedback is truthful about execCommand returning false.
  check('clipboard success reports ok', core.copyOutcome(true, false) === 'ok');
  check('execCommand(false) is not reported as success', core.copyOutcome(false, false) === 'manual');
  check('truthy execCommand fallback reports ok', core.copyOutcome(false, true) === 'ok');
  check('missing clipboards force manual recovery', core.copyOutcome(false, undefined) === 'manual');

  // (5) stage toggles update in place and report completion.
  const target = { root: false, proof: false };
  const doneAfterRoot = core.nextTargetDone(target, 'root', true);
  check('stage toggle mutates the model in place', target.root === true && doneAfterRoot === false);
  const doneAfterProof = core.nextTargetDone(target, 'proof', true);
  check('root+proof marks the card done', target.proof === true && doneAfterProof === true);

  // (6) mobile navigation avoids the soft keyboard; reduced motion is honored.
  check('mobile selection scrolls instead of focusing', core.mobileSelectAction(true) === 'scroll');
  check('desktop selection focuses the first input', core.mobileSelectAction(false) === 'focus');
  check('reduced motion uses instant scrolling', core.scrollBehavior(true) === 'auto');
  check('normal motion allows smooth scrolling', core.scrollBehavior(false) === 'smooth');

  // (7) methodology checklist is scoped per active target, global when none.
  check('methodology key is global with no active target', core.methodologyKey('') === 'floki_done');
  check('methodology key is namespaced per target', core.methodologyKey('box-1') === 'floki_done:box-1');
  check('two targets get distinct methodology keys',
    core.methodologyKey('a') !== core.methodologyKey('b'));

  // (8) pivot helper maps have/want to a filter that hits real tunneling entries.
  check('socks + ssh -> ssh dynamic', core.pivotQuery('ssh', 'socks') === 'ssh dynamic');
  check('local + ssh -> ssh local forward', core.pivotQuery('ssh', 'local') === 'ssh local forward');
  check('remote + ssh -> ssh remote forward', core.pivotQuery('ssh', 'remote') === 'ssh remote forward');
  check('subnet + ssh -> sshuttle', core.pivotQuery('ssh', 'subnet') === 'sshuttle');
  // only a foothold shell (no SSH): chisel is the sole workable option…
  check('socks + shell -> chisel', core.pivotQuery('shell', 'socks') === 'chisel');
  check('local + shell -> chisel', core.pivotQuery('shell', 'local') === 'chisel');
  check('remote + shell -> chisel', core.pivotQuery('shell', 'remote') === 'chisel');
  check('subnet + shell -> chisel', core.pivotQuery('shell', 'subnet') === 'chisel');
  // …except a host redirector, which needs no SSH either way.
  check('redirect + shell -> socat redirect', core.pivotQuery('shell', 'redirect') === 'socat redirect');
  check('redirect + ssh -> socat redirect', core.pivotQuery('ssh', 'redirect') === 'socat redirect');

  console.log('');
  console.log(checks + ' checks, ' + failures + ' failure(s)');
  process.exit(failures === 0 ? 0 : 1);
}

main();
