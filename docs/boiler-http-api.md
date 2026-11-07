# Smart HV 2.7.4 — HTTP API reference (reverse-engineered)

Scope: the service listening on **TCP/80** of the `smart_hv_2.7.4` firmware.

Sources:

- `data/app/bin/app` — ARM 32-bit (ARMv8, hard-float), Qt 5, dynamically linked.
- `data/app/bin/app.kuna/` — Kuna decompilation (8,458 functions, 0 failures).
  Use `index.jsonl` to jump to a function; `app.asm` to verify critical branches.
- `data/smart-web/bin/smart-web` — a separate D-Bus/MQTT client. It is **not**
  the port-80 server; see [`smart-web.md`](smart-web.md) for its functions and
  MQTT job surface.

Endpoint and implementation descriptions below are derived from the decompiled
code. The live-validation note records observations from one LAN unit; it does
not establish behavior for every unit or firmware version. Items marked
**VERIFY** still need hardware confirmation before they are relied on.

See also: [`client-development-plan.md`](client-development-plan.md) for the
phased investigation, validation, and client-design plan, and
[`../tools/probe.py`](../tools/probe.py) for a safe read-only protocol probe.

### Live validation status

On one LAN unit, the following read-only path was confirmed with
`tools/probe.py`:

- `GET /challenge` returned `200 application/json` with a 64-character
  lowercase-hex `challenge`.
- Authentication with `daqOnly=true` returned `204` and set `auth-cookie`.
- Authenticated `GET /channels` returned `200 application/json` with a
  non-empty channel map and refreshed `auth-cookie`.

This confirms the daq-only telemetry path on that unit. The unit's firmware
version was not independently fingerprinted during this check. No full-API
(`daqOnly=false`) login was performed; SERVICE-mode behavior and routing
exclusivity remain code-derived and **VERIFY**.

---

## 1. Server stack and transport

