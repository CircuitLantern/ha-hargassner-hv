# Client development plan — Hargassner Smart HV 2.7.4 HTTP API

Status: draft for review
Companion document: [`boiler-http-api.md`](boiler-http-api.md) (reverse-engineered API reference)
Target firmware: `smart_hv_2.7.4` (`data/app/bin/app`, ARM 32-bit Qt 5)
Target transport: `http://<boiler>:80/` — plain HTTP, no TLS

This document is a **plan**, not a client. It sequences the work from firmware
investigation through protocol validation to a high-level client design, and it
defines the gates that must pass before the next phase starts.

---

## 0. How to read this plan

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| `[CODE]` | Established from the decompiled firmware (`app.kuna`). |
| `[VERIFY]` | Derived from code but must be confirmed on a live unit. |
| `[UNKNOWN]` | Not yet determined; needs investigation or hardware. |

Guiding principles:

1. **The boiler is a piece of heating equipment, not a lab toy.** Reads are
   cheap; writes can move actuators, change operating mode, or wipe
   configuration. Every phase treats writes as a separate, gated capability.
2. **The server has global state.** Challenge, rate limit, `daqOnly` routing and
   the service-mode side effect are device-wide. The client must be a polite
   single-tenant citizen and must not assume it is alone.
3. **The API is discovered, not specified.** The only contract is the binary.
   Anything not exercised against hardware stays marked `[VERIFY]`.
4. **Prefer observation over assumption.** Capture real responses, diff them
   against the mock, and keep the API document current.

Non-goals for this plan:

- A polished consumer UI.
- Cloud connectivity, remote access, or TLS termination at the boiler.
- Reimplementing the boiler controller or safety logic.

---

## 1. Executive summary

The port-80 service is a QtWebApp HTTP server with a small, discoverable REST-ish
API. Authentication is a challenge/response handshake whose signing salt is
hard-coded in the firmware, so it provides access control only, not secrecy.
Standard read/write routes remain available with `daqOnly=true`; that value also
enables `/channels` and avoids requesting SERVICE. Authenticating with
`daqOnly=false` (or omitting it) requests **SERVICE mode**
(`SystemModel::setRequestedMode(6)` `[CODE]`).

The recommended delivery path is:

```
Phase 0  Safety + lab setup
Phase 1  Firmware investigation (fill remaining gaps)
Phase 2  Protocol validation with curl on real hardware
Phase 3  Mock server + contract harness
Phase 4  Read-only client MVP
Phase 5  Write + safety layer
Phase 6  Hardening, observability, packaging
Phase 7  Field validation + handover
```

The client should be built as a small layered library:

- **Transport** — HTTP/1.1, cookie handling, reason-phrase capture, timeouts.
- **Auth manager** — serialised challenge/response, cooldown handling, idle
  keepalive, and tracking of the global `daqOnly` channel-route flag.
- **Typed endpoint APIs** — system, parameters, channels, devices, maintenance.
- **Safety policy** — capability tiers, interlocks, confirmations, audit log.
- **Scheduler + store** — polling, caching, time series.
- **Front end** — CLI/TUI first; any GUI or HTTP facade comes later.

The highest-risk unknowns are behavioural, not cryptographic:

- Does `daqOnly=false` really switch the boiler to SERVICE mode, and can a client
  detect/restore the prior mode? `[VERIFY]`
- Does the 2.7.4 runtime match the dispatcher behavior reconstructed from
  `ServiceRequestHandler::service`: `daqOnly=true` checks `/channels` first and
  then falls through to the standard route vector? `[VERIFY]` against 2.7.4
- Are parameter semantics and ranges safe to change programmatically? `[VERIFY]`
- Can two authenticated clients coexist without fighting over the global
  challenge and `daqOnly`? `[VERIFY]`

These are resolved in Phases 1–2 before any write code is written.

### Current live validation

