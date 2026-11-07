# `smart-web` — reverse-engineered component notes

Scope: `smart_hv_2.7.4/data/smart-web/bin/smart-web`, analyzed from the existing
Kuna export at `smart_hv_2.7.4/data/smart-web/bin/smart-web.kuna/`. No
re-decompilation or live-device testing was performed for this note.

The export reports an ARM 32-bit executable with **822 functions, all
decompiled**. The principal evidence is `smart-web.c`; use `index.jsonl` for
function addresses and `smart-web.asm` to verify important branches. Findings
below are code-derived; runtime behavior and broker-side policy remain **VERIFY**.

Related: [`boiler-http-api.md`](boiler-http-api.md) documents the separate
TCP/80 HTTP service implemented by `data/app/bin/app`.

---

## 1. Executive summary

`smart-web` is a background **D-Bus client and MQTT client**. It reads web-client
configuration and boiler state through the local D-Bus interface, connects to
an MQTT broker, publishes information/events, and subscribes to remote jobs. It
does **not** appear to implement a local HTTP, MQTT, or other TCP listener.

Its MQTT job registry includes read operations, but also operations that can
change parameters or system information, change permissions, upload files, and
request application/web updates. It is therefore a remote management bridge,
not merely a telemetry forwarder.

This component is **not the port-80 API** and is not a local LAN client API. Its
MQTT connection is outbound; the interface it consumes locally is D-Bus.

---

## 2. Startup, configuration, and transport

### Startup sequence

`main` (`0x17f80`) does the following:

1. Creates a Qt application and connects to the D-Bus session bus. It exits if
   the bus is unavailable.
2. Changes to UID/GID `2000` (the log message identifies this as the `mqtt` user).
   Failure to drop privileges is fatal.
3. Calls `getWebClientConfiguration` on D-Bus service
   `at.hargassner.smart`, object path `/at/hargassner/smart/system`, interface
   `at.hargassner.smart.SystemModel`.
4. Reads configured MQTT URL/key and system metadata, then calls
   `setWebClientInformation` with client/version information (version string
   `1.1.2`). Missing broker URL or key is fatal.
5. Constructs manager objects, registers their job handlers, connects to the
   MQTT broker, connects D-Bus signals, and enters the Qt event loop.

The same SystemModel object is used for `setWebClientState` reporting. Other
managers call into D-Bus objects under the `at.hargassner.smart` service to
fetch or update boiler information.

### MQTT transport

- The binary links Paho MQTT C++ and Qt D-Bus/XML libraries.
- The default broker URL string is `ssl://mqtt.hargassner.at`; the connection
  code also selects TLS options when the configured URL has that prefix.
- Connection options include a client username and password. Configuration and
  credential-construction material are present in the executable/configuration;
  **credential values and construction bytes are intentionally not reproduced**
  here. Treat the executable and D-Bus web-client configuration as
  secret-bearing.
- The client has reconnect handling and a last-will/state-reporting path.
- Managers share configured MQTT topic prefixes for outbound job results,
  channel updates, and inbound jobs. The job manager subscribes to two inbound
  topic forms, one associated with the configured web-client identity and one
  with the system ID. Exact per-device topic values are configuration-derived
  and are not listed here.

The default broker string and TLS setup are code evidence, not proof that every
configured deployment uses TLS or that broker-side authorization is correct.
**VERIFY** the deployed URL, certificate validation, broker ACLs, and connection
failure behavior on a controlled unit.

---

## 3. Core components

Function addresses refer to the Kuna export for this `smart-web` executable.

