<!-- SPDX-License-Identifier: Apache-2.0 -->

# Development plan — reusable Hargassner client and Home Assistant integration

Status: the independent async library and thin testing CLI support **USER
parameters, six heating-curve slope and three circuit-mode exceptions, and read-only channels,
device info, and PCB diagnostics**. A separate Home Assistant custom integration
now implements config flow, coordinators, sensors, opt-in climate/numeric/mode controls,
and a guarded full-program action against HA 2026.11.1. It has synthetic/loopback
coverage, not live deployment or write validation. See [the integration guide](home-assistant.md) and the
[implemented API](library.md). Other installer-level parameters, identity mutation,
and other device routes are not part of the current delivery.

- Protocol reference: [`boiler-http-api.md`](boiler-http-api.md).
- Code-analysis baseline: `smart_hv_2.7.4`, ARM 32-bit Qt 5 application.
- Live read baseline: `smart_hv_3.4.0`, as reported in the local captures.
- Transport: local `http://<boiler>:80/`; no cloud dependency or TLS at the boiler.
- Final consumer: **Home Assistant**.

### Implemented extension: circuit climate entities

The [climate feature development plan](climate-development-plan.md) records the
design for one entity per heating circuit, an Off/Auto/Heat plus preset
mapping of all six circuit modes, controller-owned one-time expiry, permission
boundaries, target-editing semantics, and offline validation gates. The user
approved permanent comfort as the Heat default, persistent target editing in
both permanent and one-time presets, and registration only for applicable
circuits with mode writes opted in, followed by the complete command matrix.
Integration 0.3.0 implements this convenience layer over existing SDK/controls,
not a new thermostat algorithm. Runtime channels observe expiry; verified mode
readbacks are protected against older polls. Controls share a lock and reject
ambiguous target routing or multi-write actions. Actual-HA/loopback tests are
offline coverage, not live hardware or visual UI validation.

## 1. Goal and deliverables

Build a small reusable Python library for the boiler HTTP API, then a separate
Home Assistant integration that uses that library. An optional thin CLI helps
exercise the library outside Home Assistant; it is not a standalone application.
The current implementation supports USER parameter reads, six PLUMBER slopes,
three DEVELOPER circuit-mode choices, and opt-in reviewed writes, plus exact
GET reads of `/channels`, `/info`, and `/pcb`. It
exposes no higher-level parameter catalog option, identity writes, or arbitrary
hardware/endpoint operations.

| Deliverable | Responsibility | Dependencies |
|---|---|---|
| Python library (`hvclient`, distribution `hargassner-hvclient`) | Async HTTP/auth, typed reads, metadata, reviewed temperature/curve/mode/program writes, verification, errors | No Home Assistant imports or runtime dependency |
| Thin testing CLI (`hvctl`) | Invoke library methods, print results/errors, explicitly confirm permitted parameter writes | Library; single console entry point, no second protocol implementation |
| Home Assistant integration (`hargassner_hv`) | Config entries, polling, sensors, opt-in number/select controls, program action, lifecycle, diagnostics | Home Assistant and an independently installed/released library version |

The library must be independently installable and testable. Protocol fixes belong
there, not in entity code or the CLI. The integration must be a thin adapter,
not a second implementation of authentication, parsing, or write verification.

```text
Home Assistant integration ─┐
                           ├─ reusable async Python library ─ HTTP ─ boiler
Optional thin CLI ─────────┘
```

### Explicit non-goals

- No custom GUI, TUI, dashboards, web server, REST facade, or desktop app.
- No application database, SQLite layer, time-series store, retention policy,
  disk cache, or persistent audit-file subsystem.
- No standalone polling daemon, systemd service, or container deployment for
  the client. Home Assistant hosts the integration and schedules updates.
- No cloud/MQTT bridge or Modbus gateway.
- No raw actuator control, CAN injection, identity/EEPROM changes, firmware
  updates, factory resets, or calibrations in the initial library/integration.
- No recreation of the boiler controller, thermostat algorithms, or safety logic.

Home Assistant and existing external systems handle presentation, automation,
history, and data storage. Cookies, locks, in-flight state, parsed metadata, and
Home Assistant's latest coordinator snapshot may exist in memory; these are
operational state, not a planned historical data store. Repository captures and
test fixtures remain development evidence, not a runtime storage feature.

