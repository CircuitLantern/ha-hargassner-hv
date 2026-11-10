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
[`../tools/probe.py`](../tools/probe.py) for a guarded GET-oriented probe. It
blocks known method-agnostic mutator paths but is not a general API sandbox.
For English meanings, hydraulic context, and a crosswalk to the German service
and electrical manuals, see [`parameter-guide.md`](parameter-guide.md).
The [independent async library](library.md) currently implements USER parameter
reads and opt-in reviewed numeric writes only, with no SERVICE authentication
option. Its thin CLI uses the same library; other documented routes are outside
that package's scope.

### Live validation status

The local captures include successful read-only requests made with
`tools/probe.py` and no `--full` flag:

- `GET /challenge` returned `200 application/json` with a 64-character
  lowercase-hex `challenge`.
- Authentication with `daqOnly=true` returned `204` and set `auth-cookie`.
- Authenticated `GET /channels`, `/info`, and `/parameters` returned `200`
  `application/json`; the responses refreshed `auth-cookie`.

`captures/info.sample.jsonc` reports `softwareVersion: smart_hv_3.4.0`, so these
are live observations from a 3.4.0 unit, not direct live validation of the
2.7.4 binary analyzed below. No `daqOnly=false` login was performed; its
SERVICE-mode side effect remains code-derived and **VERIFY** on hardware.