The checked-in probe captures confirm that a default (no `--full`)
`daqOnly=true` login can retrieve `/channels`, `/info`, and `/parameters` with
`200 application/json` responses. `captures/info.sample.jsonc` identifies that
unit as `smart_hv_3.4.0`; these are not live tests of the 2.7.4 binary. No
`daqOnly=false` login was performed, and no SERVICE-mode test was performed.
See [`boiler-http-api.md`](boiler-http-api.md#7-verification-checklist-for-a-live-unit).

---

## 2. Ground truth and operating constraints

### 2.1 Server facts

| Property | Value | Source |
|---|---|---|
| Port | `80` | `[CODE]` |
| Protocol | HTTP/1.1, plain | `[CODE]` |
| Server library | Stefan Frings QtWebApp (`HttpListener`) | `[CODE]` |
| Threads | min 1, max 10 | `[CODE]` |
| Read timeout | 60,000 ms | `[CODE]` |
| Max request | 30,000,000 bytes | `[CODE]` |
| Static files | none (`404 no valid route`) | `[CODE]` |
| Success body | compact JSON via `RouteHandler::writeObject` | `[CODE]` |
| Error body | empty; message is the HTTP **reason phrase** | `[CODE]` |
| Auth cookie | `auth-cookie`, 24 h `Max-Age`, 5 min idle | `[CODE]` |
| Challenge rate limit | 5,000 ms, global | `[CODE]` |

### 2.2 Authentication in one paragraph

`GET /challenge` returns a 64-hex-character nonce. The client computes
`SHA512(ascii(challenge) + SALT)` where `SALT` is the fixed 64-hex string in
`boiler-http-api.md` §2.3, and posts `{"key": <hex digest>, "daqOnly": <bool>}`
to `/authenticate`. Success is `204` after a ~1 s server-side sleep plus a
`Set-Cookie: auth-cookie=<64 hex>`. The cookie maps to an idle timer of 300 s
that is refreshed by every authenticated request. Only one challenge exists at a
time and the 5 s limit is global.

### 2.3 Side effects and global state

These are the constraints that shape the whole design:

1. **`daqOnly=false` is a mode change.** Code evidence says it (or omitting
   `daqOnly`) requests SERVICE mode: automatic control is suspended, outputs are
   forced off, and the mode is latched until a reboot. This has not been tested
   on hardware; assume the boiler's behavior changes. `[CODE; live VERIFY pending]`
2. **`daqOnly` is a global channel-route switch, not an access-control mode.**
   Code checks the special `/channels` handler when true, then falls through to
   the standard route vector if that handler does not match. Captures from
   3.4.0 show `/channels`, `/info`, and `/parameters` work with true. Verify this
   behavior on 2.7.4 and assess multi-client effects. `[CODE; 3.4.0 live check]`
3. **The challenge slot is global.** Concurrent logins interfere.
4. **There is no API to read either global flag.** The client must track what it
   set and must treat the device as single-tenant. `[VERIFY]` that `/info`
   or another endpoint does not leak the current mode.
5. **The session map is process-global.** Restarting the app invalidates all
   cookies (does the device restart the app on mode changes? `[VERIFY]`).

### 2.4 Security posture

- The salt is public; anyone with network access can authenticate.
- No TLS, no CSRF token, no per-client authorisation.
- The inspected HTTP scalar parameter-write chain does not compare catalog
  `securityLevel` with caller authorization; the UI's level filter is separate.
  Do not use `USER` or `daqOnly=true` as the client's authorization boundary.
- Recommendation: run the boiler on an isolated management VLAN, reachable only
  from the client host, and never port-forward port 80.

### 2.5 Customer parameter writes

The analyzed 2.7.4 implementation supports real parameter changes through
`POST /parameters` while authenticated with `daqOnly=true`. It looks up each
actual parameter `name`, dispatches `updateFromJson` to the main control thread,
runs the typed setter, emits change signals, invokes file/EEPROM save paths,
and marks the system configured. The handler does not require or unconditionally
request SERVICE; the explicit authentication side effect belongs to
`daqOnly=false`. The non-GET branch also handles PUT in 2.7.4. `[CODE]`

Customer-facing catalog entries include room and hot-water temperature
setpoints, heating/loading/circulation schedules, and display settings. The
complete table in [`boiler-http-api.md` §5.2](boiler-http-api.md#52-parameters)
contains all 44 USER entries from the 3.4.0 sample, identifying the 39 names
independently defined as USER by the 2.7.4 provider. Inactive entries and the
five capture-only additions are included; none is automatically safe to write.

Design consequences:

- Select entries by API `name`, never by display label or channel name.
- Send only intended `name`/`value` entries; do not round-trip the full catalog.
- Enforce type, range, increment, and an explicit allowlist in the client.
- Do not trust `200` alone: float JSON parsing can succeed even when the setter
  rejects an out-of-range value. Compare returned and subsequent GET values.
- Batches are sequential and have no rollback. Prefer one change per request
  during validation; reconcile each entry before any retry.
- Parameter callbacks can affect heating immediately. Persistence and physical
  effects require controlled hardware checks; no successful live write capture
  is recorded. `[VERIFY]`

---

## 3. Phase map

| Phase | Goal | Primary output | Exit gate |
|---|---|---|---|
| 0 | Do no harm | Lab network, access rules, baseline | Device reachable only from client host; rollback known |
| 1 | Close code gaps | Updated API notes, symbol map, open-question list | Every handler and side effect accounted for |
| 2 | Validate reality | curl transcript + observation log | Test matrix green or discrepancies documented |
| 3 | Remove hardware from the loop | Mock server + contract tests | Client passes against mock byte-for-byte |
| 4 | Read-only MVP | Library + CLI that only GETs | 24 h read soak, no unintended side effects |
| 5 | Safe writes | Policy engine + audited actions | Write suite passes with interlocks proven |
| 6 | Harden | Reconnect, config, metrics, packaging | Chaos tests pass; upgrade/downgrade works |
| 7 | Prove it | Field report, runbook, handover | Operator sign-off |

Rough effort: Phases 0–3 are the bulk of the uncertainty (≈ 1–2 weeks for one
engineer familiar with the code); Phases 4–6 are ≈ 2–3 weeks; Phase 7 is
calendar time, not effort.

---

## 4. Phase 0 — Safety and lab setup

### 4.1 Objectives

Make it impossible for an experiment to damage the boiler or the building, and
make every experiment reproducible.

### 4.2 Network isolation

- Put the boiler on a dedicated switch/VLAN with no route to the internet.
- Allow only the development host to reach TCP/80 (and whatever the boiler needs
  for its own operation — do not disturb existing heating control links).
- Give the boiler a fixed address or DHCP reservation; record MAC and
  `systemId` from `GET /info`.
- If the boiler is in active use, schedule work with the operator and know how
  to reach the physical controls.

### 4.3 Access and change-control rules

- **Read-only by default.** No `POST`/`PUT`/`DELETE` until Phase 5.
- Keep a written list of destructive endpoints and never call them casually:
  `POST /factory-reset`, `POST /update`, `POST /backup/sync`,
  `POST /devices/pt1000/<name>/calibrate-*`,
  `POST /devices/lambda-probe/calibrate`.
- Before any authentication with `daqOnly=false` (or omitted), capture the
  current display/state and tell the operator that the boiler may enter SERVICE
  mode. Standard route access does not require this value.
- Know the recovery path for SERVICE mode: code evidence says the mode is
  **latched** (`HvStateManager::transitionToState` re-enters mode 6) and a
  **reboot** is the practical exit. Leaving the service screen is not confirmed
  to clear it. Document the procedure before you need it. `[VERIFY]`

### 4.4 Development environment

- Python 3.11+ with `requests`/`httpx` and `pytest`; `curl` for manual work.
- Optional: `mitmproxy` or a small logging reverse proxy for capture.
- Store captures under `captures/<date>/` (never commit credentials, though in
  this system the "credential" is derivable anyway).
- Keep the Kuna export read-only; do not put helper scripts inside it. Move
  `dumpfn.py` / `summarize.py` to `tools/re/` (see §12.6).

### 4.5 Baseline capture

1. `GET /info`, `GET /pcb`, `GET /parameters` (`daqOnly=true`; standard routes
   are still reachable, one login only).
2. Record firmware version, serial numbers, parameter names/types, current values.
3. Record the boiler's physical/display state before and after the daqOnly=true
   login to confirm it did not request SERVICE. Test `daqOnly=false` separately
   only when SERVICE entry is intentionally approved.
4. Save raw responses (headers + body) as fixtures for Phase 3.

### 4.6 Exit criteria

- [ ] Boiler reachable only from the dev host.
- [ ] Operator aware of the plan and the SERVICE-mode risk.
- [ ] Baseline capture stored and reviewed.
- [ ] Recovery procedure for SERVICE mode written down.

---

## 5. Phase 1 — Firmware investigation

### 5.1 Objectives

Turn the existing decompilation into a complete, indexed behavioural model, and
close the gaps that would otherwise become client bugs.

### 5.2 Work items

#### 5.2.1 Route table and dispatch

- Re-read `ServiceRequestHandler::service` and the handler `canHandle` methods.
- Produce a table: handler class → path prefix → accepted methods → sub-paths.
- Confirm ordering effects (first match wins) and whether any prefix shadows
  another (e.g. `/info/systemid/generate` vs `/info`).
- Confirm what happens on trailing slashes, URL encoding, and case sensitivity.
  `[VERIFY]`

#### 5.2.2 Authentication and session

- Re-derive `AuthenticationHandler::handleChallengeRequest`,
  `handleAuthenticationRequest`, `isAuthenticated`, `isDaqOnlyActive`.
- Confirm the exact key input (hex string vs raw bytes), the digest encoding, and
  the salt bytes.
- Confirm the challenge TTL (is it only the 5 s rate limit, or does the challenge
  itself expire?).
- Confirm cookie attributes (`HttpOnly`? `SameSite`? `Secure`?).
- Confirm the idle timer semantics: is it reset on *any* request including
  `/challenge`, or only authenticated ones?
- Confirm what happens to existing sessions when `daqOnly` flips.
- Inspect `SystemModel::setRequestedMode` and the mode enum; identify all modes
  and whether SERVICE can be left via the API. Code evidence: it cannot —
  `HvStateManager::transitionToState` latches mode 6 and `ServiceModeState::end`
  is empty; a reboot is required. `[VERIFY]`

#### 5.2.3 Parameter system

- Enumerate `ParameterType` values and their JSON representations
  (`Parameter::allDataToJsonObject`, `ParameterBase::addJson`).
- For each type, determine accepted write shapes and validation rules
  (`Parameter::setValue` / `handlePost`).
- The 2.7.4 HTTP lookup → scalar JSON updater → setter path has no
  security-level comparison; UI filtering is separate. Validate whether the
  deployed version changes this behavior. `[CODE; live VERIFY pending]`
- Identify parameters that trigger mode changes or actuator activity when
  written (search `handlePost` for `setRequestedMode` and similar).
- Build a machine-readable catalogue (`parameters.json`) from a live
  `GET /parameters`, enriched by type info from code.

#### 5.2.4 DAQ channels

- Inspect `log*Daq` call sites and `ChannelHandler::handle`.
- Determine when channels are registered (startup? mode-dependent?), their value
  types, units, and whether names are stable across firmware versions.
- Determine whether `/channels` returns a snapshot or a live map, and how often
  values update.
- Build a channel dictionary with guessed units; mark guesses `[VERIFY]`.

#### 5.2.5 Device handlers

For each of the 24 handlers, extract:

- path grammar and method checks,
- request schema and validation ranges,
- response schema,
- side effects (actuator movement, sleeps, blocking),
- error paths and reason phrases.

Focus on timing: `POST /authenticate` (~1 s), `/devices/mixer` PUT (~1 s),
`GET /devices/can` (up to 5 s). These drive client timeouts and concurrency.

#### 5.2.6 Failure and edge behaviour

- What does the server do on malformed JSON, missing `Content-Type`, chunked
  bodies, very large bodies, or slow clients?
- Does it keep connections alive? What is the idle keep-alive timeout?
- What happens on concurrent requests from one or many cookies?
- Parameter batches are sequential with no rollback in 2.7.4. Mock partial
  application and verify deployed behavior before enabling batch writes.

### 5.3 Tooling

- `tools/re/dumpfn.py` — print a function from `app.c` by symbol.
- `tools/re/summarize.py` — summarise handler strings, statuses, callees.
- `app.asm` — verify branches the decompiler renders ambiguously (especially
  status codes and default cases).
- Keep notes next to the API reference; update it as facts are confirmed.

### 5.4 Deliverables

- Updated `docs/boiler-http-api.md` with confirmed/[VERIFY] labels.
- `docs/firmware-map.md`: handlers, symbols, addresses, call graph of the HTTP
  path.
- `docs/parameters.md` + `parameters.json`: catalogue and write-safety notes.
- `docs/channels.md`: channel dictionary and update semantics.
- A prioritised open-questions list feeding Phase 2.

### 5.5 Exit criteria

- [ ] Every registered handler has a documented path/method/body/response.
- [ ] Auth/session behaviour is understood down to timer resets and cookie flags.
- [ ] All state-changing code paths are identified and classified.
- [ ] No `[UNKNOWN]` remains that blocks Phase 2.

---

## 6. Phase 2 — Protocol validation with curl

### 6.1 Objectives

Confirm the code-derived model against the real device, capture exact bytes, and
discover behaviour the code did not make obvious.

### 6.2 Auth cookbook

```bash
HOST=http://192.0.2.10
SALT=fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27

# 1. Challenge (store headers so you can see the 403 reason phrase)
curl -sS -D challenge.hdr "$HOST/challenge" -o challenge.json
cat challenge.json

# 2. Derive the key
CHALLENGE=$(python3 -c 'import json;print(json.load(open("challenge.json"))["challenge"])')
KEY=$(printf '%s%s' "$CHALLENGE" "$SALT" | sha512sum | cut -d' ' -f1)

# 3. Authenticate with daqOnly=true to avoid requesting SERVICE.
# This enables /channels but is not a server-side read-only restriction.
curl -sS -i -c cookies.txt -X POST "$HOST/authenticate" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\",\"daqOnly\":true}"

# 4. Fetch channel telemetry or another standard GET route
curl -sS -i -b cookies.txt "$HOST/channels"
curl -sS -i -b cookies.txt "$HOST/parameters"
```

Use `daqOnly=false` only when intentionally requesting SERVICE mode; it is not
needed to access standard API routes. See
[`boiler-http-api.md`](boiler-http-api.md#3-service-mode-the-side-effect-of-daqonlyfalse-authentication).

Notes:

- `curl -i` shows the reason phrase; the body of an error is empty.
- `curl --trace-ascii trace.txt` is useful to confirm that a `GET` body is
  actually sent.
- Use a fresh cookie jar per experiment to avoid confusing 401s.
- The 5 s challenge limit is global: if you get `403 wait between challenge
  requests`, wait 5 s and retry; do not hammer.

### 6.3 Standard GET endpoint exercises

The following standard routes are dispatched with `daqOnly=true` in the
reconstructed 2.7.4 code path, without requesting SERVICE mode. This is not a
read-only permission boundary for other HTTP methods: keep a client-side
allowlist and use GET only for observation.

```bash
curl -sS -b cookies.txt "$HOST/info"
curl -sS -b cookies.txt "$HOST/pcb"
curl -sS -b cookies.txt "$HOST/parameters"          # all parameters
curl -sS -b cookies.txt -X GET "$HOST/parameters" \
  -H 'Content-Type: application/json' \
  -d '{"parameters":["K3","T1"]}'                    # selected; GET body

curl -sS -b cookies.txt "$HOST/devices/fuse"
curl -sS -b cookies.txt "$HOST/devices/interrupts/tuerkontakt"
curl -sS -b cookies.txt "$HOST/devices/leds"
curl -sS -b cookies.txt "$HOST/devices/nicrni/rauchgasfuehler"
curl -sS -b cookies.txt "$HOST/devices/usb"
curl -sS -b cookies.txt "$HOST/devices/eeprom"
curl -sS -b cookies.txt "$HOST/devices/rtc"
curl -sS -b cookies.txt "$HOST/devices/exhaust-fan"
curl -sS -b cookies.txt "$HOST/devices/linear-motor"
curl -sS -b cookies.txt "$HOST/devices/pwm-pump"
curl -sS -b cookies.txt "$HOST/devices/stepper/primaerluftklappe"
curl -sS -b cookies.txt "$HOST/devices/stocker"
curl -sS -b cookies.txt "$HOST/devices/mixer"
curl -sS -b cookies.txt "$HOST/devices/relais/sk-ein"
curl -sS -b cookies.txt "$HOST/devices/pt1000/Kesselfuehler"
curl -sS -b cookies.txt "$HOST/devices/lambda-probe"
curl -sS -b cookies.txt "$HOST/backup"
```

Channel data (also available after authentication with `daqOnly=true`):

```bash
curl -sS -b cookies.txt "$HOST/channels"
```

Long-poll and timing:

```bash
time curl -sS -i -b cookies.txt "$HOST/devices/can"   # ~5 s, 204 on timeout
time curl -sS -i -b cookies.txt -X POST "$HOST/authenticate" ...  # ~1 s
```

### 6.4 Observation log

For every call record:

- request line, headers, body (including GET bodies),
- status code and **reason phrase**,
- response headers (especially `Set-Cookie`, `Content-Length`,
  `Connection`),
- response body, pretty-printed,
- wall-clock duration,
- physical/display state before and after (for writes/side effects).

### 6.5 Endpoint test matrix

Legend: **R** read-only, **W** actuator write, **C** configuration write,
**D** destructive. All rows assume an authenticated session unless stated.

| # | Endpoint | Method | Class | Expected | Notes / open questions |
|---|---|---|---|---|---|
| 1 | `/challenge` | GET | — | 200 JSON; 403 if <5 s | Global; second call within 5 s must be 403 |
| 2 | `/authenticate` | POST | side effect | 204 + cookie; 400 bad key | ~1 s; `daqOnly=false` → SERVICE |
| 3 | `/info` | GET | R | 200 `{mac,systemId,commissionNumber,articleSerialNumber,softwareVersion}` | Does it expose mode? |
| 4 | `/info` | POST | C | 200 updated info | Only commission/article numbers |
| 5 | `/info/systemid/generate` | POST | C | 200 `{systemId}` | Path precedence over `/info` |
| 6 | `/pcb` | GET | R | 200 `{internalTemperature,internalVoltage}` | |
| 7 | `/parameters` | GET | R | 200 all parameters | Large payload; capture once |
| 8 | `/parameters` | GET + body | R | 200 selected | Confirm GET body survives real HTTP stack |
| 9 | `/parameters` | POST (PUT also dispatches in 2.7.4) | C | Read-back objects; status alone is insufficient | `daqOnly=true` is sufficient; sparse entries, non-atomic batch; float range rejection can be silent |
| 10 | `/channels` | any | R | 200 map | Additional handler checked when `daqOnly=true`; standard routes remain available |
| 11 | `/devices/fuse` | GET | R | 200 `{state}` | |
| 12 | `/devices/interrupts/<name>` | GET | R | 200 `{state}`; 404 unknown | 7 names |
| 13 | `/devices/leds` | GET | R | 200 `{requested,selected}` | |
| 14 | `/devices/leds` | POST | W | 200 new state; 400 bad color | Strict enum |
| 15 | `/devices/nicrni/rauchgasfuehler` | GET | R | 200 `{value,valid}`; 405 non-GET | |
| 16 | `/devices/usb` | GET | R | 200 `{usbDrives:[…]}` | |
| 17 | `/devices/eeprom` | GET | R | 200 identity fields | |
| 18 | `/devices/eeprom` | PUT | C | 200 new state | Identity fields; careful |
| 19 | `/devices/rtc` | GET | R | 200 `{utcDatetime}` | |
| 20 | `/devices/rtc` | PUT | C | 200 state | Time sync; `doTrim` |
| 21 | `/devices/can` | GET | R | 200 frame or 204 after ~5 s | Long-poll |
| 22 | `/devices/can` | POST | W | 204; 400/500 | Raw CAN injection — high risk |
| 23 | `/devices/exhaust-fan` | GET/PUT | R/W | state / updated | Actuator |
| 24 | `/devices/linear-motor` | GET/PUT | R/W | state / updated | Actuator |
| 25 | `/devices/pwm-pump` | GET/PUT | R/W | state / updated | Actuator |
| 26 | `/devices/stepper/<name>` | GET/PUT | R/W | state / updated | Relative motion risk |
| 27 | `/devices/stocker` | GET/PUT | R/W | state / updated | Actuator |
| 28 | `/devices/stocker/testrun` | POST | W | 200 incl. rpm | Moves hardware; timed |
| 29 | `/devices/mixer` | GET/PUT | R/W | state / updated | ~1 s sleep; strict enum |
| 30 | `/devices/relais/<name>` | GET | R | 200 `{state}`; 404 unknown | 5 names |
| 31 | `/devices/relais/<name>` | POST | W | 200 new state | Mains relay; high risk |
| 32 | `/devices/pt1000/<name>` | GET | R | 200 sensor object | 7 names |
| 33 | `/devices/pt1000/<name>/calibrate-start` | POST | D | 204 | Calibration procedure |
| 34 | `/devices/pt1000/<name>/calibrate-end` | POST | D | 204 | |
| 35 | `/devices/lambda-probe` | GET | R | 200 state object | |
| 36 | `/devices/lambda-probe/calibrate` | POST | D | 200 `{o2Raw,o2Correction}` | |
| 37 | `/backup` | GET | R | 200 status | |
| 38 | `/backup/sync` | GET | R | 200 status | |
| 39 | `/backup/sync` | POST | D | 200 status | Starts partition sync |
| 40 | `/update` | POST | D | 200 | Path-based; firmware change |
| 41 | `/factory-reset` | any | D | 200 | Wipes configuration |

Gate: no row may be marked done without a captured response and a note on side
effects. Rows 33–41 are **not** exercised in Phase 2 unless the operator
explicitly approves; they remain `[UNKNOWN]` and are fenced off in the client.

### 6.6 Exit criteria

- [x] `daqOnly=true` authentication and `GET /channels`, `/info`, and `/parameters` confirmed on one unit (`smart_hv_3.4.0`).
- [ ] Session expiry, keepalive and challenge cooldown verified.
- [ ] 2.7.4 runtime confirms the standard-route fallback with `daqOnly=true` and
      whether `/channels` is omitted with false.
- [ ] SERVICE-mode side effect observed and documented.
- [ ] Every R/W row in the matrix has a real capture.
- [ ] Error reason phrases and empty bodies confirmed.
- [ ] GET-with-body works through the client library.
- [ ] Discrepancies between code and device recorded and resolved (or raised).

---

## 7. Phase 3 — Mock server and contract harness

### 7.1 Why a mock

The boiler is slow, stateful, shared, and sometimes unavailable. A faithful mock
lets the client team iterate quickly, run fault injection, and test destructive
paths without touching hardware.

### 7.2 Fidelity requirements

The mock must reproduce:

- the exact auth handshake, salt, key derivation and cookie value shape;
- a **single global challenge** and the 5 s cooldown, including the `403 reason
  phrase`;
- the ~1 s authenticate delay (configurable, default on for realistic tests);
- `Set-Cookie: auth-cookie=…; Max-Age=86400; Path=/` and idle expiration at
  300 s, refreshed per authenticated request;
- global `daqOnly` routing: special `/channels` check when true, followed by
  standard route-vector fallback; false disables the channel route and requests
  SERVICE during authentication;
- empty error bodies and messages in the reason phrase;
- `200 application/json` compact bodies, `204` no-body responses;
- GET-with-body support for `/parameters`;
- `/devices/can` long-poll (default 5 s, configurable; optional frame queue);
- parameter catalogue with types and validation rules loaded from a fixture;
- device states, including strict enums and the ~1 s mixer delay;
- an injectable clock so idle/cooldown tests do not sleep for minutes;
- fault-injection switches: drop connection, delay, 500, truncated body,
  wrong content type, duplicate `Set-Cookie`.

### 7.3 Implementation options

| Option | Pros | Cons |
|---|---|---|
| Python stdlib `http.server` + `ThreadingHTTPServer` | No deps, easy to read | Manual routing/parsing; weak HTTP edge cases |
| FastAPI/Starlette + uvicorn | Fast to build, async faults | Opinionated status/reason handling; needs a dependency |
| `aiohttp` server | Good low-level control | More code |
| Record/replay proxy in front of the device | Perfect real payloads | Does not model state/expiry |

Recommendation: **FastAPI (or aiohttp) mock for stateful tests**, plus a thin
**logging reverse proxy** for recording real traffic into fixtures. Keep the mock
in `tests/mock/` and run it in CI.

### 7.4 Contract tests

- Define one test per row of the Phase 2 matrix, asserting status, headers, and
  JSON shape.
- Run the same tests against the mock and (manually/opt-in) against hardware.
- Assert the *negative* space: 401 without cookie, 404 in the wrong mode, 400 on
  bad types, 403 within cooldown, 405 on non-GET where required.
- Golden fixtures: captured responses are canonicalised and compared
  structurally (ignore volatile values such as timestamps).

### 7.5 Exit criteria

- [ ] Mock passes the full contract suite.
- [ ] Client passes the suite against the mock without hardware.
- [ ] Recorded fixtures exist for every endpoint the client uses.
- [ ] Clock/fault injection is covered by tests.

---

## 8. Phase 4 — Read-only client MVP

### 8.1 Scope

A library plus a CLI that can:

- authenticate (`daqOnly` selectable, defaulting to the least invasive mode);
- keep a session alive and re-authenticate on 401;
- read system info, parameters, device states, and channels (`/channels` is an
  additional route when `daqOnly=true`);
- cache and expose typed models;
- log every request/response with redaction of the cookie;
- expose a `health`/`status` command.

No writes. No destructive endpoints. No automatic mode flipping beyond what the
operator asked for.

### 8.2 Functional requirements

| ID | Requirement |
|---|---|
| R1 | Authentication defaults to `daqOnly=true`; `daqOnly=false` requires explicit SERVICE-mode confirmation and is not needed to access standard routes. |
| R2 | Session keepalive sends a cheap authenticated request before 5 min idle. |
| R3 | On 401, re-authenticate once, respecting the 5 s challenge cooldown. |
| R4 | All endpoint calls return typed objects with raw JSON available. |
| R5 | Polling is configurable per resource; defaults are conservative. |
| R6 | A single in-flight request per client by default; `/devices/can` may run on its own slot. |
| R7 | Errors expose status code, reason phrase, endpoint, and retryability. |
| R8 | No write path is reachable from the CLI in this phase. |
| R9 | The client can run against the mock via a config switch. |
| R10 | Logs never contain the full cookie; challenge/key values are redacted. |

### 8.3 Non-functional requirements

- Memory stable over a 24 h soak; bounded caches.
- Timeouts: default 10 s for normal calls, 65 s for `/devices/can` and
  `/authenticate`.
- No busy loops; jittered backoff on failures.
- Deterministic shutdown; no orphan connections.
- Works on Linux and macOS; Windows optional.

### 8.4 CLI sketch

```text
hvctl --host 192.0.2.10 auth login --mode daq-only
hvctl --host 192.0.2.10 status
hvctl --host 192.0.2.10 info
hvctl --host 192.0.2.10 parameters list [--names K3,T1]
hvctl --host 192.0.2.10 device get relais/sk-ein
hvctl --host 192.0.2.10 channels [--watch 5s]
hvctl --host 192.0.2.10 monitor --interval 10s
```

### 8.5 Exit criteria

- [ ] CLI performs a standard-route read session with `daqOnly=true` on
      hardware, including channels.
- [ ] 24 h soak with 10 s polling: no 401 storms, no memory growth.
- [ ] Session survives a 10 min idle gap via keepalive.
- [ ] All reads against mock pass; hardware discrepancies filed.
- [ ] No write endpoint is implemented.

---

## 9. Phase 5 — Write and safety layer

### 9.1 Capability tiers

| Tier | Examples | Default | Requirements |
|---|---|---|---|
| T0 — telemetry | `/channels`, `GET /info`, `GET /parameters` | enabled | none |
| T1 — diagnostics | device GETs, `/pcb`, `/backup` status | enabled | authenticated |
| T2 — actuators | relais, fan, pump, stepper, mixer, stocker, LEDs | disabled | explicit allowlist + confirmation + interlocks |
| T3 — configuration | parameters POST, EEPROM PUT, RTC PUT, info POST | disabled | allowlist + range checks + confirmation + audit |
| T4 — destructive | factory reset, update, CAN send, calibrations, backup sync | hard-disabled | out-of-band operator procedure only |

Parameter writes can remain in `daqOnly=true`; do not request SERVICE just to
unlock `/parameters`. Assign risk by the particular setting and effect, not
solely by catalog `securityLevel`. The USER table includes ignition and timing
controls in the live sample, not only low-impact preferences.

T4 must be **unreachable in normal code paths**, not merely warned about. Put the
paths behind a compile/runtime flag that requires an environment variable and an
interactive confirmation.

### 9.2 Write pipeline

Every write goes through the same pipeline:

```
intent → policy check → precondition read → interlock → confirm
       → execute (single attempt) → read-back verify → audit → notify
```

1. **Intent** — typed request, never a raw path/body from the UI.
2. **Policy** — tier allowlist, value ranges, device state.
3. **Precondition read** — fetch current state; abort if it is not what the
   operator expects (TOCTOU mitigation).
4. **Interlock** — e.g. refuse to run the stocker while the boiler is in a
   state where the code forbids it; refuse actuator changes unless the boiler is
   in SERVICE/manual mode where required. `[VERIFY]` which interlocks exist.
5. **Confirm** — interactive or signed-off config; T2+ only.
6. **Execute once** — no blind retries.
7. **Read back** — GET the resource and compare; treat mismatch as failure.
8. **Audit** — append-only record: who, what, before, after, result.
9. **Notify** — surface success/failure; on failure, suggest recovery.

### 9.3 Interlocks and confirmations

- A global "write mode" switch, off by default, must be enabled per session.
- Per-device enable flags (e.g. `allow_relais: false`).
- Confirmation text includes the device, action, and expected physical effect.
- Rate limits: minimum interval between writes to the same actuator; a global
  budget per minute.
- Refuse writes while a mode switch or re-auth is in progress.
- Refuse to write if the last read is older than N seconds.
- Log the physical state before/after where observable.

### 9.4 Idempotency and retry classes

| Operation | Idempotent? | Retry policy |
|---|---|---|
| GET any resource | yes | 3 attempts, backoff + jitter |
| `POST /authenticate` | effect-free on repeat? `[VERIFY]` | serialise; one retry on network error |
| `PUT` absolute state (relais, fan, pump, mixer) | mostly yes | retry only after read-back confirms no change; max 1 |
| Stepper move (`steps`, relative) | no | never auto-retry |
| Stocker testrun | no | never auto-retry |
| Parameter POST | setting-dependent; batch is non-atomic | reconcile every value; no blind retry based on HTTP status; prefer one-entry requests |
| EEPROM/RTC PUT | yes | retry after read-back; max 1 |
| CAN send | no | never auto-retry |
| Factory reset/update | no | never auto-retry |
| `/devices/can` long-poll timeout | n/a | immediate re-poll or idle backoff |

### 9.5 Audit and observability

- Append-only JSONL audit file with monotonic sequence numbers.
- Include request id, session id (not cookie), endpoint, payload hash, result.
- Keep a ring buffer in memory for the UI.
- Provide `hvctl audit tail` and export.

### 9.6 Exit criteria

- [ ] All T2/T3 writes go through the pipeline; unit tests prove bypass is
  impossible.
- [ ] T4 endpoints are unreachable unless a documented maintenance mode is on.
- [ ] Read-back verification catches injected failures.
- [ ] Audit records reconstruct a full session.
- [ ] Hardware write tests pass with the operator present.
- [ ] Recovery from a failed write is documented and tested.

---

## 10. Phase 6 — Hardening, observability, packaging

### 10.1 Session and reconnect

- Explicit state machine (see §13) with metrics per state.
- Challenge cooldown handling shared across all callers.
- Distinguish 401 (cookie expired) from 403 (too-soon challenge) from network
  failure; never conflate them.
- After a device reboot, drop the cookie and re-authenticate.
- Detect `daqOnly` drift: if a request that should work returns `404`, assume
  someone changed the channel-route flag (only `/channels` is affected); require
  an explicit re-login before retrying that route. Do not treat other standard
  route 404s as `daqOnly` drift.

### 10.2 Configuration

```yaml
boiler:
  host: 192.0.2.10
  port: 80
  daq_only: true          # false requests SERVICE; not a route privilege
  allow_service_mode: false

http:
  timeout_s: 10
  long_poll_timeout_s: 65
  user_agent: hvctl/0.1
  max_in_flight: 1

auth:
  keepalive_interval_s: 120
  challenge_cooldown_s: 5
  max_auth_attempts: 3

polling:
  info_s: 60
  pcb_s: 60
  parameters_s: 300
  devices_s: 30
  channels_s: 5
  can_long_poll: false

safety:
  write_mode: false
  allowed_devices: []
  allowed_parameters: []
  min_write_interval_s: 2
  audit_path: var/audit.jsonl
```

Validate on load; refuse unsafe combinations (e.g. `write_mode: true` with an
empty allowlist, or `daq_only: false` with `allow_service_mode: false`).

### 10.3 Logging and metrics

- Structured logs (JSON) with request id, endpoint, status, duration, retry
  count, and state transition.
- Redact `Cookie`, `Set-Cookie`, challenge and key.
- Metrics: request count/latency by endpoint, auth attempts, 401/403 rate,
  session uptime, poll lag, write count, audit failures.

### 10.4 Persistence

- Optional SQLite for parameter snapshots and channel time series.
- Retention policy and downsampling.
- Never store the auth cookie on disk beyond process lifetime.

### 10.5 Packaging and delivery

- Python package with pinned dependencies; or a single static binary if the
  deployment target requires it.
- Systemd unit / container image with read-only filesystem except the audit and
  data directories.
- Version pinning against firmware `2.7.4`; warn on other versions.
- An upgrade path: schema migrations for the parameter catalogue.

### 10.6 Exit criteria

- [ ] Chaos tests pass (network loss, delay, duplicate responses, reboot).
- [ ] Config validation rejects unsafe combinations.
- [ ] Logs and metrics are useful in a post-mortem.
- [ ] Package installs and runs unattended.
- [ ] Firmware-version guard works.

---

## 11. Phase 7 — Field validation and handover

### 11.1 Test plan

- **Read soak:** 7 days, 5–10 s channel polling, 60 s device polling.
- **Session churn:** force expiry, reboot the app/device, change Wi-Fi, resume.
- **Channel-route flag flip:** verify `/channels` availability across
  `daqOnly=true/false`. The false value also requests SERVICE and must be tested
  only if explicitly approved; include a second client to test global effects.
- **Write rehearsal:** with the operator, exercise a small set of T2 actions
  (LED, mixer, one relay) and one T3 parameter change, then restore.
- **Failure drills:** unplug network mid-write, kill the client mid-session,
  simulate a slow server.
- **Recovery:** verify SERVICE mode exits via reboot (code suggests it is
  latched and cannot be left through the API) and the boiler returns to normal
  heating control. `[VERIFY]`

### 11.2 Acceptance criteria

- [ ] No unintended writes during the entire validation period.
- [ ] Session recovers from all injected faults without operator intervention.
- [ ] Channel/parameter data matches the device UI within an agreed tolerance.
- [ ] Writes are verified and audited; operator signs off.
- [ ] Runbook covers install, upgrade, rollback, and emergency stop.

### 11.3 Handover artifacts

- Final API reference and parameter/channel catalogues.
- Architecture and operations runbook.
- Test reports and captured fixtures.
- Known-issues list with `[VERIFY]` items resolved or explicitly accepted.

---

## 12. High-level client design

### 12.1 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Front end: CLI / TUI / (later) HTTP facade                   │
└───────────────┬──────────────────────────────────────────────┘
                │ typed requests
┌───────────────▼──────────────────────────────────────────────┐
│ Application services                                         │
│  status · parameters · devices · channels · maintenance      │
└───────┬───────────────────┬───────────────────┬──────────────┘
        │                   │                   │
┌───────▼────────┐  ┌───────▼────────┐  ┌───────▼─────────────┐
│ Safety policy  │  │ Scheduler      │  │ Store / time series │
│ tiers, audit   │  │ polls, backoff │  │ SQLite, cache       │
└───────┬────────┘  └───────┬────────┘  └─────────────────────┘
        │                   │
┌───────▼───────────────────▼──────────────────────────────────┐
│ API layer (typed endpoints, models, validation)              │
│  system · parameters · channels · devices · maintenance      │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Auth manager                                                 │
│  challenge serialisation · cooldown · keepalive · re-auth    │
│  routing-state ledger (global daqOnly flag)                   │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Transport                                                    │
│  HTTP/1.1 · cookies · reason phrases · timeouts · metrics    │
└───────────────────────────┬──────────────────────────────────┘
                            │
                     http://<boiler>:80
```

### 12.2 Component responsibilities

**Transport**

- One `httpx.Client`/connection pool (or `requests.Session`) per process.
- Manual cookie jar for `auth-cookie`; capture `Set-Cookie` refreshes.
- Return a `Response` envelope: status, reason, headers, raw body, parsed JSON,
  elapsed time, request id.
- Enforce per-endpoint timeouts and max body size.
- Never retry at this layer; retries belong to the API/retry policy.

**Auth manager**

- Holds the single global challenge state and a lock so only one caller performs
  the handshake.
- Tracks `last_challenge_at`; enforces the 5 s cooldown locally.
- Stores the cookie and `last_used_at`; proactively refreshes if idle > 240 s.
- Tracks the requested global `daqOnly` flag. It is a `/channels` route toggle,
  not a read/write permission mode. Track SystemMode/SERVICE risk separately;
  neither has a reliable read-back through this API.
- Emits state transitions.

**API layer**

- One module per resource, each with typed request/response models.
- Coerces `0/1` and `true/false` for device states.
- Validates enum names against the documented sets.
- Turns empty error bodies into `ApiError(status, reason, endpoint)`.
- Keeps raw JSON for forward compatibility.

**Safety policy**

- Central allowlist and tier checks.
- Preconditions and interlocks.
- Confirmation callback abstraction (CLI prompt, UI dialog, or auto-approve in
  tests).
- Audit writer.
- The only component allowed to call write methods.

**Scheduler**

- Declarative jobs: interval, jitter, timeout, enabled, backoff on failure.
- Single-flight per job; global in-flight cap.
- Long-poll job is special: no overlap, immediate re-arm.
- Pauses when auth is not READY.

**Store**

- Last-known state per resource with timestamps.
- Optional time-series tables.
- Bounded memory; eviction policy.

**Front end**

- Thin; all safety and validation live below it.
- `--json` output for scripting.

### 12.3 Read path

```
scheduler tick
  → API.get_device("relais/sk-ein")
    → auth.ensure_ready()           # refresh/re-auth if needed
    → transport.request(GET, path)
    → parse + validate
  → store.update(...)
  → front end renders
```

### 12.4 Write path

```
user intent
  → safety.authorise(intent)        # tier, allowlist, range
  → API.get_current(intent.target)  # precondition + freshness
  → safety.interlock(intent, state)
  → safety.confirm(intent)          # interactive / config
  → transport.request(write)
  → API.get_current(target)         # read-back
  → safety.verify(intent, before, after)
  → audit.append(...)
  → notify
```

### 12.5 Concurrency model

- One event loop (asyncio) or a thread pool with a single auth lock.
- Default `max_in_flight = 1`; `/devices/can` may use a second slot.
- The auth lock serialises: challenge → key → authenticate → cookie publish.
- Scheduler jobs share the auth manager but never the transport connection
  unless the HTTP library is thread-safe.
- Shutdown cancels jobs, closes connections, flushes the audit log.

### 12.6 Suggested repository layout

```text
src/hvclient/
  transport.py
  auth.py
  retry.py
  models/
    system.py
    parameter.py
    device.py
    channel.py
  api/
    system.py
    parameters.py
    channels.py
    devices.py
    maintenance.py
  safety/
    policy.py
    audit.py
    interlocks.py
  scheduler.py
  store.py
  config.py
  cli.py
tests/
  unit/
  contract/
  integration/
  fixtures/
  mock/
tools/
  re/
    dumpfn.py
    summarize.py
  probe.py
docs/
  boiler-http-api.md
  client-development-plan.md
  firmware-map.md
  parameters.md
  channels.md
```

### 12.7 Mode-lease design

Because `daqOnly` is global and unreadable:

- The client has a `ModeLease` with `{mode, acquired_at, owner, warning}`.
- Acquiring a lease requires an explicit operator action.
- Only one lease exists per process; log loudly if a second process is detected
  (best-effort via a lock file on the client host).
- On exit, optionally re-authenticate with the other mode only if the operator
  configured a restore policy. There is no "previous mode" to read, so define
  the default restore target in config and document it.
- Never flip modes automatically inside polling. Mode is a session-level
  decision.

---

## 13. Error, retry, and session state machine

### 13.1 States

```
DISCONNECTED ──connect──▶ PROBING
PROBING ──tcp ok──▶ CHALLENGE
CHALLENGE ──200──▶ AUTHENTICATING
CHALLENGE ──403──▶ COOLDOWN ──5s──▶ CHALLENGE
AUTHENTICATING ──204──▶ READY
AUTHENTICATING ──400/network──▶ BACKOFF ──▶ CHALLENGE
READY ──401──▶ EXPIRED ──▶ CHALLENGE
READY ──network error──▶ BACKOFF ──▶ PROBING
READY ──mode change requested──▶ DRAINING ──▶ CHALLENGE
any ──N failures──▶ LOCKED (operator action)
```

### 13.2 Transition rules

| From | Event | To | Action |
|---|---|---|---|
| DISCONNECTED | start | PROBING | open connection, check TCP |
| PROBING | success | CHALLENGE | request challenge (respect cooldown) |
| PROBING | failure | BACKOFF | exponential backoff |
| CHALLENGE | 200 | AUTHENTICATING | compute key, POST auth |
| CHALLENGE | 403 | COOLDOWN | wait `Retry-After` or 5 s + jitter |
| CHALLENGE | other | BACKOFF | log, retry |
| AUTHENTICATING | 204 | READY | store cookie, notify waiters |
| AUTHENTICATING | 400 | CHALLENGE | challenge was overwritten; redo once |
| AUTHENTICATING | network | BACKOFF | do not assume auth failed |
| READY | 401 | EXPIRED | drop cookie, go to CHALLENGE |
| READY | network | BACKOFF | keep cookie, retry connection |
| READY | idle > 240 s | READY | keepalive request |
| READY | mode change | DRAINING | wait for in-flight, then CHALLENGE |
| any | 5 consecutive auth failures | LOCKED | require operator reset |

### 13.3 Error mapping

| Condition | Client meaning | Action |
|---|---|---|
| `200` | success | parse; update store |
| `204` | success, no body | return `None`; for auth, record cookie |
| `400` + reason | bad request/value | do not retry; surface reason |
| `401` | session missing/expired | re-auth once, then retry read; never retry writes blindly |
| `403` on `/challenge` | cooldown | wait 5 s + jitter, retry |
| `403` elsewhere | unexpected | surface; likely auth/global issue |
| `404 no valid route` | wrong mode or bad path | check mode lease; do not loop |
| `405` | wrong method | programming error; surface |
| `500 send failed` (CAN) | device-level failure | limited retry with backoff |
| Timeout | unknown outcome | read-only: retry; writes: verify state before retry |
| Connection reset | unknown outcome | backoff; for writes, verify |
| Empty body on error | normal | use reason phrase |

### 13.4 Retry policy

```text
attempts = 3
base = 0.5 s
factor = 2
jitter = ±20%
max = 8 s
```

- Reads: full policy.
- Auth: at most 2 challenge attempts per user action.
- Writes: at most 1 automatic retry, and only after a read-back proves the
  original attempt did not take effect.
- Never retry `400`, `404`, `405`, or T4 operations.
- Add a circuit breaker after repeated failures; move to `LOCKED` and require an
  operator to reset.

### 13.5 `daqOnly` state protocol

```
1. Stop scheduler jobs that depend on the current authentication/routing state.
2. Wait for in-flight requests (bounded).
3. Re-authenticate only when intentionally changing the global `daqOnly` flag.
   Use true for normal clients; false requests SERVICE and is not needed to
   access standard endpoints.
4. Update the local state record of the global flag.
5. Probe an allowed route (`GET /channels`, `GET /info`, or `GET /parameters`)
   to check the session and route.
6. Resume scheduled reads; keep writes behind separate safety policy.
```

If step 5 fails, do not blindly retry; surface a mode-conflict error, because
another client may have flipped the flag.

---

## 14. Testing strategy

### 14.1 Levels

| Level | What | Where | Speed |
|---|---|---|---|
| Unit | key derivation, parsers, models, policy, state machine | local | ms |
| Contract | endpoint status/headers/body against mock | local/CI | s |
| Integration | real device, read-only | lab | min |
| System | scheduler + auth + store + CLI | lab | h |
| Soak | 24 h / 7 day read | lab | days |
| Chaos | faults, reboots, mode flips | lab | h |
| Safety | attempts to bypass write policy | local + lab | min |

### 14.2 Key test cases

- Key derivation vectors (see Appendix C).
- Challenge 403 within 5 s; success after.
- Auth with `daqOnly=true` and verify standard-route plus `/channels` access.
- Only test `daqOnly=false`/`/channels` removal if SERVICE entry is explicitly
  approved for a cold, attended unit.
- Session expiry after 300 s idle; refresh before expiry.
- 401 → re-auth → retry succeeds.
- 403 → cooldown → success.
- GET `/parameters` with body through each supported HTTP client.
- `/devices/can` 204 after ~5 s; no retry storm.
- Error reason phrase capture and empty body.
- Parameter type round-trip for every `type`.
- Enum rejection for device names, LED colours, mixer states.
- Write policy bypass attempts (direct API call, CLI flag, config file).
- Audit record completeness.
- Config validation rejects unsafe combinations.

### 14.3 Fault injection

- Connection refused / reset / half-open.
- Response delay > client timeout.
- Truncated body, wrong `Content-Length`.
- Duplicate `Set-Cookie`, missing cookie.
- 500 on CAN send.
- Device reboot (cookie invalid).
- Second client steals challenge / flips `daqOnly`.
- Clock jumps (test idle logic).

### 14.4 Safety test suite

- T4 endpoints cannot be called even if the user knows the path.
- Write mode off by default after config reload.
- Read-back mismatch aborts and alerts.
- Rate limiting prevents rapid repeated actuator writes.
- Interlock refusal is logged with a reason.
- Audit is append-only and survives process crash.

---

## 15. Risks and mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| SERVICE mode disrupts heating | High | Medium | Explicit consent, lab schedule, recovery runbook |
| Global `daqOnly` fights another client | High | Medium | Single-tenant assumption, lock file, mode probe, loud errors |
| A write moves an actuator unexpectedly | High | Low | Tiers, confirmation, interlocks, read-back, audit |
| `/factory-reset` or `/update` called by mistake | Critical | Low | Hard-disable, remove from API surface, operator procedure |
| Parameter ranges unknown | Medium | High | Discover from code + device, validate conservatively, allowlist |
| GET-with-body unsupported by HTTP lib | Medium | Medium | Test early; fall back to raw socket or all-parameters GET |
| Session expiry during a write | Medium | Medium | Keepalive, write pipeline pauses on non-READY |
| Firmware update changes API | Medium | Medium | Version guard, contract tests, catalogue snapshots |
| Cookie/challenge logged | Low | Medium | Redaction, log review tests |
| QtWebApp quirks (reason phrase, keep-alive) | Low | Medium | Fidelity in mock, raw captures |
| Boiler unavailable / network unstable | Medium | Medium | Backoff, caching, stale-state warnings |

---

## 16. Open questions / VERIFY list

### 16.1 High priority (block design)

1. Does `daqOnly=false` authentication actually switch the boiler to SERVICE,
   and what are the observable effects? Can it be exited via the API?
2. Does 2.7.4 live behavior match the code path and 3.4.0 captures, where
   `/channels` is additionally routed for `daqOnly=true` while `/info` and
   `/parameters` still reach standard handlers? Is `/channels` unavailable when
   `daqOnly=false`?
3. Is there any endpoint that reveals the current mode or `daqOnly` flag?
4. Can two clients authenticate without invalidating each other? What happens to
   sessions on a mode flip?
5. Confirm deployed `/parameters` partial-batch behavior and float range
   rejection/read-back: the 2.7.4 code allows partial application and can return
   success without changing an out-of-range float.
6. Which parameters are dangerous to write (mode changes, actuator tests,
   calibration)?
7. Does the deployed firmware enforce parameter security levels differently
   from 2.7.4's direct HTTP setter path, which has no level comparison?

### 16.2 Medium priority

8. Challenge TTL vs. just the 5 s rate limit.
9. Cookie flags (`HttpOnly`, `SameSite`, `Secure`).
10. Keep-alive timeout and connection reuse behaviour.
11. Channel registration timing and name stability.
12. Behaviour on trailing slash, URL encoding, case.
13. Exact validation ranges for numeric device fields.
14. Whether `/authenticate` is safe to repeat (idempotency).
15. Behaviour when the request body is chunked or has no `Content-Type`.

### 16.3 Low priority

16. Long-term stability of `systemId` and serial fields.
17. USB drive semantics and path exposure.
18. CAN frame format and safe IDs (only relevant for T4).
19. Firmware update path expectations (`updatePath`).
20. Logging/diagnostics endpoints not yet found.

---

## 17. Milestones

| Milestone | Contents | Estimate |
|---|---|---|
| M0 — Lab ready | Phase 0 complete | 1–2 days |
| M1 — Code model complete | Phase 1 docs + catalogues | 3–5 days |
| M2 — Reality check | Phase 2 matrix + transcripts | 2–4 days |
| M3 — Mock + contract | Phase 3 harness, CI green | 3–5 days |
| M4 — Read-only MVP | Phase 4 CLI + soak | 1 week |
| M5 — Safe writes | Phase 5 policy + hardware tests | 1–2 weeks |
| M6 — Hardened release | Phase 6 packaging | 1 week |
| M7 — Field sign-off | Phase 7 | 1–2 weeks calendar |

These assume one engineer with access to the device and the decompilation.
Parallelising firmware investigation and mock construction can compress M1–M3.

---

## Appendix A — curl cookbook

```bash
# Show status line, headers, body
curl -sS -i -b cookies.txt "$HOST/info"

# Pretty-print JSON
curl -sS -b cookies.txt "$HOST/parameters" | python3 -m json.tool | less

# GET with a JSON body (selected parameters)
curl -sS -b cookies.txt -X GET "$HOST/parameters" \
  -H 'Content-Type: application/json' \
  -d '{"parameters":["K3","T1"]}' | python3 -m json.tool

# Capture a trace including request/response headers and bodies
curl -sS --trace-ascii trace.txt -b cookies.txt "$HOST/devices/can"

# Time a call
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' -b cookies.txt "$HOST/pcb"

# Check the reason phrase on an error (body is empty)
curl -sS -i -b cookies.txt "$HOST/does-not-exist"

# Test the challenge cooldown
curl -sS -o /dev/null -w '%{http_code}\n' "$HOST/challenge"
curl -sS -o /dev/null -w '%{http_code}\n' "$HOST/challenge"   # 403
```

## Appendix B — Authentication test vectors

`key = SHA512(ascii(challenge) + SALT)`, lowercase hex (128 hex characters).

SALT = `fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27`

| Challenge | Expected key |
|---|---|
| `0000000000000000000000000000000000000000000000000000000000000000` | `a7cb08a85c66c00f574c4379c2cae8b2c5fe225ea87a4b9780edce4461282185aebd83a18eaeea52463076a3dd1bc0e19c9dc3d9d895c5debe3b2f480aeffd49` |
| `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef` | `ac9a5d356b1160cb0e81a416bd06708d91af49d117c5b4e83a11fb9b9a5284327222a0551fa687f6c78fe8843877681ea97defe2565eaefd5be346992d013355` |
| `deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef` | `01c8aea4cdf6520ecb91acc817bb55994133f0b84477f5d92bb7f0a058a5f379afdef2488598408599d6d6aca2551def34f84c4e414b7778ae279d4ebe68ff9a` |

Python verification:

```python
import hashlib
SALT = "fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27"
def key(challenge: str) -> str:
    return hashlib.sha512((challenge + SALT).encode("ascii")).hexdigest()
assert key("00" * 32) == "a7cb08a85c66c00f574c4379c2cae8b2c5fe225ea87a4b9780edce4461282185aebd83a18eaeea52463076a3dd1bc0e19c9dc3d9d895c5debe3b2f480aeffd49"
```

## Appendix C — Read-only probe

`tools/probe.py` implements the handshake and a few GET-only probes. It defaults
to `daqOnly=true`, which avoids requesting SERVICE and makes `/channels`
available; it does not restrict other standard routes or server-side writes.
Using `--full` sets `daqOnly=false` and requires `--allow-service-mode` because
that login requests SERVICE mode.

```bash
python3 tools/probe.py 192.0.2.10 channels
python3 tools/probe.py 192.0.2.10 challenge
python3 tools/probe.py 192.0.2.10 get /info
python3 tools/probe.py 192.0.2.10 get /parameters
```