## 2. Evidence and protocol constraints

| Label | Meaning |
|---|---|
| `[CODE]` | Established from the analyzed 2.7.4 executable/decompilation; important branches checked against assembly |
| `[LIVE: 3.4.0]` | Observed on the captured unit reporting 3.4.0; not proof for every firmware version |
| `[VERIFY]` | Needs a scoped code investigation or controlled hardware check |

### Known facts

- The service is HTTP on TCP/80. Error details may be in the HTTP reason phrase,
  with an empty body. Preserve that phrase in library exceptions. `[CODE]`
- `GET /challenge` returns a 64-character lowercase-hex string. Compute
  `lower_hex(SHA512(ASCII(challenge) + ASCII(SALT)))`, then POST the 128-hex
  digest as `key` with **`daqOnly=true`**. `[CODE; LIVE: 3.4.0]`
- Successful authentication returns `204` and sets `auth-cookie`; the analyzed
  idle timeout is 300 seconds and the challenge cooldown is 5 seconds. The
  challenge, cooldown, and `daqOnly` flag are global. `[CODE]`
- `daqOnly=true` enables the special `/channels` route and falls through to the
  standard handlers for other paths. It is **not** a read-only or channels-only
  permission. Reads of `/channels`, `/info`, and `/parameters` work with the
  default probe login. `[CODE; LIVE: 3.4.0]`
- Authenticating with `daqOnly=false`, or omitting the flag, requests SERVICE
  mode. Code indicates that normal control stops, outputs are forced off, and
  SERVICE is latched until reboot. No such live test is recorded. `[CODE; VERIFY]`
- `/channels` is a snapshot endpoint, not a setter. POST/PUT bodies are ignored
  by its analyzed handler. `[CODE]`
- `POST /parameters` runs real setters on the main control thread, emits change
  signals, invokes file/EEPROM persistence, and marks the system configured.
  It does not require or unconditionally request SERVICE. `[CODE]`
- The HTTP scalar parameter-write chain has no catalog security-level check;
  UI filtering is separate. `USER` is not an authorization or safety boundary.
  `[CODE; VERIFY on deployed versions]`
- Writes accept sparse `name`/`value` entries, not a complete catalog. Batches
  have no rollback; an out-of-range float can remain unchanged despite `200`.
  Compare read-back values, not just status codes. `[CODE]`