| Component | Core functions | Role |
|---|---|---|
| Startup | `main` (`0x17f80`) | Loads D-Bus configuration, drops privileges, constructs managers and starts the event loop. |
| `MqttManager` / `MqttCallback` | `connect` (`0x38ae0`), `subscribe` (`0x376fc`), `publish` (`0x384ac`), `message_arrived` (`0x36ea0`), `connected` (`0x3894c`), `reconnect` (`0x37a38`) | Owns the broker connection, subscriptions, outgoing JSON publication, callbacks, and reconnect behavior. |
| `JobManager` | `registerJobHandler` (`0x31eb0`), `enqueueJob` (`0x320d8`), `executeJob` (`0x3265c`), `processNextJob` (`0x32b1c`) | Maps job names to callbacks, queues incoming work, dispatches it, and publishes non-empty results. Jobs are processed through a queue rather than directly in the MQTT callback. |
| `ChannelManager` | `postInitialize` (`0x1b160`), `publishChannels` (`0x1a198`), `onRemoteChannelChanged` (`0x1b074`), `setChannelValue` (`0x1ac18`) | Synchronizes channel values/changes with D-Bus and publishes channel updates. Applies configured channel tags. |
| `ErrorManager` | `postInitialize` (`0x1f380`), `sendAllErrors` (`0x20dd4`), `onRemoteErrorsChanged` (`0x21208`) | Reads and publishes active errors; error JSON contains fields such as code, severity, description, and timestamp. |
| `FileManager` | `postInitialize` (`0x241d4`) | Lists files and brokers file-upload requests; available DAQ and log files are queried through D-Bus. |
| `InfoManager` | `createInfo` (`0x2f160`), `getApplicationProperties` (`0x2ebd4`), `sendSystemMode` (`0x2dc84`) | Publishes system/application information and mode; handles registration and system-information jobs. |
| `ParameterManager` | `postInitialize` (`0x3ce00`), `publishAllParameters` (`0x3d370`), `publishResults` (`0x3d09c`) | Publishes parameter values/metadata, handles parameter jobs, and tracks D-Bus parameter-change signals. |
| `PermissionManager` | `registerJobs` (`0x3e528`), `publish` (`0x3e644`) | Reads/publishes permission data and registers a permission update job. The export alone does not establish the end-to-end authorization policy. |
| `SessionManager` | `registerJobs` (`0x3e69c`), `getCurrentSession` (`0x3e738`) | Publishes or retrieves current web-client session information. |
| `UpdateManager` | `registerJobs` (`0x3fe94`), `postInitialize` (`0x3ffc0`) | Requests Smart Web/Smart App updates and observes download progress through D-Bus. |

---

## 4. API surfaces

### 4.1 Local D-Bus interface consumed

`smart-web` is a **client** of the local D-Bus service, not the owner of the
SystemModel API. The primary endpoint is:

```text
service:   at.hargassner.smart
path:      /at/hargassner/smart/system
interface: at.hargassner.smart.SystemModel
```

Calls/signals evidenced in `smart-web.c` include:

| D-Bus method or signal | Use in `smart-web` |
|---|---|
| `getWebClientConfiguration` | Read broker URL/key and web-client configuration. |
| `setWebClientInformation` | Report the smart-web client/version information. |
| `setWebClientState` | Report web-client connection/state changes. |
| `registrationCodeRequested` | Trigger/observe a registration-code request. |
| `systemModeChanged(QString)` | Observe mode changes and publish updated mode information. |
| `parameterChanged(QString)` | Observe parameter changes. |
| `channelChanged` | Observe channel changes. |
| `errorsChanged` | Observe error-list changes. |
| `uploadInProgressChanged(bool)`, `uploadFailed(...)` | Report file-transfer status. |
| `smartWebDownloadProgressChanged(int)` | Report Smart Web update progress. |

Manager operations also query D-Bus node/object interfaces for channel, error,
file, parameter, permission, session, and update data. These are calls from
`smart-web` into the main application; they are not additional HTTP endpoints.

### 4.2 MQTT job API

The job manager registers the following incoming job names. These are names in
the MQTT job dispatcher, **not HTTP paths**. No message templates, invocation
examples, or credentials are provided here.