- HTTP server: the [Stefan Frings QtWebApp](https://stefanfrings.de/qtwebapp/)
  `stefanfrings::HttpListener` / `HttpConnectionHandler`.
- Listener is created in a `setup` routine with a `QSettings` object that is
  populated programmatically:

  | Setting | Value |
  |---|---|
  | `port` | `80` (`QVariant(0x50)`) |
  | `minThreads` | `1` |
  | `maxThreads` | `10` |
  | `cleanupInterval` | `1000` ms |
  | `readTimeout` | `60000` ms |
  | `maxRequestSize` | `30,000,000` bytes |
  | `maxMultiPartSize` | `30,000,000` bytes |
  | SSL | not configured — **plain HTTP** |

- One `ServiceRequestHandler` is registered. There is **no static file handler**;
  unknown paths return `404 no valid route`.
- Wire format is ordinary HTTP/1.1. Responses carry `Content-Length`; the library
  also knows `Transfer-Encoding: chunked`, but the app handlers use fixed buffers.
- Success bodies are produced by `RouteHandler::writeObject`, which does:
  `200`, `Content-Type: application/json`, body = compact JSON.
- Error responses are produced with `HttpResponse::setStatus(code, message)`.
  The message is the **HTTP reason phrase**, not a JSON body. Expect an **empty
  body** on errors (exception: the message is visible in the status line/proxies).

### Protocols present in this firmware image; Modbus check

The inspected `smart_hv_2.7.4` update image provides no evidence of a Modbus
interface:

- No Modbus symbols, strings, or `QModbus*` classes were found in the
  decompiled `app`, `smart-web`, kernel image, or supplied kernel modules.
- The `app` binary links Qt SerialBus, but the recovered call sites use its
  **CAN** API (`QCanBus`, `QCanBusDevice`, `QCanBusFrame`). `BackendImpl::setupCan`
  and `anonymous_namespace::startCan` set up `can0`; the firmware also includes
  CAN device classes and `Io36` module operations over CAN.
- The exposed `/devices/can` endpoint sends or long-polls raw CAN frames. It is
  not a Modbus endpoint.
- The observed network services are the QtWebApp HTTP server on TCP/80,
  `smart-web`'s MQTT client, and the application's InfluxDB line-protocol
  exporter. The available TCP listeners resolve to the HTTP server; no
  Modbus/TCP listener or port 502 was identified. No Modbus RTU serial path was
  identified either.

**Conclusion:** do not assume this firmware exposes Modbus TCP or RTU. This is
an evidence-based finding about the supplied update image, not proof that no
separate base-rootfs service, external gateway/accessory, or other firmware
version supports Modbus. The SoC has UART hardware, but its presence alone does
not establish a Modbus implementation. For this image, the documented
field-bus path is CAN; see `/devices/can` in §5.4.

### Status codes seen in the code

| Code | Meaning in this API |
|---|---|
| `200` | JSON success (`writeObject`). |
| `204` | Write accepted, no body (`/authenticate`, CAN send, Pt1000 calibrate). |
| `400` | Bad JSON / missing key / wrong type / out-of-range value. |
| `401` | No or expired `auth-cookie`. |
| `403` | `/challenge` called less than 5 s after the previous one. |
| `404` | Unknown path, or method not allowed on some handlers. |
| `405` | Method not allowed (`/devices/nicrni/rauchgasfuehler`). |
| `500` | CAN send failed. |

---

## 2. Authentication

### 2.1 Overview

Authentication is a challenge/response handshake that ends in an opaque session
cookie. The signing key is derived from a **hard-coded public salt**, so this is an
access-control handshake, not a cryptographic secret.

### 2.2 `GET /challenge`

Response `200 application/json`:

```json
{ "challenge": "<64 lowercase hex chars>" }
```

- The challenge is 32 random bytes encoded as hex.
- The server keeps **one global challenge** (`AuthenticationHandler + 0x0c`).
- Requesting a new challenge is rate-limited: if less than **5000 ms** have elapsed
  since the previous challenge request, the server returns `403 wait between challenge requests`.
- Any HTTP method is accepted, but use `GET`.

### 2.3 `POST /authenticate`

Request body (`application/json`):

```json
{ "key": "<128 lowercase hex chars: SHA-512>", "daqOnly": false }
```

Key derivation (exact, from `handleAuthenticationRequest`):

```text
key = lower_hex( SHA512( ASCII(challenge) || "fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27" ) )
```

`AuthenticationHandler::handleAuthenticationRequest` constructs
`QCryptographicHash` with algorithm value `6`; in the firmware's Qt 5.12 API,
that enum value is `Sha512` (not `Sha256`).

where `challenge` is the 64-character hex string returned by `/challenge`
(not the 32 raw bytes).

Behaviour on success:

- Returns `204 No Content` after `QThread::sleep(1)` (expect ~1 s latency).
- Sets a cookie:

  ```http
  Set-Cookie: auth-cookie=<64 hex chars>; Max-Age=86400; Path=/
  ```

- Stores a session entry keyed by the cookie value, with an **idle timeout of
  300,000 ms (5 minutes)**.
- On every authenticated request the timer is restarted and the cookie is
  re-sent, so an active session stays alive.
- If `daqOnly` is **false or omitted** (the JSON default is `false`) and the
  current system mode is not `SERVICE` (`6`), it calls
  `SystemModel::setRequestedMode(6)` — **authentication switches the boiler to
  SERVICE mode**. This is a real, device-wide mode change, not a read-only
  login; see §3.
- If `daqOnly` is **true**, it sets a **global** `daqOnly` flag on the
  `AuthenticationHandler` (offset `0x34`). In that mode the server exposes only
  `/channels` (see §4).
- On failure (missing/incorrect `key`): `400`.

### 2.4 Session use

Send the cookie on every request:

```http
Cookie: auth-cookie=<value>
```

`AuthenticationHandler::isAuthenticated`:

- Reads the `auth-cookie` cookie.
- Looks up the value in a global `QMap<QString, QSharedPointer<QElapsedTimer>>`.
- Returns `401` if absent/unknown.
- If the entry is older than 300 s (idle), removes it and returns `401`.

### 2.5 Global-state caveats (important for a client)

These are properties of the server, not of your connection:

1. **Only one challenge exists at a time.** Two clients calling `/challenge`
   concurrently will overwrite each other's challenge; the older key then fails
   with `400`.
2. **The 5 s rate limit is global.**
3. **`daqOnly` is a single global flag.** One client authenticating with
   `daqOnly=true` changes routing for every session on the device until a later
   `/authenticate` sets it back to false.
4. **Authentication forces `SERVICE` mode** unless `daqOnly=true`. Omitting
   `daqOnly` counts as `false`. The mode is latched and normally needs a reboot
   to clear (see §3).

A client must therefore serialise authentication and treat mode changes as
device-wide operations.

### 2.6 Minimal protocol script

```bash
HOST=http://<boiler-ip>
SALT=fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27

CHALLENGE=$(curl -s "$HOST/challenge" | python3 -c 'import sys,json;print(json.load(sys.stdin)["challenge"])')
KEY=$(printf '%s%s' "$CHALLENGE" "$SALT" | sha512sum | cut -d' ' -f1)

# daqOnly=false → full API, but switches the boiler to SERVICE mode.
# SERVICE is latched; a reboot is normally required to return to IDLE.
curl -i -c cookies.txt -X POST "$HOST/authenticate" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\",\"daqOnly\":false}"

curl -i -b cookies.txt "$HOST/info"
```

---

## 3. SERVICE mode (the side effect of a full login)

`SystemMode` is a **device-wide** operating mode, not a per-session or
per-connection setting. `HvStateManager::transitionToState` (`0xa0438`) decodes
it as:

| value | name |
|---|---|
| 0 | `IDLE` |
| 1 | `AUTO` |
| 2 | `MANUAL` |
| 3 | `ERROR_HALT` |
| 4 | `BOILER` |
| 5 | `CHIMNEY_SWEEPER` |
| 6 | `SERVICE` |
| 7 | `SOLAR` |

`SERVICE` (6) is a technician/maintenance state.
`StateManager::getCommonStateFromMode` (`0x1b1d7c`) maps it to a state whose
display name is `ServiceMode`; its `begin()` is `ServiceModeState::begin`
(`0x267940`), and it logs `"Neuer Modus: ServiceMode"`.

### 3.1 What entry does

`ServiceModeState::begin`:

1. Puts the boiler module into `ModuleState::SERVICE` via
   `KesselBase::ServiceState` (`0x14efa4` → `changeStateTo(6)`).
2. Propagates SERVICE to every child module: `HvKessel::AblaufService`
   (`0x43544`) calls `changeStateTo(6)` on each child.
3. Drives all configured relays, simple motors, PWM motors and steppers to
   their off/zero state (loops over the device provider's
   `QVector<QSharedPointer<...>>` collections).
4. Calls `LedControl::AblaufService` (`0xe45a4`), which deletes LED callbacks
   and schedules the literal `"leds_off"`.

`ModuleBase::Ablauf` (`0x156c90`) dispatches on module state; for state 6 it
calls only `AblaufService`. `ModuleBase::AblaufService` is empty,
`HvKessel::AblaufService` only re-propagates SERVICE, and
`LedControl::AblaufService` only handles LEDs. The normal control routines
(`AblaufWork`, `AblaufIdle`, `AblaufReady`, …) are therefore **not run**.

### 3.2 Behavioural impact

| Area | Effect in SERVICE |
|---|---|
| Heating regulation | Suspended |
| Burner sequencing | Not run (module state is SERVICE, not WORK/READY) |
| Pumps, fans, mixers, relays | Forced off at entry; no automatic re-energising |
| Motors / steppers | Forced to zero/off at entry |
| LEDs | Callbacks deleted, `"leds_off"` scheduled |
| Manual `/devices/...` writes | Still effective; handlers call hardware setters directly and show no visible mode check |
| Other clients | Affected: the mode is global |
| Persistence | Written to EEPROM, but boot converts 6 → 0 |

> **Safety.** `ServiceModeState::begin` does not appear to run a controlled
> shutdown or cooldown before zeroing outputs. Entering SERVICE during active
> combustion may stop the fan/pump abruptly. **VERIFY** hardware interlocks on a
> cold, attended boiler before relying on this.

### 3.3 Entry and exit

Entry:

- `POST /authenticate` with `daqOnly` **absent or `false`** → the handler calls
  `SystemModel::setRequestedMode(6)`.
- `DBusSystemModelAdaptor::setSystemMode` (`0xf1704`) refuses SERVICE and
  MANUAL; its guard `if (v1 && v2 != 7) return;` lets only `SOLAR` through in
  that group. So the DBus/UI path is not an entry point.

Exit:

- `ServiceModeState::end` (`0x267844`) is empty.
- `HvStateManager::transitionToState` (`0xa0438`) latches the mode: when the
  active mode is 6 and any other mode is requested, it calls
  `StateManager::resetToState(6)`, i.e. it re-enters SERVICE instead of leaving.
- No logout/session-expiry handler resets the mode.
- Reboot is the practical exit: `InitializationState::checkPerformanceClass`
  (`0xa0eb8`) reads the persisted mode and coerces values `3..255` except `4`
  to `0` (`IDLE`), so persisted `6` becomes `0`.

**VERIFY** on hardware that SERVICE is really reboot-only in practice and that
no UI/update path performs a hidden transition.

### 3.4 Do not confuse the two "SERVICE" names

- `SystemMode::SERVICE = 6` — the plant operating mode described above.
- `SecurityLevel::SERVICE = 0x14` (20) — a technician **authorization** level
  granted by `SecurityManagerImpl::authorizeService` (`0x275878`) for 30 min
  (`internalAuthorizeService` → `setSecurityLevel(0x14, 1800000)`), validated
  against a `ddMM` date code. It gates parameter/UI operations.

The port-80 login sets `daqOnly` and requests mode 6; it does not visibly call
`authorizeService`. **VERIFY** whether web handlers reach the security level
indirectly.

### 3.5 Client rule

Always send `"daqOnly": true`. Omitting `daqOnly` is not neutral — it is a
device-wide mode change. Treat a full login as a write operation that requires
explicit user consent.

---

## 4. Routing model

`ServiceRequestHandler::service` evaluates routes in this order:

1. If path is `/challenge` or `/authenticate` → `AuthenticationHandler`.
2. Else, require a valid session cookie; otherwise `401`.
3. If the global `daqOnly` flag is set → the **only** handler is `ChannelHandler`
   (`/channels`). Everything else is `404 no valid route`.
4. Otherwise iterate 24 registered route handlers (below) and use the first whose
   `canHandle(path)` matches.
5. If none match → `404 no valid route`.

### Route handlers in the full-API vector

`RelaisHandler`, `InterruptHandler`, `StockerHandler`, `CanHandler`,
`UsbHandler`, `LambdaProbeHandler`, `MixerHandler`, `StepperHandler`,
`LinearMotorHandler`, `ExhaustFanHandler`, `NiCrNiHandler`, `EEPromHandler`,
`RtcHandler`, `PwmPumpHandler`, `PcbHandler`, `FuseHandler`, `InfoHandler`,
`LedHandler`, `ParameterHandler`, `UpdateHandler`, `ResetHandler`,
`BackupHandler`, `Pt1000Handler` — plus the daq-only `ChannelHandler`.

**Consequence:** in normal (`daqOnly=false`) mode there is **no `/channels`**;
in `daqOnly=true` mode there is **only `/channels`**. A client that needs both
live channels and configuration must switch modes and accept the global effect.
**VERIFY** this against hardware early — it drives the whole client design.

---

## 5. Endpoint reference

Unless stated otherwise:

- JSON request bodies are parsed with `QJsonDocument::fromJson`; malformed or
  missing keys → `400`.
- `GET` handlers that accept a body read it with `HttpRequest::getBody()`.
  This matters for `/parameters` (see below): the selection is sent as a **GET
  body**, not a query string.
- Successful device writes usually return the **new state** as JSON `200`
  (they call `handleInfo`/`writeObject`), not `204`.

### 5.1 Auth and system

| Method | Path | Body → Response |
|---|---|---|
| GET | `/challenge` | → `200 {"challenge":"<hex>"}`; `403` if <5 s since last. |
| POST | `/authenticate` | `{"key":str,"daqOnly":bool}` → `204` + `Set-Cookie`; `400`. |
| GET | `/info` | → `200 {"mac","systemId","commissionNumber","articleSerialNumber","softwareVersion"}` |
| POST | `/info` | `{"commissionNumber"?:str,"articleSerialNumber"?:str}` → `200` (same shape as GET); `400` if neither valid. |
| POST | `/info/systemid/generate` | no body → `200 {"systemId":str}`. *(Path takes precedence; method is not checked.)* |
| GET | `/pcb` | → `200 {"internalTemperature":num,"internalVoltage":num}` |

### 5.2 Parameters

Parameter objects (from `ParameterBase::addJson` + `Parameter::allDataToJsonObject`)
have this shape:

```json
{
  "name": "K3",
  "displayName": "…",
  "categorisedName": "…",
  "description": "…",
  "helpText": "…",
  "type": "INTEGER|FLOAT|TEXT|BOOLEAN|CHOICE|MULTIPLE_CHOICE|DATETIME|PID_PART|DAILY_CLOCK_RANGE|WEEKLY_CLOCK_RANGE|DATE_TIME_RANGE|UNKNOWN_TYPE",
  "active": true,
  "securityLevel": "USER|PLUMBER|SERVICE|DEVELOPER|UNKNOWN_LEVEL",
  "category": <int>,
  "last_changed": "<datetime>",
  "value": <typed>,
  "factory_value": <typed>,
  "performance_classes": { … },
  "children": [ … ]
}
```

| Method | Path | Body → Response |
|---|---|---|
| GET | `/parameters` | empty body → `200 {"parameters":[<parameter>, …]}` (all). |
| GET | `/parameters` | `{"parameters":["K3","T1", …]}` → `200 {"parameters":[…]}` (selected). |
| POST | `/parameters` | `{"parameters":[{"name":"K3","value":…}, …]}` → `200 {"parameters":[…]}` (updated values). Also marks the system configured and can change `requestedMode` side effects. `400` with a reason on unknown name / bad value. |

Notes:

- `GET` **with** a JSON body is required for the selected-list form. Avoid proxies
  that strip GET bodies; talk to port 80 directly.
- Parameter values are type-dependent (`int`, `double`, `bool`, `string`,
  structured for clock ranges, etc.). The `type` field is authoritative.

### 5.3 Channels (daq-only)

| Method | Path | Response |
|---|---|---|
| any | `/channels` | `200 {"<channelName>": <number>, …}` |

- The handler has no method/path sub-check beyond `canHandle`; it always returns
  the current exported channel map.
- Channel names are registered dynamically by `log*Daq` functions and are not a
  fixed compile-time list. Discover them at runtime and cache.
- Only reachable while the global `daqOnly` flag is true.

### 5.4 Devices

| Method | Path | Body → Response |
|---|---|---|
| GET | `/devices/fuse` | → `200 {"state":<int/bool>}` |
| GET | `/devices/interrupts/<name>` | → `200 {"state":<int/bool>}`. `<name>` ∈ `ext-anf`, `fuellstand`, `sk-abs`, `sk-stb`, `stb-uc`, `tuerkontakt`, `x1x2`; else `404`. |
| GET | `/devices/leds` | → `200 {"requested":…,"selected":…}` (no body). |
| any | `/devices/leds` | `{"color":"red|green|blue|white|none"}` → `200` new LED state; `400 invalid color`. Body presence switches set vs. read. |
| GET | `/devices/nicrni/rauchgasfuehler` | → `200 {"value":num,"valid":bool}`; non-GET → `405`. |
| GET | `/devices/usb` | → `200 {"usbDrives":[{"path","mounted","files"}]}` |
| GET | `/devices/eeprom` | → `200 {"pcbSerialNumber","pcbRevision","pcbProductionWeek","pcbProductionYear","deviceSerialNumber","somInfo","systemId","performanceClass"}` |
| PUT | `/devices/eeprom` | any of the above string/number fields → `200` new state; `400` on type/length/range errors. |
| GET | `/devices/rtc` | → `200 {"utcDatetime":"<ISO-8601>"}` |
| PUT | `/devices/rtc` | `{"utcDatetime":"<ISO-8601>","doTrim"?:bool}` → `200` state incl. `rtcDateTime`, `lastConfigured`, `msOffsetPerDay`, `trimMilliseconds`; `400` on bad datetime. |
| GET | `/devices/can` | **long-poll**: waits up to 5 s for a CAN frame → `200 {"id":"<hex>","data":"<hex>"}` or `204` on timeout. |
| POST | `/devices/can` | `{"id":"<hex>","data":"<hex>"}` → `204` on success; `400` bad id/data; `500 send failed`. |
| GET | `/devices/exhaust-fan` | → `200 {"running":bool,"rpm":num,"direction":num,"fault":bool}` |
| PUT | `/devices/exhaust-fan` | `{"direction"?,"rpm"?,"running"?}` → `200` new state; `400`. |
| GET | `/devices/linear-motor` | → `200 {"direction","running","current"}` |
| PUT | `/devices/linear-motor` | `{"direction"?,"running"?}` → `200` new state; `400`. |
| GET | `/devices/pwm-pump` | → `200 {"msPeriod":num,"speed":num}` |
| PUT | `/devices/pwm-pump` | `{"msPeriod"?,"speed"?}` → `200` new state; `400`. |
| GET | `/devices/stepper/<name>` | `<name>` ∈ `primaerluftklappe`, `sekundaerluftklappe` → `200 {"running","direction","speed","steps","currentPercent"}`; other → `404`. |
| PUT | `/devices/stepper/<name>` | `{"direction"?,"speed"?,"steps"?,"currentPercent"?,"running"?}` → `200` new state; `400`. |
| GET | `/devices/stocker` | → `200 {"direction","speed","rpm","running"}` |
| PUT | `/devices/stocker` | `{"direction"?,"speed"?,"running"?}` → `200` new state; `400`. |
| POST | `/devices/stocker/testrun` | `{"duration":…,"speed":…,"direction"?}` → `200` result incl. `rpm`. |
| GET | `/devices/mixer` | → `200 {"state":"OFF|OPENING|CLOSING|UNKNOWN"}` |
| PUT | `/devices/mixer` | `{"state":"OFF|OPENING|CLOSING"}` → `200` state; `400` for other values. Handler sleeps ~1 s. |
| GET | `/devices/relais/<name>` | `<name>` ∈ `sk-ein`, `zuendung`, `pufferpumpe`, `saugturbine`, `option` → `200 {"state":<int/bool>}`; other → `404`. |
| POST | `/devices/relais/<name>` | `{"state":<int|bool>}` → `200` new state; `400` on missing/wrong type. |
| GET | `/devices/pt1000/<name>` | `<name>` ∈ `Aussentemperatur`, `Kesselfuehler`, `Ruecklauf`, `Rauchgas`, `Puffer-oben`, `Puffer-mitte`, `Puffer-unten` → `200 {"index","name","value","raw_value","calibrationOffset","calibrationGradient"}` |
| POST | `/devices/pt1000/<name>/calibrate-start` | `{"temperature":num}` → `204`; `400`. |
| POST | `/devices/pt1000/<name>/calibrate-end` | `{"temperature":num}` → `204`; `400`/`404`. |
| GET | `/devices/lambda-probe` | → `200` object incl. `state` (`OKAY`, `NOT_READY`, `NO_VALID_STATE`, `WRONG_POLARITY`, `UNKNOWN`, `INVALID`, `NOT_CONNECTED`), `current`, `setCurrentPercent`, `heatingSeconds`. |
| POST | `/devices/lambda-probe/calibrate` | → `200 {"o2Raw","o2Correction"}`. |

### 5.5 Maintenance / lifecycle

| Method | Path | Body → Response |
|---|---|---|
| GET | `/backup` | → `200 {"synchronizationInProgress":bool,"synchronized":bool}` |
| GET | `/backup/sync` | same as `GET /backup`. |
| POST | `/backup/sync` | no body → starts partition sync, then `200` status JSON (the `GET` shape). |
| POST | `/update` | `{"updatePath":"<path>"}` → `200` (empty body). `400` if key missing/wrong type. |
| any | `/factory-reset` | no body → performs a full reset (`setDoTotalReset(true)`, clears measurement mode); no explicit status, so `200`. **Destructive.** |

> `/factory-reset` does not check the HTTP method or body; the `canHandle` path
> check is the only guard.

---

## 6. Field-by-field notes for a client

- **Numbers**: channels are JSON doubles; device states mix ints and booleans.
  Accept both (`0/1`, `true/false`) where the server documents "Integer or Boolean".
- **`direction`**: integer in device state objects (steppers, fans, stocker).
- **`speed`, `steps`, `rpm`, `msPeriod`, `currentPercent`**: numeric; validation
  ranges live server-side and return `400` with a reason phrase.
- **`utcDatetime`**: string, parsed by Qt; send ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`).
- **`state` on relais**: accepts JSON bool or int; read back as int/bool.
- **`state` on mixer**: strict string enum.
- **`color` on LEDs**: strict `red|green|blue|white|none`.
- **Error details**: because the reason phrase carries the message, a client that
  needs the text must read the status line (most HTTP libraries expose it), not
  the body.

---

## 7. Verification checklist for a live unit

### Confirmed on one unit

1. `GET /challenge` → `200 application/json`; the body contains a
   64-character lowercase-hex challenge.
2. Authenticate with `daqOnly=true` → `204` and `Set-Cookie`.
3. Authenticated `GET /channels` → `200 application/json` with a non-empty map;
   the response refreshes `Set-Cookie`.

These observations validate only the daq-only telemetry path. They do not prove
that other routes are hidden in daq-only mode, nor do they validate the full API.

### Remaining checks

1. `GET /challenge` twice back-to-back → confirm the second is `403`.
2. In daq-only mode, request a non-channel route and confirm it is unavailable
   (`404` expected; **VERIFY**).
3. Sleep 6 minutes, reuse cookie → expect `401`.
4. Make an authenticated request every 60 s for 10 min → session stays alive and
   `Set-Cookie` is refreshed with the same value.
5. Confirm `GET /parameters` with a JSON body works through your HTTP client
   (some libraries refuse GET bodies).
6. Confirm `GET /devices/can` blocks for ~5 s and returns `204` with no frame.
7. Confirm error bodies are empty and messages are only in the status line.

Full-API/SERVICE checks are intentionally **not** part of the read-only
validation. Only if they are explicitly needed, perform them on a cold,
attended boiler that you are prepared to power-cycle; see §3. **Do not** use
`daqOnly=false` or omit `daqOnly` for routine channel monitoring.

---

## 8. Security assessment (for the plan)

- The "secret" used to sign challenges is a fixed string in the firmware, and
  `/challenge` is unauthenticated. Anyone who can reach port 80 can compute a
  valid `key`. **Do not expose port 80 to an untrusted network.**
- No TLS. No CSRF token. Session cookie is the only state.
- Session idle timeout is 5 min; cookie lifetime is 24 h but the map entry is
  removed after 5 min idle.
- Rate limiting exists only on `/challenge` (5 s).
- `daqOnly` and the current challenge are global, so one client can disrupt
  another's login and change the routing mode for everyone.
