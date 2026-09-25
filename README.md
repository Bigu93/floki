# Floki

Local reverse-shell / command payload templating utility for **authorized**
penetration-testing engagements. Pick a template, fill in the variables, and
Floki renders the ready-to-run command — encoded for its target context (raw,
URL-encoded, or base64 for PowerShell `-EncodedCommand`).

Floki only **assembles and formats text you curate** in `templates.json`. It
never executes a payload, never targets a host, and makes no outbound calls.
Python 3.7+ standard library only — no dependencies.

## Run

### Local

```bash
python3 server.py            # http://127.0.0.1:8000
python3 server.py 9001       # custom port
```

Binds `127.0.0.1` by default. Set `FLOKI_HOST` / `FLOKI_PORT` to override.

### Docker

```bash
docker build -t floki .
docker run -d -p 8000:8000 --name floki floki
```

### Portainer

**Stacks → Add stack**, then either paste the repo's `docker-compose.yml` or
point the stack at this Git repository. It publishes `8000:8000`; change the
host port in the compose `ports` line if needed.

> ⚠️ The container binds `0.0.0.0` so the port can be published — anyone who
> can reach the published port gets the UI. Publish it only on a network you
> trust (e.g. bind to a specific interface: `-p 127.0.0.1:8000:8000`).

## Use

1. Start your listener first, e.g. `nc -lvnp 4444`.
2. **Navigator** (left): pick a template. **Builder** (center): fill its fields,
   optionally toggle **flags** and add **extra args**, then **Render** → **Copy**.
3. **Dock** (right): **Reference** tab (click any row to copy) and **Methodology**
   tab (interactive checklist + scratchpad, saved in your browser).

**154 starter templates** across Reverse Shells, Listeners, Enumeration, Web,
Brute Force, Active Directory, Databases, Privilege Escalation, File Transfer,
Pivoting, Cracking, and NetExec — OSCP-oriented, grounded in public references
(0xsyr0/OSCP, netexec.wiki, PayloadsAllTheThings). Edit or add your own.

## Data & files

The runtime store is `floki.db` (sqlite, git-ignored), **rebuilt on every
startup** from editable seed files — edit text, restart:

| File | Purpose |
|------|---------|
| `server.py` | stdlib HTTP server: validation, substitution, encoding, sqlite build/search |
| `index.html` | single-page UI (navigator · builder · reference/methodology dock) |
| `templates.json` | payload/command library seed |
| `reference_seed.json` | reference data (ports, creds, hashmodes, gtfobins, wordlists, modules) |
| `methodology.json` | phases/items for the Methodology checklist |
| `test_floki.py` | `python3 test_floki.py` — engine + DB self-check |

Checklist state and the "what you have" scratchpad live in your browser
(localStorage), never on the server.

## Add / edit templates

Add objects to `templates.json`:

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

- **Placeholders:** any `{UPPER_CASE}` token auto-generates an input field.
- **group / category:** two-level sidebar grouping (rendered in file order).
- **encoding:** `raw` = as-is · `url` = percent-encode · `base64` = UTF-16LE
  base64 for PowerShell `-EncodedCommand` (run as `powershell -e <output>`).
- **options:** optional flag chips; only declared flags are honored. The
  **Extra args** box appends anything else verbatim.

Validation on render: `LHOST`/`RHOST`/`DC_IP` must be a valid IP or hostname;
`LPORT`/`RPORT` must be 1–65535; other vars are required free-text (`SHELL`
defaults to `/bin/bash`). Bad input fails with a clear message.

## Notes

- For use only against systems you are explicitly authorized to test.
- Payloads are standard, publicly documented one-liners — curate your own.
- `floki.db` and any local `reference/` notes are git-ignored — keep engagement
  data out of the repo.
