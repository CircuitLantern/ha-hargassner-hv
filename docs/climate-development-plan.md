<!-- SPDX-License-Identifier: MPL-2.0 -->

# Feature development plan — heating-circuit climate entities

Status: **investigated; core behavior decisions approved; not implemented**.
The user requested one climate entity per zone and approved the Heat default,
persistent target editing in all four presets, and mode-opt-in-based registration.
The exact approval record and remaining command details are in section 9.
No live requests or hardware writes were made for the investigation.

This feature extends the [main development plan](client-development-plan.md)
and the existing [Home Assistant adapter](home-assistant.md), targeting the
tested HA **2026.11.1** baseline. The independently installable SDK remains the
only implementation of boiler transport, authentication, and verified writes.

## 1. Goal and boundaries

- One normal, non-Configuration climate entity for each applicable heating
  circuit `1`, `2`, and `A`, with stable controller-based identity. These are
  circuit designations, not discovered physical rooms; users may rename them.
- Expose the controller's existing circuit modes through standard HA HVAC modes
  and translated presets, without adding a thermostat/control algorithm.
- Expose the reported room temperature when available and persistent target
  editing in both permanent and one-time presets, subject to the particular
  setting's write permission and validated metadata.
- Keep existing sensors, numbers, selects, program actions, IDs, and automations
  intact. Climate is an additional interface, not an automatic replacement.
- Reuse per-setting write opt-ins. Creating/enabling an entity must never grant
  new write permissions or alter settings.

Non-goals: boiler-wide mode control, SERVICE mode, a climate entity for the whole
boiler, DHW control, cooling, fan/swing control, schedule editing through climate,
new arbitrary writes, temporary setpoint inventions, external-room-sensor control
loops, HA timers that cancel overrides, or restoration/replay of controller state.
DHW, if separately planned later, belongs to the water-heater platform.

## 2. Evidence and HA contract