| Area | Registered job names | Capability indicated by the handler names/code |
|---|---|---|
| Channels | `getChannels` | Read/publish channel data. |
| Errors | `getErrors` | Read/publish error data. |
| Files | `getFileList`, `uploadFiles` | List and upload device files. |
| System/info | `getInfo`, `getSystemMode`, `setSystemMode`, `setPcbSerialNumber`, `generateSystemId`, `webRegistrationCodeResponse`, `setWebRegistrationInformation` | Read system info/mode; submit system, identity, or registration operations. |
| Parameters | `getParameters`, `getParametersMetadata`, `setParameters` | Read parameter values/metadata and request parameter changes. |
| Permissions | `getPermissions`, `setPermissions` | Read or change permission data. |
| Session | `getSession` | Retrieve session information. |
| Updates | `updateSmartWeb`, `updateSmartApp` | Request software updates. |

Implementation evidence: each manager's `registerJobs` function registers its
names with `JobManager::registerJobHandler`; `JobManager::postInitialize`
subscribes to the configured inbound topics. Incoming messages are queued,
dispatched by job name, and results are published through `MqttManager` when a
handler returns a value.

Some jobs are state-changing or operationally sensitive. In particular,
parameter changes, file uploads, permission changes, system-mode requests, and
software updates must not be treated as ordinary telemetry. The registered
names do not by themselves prove that a request will succeed: D-Bus-side
authorization, mode restrictions, broker ACLs, and handler validation also
matter. For example, the main application's `DBusSystemModelAdaptor::setSystemMode`
restricts mode requests; see the SERVICE-mode discussion in
[`boiler-http-api.md`](boiler-http-api.md#3-service-mode-the-side-effect-of-daqonlyfalse-authentication).

---

## 5. Security and operational observations

- **Not a local LAN API:** no HTTP route registration or local TCP listener was
  identified in this executable. The API-like surface is the outbound MQTT job
  client plus local D-Bus calls.
- **Remote control plane:** the job registry reaches beyond telemetry to
  parameter, permission, file-transfer, system-mode, and update functions.
  Protect the broker credentials and configuration, restrict broker ACLs, and
  treat broker-side job authorization as part of the safety boundary.
- **Credential-sensitive binary/config:** the MQTT connection path configures
  username/password authentication and includes credential-construction
  material. Do not copy secrets into source control or public reverse-engineering
  notes. **VERIFY** whether the material is shared, device-specific, rotated,
  and protected by the broker's ACLs.
- **TLS is conditional in code:** the default URL is TLS-prefixed and SSL
  options are configured for that prefix. Confirm the actual deployed URL and
  certificate-verification behavior; do not infer it from the default string.
- **Least privilege:** startup attempts to run as UID/GID 2000 (`mqtt`) and
  treats failure to drop privileges as an error.
- **Permissions need end-to-end review:** a `PermissionManager` and
  `getPermissions`/`setPermissions` jobs exist, but this export does not establish
  which checks are applied to each incoming job or how the remote service
  authenticates/authorizes the sender. **VERIFY** those controls before relying
  on them.
- **No live validation:** the broker was not contacted; connection, topic
  routing, job schemas, broker ACLs, and operational effects remain unverified.

---

## 6. Follow-up questions for a controlled review

1. What exact permissions/ACLs are applied to the device's broker identity and
   the inbound job topics?
2. Are TLS certificates validated, and can configuration redirect the client
   to a non-TLS broker?
3. Which individual jobs have local D-Bus security checks, and what are their
   validation/error behaviors?
4. Which operations require a specific boiler mode, operator confirmation, or
   service authorization?
5. What file types/paths can `uploadFiles` access, and what are the update
   verification and rollback guarantees?
6. Are MQTT credentials device-specific and revocable/rotatable?

Answer these from configuration and controlled vendor-supported testing; do not
experiment with state-changing jobs on a live, heating boiler.