The complete USER table and setter evidence are in
[`boiler-http-api.md` §5.2](boiler-http-api.md#52-parameters). It includes 44
USER entries from the 3.4.0 sample, of which 39 names are independently defined
as USER in the 2.7.4 provider. Inactive settings and capture-only additions must
not automatically become enabled controls.

### Safety baseline

- The production library and Home Assistant integration always authenticate
  with `daqOnly=true`. Do not expose a SERVICE-mode switch in config flow,
  entities, or normal CLI commands.
- Do not restore an assumed prior mode on shutdown. A true login does not undo
  SERVICE previously entered by another client, and there is no reliable HTTP
  mode read-back.
- Use only known read endpoints; HTTP GET is not inherently harmless. Exclude
  method-agnostic mutators such as `/factory-reset` and
  `/info/systemid/generate` from the public library API.
- Customer parameter writes remain opt-in, individually allowlisted, and
  disabled until their actual behavior is validated. Some USER settings concern
  ignition, operation timing, and external heat sources.
- Restrict TCP/80 to trusted LAN clients. Do not port-forward it. The salt is
  embedded in the firmware, and session data is transmitted over plain HTTP.
- Use one client instance per physical boiler. Locks coordinate callers of
  that instance, not independent processes or third-party clients.

## 3. Delivery phases and gates

| Phase | Work | Deliverable / gate |
|---|---|---|
| 0 — Scope and safe baseline | Identify deployed firmware; review operator constraints and existing consumers; capture allowed GETs | Read-only fixture set, version identity, explicit endpoint scope |
| 1 — Protocol contract | Resolve session/retry behavior, parameter types, supported setting effects | API contract and metadata sufficient for scoped library methods |
| 2 — Mock and library reads | Build async client and stateful mock; implement typed read methods | Library unit/contract tests pass without Home Assistant or hardware |
| 3 — Thin CLI, if useful | Add one-shot library commands | No duplicated protocol logic; optional dependency only |
| 4 — Read-only Home Assistant integration | Config flow, shared client, coordinators, sensors/diagnostics, unload handling | Stable setup/reload/recovery; no write or SERVICE request |
| 5 — Guarded customer writes | Validate selected parameters; implement library write verification; add narrowly mapped entities | Operator-approved read-back/physical-effect tests; writes off by default |
| 6 — Release and validation | Package library independently; pin integration dependency; run integration and soak tests | Install/upgrade/unload work; no session storms, task leaks, or unintended writes |

Do not require live coverage of every service/maintenance endpoint before the
read-only integration can ship. Those endpoints are outside product scope.
Write-related uncertainties block only the affected controls, not telemetry.
Avoid duplicating the full endpoint matrix from the protocol reference here.

### Current validation

- [x] Challenge format, SHA-512 authentication, and cookie acquisition confirmed.
- [x] Default `daqOnly=true` reads of `/channels`, `/info`, and `/parameters`
      confirmed on the captured 3.4.0 unit.
- [x] The 2.7.4 parameter write chain, standard-route fallback, and USER catalog
      traced through code; version boundaries recorded.
- [x] One operator-approved zone-1 daytime target round trip, 20.5 → 21.0 →
      20.5 °C, verified through POST responses and separate GETs with
      `daqOnly=true`; see the [live record](parameter-setter.md#live-validation--2026-11-09).
- [ ] Idle expiration, cookie refresh, cooldown, and bounded recovery validated
      against the deployed unit.
- [x] Independently installable USER-parameter library, thin library-backed CLI,
      and async contract/lifecycle tests implemented with loopback mock servers.
- [x] Read-only channel, system-info, and PCB methods/CLI commands implemented
      with shared authentication/recovery, immutable models, and loopback tests.
- [x] Six heating-curve slope exceptions implemented with explicit policies,
      profile-context validation, mandatory expected values, and offline tests;
      no general installer access or live curve validation is implied.
- [x] Three deliberately hidden circuit-mode exceptions implemented with integer/
      enum IDs, correct object/ID serialization, circuit-context checks, mandatory
      expected IDs, and offline tests; no general inactive/DEVELOPER access or
      live mode validation is implied.
- [x] Immutable English circuit-mode meanings exposed through a public zone-keyed
      registry, offline name lookup, and `Parameter.meaning`, separately from
      localized firmware descriptions and without changing wire JSON/permissions.
- [x] Six USER circuit program writers implemented with daily/weekly value
      objects, verified weekday IDs, strict time/window validation, complete
      replacement/expected checks, circuit/peer/D9 context, and offline tests.
      Global scheduling selection and circuit mode are never changed implicitly.
- [x] Public immutable `ScheduleLimits` exposed through program/value/parameter
      objects and offline lookup. Validators and serializers share the same
      policy records; live eligibility remains a separate check, not an inferred
      capability advertised by the controller.
- [x] Library reads and previews smoke-tested against a unit reporting 3.4.0;
      selected values unchanged on reread, zero parameter POSTs, authentication
      explicitly `daqOnly=true`. Opt-in hardware test added with default skip,
      explicit target, serial execution, privacy protection, and request guards.
- [ ] Library write implementation validated against the deployed unit; the
      earlier live round trip exercised the research setter, not this implementation.
- [x] Home Assistant config flow, coordinators, entity mappings, guarded controls,
      program actions, diagnostics, recovery, and lifecycle tested using actual
      pinned HA core objects and synthetic SDK/loopback HTTP devices.
- [ ] Integration deployed and observed on a live HA/controller installation.
- [ ] Selected live parameter writes validated with physical observation and
      read-back; the narrow round trip establishes stored-value read-back only,
      not physical effects, reboot persistence, or other controls.

## 4. Independent library design

### 4.1 Public API and boundaries

Use an async-first Python API with `aiohttp` as the transport so it fits Home
Assistant without blocking its event loop. A synchronous wrapper is not needed;
the optional CLI can call `asyncio.run()`.

Implemented public methods:

```text
AsyncHargassnerClient(host, port=80, session=None, write_policy=deny_all)
  async_get_channels()                                # read-only snapshot
  async_get_info()                                    # read-only identity/version
  async_get_pcb()                                     # read-only board diagnostics
  async_get_parameters(names=None)                     # USER + six slopes + three modes
  async_get_parameter(name)
  async_preview_parameter_write(name, value, expected=...)  # no POST
  async_set_parameter(name, value, expected=...)        # opt-in and verified
  async_close()
```

Channel snapshots expose dynamic numeric names without inferred units or enum
meanings. System info includes optional identity/version strings and must be
redacted before publishing diagnostics. PCB snapshots expose both numeric board
readings. Only exact GET roots are supported; identity writes and the mutating
`/info/systemid/generate` subpath remain blocked. Parameter reads are limited to
USER plus six PLUMBER slopes and three DEVELOPER circuit modes;
schedules, choices, booleans, text, and raw controller date/time components are
readable. Writes require a subset of 11 USER temperatures, six dimensionless
slopes, three mode choices, and six circuit programs. Slopes require live profile/peer/selector checks;
modes require a configured circuit, the exact hidden-choice contract, and IDs
0–5. Both require expected values. Mode IDs are separate from boiler-wide modes
and do not include SERVICE. Type selectors are internal read-only context, not public
catalog entries or writable controls. Other higher-level parameters and
other structured writes are still out of scope. Programs use daily/weekly
array serializers with full expected values and read-only D9 selection context;
weekly programs require the controller already in weekly mode. No implicit
global scheduling or operating-mode switch is made.

- Library initialization must not perform network I/O or authenticate with a
  false/missing `daqOnly` flag.
- Reads ensure a session lazily. The library has no recurring resource polling
  loop and does not require a background keepalive task.
- Expose supported methods, not an unrestricted public method/path/body tunnel.
- Preserve unknown response fields for compatibility, but do not silently infer
  serializers or enable writes for unknown parameter types.
- Keep Home Assistant classes, entity IDs, config entries, and recorder concepts
  out of the library. Callers receive ordinary typed Python objects.
- Support a caller-provided `aiohttp.ClientSession`; do not close a borrowed
  session. If the library creates a session for standalone use, it owns and
  closes it. Make ownership and cancellation behavior explicit and testable.

### 4.2 Transport and authentication

- Store `auth-cookie` in the client instance and explicitly attach it to the
  intended boiler requests. Do not rely on Home Assistant's shared session cookie
  jar accepting cookies from an IP host or isolating multiple boilers.
- Parse refreshed Set-Cookie headers without logging token values. Reject an
  authentication response that does not issue the expected cookie; status alone
  is insufficient.
- Do not forward credentials through redirects to arbitrary hosts. Bound
  response sizes and preserve status, reason, and JSON decoding errors.
- Support the selected-parameter **GET body** through `aiohttp`; do not rewrite
  it into query parameters or assume proxies preserve it.
- Serialize authentication, including challenge cooldown waits. Use monotonic
  time and `asyncio.sleep`, never blocking sleeps. The implemented client uses
  one operation lock for authentication, reads, and full write/read-back
  transactions; internal helpers never re-acquire it. This avoids nested-lock
  deadlocks and gives concurrent callers one shared authentication session.
- Start with serialized device requests. Coordinate writes and their read-back
  against polling on the same client; do not hold a separate Home Assistant lock
  that deadlocks the library.
- Normal Home Assistant polls refresh the session. If idle polling stops, the
  next read handles expiration and re-authenticates; no standalone heartbeat
  scheduler is necessary.

### 4.3 Typed models

| Model | Contents |
|---|---|
| System information | Firmware version and device identity, with optional fields and raw response access |
| Channel snapshot | Mapping of channel name to numeric value; no channel setter |
| Parameter metadata/value | API name, display label, description, type, security classification, active flag, units, limits, increment, choices, and structured values |
| Diagnostic values | Optional PCB/read-only data needed by integration diagnostics |
| Write result | Requested, before, and read-back values; verified/mismatched/unknown outcome |

Decode booleans and numeric values carefully; an active flag and `securityLevel`
are metadata, not permissions. Validate structured schedule values by their
documented type. Do not accept NaN/infinite values or invent units for channels.
Support only validated type-specific write serializers in the first release.

### 4.4 Errors and retry behavior

Provide Home Assistant–independent exceptions for connection/timeout,
authentication, cooldown, malformed response, HTTP rejection, unsupported type,
policy refusal, and write mismatch/unknown outcome. Carry the endpoint and HTTP
reason where available; never include cookies, keys, or full challenge values.

| Condition | Library behavior |
|---|---|
| `204` plus issued cookie on authentication | Publish the session to waiting callers |
| `200` JSON on a read | Parse and return typed data |
| `200` on a parameter write | Verify the stored value; do not equate status with application |
| `401` on a read | Re-authenticate once with true, respecting cooldown, then retry once |
| `403` on `/challenge` | Wait for the known cooldown with small jitter; bounded attempts |
| `400` / unknown name / invalid value | Surface rejection; no blind retry |
| `404` on `/channels` | Report possible global flag drift or unsupported route; do not switch to false |
| `404` on a standard route | Unsupported/bad path or firmware difference, not proof of `daqOnly` drift |
| Read timeout / connection error | Bounded reconnect/read retries; return failure after the budget |
| Write timeout / disconnect | Outcome unknown; attempt a read-back if possible, never blindly replay the write |
| Cancellation | Propagate cancellation, release locks, and clean up owned resources |

The integration's polling schedule controls later recovery attempts. The library
must not run an unbounded reconnect loop or repeatedly sleep through Home
Assistant setup deadlines. Keep retry budgets configurable and independently
tested; avoid multiplying retries in the library and integration layers.

### 4.5 Customer parameter writes

```text
caller intent
  → allowed-name/type/range/increment policy
  → read current parameter and validate freshness/active state
  → send one sparse name/value entry
  → inspect returned value and fetch read-back
  → compare typed values and return verified result or an exception
```

- Default policy denies all writes. The consumer supplies an explicit allowlist
  for validated parameter names; catalog USER classification can help select
  candidates but must not automatically enable all of them.
- Write verification and validation live in the library, shared by CLI and Home
  Assistant. Confirmation/opt-in UI belongs to the consumer; no library prompts.
- Submit only `name`/`value`, not a copied metadata object or channel snapshot.
- Preserve false, zero, and other valid values without truthiness-based filtering.
- Honor ranges and increments in client validation; the float setter does not
  enforce the increment and may reject a value without producing an HTTP error.
- Treat multi-parameter operations as sequential, non-atomic operations. Start
  with a single-parameter public setter rather than promising batch transactions.
- Return before/after information or emit a redacted logging/callback event to
  the caller. Do not implement a persistent audit database or JSONL file writer.
- Parameter persistence belongs to the controller. Check it during approved
  validation; do not persist requested setpoints locally and replay them after
  a restart or reconnect.

## 5. Home Assistant integration layer

### 5.1 Config flow and lifecycle

- Use a config flow to collect host/port and verify connectivity using allowed
  reads with `daqOnly=true`. Provide appropriate connection/auth/protocol errors;
  do not write a setting to test connectivity.
- Identify the boiler from `/info` and use a validated stable identifier for the
  device registry and duplicate-entry detection. Do not use the IP address as
  the device's identity; handle changes of host and firmware.
- Create one library client per config entry, using Home Assistant's managed
  `aiohttp` session (for example `async_get_clientsession(hass)`). Store runtime
  client/coordinator objects in the integration's config-entry runtime data.
- Prevent duplicate config entries for the same boiler. Document that external
  clients can still interfere with the firmware's global authentication state.
- During setup, perform the initial coordinator refresh before creating entities.
  Perform no SERVICE login or parameter write in setup, reload, migration,
  options updates, shutdown, or recovery.
- On unload, remove entity platforms, cancel integration-owned work, and close
  the client's own resources without closing Home Assistant's shared HTTP session.
- Ship a separately versioned library dependency in the integration manifest;
  do not copy the library into the integration or use repository-relative imports.

### 5.2 Polling and availability

Home Assistant owns recurring polling through `DataUpdateCoordinator` and its
normal entity lifecycle. There is no second polling scheduler in the library.

- Start with one primary coordinator for telemetry, plus an optional slower
  metadata/parameter coordinator if needed. Both share the same library client
  and request budget, not independent authentication sessions.
- Proposed initial intervals: channels every 30 seconds, parameters every
  300 seconds, optional PCB diagnostics every 60 seconds. Fetch identity/version
  at setup and when needed after recovery. Tune from measured device load.
- Fetch the parameter catalog at setup and refresh after a verified write;
  avoid downloading it once per entity on every telemetry update.
- Use coordinator snapshots for entity state. Entities must not individually
  authenticate or perform their own recurring requests.
- Map library failures into Home Assistant setup/update errors (for example
  `ConfigEntryNotReady` and `UpdateFailed`) and availability appropriately.
  Retaining the last snapshot must not make stale data appear current.
- An entity write awaits the library's verification, then requests a coordinator
  refresh. Avoid optimistic state changes that claim success before read-back.
- Test reload, unload, network loss, controller restart, and overlapping
  polling/write requests for leaked tasks, deadlocks, or authentication storms.

### 5.3 Entity mapping

| Home Assistant platform | Source | Initial policy |
|---|---|---|
| `sensor` | Validated channels and read-only parameter values | Enable a small useful set; unknown/diagnostic channels disabled by default or omitted |
| `binary_sensor` | Future values whose boolean semantics are verified | Not implemented; raw 0/1 channels are not guessed to be booleans |
| `number` | Validated writable numeric customer parameters | Opt-in; use units, min/max, and step from validated metadata |
| `select` | Validated writable choice parameters | Opt-in; preserve API choice IDs separately from translated labels |
| `climate` | Applicable opted-in circuits, six modes, and persistent comfort/reduced targets | Implemented in 0.3.0; runtime-mode state, optional reported room feedback, no inferred HVAC action |
| `switch` | Future validated ordinary boolean settings | Not implemented; the SDK currently permits no boolean writes |

Not every USER parameter warrants an entity. The six reviewed daily/weekly
circuit programs have read-only sensor attributes and a dedicated admin-only,
device-targeted full-replacement action with preview/expected-value checks.
Other schedules, dates, preferences, and unreviewed high-impact settings remain
unsupported as controls. There is no arbitrary parameter-write service.

Use channel/parameter wire names plus the device identifier for stable entity
unique IDs, not localized display names. Specify units, device classes, and state
classes only where semantics are known. Do not declare cumulative/energy
statistics merely because a channel is numeric.

The [climate feature](climate-development-plan.md) uses reviewed mode and target
controls, not the mere presence of a room setpoint. HVAC action is not inferred;
regulation and one-time expiry remain controller-owned. No custom heating
algorithm is planned. Offline coverage does not establish hardware effects.

### 5.4 Configuration, presentation, and history ownership

- Host, polling options, selected entities, and write opt-in are integration
  configuration. They are not a standalone application YAML/config subsystem.
- Home Assistant provides the existing entity UI, dashboards, automations,
  recorder/history, and statistics. Plan no custom GUI or separate data store.
- Read history is not duplicated in the library. Any Home Assistant configuration
  persistence is framework-managed, not a client database.
- Use ordinary redacted logs and integration diagnostics. Sanitize identity
  fields, cookies, authentication material, and unnecessary full responses;
  diagnostics are generated on demand, not saved as a local archive.

## 6. Optional thin CLI

Provide only enough CLI to validate library behavior and troubleshoot outside
Home Assistant. Use the same async client and exceptions; no alternative HTTP
stack, TUI, embedded web service, daemon, or disk cookie store.

Implemented one-shot testing commands:

```text
hvctl <boiler-ip> channels
hvctl <boiler-ip> info  # contains private device identifiers
hvctl <boiler-ip> pcb
hvctl <boiler-ip> list
hvctl <boiler-ip> get <api-name>
hvctl <boiler-ip> set <allowed-name> <value>  # preview
hvctl <boiler-ip> set <allowed-name> <value> --apply --expect <current>
```

The testing setter exercises the library's guarded numeric implementation; it
also supports the three explicit circuit-mode choice exceptions. It
does not complete hardware/physical-effect validation or enable Home Assistant
controls. It requires an explicitly permitted name and remains in `daqOnly=true`.
It prints the verified result or a clear unknown/mismatched outcome and never
exposes a raw path/body write command. stdout/stderr are sufficient; redirected
development output is an external caller choice, not a library storage feature.

`tools/probe.py` remains investigation tooling. It is not the production library
and should not be imported by the integration.
The separate [guarded numeric setter](parameter-setter.md) is also investigation
tooling: it reuses the probe, not the independent async library. Its offline contract
tests do not complete the guarded-write phase or validate live physical effects.
The `hvctl` CLI instead calls `hvclient` exclusively. Research tooling is
retained as historical evidence, not copied into the library/integration.

## 7. Suggested layout and release boundary

`src/hvclient/`, packaging, library tests, and the CLI entry point now exist.
The custom integration is implemented under `custom_components/hargassner_hv/`;
the broader layout below remains a roadmap, not a directory specification.
The independent library and integration can begin in one repository, but must
retain separate import and release boundaries. They can be split later.

```text
src/hvclient/
  client.py
  transport.py
  auth.py
  models.py
  parameters.py
  policy.py
  exceptions.py
  cli.py                         # optional entry point/extra only
custom_components/hargassner_hv/
  manifest.json                  # released hvclient requirement
  __init__.py
  config_flow.py
  coordinator.py
  entity.py
  sensor.py
  binary_sensor.py
  number.py                      # only validated opt-in controls
  select.py
  switch.py
  diagnostics.py
  strings.json
  translations/
tests/
  library/
  home_assistant/
  fixtures/
  mock/
captures/                        # development evidence, not runtime history
docs/
  boiler-http-api.md
  client-development-plan.md
tools/
  probe.py
  re/
```

- Publish the library as its own package with no Home Assistant dependency.
  Importing it must not require CLI extras or the integration test environment.
- Pin a released library version in the integration; maintain dependency
  compatibility with Home Assistant's Python and `aiohttp` environment.
- Start as a custom integration if appropriate; decide HACS/distribution or
  upstream submission later. Do not make release infrastructure a blocker for
  proving the library/integration boundary.
- Track tested firmware versions/capabilities explicitly. The 3.4.0 read
  captures do not establish full write compatibility with 2.7.4 or later versions.

## 8. Validation and tests

### Library contract and fault tests

- SHA-512 vectors; challenge format; authentication always sends `daqOnly=true`.
- Cookie acquisition/refresh, IP-host handling, borrowed-session ownership,
  idle expiration, cooldown, and one re-authentication for concurrent callers.
- Plain HTTP reason phrases, empty error bodies, chunked responses, malformed
  JSON, body-size bounds, and selected-parameter GET bodies.
- `/channels` plus standard-route access with true; no assumed channels-only
  authorization boundary.
- Parameter name/type/range/increment checks, false and zero values, unsupported
  serializers, non-atomic batch simulation, and `200` with unchanged read-back.
- Network loss mid-write, cancellation, timeout, controller reboot, and an
  external client replacing the global challenge or toggling channel routing.
- Forbidden endpoints and SERVICE authentication are absent from the normal
  API. Automatic retries never turn an unknown write outcome into a replay.
- The package imports and all library tests pass without Home Assistant installed.

Use a stateful local async mock and sanitized fixtures. A mock must model the
quirks actually observed in code, including silent float rejection; it must
not invent stronger security or transaction guarantees. Keep clocks/delays
injectable so tests do not sleep for minutes.

### Home Assistant tests

- Config-flow success and failures, duplicate devices, host reconfiguration,
  device identity, and firmware compatibility handling.
- Setup/first refresh, coordinator polling, unavailable/stale entities,
  recovery, reload, and complete unload with no borrowed-session closure.
- Stable unique IDs despite translated labels or changed IP addresses.
- Entity setters call the library exactly once, enforce configured opt-in, await
  verification, and refresh state. No optimistic state or unrestricted writes.
- Units/device classes/state classes are correct for each curated mapping.
- Redacted diagnostics and logs; no blocking operations in the HA event loop.

### Controlled hardware checks

1. Run one session with `daqOnly=true`; identify firmware and read `/info`,
   `/channels`, `/parameters`, and any required diagnostic endpoint.
2. Observe session refresh, timeout/re-authentication, and conservative polling
   load. Do not test excluded service endpoints to complete a generic matrix.
3. For each proposed writable control, obtain approval, read the prior value,
   make one intentional change, verify returned and subsequent GET values,
   observe behavior, and restore the prior value.
4. Check controller persistence only using an agreed safe procedure. Do not
   power-cycle an operating boiler as an automatic validation step.
5. Run the integration through a sustained read-only period, network outages,
   Home Assistant restarts, reload/unload, and later approved control tests.

No new live requests are implied by this plan. SERVICE-mode investigation is
separate and is not necessary to deliver normal client sessions with true.

## 9. Remaining questions and release criteria

Resolve these for the selected features, not for every vendor endpoint:

- Which stable identity should distinguish entries when network settings or
  firmware change?
- Are channel units, availability, registration timing, and names stable enough
  for each proposed sensor mapping? What do missing values mean?
- Does the deployed unit match code-derived expiration, cooldown, flag, and
  parameter-rejection behavior?
- Which specific customer parameters have verified effects, write serializers,
  ranges/increments, persistence, and safe Home Assistant entity mappings?
- Do any writable settings have external-control enable/lock conditions or
  device-side overrides? Capture-only USER metadata is not sufficient proof.
- How should the integration present disabled/inactive settings and unknown
  versions without silently enabling unsafe controls?

Release gates:

- [x] Standard Apache-2.0 licence, SPDX notices, package licence metadata, and
      distributed agent-generation/scope/authorship notice added without
      personal attribution, contributor location, or other identifying details.
      This declaration is not a determination that every generated portion
      is copyrightable or that licence obligations are enforceable over it.
- [ ] Applicable AI-provider terms and third-party provenance checked before
      distribution; qualified software/IP advice if enforceable
      reciprocity is a requirement. See [the provenance note](../LICENSING.md).
- [ ] Publication privacy reviewed across project text, fixtures, diagnostics,
      package artifacts, archive headers, and Git identity metadata; no private
      contributor or device-owner identifying/location information disclosed.
- [ ] Independent library release with read contract/fault tests and no HA imports.
- [ ] Optional CLI uses only public library methods and introduces no new safety bypass.
- [ ] HA setup, polling, availability, recovery, reload, and unload tests pass.
- [ ] No SERVICE login, mutator GET, actuator command, or metadata write occurs
      during normal setup, reads, options updates, or recovery.
- [ ] Each enabled writable entity has an allowlist entry and controlled
      read-back/physical-effect validation; unverified controls remain disabled.
- [ ] No persistent client storage, custom UI, background client daemon, or
      duplicate polling architecture has been introduced.
- [ ] Dependency versions, tested firmware, unsupported features, and operational
      safety limitations are documented.

## Appendix A — read-only protocol check

Development commands only. Do not publish captured cookies, keys, challenges,
or identifying values without redaction.

```bash
HOST=http://<boiler-ip>
SALT=fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27

curl -sS "$HOST/challenge" -o challenge.json
CHALLENGE=$(python3 -c 'import json;print(json.load(open("challenge.json"))["challenge"])')
KEY=$(printf '%s%s' "$CHALLENGE" "$SALT" | sha512sum | cut -d' ' -f1)
curl -sS -c cookies.txt -X POST "$HOST/authenticate" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\",\"daqOnly\":true}"
curl -sS -b cookies.txt "$HOST/channels"
curl -sS -b cookies.txt "$HOST/info"
curl -sS -b cookies.txt -X GET "$HOST/parameters" \
  -H 'Content-Type: application/json' \
  -d '{"parameters":["4","5"]}'
```

The one-shot probe can perform the baseline reads without a cookie file:

```bash
python3 -B tools/probe.py <boiler-ip> auth
python3 -B tools/probe.py <boiler-ip> channels
python3 -B tools/probe.py <boiler-ip> get /info
python3 -B tools/probe.py <boiler-ip> get /parameters
```

## Appendix B — SHA-512 authentication vectors

`key = SHA512(ASCII(challenge) + ASCII(SALT))`, lowercase hex (128 characters).
The salt is the fixed ASCII string shown in Appendix A, not decoded hex bytes.

| Challenge | Expected key |
|---|---|
| `0000000000000000000000000000000000000000000000000000000000000000` | `a7cb08a85c66c00f574c4379c2cae8b2c5fe225ea87a4b9780edce4461282185aebd83a18eaeea52463076a3dd1bc0e19c9dc3d9d895c5debe3b2f480aeffd49` |
| `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef` | `ac9a5d356b1160cb0e81a416bd06708d91af49d117c5b4e83a11fb9b9a5284327222a0551fa687f6c78fe8843877681ea97defe2565eaefd5be346992d013355` |
| `deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef` | `01c8aea4cdf6520ecb91acc817bb55994133f0b84477f5d92bb7f0a058a5f379afdef2488598408599d6d6aca2551def34f84c4e414b7778ae279d4ebe68ff9a` |