The investigation checked the current
[HA climate documentation](https://contributors.home-assistant.io/core/entities/climate/)
and the actual 2026.11.1 climate `const.py`, `entity.py`, and `services.py` in the
isolated test environment, rather than relying only on moving web documentation.

- `hvac_mode` is the climate entity's state; `preset_mode` is a separate attribute.
  HVAC modes are HA enum values, while custom preset strings are supported.
- `AUTO` represents controller-owned schedule/logic, not heat/cool changeover;
  HA's definition says users cannot adjust the temperature in this mode.
- `HEAT` is a requested operating mode, not evidence of actual heat delivery.
  `hvac_action` is optional and must not be fabricated from that mode.
- Target-temperature and preset actions are feature-gated. HVAC mode changes
  have no separate read-only feature flag; do not assume a read-only climate
  entity is equivalent to a sensor or that removing target support blocks modes.
- HA converts temperature actions to the native unit and checks min/max bounds.
  The adapter/SDK must still validate finiteness, increments, types, permissions,
  context, and expected values.
- `set_temperature` can include an HVAC mode. HA forwards that combination to
  the entity; the adapter must explicitly handle or reject it, not accidentally
  perform a two-write operation.
- HA's default `turn_on` favors Heat when Off/Auto/Heat are offered. It would
  bypass the proposed intention to resume Automatic, so use an explicit mapping.
- The tested baseline supports native-temperature properties; use those for new
  code instead of deprecated non-native Python property names.

The existing SDK's `HeatingCircuitMode` defines IDs 0–5 and immutable zone-to-mode
parameter metadata. Firmware evidence and the current numeric controls identify:

| Circuit | Mode parameter | Comfort setting | Reduced setting | Room channel | Runtime mode channel |
|---|---|---|---|---|---|
| 1 | `A1z` | `4` | `5` | `Hk1Fr35RaumTemp` | `Hk1Status` |
| 2 | `A11z` | `7` | `8` | `Hk2Fr35RaumTemp` | `Hk2Status` |
| A | `A61z` | `HP4` | `HP5` | `HkAFr35RaumTemp` | `HkAStatus` |

The runtime mode-channel binding is code-derived, not a validated deployment
contract. It needs compatibility/range checks and synchronization investigation
before being used as the authoritative climate state. Do not confuse `Status`
(selected runtime mode) with `Zustand` (circuit operating state) or flow targets.

## 3. Proposed HVAC/preset mapping

| Controller mode | ID | HVAC mode | Preset key | Display label |
|---|---:|---|---|---|
| Off | 0 | `off` | `none` | None |
| Automatic | 1 | `auto` | `none` | None |
| Permanent reduced | 2 | `heat` | `permanent_reduced` | Permanent reduced |
| Permanent comfort | 3 | `heat` | `permanent_comfort` | Permanent comfort |
| One-time comfort | 4 | `heat` | `once_comfort` | One-time comfort |
| One-time reduced | 5 | `heat` | `once_reduced` | One-time reduced |

Custom presets preserve the exact distinction rather than equating one-time
comfort to Boost or reduced operation to Away. Translating/icon mapping belongs
to the HA adapter, not localized firmware text or the SDK's wire JSON.

An alternative is Auto with temporary presets, but the proposed mapping keeps
plain Auto unambiguously scheduled and groups all explicit overrides under Heat.
The controller's eventual return to Automatic becomes Heat → Auto in HA.

### Command semantics — approved Heat default; other details proposed

| Action | Proposed controller operation |
|---|---|
| Set Off | Write mode 0 for this circuit only |
| Set Auto | Write mode 1, cancelling any override |
| Set Heat while already in one of the four overrides | No change; preserve that override |
| Set Heat from Off/Auto | Write mode 3, permanent comfort; **approved default** |
| Select a named preset | Write its exact mode ID, including when currently Off/Auto |
| Select None during an override | Write mode 1, returning to Automatic |
| Select None while Off/Auto | No change; do not enable an off circuit |
| Turn on | Write mode 1, resuming Automatic rather than a remembered preset |
| Turn off | Write mode 0 for this circuit only |

A named preset is an explicit circuit-mode selection, not a separate turn-on
followed by another write. Re-selecting an already selected mode must not invent
an override rearming mechanism. No last-preset datastore or startup restoration.
Specify and test these rules before implementation; a generic Heat button cannot
encode all four overrides without a documented default.

## 4. One-time modes are controller-owned

Reused 2.7.4 C/assembly evidence:

- `HeizkreisBase::checkApproval` (`0x146160`) checks the selected daily/weekly
  comfort program.
- `HeizkreisBase::AblaufWork` (`0x148d84`) uses a transition marker and returns
  one-time modes to Automatic via `setHKModus(..., 1)`.
- One-time comfort observes a period outside comfort, then returns when a
  comfort period is entered. The relevant branch was checked in assembly at
  `0x148e9e`–`0x148ec2`.
- One-time reduced observes a comfort period, then returns when it ends. Its
  branch/reset was checked at `0x148e28`–`0x148e58`.
- `setHKModus` (`0x14687c`) does not change an already-selected mode and resets
  the transition marker on a mode change.

These are not fixed-duration overrides or a guarantee of resetting at the very
next boundary regardless of the phase in which the override was selected.
They may span a fuller schedule cycle. Firmware behavior on deployed versions,
including repeated selection and interaction with room-unit commands, remains
unverified. No decompiled vendor artifact is to be committed with this plan.

HA must observe automatic expiry, never implement it with its own timer, clear
an override after an assumed duration, or replay a temporary mode on reconnect.

## 5. Temperature editing — approved for all four presets

Approved routing when the corresponding setting is individually writable:

| Selected preset/mode | Dial setting |
|---|---|
| Permanent comfort | This circuit's stored comfort setting |
| Permanent reduced | This circuit's stored reduced setting |
| One-time comfort | This circuit's stored comfort setting, persistently |
| One-time reduced | This circuit's stored reduced setting, persistently |
| Automatic / Off | No dial write; retain separately named number entities for editing settings |

There is no reviewed temporary setpoint writer. The user explicitly approved
persistent target changes during one-time presets: changing the comfort target
during one-time comfort also changes future scheduled comfort operation; the
same applies to the reduced target. Override expiry changes the mode only and
must not undo the target edit. Explain this in the integration documentation;
do not present such a write as applying only until the override expires.

Planned feature scope includes modes/presets, optional current room temperature,
and target editing for both permanent and one-time presets. A mode-only
implementation can be an internal milestone, not a substitute for the approved
target-editing scope. Target support/bounds/step must reflect the particular permitted
setting; do not expose a functioning dial for a setting the user did not opt in.
HA has no distinct read-only-target feature; check UI/state behavior when target
editing is omitted or capabilities change with the preset. Retain number and
readback entities for explicit access to both settings.

Do not use `TARGET_TEMPERATURE_RANGE`: comfort/reduced are alternate setpoints,
not lower/upper limits of a heat/cool thermostat. Do not substitute `Hk*Soll`,
which is a calculated flow target, for a room target.

Snapshot mode and chosen parameter once per command; never silently reroute an
in-flight request to the other target after a mode change. Investigate guarding
against concurrent climate/select/number operations, automatic expiry, and
external-controller changes. Parameter expected-value checks are not an atomic
cross-parameter mode-and-target transaction; a preflight reread cannot eliminate
external races. Reject ambiguous/stale contexts without any parameter POST.
Do not claim atomicity the API cannot provide.

## 6. Measurement, state, and permissions

- Report current room temperature only from the reviewed matching channel,
  using native °C. Missing/failed channels and the known −1 sentinel yield
  unknown current temperature, not zero or circuit-flow temperature.
- Retained room readings can be stale even after a successful HTTP read; retain
  the [channel guide's validity limitations](channel-guide.md). Do not drive a
  thermostat algorithm from these reports.
- Missing room feedback alone must not make valid mode controls unavailable;
  weather-compensated operation does not require a room-temperature measurement.
- Omit `hvac_action` until separately reviewed evidence supports it. A pump
  request, comfort mode, or warm flow does not prove physical heat delivery.
- Off maps only to the circuit's Off mode. Frost protection/residual-heat logic
  can still operate; it does not turn off the whole boiler or guarantee no flow.
- Approved registration: climate controls only for applicable circuits whose
  mode-write parameter is individually enabled. No additional read-only climate
  representation is planned. Do not enable writes for every circuit simply
  because the user requested one entity per zone.
- Mode/preset/turn-on/off writes require the existing mode permission. Target
  edits additionally require the particular numeric permission, with no broader
  climate-wide write bypass.
- Unknown mode IDs, changed parameter contracts, failed identity checks,
  unload/closed runtime, and missing required context disable/refuse affected
  controls without guessing a default mode.

## 7. Implementation phases and gates

| Phase | Work | Gate |
|---|---|---|
| A — Finalize behavior | Record approved core decisions and resolve remaining command details below | Core decisions approved; unanswered details are not approval |
| B — Resolve state contract | Trace mode-parameter/runtime-channel synchronization and controller-owned expiry; define source precedence/failure policy | Code evidence and synthetic cases; scoped live reads only if separately authorized |
| C — Mode-only climate | Add platform, per-circuit descriptions, HVAC/presets, native units, optional room reading, translations, identity and availability | Actual-HA and HA→SDK→HTTP offline tests; no new SDK permissions |
| D — Target editing | Implement approved persistent routing for permanent and one-time presets, feature gating, metadata bounds/steps, conflict checks, and rejection of multi-write requests | Target behavior approved; actual-HA and loopback coverage required before delivery |
| E — Development installation | Update guide and regenerate the private bundle using the existing tool | Manual installation/restart verified; no automatic deployment |
| F — Controlled validation | Observe state convergence/expiry; test selected writes only under a separate agreed hardware procedure | No assumption of physical effects, reboot persistence, or production readiness |

Keep HA-only presentation code in `custom_components/hargassner_hv/`. Reuse
`RuntimeData.async_control` for expected values, identity checks, cancellation,
verified readback, and error translation. Protocol/validation fixes belong in
`src/hvclient/` and require independent SDK tests/version handling if changed.

Subscribe to both parameter and channel coordinators without extra polling
loops. Current intervals are 300 seconds for parameters and 30 seconds for
channels. Validate `Hk*Status` as the selected runtime mode and define how it
interacts with freshly verified mode-parameter readback; do not silently display
an expired preset for five minutes or let an older channel snapshot overwrite a
newly verified write. Do not restore requested state optimistically.

A combined `set_temperature`/HVAC-mode action that requires two writes should
be rejected before either write. Define whether a supplied mode already matching
the current mode is accepted; also reject unsupported ranges/extra control fields.
Do not partially apply, roll back, or replay the request.

## 8. Offline acceptance coverage

- All six IDs round-trip through the approved HVAC/preset matrix for 1/2/A.
- Mode/None/Heat/on/off commands obey the matrix, including off-circuit preset
  selection and repeated one-time selection; each intentional change writes at
  most one reviewed parameter.
- Default setup, entity addition, migration, reload, shutdown, reconnect, and
  synthetic expiry send no unsolicited parameter writes or SERVICE-initiating
  authentication requests.
- Controller/other-client changes converge to reported state; test expired
  presets, stale parameter/channel snapshots, invalid/missing runtime IDs,
  verification uncertainty, and failure/recovery of each coordinator separately.
- Unknown/sentinel/missing room readings leave mode controls usable; no room
  value is manufactured from flow telemetry, and no HVAC action is inferred.
- Permissions are checked per operation; partial numeric opt-ins do not grant
  editing of the other target, and mode opt-in does not grant temperature writes.
- Target editing: native-unit conversion, finite values, bounds,
  increments, preset-dependent routing/features, context changes/concurrency,
  invalid combined actions, and persistent effects of one-time edits. Synthetic
  override expiry must not restore an earlier comfort/reduced target.
- Existing number/select/sensor IDs and program-action behavior remain intact;
  diagnostics stay privacy-safe, unload cancels operations, and the SDK wheel
  remains free of HA dependencies/code.
- Loopback assertions verify request names/body/counts, explicit safe
  authentication, separate readback, and no retry/rollback after uncertain writes.

## 9. Approval record and remaining details

The user's numbered replies refer to the three questions closing the climate
investigation, not the previous five-item list in this document:

1. **Heat default — approved:** selecting Heat from Off/Auto means permanent
   comfort (mode 3).
2. **Dial scope — approved:** both permanent and one-time presets permit edits
   of their corresponding persistent comfort/reduced target. Per-setting write
   opt-ins remain mandatory; no temporary setpoint or automatic rollback.
3. **Registration — approved:** create climate controls only for applicable
   circuits with the mode-write permission explicitly enabled.

Remaining command details to confirm before implementation:

- The complete Off/Auto/Heat plus four-preset matrix, especially selecting None
  during an override versus while Off/Auto.
- Automatic as the meaning of Turn on. The approval of the Heat default does
  not itself decide Turn on, which was not one of the three numbered questions.
- Treatment of a combined temperature request containing a mode that already
  matches the current mode; requests requiring two writes remain excluded.

Updating this plan records approved design decisions, not completed implementation
or authorization to commit, publish, make new hardware requests, or perform live writes.