On 2026-11-09, an operator-approved test with `tools/set_parameter.py` changed
the zone-1 daytime target (`name="4"`) from **20.5 → 21.0 → 20.5 °C**. Both
parameter POSTs returned HTTP 200 with the requested value, and each separate
GET read-back matched. All logins explicitly used `daqOnly=true`; no SERVICE
request was sent. The original value was restored. The unit had previously
reported 3.4.0; identity/version was not re-read during this test. See the
[live validation record](parameter-setter.md#live-validation--2026-11-09) for
the scope and limitations: physical effects and reboot persistence remain
**VERIFY**, as do writes to other parameters/firmware versions.

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

For normal LAN clients, use `daqOnly=true`: both standard parameter routes and
the additional `/channels` route remain available, without the authentication
handler requesting SERVICE. Parameter writes are real controller configuration
changes, not channel-cache edits; see §5.2. They still require a client-side
allowlist and read-back verification.

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
{ "key": "<128 lowercase hex chars: SHA-512>", "daqOnly": true }
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
- If `daqOnly` is **true**, it sets a **global** flag on the
  `AuthenticationHandler` (offset `0x34`). This makes the dispatcher check the
  special `/channels` handler before the standard route vector. A non-channel
  path then falls through to the standard handlers; `daqOnly=true` is **not** a
  channels-only or read-only restriction (see §4).
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
   `daqOnly=true` enables the special `/channels` dispatch for every session;
   regular routes remain available. A later `/authenticate` with false disables
   that channel route. These effects are device-wide.
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

# daqOnly=true avoids requesting SERVICE mode. It enables /channels; standard
# routes such as /parameters are also handled with this flag.
curl -i -c cookies.txt -X POST "$HOST/authenticate" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\",\"daqOnly\":true}"

curl -i -b cookies.txt "$HOST/channels"
curl -i -b cookies.txt "$HOST/parameters"
```

---

## 3. SERVICE mode (the side effect of `daqOnly=false` authentication)

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

The port-80 login sets `daqOnly` and requests mode 6 only if the flag is false
and mode 6 is not already active; it does not call `authorizeService`. The
inspected HTTP parameter-write path does not compare the parameter's security
level with `SecurityManager` authorization. The level is used for UI filtering,
but is not an HTTP write permission boundary in that path (see §5.2).

### 3.5 Client rule

Send `"daqOnly": true` for ordinary client sessions to avoid the SERVICE-mode
request and to make `/channels` available. **Do not treat this flag as a
read-only permission boundary:** standard read and write handlers remain
routable with either setting. A client that promises read-only behavior must
enforce that policy itself. Omitting `daqOnly` is not neutral; it defaults to
false and requests the device-wide SERVICE mode.

---

## 4. Routing model

`ServiceRequestHandler::service` evaluates routes in this order:

1. If path is `/challenge` or `/authenticate` → `AuthenticationHandler`.
2. Else, require a valid session cookie; otherwise `401`.
3. If `daqOnly` is true, check `ChannelHandler` for `/channels`; if it matches,
   dispatch it.
4. If the path was not handled above, iterate the standard route-handler vector
   and use the first whose `canHandle(path)` matches. This fallback runs whether
   `daqOnly` is true or false.
5. If no handler matches → `404 no valid route`.

### Standard route-handler vector

`RelaisHandler`, `InterruptHandler`, `StockerHandler`, `CanHandler`,
`UsbHandler`, `LambdaProbeHandler`, `MixerHandler`, `StepperHandler`,
`LinearMotorHandler`, `ExhaustFanHandler`, `NiCrNiHandler`, `EEPromHandler`,
`RtcHandler`, `PwmPumpHandler`, `PcbHandler`, `FuseHandler`, `InfoHandler`,
`LedHandler`, `ParameterHandler`, `UpdateHandler`, `ResetHandler`,
`BackupHandler`, `Pt1000Handler`. `ChannelHandler` is a separate special route
that is checked when `daqOnly=true`.

**Consequence:** `daqOnly=true` makes `/channels` available **in addition to**
the standard routes. `daqOnly=false` leaves the standard routes available but
does not dispatch `/channels`; it also requests SERVICE during authentication.
The flag is not an API privilege or read-only mode. The captured 3.4.0 unit
successfully served `GET /parameters` with `daqOnly=true`; the 2.7.4 fallback
behavior is also visible in `ServiceRequestHandler::service` (`0x12c524`) and
the corresponding assembly.

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

The [English parameter guide](parameter-guide.md) explains the settings and
their dependencies using the manuals. This section documents wire formats,
setter behavior, and captured metadata; menu numbers are not always API names.

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
| POST | `/parameters` | `{"parameters":[{"name":"4","value":20.5}, …]}` → `200 {"parameters":[…]}` (read-back objects). Runs parameter setters, invokes file/EEPROM persistence, and marks the system configured. Unknown names / malformed requests can produce `400`; a `200` is not proof that every value changed. |
| PUT | `/parameters` | Reaches the same update handler in the analyzed 2.7.4 binary; prefer POST as the explicit update convention. **VERIFY** on other firmware versions. |

Notes:

- `GET` **with** a JSON body is required for the selected-list form. Avoid proxies
  that strip GET bodies; talk to port 80 directly.
- Parameter values are type-dependent (`int`, `double`, `bool`, `string`,
  structured for clock ranges, etc.). The `type` field is authoritative.
- `name` is the wire identifier used for lookup, not `displayName` or the
  channel name. For example, `name="4"` is the heating-circuit-1 daytime
  room-temperature parameter displayed as `Hk1-2`.

#### Parameter-write mechanism and authorization

The 2.7.4 code contains a real write mechanism for customer-facing settings,
including room-temperature setpoints, hot-water setpoints, schedules, and
display preferences. `daqOnly=true` does not prevent these writes, and the
parameter handler neither requires SERVICE nor unconditionally requests it.
Individual setting changes can still affect heating operation or trigger other
callbacks; avoiding SERVICE is not a guarantee that a write is harmless.

| Evidence | Address | What the code does |
|---|---|---|
| `ServiceRequestHandler::service` | `0x12c524` | Falls through to standard routes when the special channel handler does not match, including with `daqOnly=true`. |
| `ParameterHandler::handle` | `0x262f90` | GET selects the reader; every non-GET method selects `handlePost`. The method comparison and branch were checked in assembly. |
| `ParameterHandler::handlePost` | `0x261f20` | Parses a `parameters` array, looks up entries by `name`, and dispatches their JSON update to the main control thread. |
| `ParameterManagerImpl::getByName` | `0x16d80c` | Looks up the registered parameter without a security-level filter. |
| Main-thread update callback | `0x261eb0` | Calls virtual slot `+0x38`; the binary's parameter vtables identify this as `updateFromJson`. |
| Float JSON updater / setter | `0x96564` / `0x967c8` | Parses a numeric `value`, validates it, writes the stored value, and emits `ParameterBase::valueChanged` if it changes. |
| `ParameterManagerImpl::saveAll` | `0x16d29c` | Invokes file and EEPROM save paths; the HTTP handler calls it after processing the array. Persistence errors/availability still need live verification. |
| `SystemModel::setSystemConfigured` | `0x1b275c` | Marks the system configured. Its connected callback at `0xb2e58` records/persists the statistic, not an unconditional SERVICE request. |

No comparison with the caller's security level was found in the inspected
HTTP lookup → JSON updater → scalar setter path. In contrast,
`ParameterManagerImpl::getParametersForCurrentLevel` (`0x16eadc`) explicitly
filters parameters against the current level for the UI. Therefore `USER`,
`PLUMBER`, `SERVICE`, and `DEVELOPER` should be treated as catalog/UI
classifications, not evidence of HTTP-layer authorization. A client must enforce
its own allowed-parameter policy. **VERIFY** enforcement and side effects in
the deployed firmware; the analyzed executable is 2.7.4 and the live captures
are from 3.4.0.

#### Request scope and read-back requirements

Send **only the entries you want to change**, not the complete catalog. A
minimal entry consists of the actual `name` and a correctly typed `value`:

```json
{
  "parameters": [
    {"name": "4", "value": 20.5}
  ]
}
```

This shape was traced in 2.7.4 and subsequently exercised for `name="4"` in
the limited [live round-trip test](parameter-setter.md#live-validation--2026-11-09).

For a restricted one-shot implementation, see the
[guarded parameter setter](parameter-setter.md). It previews by default, always
authenticates with `daqOnly=true`, and permits only reviewed numeric names. Its
offline tests do not establish live write compatibility or physical effects.

- **Read back the stored value:** for floats, `updateFromJson` returns whether
  JSON parsing succeeded and does not propagate the setter's range-validation
  result. An out-of-range numeric value can leave the parameter unchanged while
  still receiving `200`. The returned objects and a subsequent GET matter more
  than the status alone.
- **Enforce advertised increments in the client:** the float validator
  (`0x183910`) checks the minimum/maximum, not alignment to the increment.
- **Batches are not atomic:** entries are processed sequentially, with no
  rollback of earlier changes if a later entry is unknown or malformed. Prefer
  one intentional change per request during validation.
- **Do not round-trip unrelated metadata:** the JSON updater accepts `value`
  and the base parser can also accept `last_changed`. Copying a full GET object
  is unnecessary and can submit stale metadata.
- **`USER` is not a safety guarantee:** user-level ignition, schedule, time,
  and heating settings can have immediate physical effects. The successful live
  target/read-back test does not validate other USER writes or physical effects.

#### All USER parameters: code catalog and live sample

The table contains **all 44 USER entries** in
[`captures/parameters.sample.jsonc`](../captures/parameters.sample.jsonc),
including inactive ones. `displayName`, description, type, limits/increment,
and active state are copied from that **3.4.0** sample (descriptions are in the
unit's selected language). They are not assumed identical across firmware or
configurations.

The last column records independent 2.7.4 code evidence: **39 named USER
definitions** were found in `HardcodedParameterProvider::get` (`0x60b70`), and
all 39 names occur in the sample. The constructor's security-level argument is
`0`, serialized as `USER` by `ParameterBase::addJson` (`0x16af74`). Parameter
names/display names were resolved through `ParameterName::ParameterName`
(`0x16b84c`), including binary string constants. The five **Capture only** rows
are USER in the sample but were not identified as named USER catalog entries in
the analyzed 2.7.4 provider; their write implementation remains **VERIFY**.
Unnamed fallback/default objects are not catalog entries.

| API `name` | `displayName` | Captured description | Type | Limits / increment | Active | 2.7.4 USER definition |
|---|---|---|---|---|---|---|
| `Setup1` | `E1` | Veille affichage | `BOOLEAN` | — | Yes | Yes |
| `Setup1a` | `E1a` | Veille affichage après | `INTEGER` | 10–200 min; step 10 | Yes | Yes |
| `Setup2` | `E2` | Aff. Accueil après | `INTEGER` | 1–100 min; step 1 | Yes | Yes |
| `Setup3` | `E3` | Luminosité écran | `INTEGER` | 5–100 %; step 5 | Yes | Yes |
| `Setup4` | `E4` | Langue système | `CHOICE` | — | Yes | Yes |
| `Setup4a` | `E4a` | Chemin vers fichier langue | `TEXT` | — | Yes | Yes |
| `Setup5` | `E5` | Régler heure | `BOOLEAN` | — | Yes | Yes |
| `Setup6` | `E5a` | Heure | `DATETIME` | — | Yes | Yes |
| `Setup7` | `E6` | Fus. hor. | `CHOICE` | — | Yes | Yes |
| `Setup8` | `E7` | Aff. Accueil | `MULTIPLE_CHOICE` | — | Yes | Yes |
| `1` | `B1-1` | Ball. 1 horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | Yes | Yes |
| `1a` | `B1-1` | Ballon 1 horl. hebd. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `2` | `B1-2` | Ballon 1 temp. consigne | `INTEGER` | 0–75 °C; step 1 | Yes | Yes |
| `boiler1.zirk.freigabe` | `B1-3` | Ballon 1 circul. horl. jour. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `HP1` | `BA-1` | Ball. A horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `HP1a` | `BA-1` | Ballon A horl. hebd. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `HP2` | `BA-2` | Ballon A temp. consigne | `INTEGER` | 0–75 °C; step 1 | No | Yes |
| `boilerA.zirk.freigabe` | `BA-3` | Ballon A circul. horl. jour. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `3` | `Hk1-1` | Zone 1 horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | Yes | Yes |
| `3a` | `Hk1-1` | Zone 1 horl. hebd. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `4` | `Hk1-2` | Zone 1 temp. amb. jour | `FLOAT` | 0–30 °C; step 0.5 | Yes | Yes |
| `5` | `Hk1-3` | Zone 1 temp. temp. amb. abais. | `FLOAT` | 0–30 °C; step 0.5 | Yes | Yes |
| `6` | `Hk2-1` | Zone 2 horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | Yes | Yes |
| `6a` | `Hk2-1` | Zone 2 horl. hebd. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `7` | `Hk2-2` | Zone 2 temp. amb. jour | `FLOAT` | 0–30 °C; step 0.5 | Yes | Yes |
| `8` | `Hk2-3` | Zone 2 temp. temp. amb. abais. | `FLOAT` | 0–30 °C; step 0.5 | Yes | Yes |
| `HP3` | `HkA-1` | Zone A horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `HP3a` | `HkA-1` | Zone A horl. hebd. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `HP4` | `HkA-2` | Zone A temp. amb. jour | `FLOAT` | 0–30 °C; step 0.5 | No | Yes |
| `HP5` | `HkA-3` | Zone A temp. temp. amb. abais. | `FLOAT` | 0–30 °C; step 0.5 | No | Yes |
| `11` | `Hk-11` | Toutes zones arrêt via temp. ext. | `INTEGER` | -20–30 °C; step 1 | Yes | Yes |
| `12` | `Hk-12` | Toutes zones arrêt via (coupure jour) | `INTEGER` | -20–30 °C; step 1 | Yes | Yes |
| `13` | `Hk-13` | Toutes zones arrêt via (coupure nuit) | `INTEGER` | -15–10 °C; step 1 | Yes | Yes |
| `fws.freigabe.tagesuhr` | `FWS1-1` | FWS Dispo. Horl. jour. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `fws.freigabe.wochenuhr` | `FWS1-1` | FWS Disponibilité Horl. hebdo | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `fws.auslaufSoll` | `FWS1-2` | FWS Température sortie | `INTEGER` | 45–60 °C; step 1 | No | Yes |
| `fws.freigabe.tagesuhr.zirk` | `FWS1-5` | FWS Circul. Horl. journ. Lu-Di | `DAILY_CLOCK_RANGE` | — | No | Yes |
| `fws.freigabe.wochenuhr.zirk` | `FWS1-5` | FWS Circul. Horl. hebdo. Lu-Di | `WEEKLY_CLOCK_RANGE` | — | No | Capture only |
| `kessel.fremdwaerme.freigabezeit` | `Fw1` | Autoris. chal. ext. | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `kombi.freigabezeit` | `EKombi0` | Autoris. chaud. esclave | `WEEKLY_CLOCK_RANGE` | — | No | Yes |
| `mode.remote_control.enabled` | `21b` | Autoris. télécommande | `BOOLEAN` | — | Yes | Capture only |
| `mode.remote_control.lock_duration` | `21c` | Temps verrouill. comm. dist. | `INTEGER` | 1–1500 min; step 1 | No | Capture only |
| `E18` | `E19` | Allum. imméd. | `BOOLEAN` | — | Yes | Capture only |
| `Z91` | `Z91` | Heure de démarrage au plus tôt | `DATETIME` | — | Yes | Capture only |

`Active` is the state at capture time, not a permission or a permanent
property. `—` means no scalar minimum/maximum metadata was supplied; it does
not mean unrestricted values. For choices, schedules, and dates, inspect the
live object's structured value and metadata. Discover the current catalog
before selecting any write allowlist; no row above is an instruction to test
the full advertised range on a running boiler.

### 5.3 Channels (conditional route)

| Method | Path | Response |
|---|---|---|
| any | `/channels` | `200 {"<channelName>": <number>, …}` |

- `ChannelHandler::canHandle` (`0x221b80`) matches the path, not the method.
  `ChannelHandler::handle` (`0x221bac`) reads the current channel values,
  builds a JSON object, and calls `RouteHandler::writeObject`; it does not read
  the request method/body or call a channel setter. Thus GET, POST, and PUT on
  this path dispatch the same snapshot response in the analyzed 2.7.4 code.
  There is no channel-write operation here; a POST/PUT body is ignored.
- Channel names are registered dynamically by `log*Daq` functions and are not a
  fixed compile-time list. Discover them at runtime and cache.
- The special `ChannelHandler` is checked when the global `daqOnly` flag is
  true. This does not disable the standard routes in that mode.
- A channel name is not a parameter name. Some observed channels are linked
  internally to writable parameters with different wire identifiers, so the
  lack of a setter in `/channels` does not establish that the underlying setting
  is unwritable. Use the parameter catalog and trace the relevant setter; do
  not submit channel names or a channel snapshot to `/parameters`.
- The live 3.4.0 capture contains only a GET sample. POST/PUT behavior above is
  code-derived from 2.7.4, not live-tested against the 3.4.0 unit.

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
3. Authenticated `GET /channels`, `/info`, and `/parameters` → `200` with JSON;
   the responses refresh `Set-Cookie`.

These observations show that ordinary GET routes are available with
`daqOnly=true` on the captured 3.4.0 unit. They do not independently validate
every route or prove 2.7.4 runtime behavior.

### Remaining checks

1. `GET /challenge` twice back-to-back → confirm the second is `403`.
2. Confirm on a 2.7.4 unit that a non-channel route such as `GET /parameters`
   also falls through to the standard handler vector with `daqOnly=true`.
3. Authenticate with `daqOnly=false` and confirm `/channels` is unavailable
   (`404` expected); perform this only if the SERVICE-mode side effect is
   explicitly accepted.
4. Sleep 6 minutes, reuse cookie → expect `401`.
5. Make an authenticated request every 60 s for 10 min → session stays alive and
   `Set-Cookie` is refreshed with the same value.
6. Confirm `GET /parameters` with a JSON body works through any additional HTTP
   clients/proxies (some refuse/strip GET bodies). The guarded setter's selected
   GET for `name="4"` worked in the live test.
7. Confirm `GET /devices/can` blocks for ~5 s and returns `204` with no frame.
8. Confirm error bodies are empty and messages are only in the status line.
9. If parameter writes are explicitly approved, validate one intended USER
   change with `daqOnly=true`, read it back, observe its physical effect, restore
   the prior value, and verify persistence. Test rejection and partial-batch
   behavior against the mock first. The zone-1 target round trip confirmed
   immediate write/read-back and restoration, not physical effects or persistence.

SERVICE-mode checks are intentionally **not** part of the read-only validation.
Only if they are explicitly needed, perform them on a cold, attended boiler
that you are prepared to power-cycle; see §3. For routine monitoring, use
`daqOnly=true`, but apply a client-side read-only endpoint allowlist.

---

## 8. Security assessment (for the plan)

- The "secret" used to sign challenges is a fixed string in the firmware, and
  `/challenge` is unauthenticated. Anyone who can reach port 80 can compute a
  valid `key`. **Do not expose port 80 to an untrusted network.**
- No TLS. No CSRF token. Session cookie is the only state.
- In the analyzed HTTP scalar parameter-write path, there is no security-level
  authorization check beyond the session gate. Catalog `securityLevel` labels
  and `daqOnly=true` are not a substitute for a client-side write allowlist.
- Session idle timeout is 5 min; cookie lifetime is 24 h but the map entry is
  removed after 5 min idle.
- Rate limiting exists only on `/challenge` (5 s).
- `daqOnly` and the current challenge are global, so one client can disrupt
  another's login and toggle `/channels` availability for everyone. The flag
  does not prevent access to the standard route vector.
