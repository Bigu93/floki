# Floki

Local command/payload workbench for **authorized** penetration testing and
OSCP/HTB prep. Pick a template, fill the variables, and Floki renders the
ready-to-run command — encoded for its context (raw, URL, or PowerShell base64).

It only **assembles text you curate** in `templates.json` — never executes,
never targets a host, no outbound calls. Python 3.7+ stdlib only, no deps.

## Run

```bash
python3 server.py            # http://127.0.0.1:8000  (custom port: server.py 9001)
```

Binds `127.0.0.1`; override with `FLOKI_HOST` / `FLOKI_PORT`.

**Docker:** `docker build -t floki . && docker run -d -p 8000:8000 floki`
**Portainer:** *Stacks → Add stack*, paste `docker-compose.yml` or point it at this repo.

> ⚠️ The container binds `0.0.0.0` so the port is publishable — anyone who can
> reach it gets the UI. Publish only on a trusted network (e.g. `-p 127.0.0.1:8000:8000`).

## Use

1. Start your listener first (`nc -lvnp 4444`).
2. **Globals** (top of Builder): set `LHOST`, `DOMAIN`, `USER`… once — every template prefills from them.
3. **Templates** (left): pick one, or filter (`/`) and re-group by **Group / Phase / Tool / Platform**.
4. **Builder** (center): fill fields, toggle **flags**, add **extra args**, then **Render** → **Copy**.
5. **Dock** (right), four tabs — click any row to copy:
   - **Reference** — ports, creds, hashcat modes, GTFOBins, LOLBAS, NSE, wordlists, nxc modules.
   - **Targets** — per-box tracker (recon → foothold → root → proof, points). Hit **Use** to push a box's IP into the globals.
   - **History** — every render this session; re-copy or export.
   - **Methodology** — engagement checklist + scratchpad.

**200+ templates** across Reverse Shells, Listeners, Enumeration, Web Attacks,
Brute Force, Active Directory, Databases, Privilege Escalation, File Transfer,
Pivoting, Cracking, and NetExec — OSCP-oriented, grounded in public references
(0xsyr0/OSCP, netexec.wiki, PayloadsAllTheThings). Edit or add your own.

Tracker, history, checklist, and globals live in your browser (localStorage), never on the server.

## Edit the data

`floki.db` (sqlite, git-ignored) is **rebuilt from the seed files on every
startup** — edit text, restart:

| File | Purpose |
|------|---------|
| `templates.json` | payload/command library |
| `reference_seed.json` | reference tables (ports, creds, hashmodes, gtfobins, lolbas, nse, wordlists, modules) |
| `methodology.json` | methodology checklist phases |
| `server.py` · `index.html` | stdlib server · single-page UI |
| `test_floki.py` | `python3 test_floki.py` — engine + DB self-check |

Add a template object to `templates.json`:

```json
{
  "id": "unique-id",
  "group": "NetExec (nxc)",
  "category": "SMB",
  "name": "nxc smb — enumerate",
  "type": "nxc",
  "encoding": "raw",
  "template": "nxc smb {TARGET} -u {USER} -p {PASS}",
  "options": ["--shares", "--users", "--sam"],
  "notes": "Anything worth remembering."
}
```

- **Placeholders:** any `{UPPER_CASE}` token auto-generates a field.
- **encoding:** `raw` · `url` (percent-encode) · `base64` (UTF-16LE for `powershell -e`).
- **options:** optional flag chips (only declared flags are honored); **Extra args** appends anything verbatim.
- **Validation:** `LHOST`/`RHOST`/`DC_IP` must be a valid IP/host, `LPORT`/`RPORT` 1–65535; other vars are free-text.

## Notes

- Use only against systems you are explicitly authorized to test.
- Payloads are standard, publicly documented one-liners — curate your own.
- Keep engagement data out of the repo (`floki.db` and any `reference/` notes are git-ignored).
