> [!NOTE]
> **npinguin development fork.** `master` mirrors upstream exactly; fork changes live on `dev`. Existing HACS releases are preserved. Install only a validated fork release from `npinguin/hass-Deltasol-KM2` (same `deltasol` domain; do not install both). Upstream now incorporates the UTC and missing-field fixes. See [open work](https://github.com/npinguin/hass-Deltasol-KM2/issues).
> Fork publication is explicit: an authorized `release: ...` commit on `dev`, with a new manifest version, must pass the existing release checks. Ordinary development pushes only validate. TEST releases are GitHub prereleases; enable prereleases in Marketplace. The shipped ZIP must pass the isolated Home Assistant lifecycle and network-protocol contract checks, including upgrade and rollback to the retained predecessor artifact (exact 1.2.16 for the next candidate; earlier releases remain available). Isolated protocol-peer evidence is not physical RESOL acceptance. Do not test manual overrides on a hot solid-fuel installation.

[![version](https://img.shields.io/github/v/release/dm82m/hass-deltasol-km2?style=for-the-badge)](https://github.com/dm82m/hass-Deltasol-KM2)
[![downloads_total](https://img.shields.io/github/downloads/dm82m/hass-deltasol-km2/total?style=for-the-badge)](https://github.com/dm82m/hass-Deltasol-KM2/releases)
[![downloads_latest](https://img.shields.io/github/downloads/dm82m/hass-deltasol-km2/latest/total?style=for-the-badge)](https://github.com/dm82m/hass-Deltasol-KM2/releases)
[![maintained](https://img.shields.io/maintenance/yes/2026?style=for-the-badge)](https://github.com/dm82m/hass-Deltasol-KM2)
[![license](https://img.shields.io/github/license/dm82m/hass-deltasol-km2.svg?style=for-the-badge)](https://github.com/dm82m/hass-Deltasol-KM2/blob/master/LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![buy_me_a_coffee](https://img.shields.io/badge/If%20you%20like%20it-Buy%20me%20a%20coffee-yellow.svg?style=for-the-badge)](https://www.buymeacoffee.com/dirkmaucher)
[![paypal](https://img.shields.io/badge/If%20you%20like%20it-PayPal%20Me-blue.svg?style=for-the-badge)](https://paypal.me/dirkmaucher)
[![forum](https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge)](https://community.home-assistant.io/t/hass-deltasol-km2-resol-km1-km2-dl2-dl3-vbus-lan-vbus-usb/871497)

[![GitHub Repo stars](https://img.shields.io/github/stars/dm82m/hass-deltasol-km2?style=flat)](https://github.com/dm82m/hass-Deltasol-KM2/stargazers) _Thanks to everyone having starred my repo! To star it click [here](https://github.com/dm82m/hass-Deltasol-KM2), then click on the star on the top right. Thanks!_

# hass-Deltasol-KM2

Custom component for retrieving sensor information from Resol KM1/KM2 or DL2/DL2Plus/DL3 or VBus/LAN or VBus/USB. This component automatically determines if you are using KM2 or DL2/DL2Plus/DL3 device and can also be set up to work with KM1, VBus/LAN or VBus/USB.
Component uses webservice to get all the sensor data and makes it available in [Home Assistant](https://home-assistant.io/).

## Features

### Currently Available Features

- :computer: This integration supports all Resol devices: KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN and VBus/USB. Welcome the *DL2Plus* as the newest family member.
- :tv: This integration is fully configurable within the UI, no `yaml` configuration needed. *It now even guides you through the whole setup process and only asks you for the options that are really needed for your specific device. And we even have the possibility to re-configure your device and change all options you made before.*
- :star: The exclusive filter that is only available on DL2/DL3 devices can be used with this integration!
- :link: If the connection to your Resol device gets lost (network issue, App not yet started, ...) the integration makes retries until the connection is established.
- :large_blue_diamond: All the data of your Resol device is shown in Home Assistant. I.e. Product Name, Serial Number, Product Features, Software Version, Hardware Version, ...
- :small_blue_diamond: All bus devices that are connected to your Resol device are shown as device in Home Assistant with the specific bus device name. Additionally all the sensors are grouped by these bus devices to make it easier to find your desired sensors.
- :small_orange_diamond: By default all sensors without a unit are handled as diagnostic sensors and disabled by default. You can manually enable them if needed.
- :earth_africa: Multiple language support, currently we have :uk:, :de:, :it:, :netherlands: and :fr:. If you speak another language, just directly open a PR or create an [Translation Request](https://github.com/dm82m/hass-Deltasol-KM2/issues/new?template=translation_request.yml) for your language and provide the translation there.

### Next To Come Features (Wishlist)

- ... more ideas? Feel free to open an [Feature Request](https://github.com/dm82m/hass-Deltasol-KM2/issues/new?template=feature_request.yml).

### Bugs

:bug: No software is perfect and therefore neither is this one. If you encounter a problem, please open an [Bug Report](https://github.com/dm82m/hass-Deltasol-KM2/issues/new?template=bug_report.yml).

## Release 1.x.x

> [!IMPORTANT]
>
>With version 1.x.x we have moved on to UI based configuration. The `yaml` configuration will be automtically migrated. The process is as following:
> - Update the integration to version 1.x.x.
> - Restart Home Assistant Core.
> - Remove the old sensor definition from your `configuration.yaml`.

## Installation

### HACS (preferred method)

- [![add_resol](https://img.shields.io/badge/Add%20Integration-Home%20Assistant-blue?style=flat)](https://my.home-assistant.io/redirect/hacs_repository/?owner=npinguin&repository=hass-Deltasol-KM2&category=integration) or go to your HACS store and search for `dm82m/hass-Deltasol-KM2`.
- Click `Download` in the lower right side and install it.
- Restart Home Assistant Core.
- [![configure_resol](https://img.shields.io/badge/Configure%20Integration-Home%20Assistant-blue?style=flat)](https://my.home-assistant.io/redirect/config_flow_start/?domain=deltasol) or configure the integration by adding it via UI `Settings` -> `Devices & Services` -> `Add Integration` -> `Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB`. Then follow the configuration steps below.

### Manual install

Create a directory called `deltasol` in the `<config directory>/custom_components/` directory on your Home Assistant instance. Install this component by copying all files in `/custom_components/deltasol/` folder from this repo into the new `<config directory>/custom_components/deltasol/` directory you just created.

This is how your custom_components directory should look like:

```bash
custom_components
├── deltasol
│   ├── __init__.py
│   ├── config_flow.py
│   ├── const.py
│   ├── deltasolapi.py
│   ├── manifest.json
│   ├── sensor.py
│   ├── strings.json
│   └── languages
│       ├── de.json
│       ├── en.json
│       ├── fr.json
│       ├── it.json
│       └── nl.json
```

Restart Home Assistant Core and afterwards [![configure_resol](https://img.shields.io/badge/Configure%20Integration-Home%20Assistant-blue?style=flat)](https://my.home-assistant.io/redirect/config_flow_start/?domain=deltasol) or configure the integration by adding it via UI `Settings` -> `Devices & Services` -> `Add Integration` -> `Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB`. Then follow the configuration steps below.

## Configuration

In any case, try to ensure to have the latest firmware running on your Resol device. Also ensure to have the latest `VBus Specification File (VSF)` installed on your Resol device.

### KM2 and DL2/DL2Plus/DL3

It works out-of-the-box, the only thing that is needed is the configuration described next.

#### Configuration

- `Host`: Hostname or IP address of your Resol KM2 or DL2/DL2Plus/DL3.
- `Port`: Port of your Resol KM2 or DL2/DL2Plus/DL3. Defaults to 80. Do not change that, unless you know what you do.
- `Username`: Username used for logging in to Resol KM2 or DL2/DL2Plus/DL3.
- `Password`: Password used for logging in to Resol KM2 or DL2/DL2Plus/DL3.
- `Scan interval` (Optional): Defines update frequency. Optional and in seconds. Defaults to 300 (5 min), minimum value is 5 (5 sec).
- `API key` (Optional):  Only applicable if you are using DL2/DL3 device. Applies the filter defined on the DL2/DL3. Use the id of the DL2/DL3 defined filter here.

### KM1, VBus/LAN and VBus/USB

There is one prerequisite before it works. You need to run the resol-vbus [json-live-data-server](https://github.com/danielwippermann/resol-vbus/tree/master/examples/json-live-data-server). To do so you have at least two possibilities:
1. Run the server as an HAOS App, continue [here](https://github.com/dm82m/hassio-addons/tree/main/resol-vbus).
2. Run the server on your own, continue [here](https://github.com/danielwippermann/resol-vbus/tree/master/examples/json-live-data-server) and ensure to be at least on git commit `7c9e88af5af3e14443c01a4ec5d7c042be2163f9`.

If your json-live-data-server is successfully running, lets continue here.

#### Configuration

- `Host`: If you went with point 1 it is `127.0.0.1` and if you went with point 2 it is `hostname_or_ip_of_your_json-live-data-server`.
- `Port`: Within default `json-live-data-server` it is port `3333`. If you have changed that, you need to use this port here aswell. Otherweise set it to `3333`.
- `Scan interval` (Optional): Defines update frequency. Optional and in seconds. Defaults to 300 (5 min), minimum value is 5 (5 sec).
- Do not set `Username`, `Password` or `API key` here, they are not needed.

## Troubleshooting
Please set your logging for this custom component to debug during initial setup phase. If everything works well, you are safe to remove the debug logging:
```yaml
logger:
  default: warn
  logs:
    custom_components.deltasol: debug
```


### Relay mode control (development scope, issue #5)

- **DS-REL-01:** Discover supported controller relay parameters without changing settings. Show observed relay mode separately from actual output; retain all sensor identities, values and history.
- **DS-REL-02:** An engineer explicitly selects a verified operating mode for a supported relay: Auto/Off/On for fixed switching, or Auto/Off/Min/Max for speed-controlled switching. Use one authenticated local VBus transaction, confirm parameter readback and preserve every other relay and the installed program. Manual override may persist after HA loss; restoring Auto gives control back to the controller.
- **DS-REL-03:** Reject unknown controller/parameter identity, ambiguous mode encoding, denied auth, bus contention, invalid target and stale/missing/mismatched readback. Release the bus and report failure without optimistic state or blind write retry. Keep existing sensor polling available independently. No physical test on a hot solid-fuel installation.

- **DS-REL-04:** Native Home Assistant Download diagnostics exports cached gateway model/version, sensor health, discovered controller profile, changeset, per-relay support and last observed mode, plus sanitized error state. No network calls or writes on export. No credentials, host, serial, device names, raw payloads or arbitrary exception text. Distinguish not configured, unsupported, unavailable and supported; stale observations must not be presented as current.

Discovery uses the observed controller address, its changeset and hashed semantic parameter identifiers to resolve current indices and read current values. Readable parameters are not automatically writable; verified model-specific enum semantics are required before exposing controls. Discovery never writes parameters.

Capability gating (DS-REL-01/03): create and enable a control only when the current controller positively resolves the named parameter, returns a valid value and has verified writable mode semantics for that controller/changeset. Unknown, unsupported, read-only or ambiguous parameters must not be offered as enabled controls or accepted by the write path, even if called directly. Temporary communication failure on a previously supported control makes it unavailable; preserve its entity identity and restore availability only after successful rediscovery/readback. Existing telemetry must remain independent. Acceptance: mixed supported/unsupported parameters, unknown firmware, stale capability after reconnect and invalid options; prove that no write datagram is sent for any rejected target.

The API owns parameter communication and verified metadata, the coordinator owns refresh/availability, and native HA Select is the consumer. No generic parameter editor or separate runtime. The initial controller profile uses the RESOL BX Plus 2.08 RPT device definition (address 0x7112) and menu metadata (Handbetrieb enum Off=0, Min=1, Auto=2, Max=3, Ein=4). The allowed modes are read from the output's speed-control flag: fixed switching uses Auto/Off/On (Ein=4); speed-controlled outputs use Auto/Off/Min/Max (1/3). Max and On are distinct. Existing 1.2.8 automations that used On for a speed-controlled relay must use Max instead. An observed value outside the current allowed options is reported separately rather than misrepresented. The exact reference parameter layout must match dynamically resolved indices; missing or incompatible controls are not created. Actual changeset is rechecked before each write. This is source-level compatibility evidence, not physical acceptance.

To enable parameter discovery on an existing KM2/DL2 Plus entry, use Settings → Devices & services → RESOL → Configure and enter the gateway's separate local VBus remote-access password. Blank retains sensor-only operation. No existing credentials or entity IDs change. The HTTP sensor refresh and slower VBus parameter refresh are independent native HA coordinators sharing one API. Manual overrides may persist when HA disconnects; return to Auto after use.

Reference: RESOL BXPLUS_2.08 RPT package, SHA256 1bcc59cac3d4176c68de255fced3b95840bada06db33b0bdae0cddb007a9f079, inspected in PR validation job 138837397481. The ZIP is not redistributed. Hardware qualification remains required.

### Device and module relay discovery (issue #8)

- **DS-REL-05:** Derive a single device inventory from existing telemetry; retain native device and entity identities. Include observed packet source addresses and device types in redacted diagnostics without exporting names, serials, credentials or site data. A controller profile defines verified relay mode and module registration/presence semantics; unknown devices remain observable, never blindly writable.
- **DS-REL-06:** Discover relay mode capabilities on each present, registered module; use the existing parameter transport and native selects, with the correct existing device grouping. No global module count, duplicate discovery or generic arbitrary parameter writer. Reject absent, ambiguous, changed or unsupported modules and parameters before a write; keep previous IDs on reconnect.
- **DS-REL-07:** Preserve the precise observed relay mode, including distinct vendor Min, Max and Ein values. Expose only verified allowed modes per physical output; report unsupported observed values accurately and do not substitute Max for Ein. Preserve the existing R1–R5 entity IDs. Reuse existing output names only where an unambiguous correspondence exists; retain the relay number.

The verified BX Plus profile has five local relays and two EM slots, with registration/presence flags (Aktiviert/Vorhanden), individually resolved Handbetrieb and ODrehzahl parameters and native EM telemetry addresses 0x6651/0x6652. Other observed devices are included in diagnostics, without implying write support. Additional verified controller profiles can supply their own group definitions; the engine has no global module count.

Field timing feedback (#5/#8): all parameter reads and writes share one serialized session owner and a conservative 1.5-second reconnect guard after session close, including errors. This is a candidate timing value, not a measured KM2 minimum. Relay read refresh additionally retries transient transport failures twice (2 and 5 seconds) before marking the update failed; every retry repeats identity/layout/registration checks on the current telemetry. Explicit negative observations remain unavailable and are not disguised as transport errors. No blind write retry; an uncertain write requires fresh readback. Diagnostics distinguish connection, handshake, acquisition, command response, uncertain write and release failures. Physical repeated-write qualification remains required.

Acceptance: existing sensor and controller controls retain identity; one or more registered modules, missing/disabled modules, ambiguous identity, unknown profile, mixed output types, independent write/readback and diagnostics, lifecycle, and rollback to 1.2.8. Physical actuation remains separate from isolated HA qualification.

### Parameter capability discovery and configuration (issue #10)

Owner-approved sequencing (25 Nov 2026): 1.2.10 Discovery TEST delivers the complete read-only inventory/snapshot/comparison journey while preserving all existing controls. 1.2.11 is the owner-approved corrective Discovery TEST; 1.2.12 is the owner-approved Resilience TEST (Discovery and relay read recovery); 1.2.13 (next planned version) is the Configuration TEST for uniform validated writes; DS-PAR-03/04 remain open on the same issue. No partial Discovery release. Follow the existing metadata-driven telemetry pattern: gateway response → native endpoint → stable device/entity; parameter catalog → resolved live observation → typed metadata → native diagnostics, with a shared VBus session owner. Use locally supplied vendor definitions if redistribution rights are not established; do not bundle proprietary metadata or backups. No raw index scan. Existing sensor, relay and EM identity/availability behavior stays intact.

**Read first, write uniformly.** Inventory all readable parameters of the actual controller firmware/changeset, not just already-supported controls. The parameter catalog is not live capability evidence. Keep the semantic ID, resolved index, actual raw/decoded result, type/constraints, access, storage and provenance distinct; distinguish missing, refused, invalid, stale and transport failure, and explicitly report incomplete discovery. Discovery is bounded, resumable and on-demand outside setup; release the bus between batches. One metadata-driven validated writer handles every positively verified writable type, not individual use-case-specific writers or a handpicked parameter list. Preserve safety, installed program, permissions and dependency constraints; never probe write access by changing a live parameter. Read-first is the owner-approved release sequence, not a reduction of the full product scope.

- **DS-PAR-01:** discover the readable parameters of the supported firmware profile against the observed controller and registered devices; resolve semantic IDs dynamically and verify layout, type, units and constraints. Do not infer availability merely from a vendor catalog.
- **DS-PAR-02:** provide an on-demand, redacted current configuration snapshot, with typed values, storage/access metadata, defaults where defined, and explicit missing/stale status. Compare snapshots without automatic restore or an additional state store.
- **DS-PAR-03:** expose all positively verified writable parameter settings with native HA entities appropriate to their types and the existing single serialized writer. Validate range, step, active function and conditional dependencies. Preserve installed program topology, safety limits and credentials; these are read-only or excluded if sensitive.
- **DS-PAR-04:** keep volatile write and persistent save distinct within the same transport. Require authoritative readback, no repeat writes for unchanged values, no blind retry after uncertain outcome. Verify persistence after reconnect and isolated peer restart; physical power-cycle retention is separate site evidence.
- **DS-PAR-05:** preserve all existing telemetry and relay controls/identities; test discovery, validation, rejection, readback, persistence, reconnect/recovery and rollback to actual 1.2.9. No public raw parameter writer, second bus engine, new service or automatic optimization.

### Discovery field corrections (issue #10, DS-PAR-01/02/05)

1.2.11 TEST corrects the 1.2.10 field findings. Exclude sensitive semantic identifiers (including Bedienercode and related reset parameters) before lookup/read and from exports/comparisons. Never read a resolved index inconsistent with the verified catalogue or a sensitive reference index. Prioritize Schema, known controller/module registration and relay parameters, then configuration storage before UI/runtime values, retaining every other safe candidate. Reuse the same serialized bus owner, with a single layout/changeset validation sequence per batch and unchanged release/recovery limits. Expose native Discovery status and count/progress attributes without a new poll or entity per parameter. Diagnostics retain the complete candidate inventory and per-result status, but export detailed metadata only for positively read values; non-readable/pending definitions retain identity, index, type and storage. The pinned local archive remains the source of full metadata. Include read-only EM registration evidence (Aktiviert, Moeglich, Vorhanden, Status and actual relay observations); do not weaken existing write gating based solely on telemetry. Verify no credential reads, no new writes, bounded coverage, compact export, progress/recovery and existing relay/telemetry identity. Rollback to exact 1.2.10. Physical performance and EM control acceptance remain open.

### Configuration Foundation TEST 1.2.13 (DS-PAR-03/04/05)

The owner requested publication of this independently usable TEST increment on 26 November 2026. The release contains the finished Discovery lifecycle improvements, exact-profile EM registration correction and initial five basic settings (automatic daylight saving time and four display-unit selections). It does not deliver full thermal configuration coverage or a production numeric setting; Number is a reusable adapter and broader configuration remains in issue #10. Physical write/actuation validation remains separate. Upgrade from 1.2.12 requires one new Discovery because the prior version held the inventory only in memory. No site values are bundled.

The owner requested a directly visible completion percentage after physical Discovery testing. Add the native diagnostic **Configuration discovery progress** sensor (0–100%) alongside the unchanged status sensor. Both use the same committed capture offset and push callbacks; no extra bus traffic, polling or device. Discovery button, status and progress are grouped under Diagnostics on the existing controller; status attributes and the downloaded diagnostics summary expose attempted, read, unresolved, other unread, remaining, retries, error and timestamps. Progress remains unchanged during retry, pause or failure and reaches 100 only on completion. Existing status distinguishes complete from incomplete; coverage is not read success. Test initial, intermediate, failed, recovered and completed states, upgrade and rollback.

Full physical Discovery on BX Plus changeset 2734DABC proved that all module presence/possible/status flags are unresolvable volatile lookups while activation and relay pairs are addressable. For this exact profile only, positive activation plus unique observed physical module identity and verified mode/speed indices may establish module registration when the presence lookup is specifically not_resolved. Explicit negative, refused, mismatched, invalid, ambiguous or missing-device evidence still blocks control. Re-observe gateway telemetry and recheck activation, layout and relay mapping before every fallback write. Physical module actuation remains a separate test; no automatic writes.

The controller owns its installed program; read access and catalogue storage are not proof of write permission or active applicability. A single typed configuration path now uses exact-profile semantic contracts, native Number/Select/Switch projections and the existing serialized parameter owner. The initial corroborated Basic settings (automatic DST and display temperature/flow/pressure/energy units) use the exact current RPT IDs, menu evidence, RESOL BX Plus manual Basic settings and MIT resol-vbus optimizer/ConnectionCustomizer (save action). The current profile rejects all unknown settings and layouts. Read hashes/indices and dependencies again before each action, validate positively established same-encoding pair constraints against the proposed value before writing, avoid unchanged writes, and confirm an actual save in a new authenticated session without resending it. Diagnostics distinguish current verified controls from historical capture and explain missing write contracts. The independent HA peer tests 0x0400 save, exact no-op behavior and peer persistence across restart. Broader thermal, compound and conditional settings remain open until their semantics are verified; native numeric tests are not physical proof of a thermal control. No raw service or automatic configuration mutation. The MIT resol-vbus base configuration optimizer provides an independent classification hint: unspecified storage or explicit allowParameterization, with compound/selector dependencies. Discovery reports these hints separately from write access and preserves selector references; the older BX Plus optimizer layout is not a permission or address source for this firmware. Known numeric missing-value sentinels and unverified differing display/store factors remain raw rather than falsely decoded. For each candidate distinguish type encoding, static limits/step, conditional limits and visibility, storage semantics and independent write-access evidence. Do not infer that unspecified storage means persistent or that forceUse means writable. Preserve explicit enum values and reject ambiguous or incomplete encodings. A shared typed validation path must reject fractional raw values, invalid enum values, limits, dependencies and unknown access before transport. Existing verified relay control is retained; any new native writable entity requires positively verified metadata and live identity/readback. Persistent saves must be independently verified after reconnect without a second save; uncertain outcomes are not retried. Exact archive tests report metadata coverage and missing permission/constraint evidence; the isolated HA lifecycle and actual predecessor ZIP are required before release. No raw generic writer or unverified thermal safety changes.


### Stabilization TEST 1.2.14 (DS-REL-03/04/05/06/07, DS-PAR-01/02/05)

Owner-approved bounded corrective release: existing relay and five configuration controls must remain usable during active Discovery; foreground operations take priority between bounded batches using the existing serialized VBus owner. A failed action is never retried automatically; fresh read-only capability recovery must restore availability when communication returns, without optimistic readback. A stopped/restarted active scan resumes compatible committed observations; manual pause and exhausted/hard failures remain explicit, with sanitized persisted terminal reason. Preserve existing identities, relay mode vs output, safety gates, telemetry independence, all safe discovery candidates and exact predecessor 1.2.13 rollback. No new thermal/compound/HQM write contracts in this increment; full scope remains on #10. Qualification must exercise native actions during active Discovery, transient acquisition failure/recovery and active reload/resume, not just idle writes or completed capture. Physical actuation is a separate supervised site test.

### VBus resilience and operational stability TEST 1.2.15 (issue #10)

Owner authorized the complete stability audit batch, not additional unverified thermal write contracts. Preserve native control IDs, all safe Discovery candidates, checkpoint compatibility and independent telemetry. Field 1.2.14 failed acquisition/release; exact physical cause not yet proven. Acceptance: bounded release-only recovery with exact-source live packet validation; shared adaptive transport cooldown and foreground priority between actual batches; no automatic repetition of uncertain writes; automatic control recovery, explicit historical observations and sanitized phase timings/counters; pre-read expected-index validation; bounded HTTP requests; realistic independent peer qualification with repeated/late/missing offers and release packets, actual 16-candidate batches and sustained sessions, native action/readback, active reload, restart and exact 1.2.14 rollback. Field acceptance separate from TEST publication. Remaining DS-PAR-03/04 thermal/compound/HQM contracts stay open in #10.

### Resilience TEST acceptance (DS-PAR-01/02/05, DS-REL-05/06/07)

Owner authorized this independently usable corrective TEST following repeated field connection interruptions. Preserve all existing native controls, identities, candidate coverage and redaction. A transient disconnect during a Discovery read must recover without another button press; a transient relay refresh disconnect must recover before marking the update failed. Authentication/identity/layout changes remain hard failures; exhausted retries remain incomplete or unavailable. Pause interrupts backoff; writes are never retried. Qualify baseline, upgrade, restart and rollback using exact published 1.2.11 and an independent protocol peer. Include read-only metadata improvements already implemented (explicit enums, type provenance, menu and quant evidence); generic configuration writes, dependency evaluation and EM registration correction remain open on #10/#8, not delivered by this release.

### Catalogue-bound configuration (development)

The existing profile authorizes semantic writes, conditional relationships and storage actions; the exact pinned catalogue supplies numeric encoding and verifies identities, aliases and enum/boolean types at runtime once acquired. A mismatched definition is rejected, not silently converted to a writable entity. Previously source-verified exact-profile settings remain usable before initial catalogue acquisition. Explicitly corroborated integer-step, duration and HQM-factor encoding exceptions are centralized in the existing typed model. No new writer, state store, or vendor-data redistribution. The normal foreground poll also rotates at most four authorized native function selectors per refresh; newer observations supersede capture hints, while all action conditions are re-read independently. Newly enabled native functions do not require another full Discovery. A complete historical capture remains unchanged. Returning a speed-controlled relay to Auto re-evaluates its controls even when its volatile role was unresolved. All authorized contracts must bind to the exact vendor archive in the source test.

### Additional operating controls (development)

Native irradiation thresholds/duration, return-preheat summer switch-off,
thermal circulation temperatures, DHW preheat differential and speed/delay
parameters reuse the existing typed writer and selected-unit checks. Basic
solar store differential, rise, target/maximum temperatures, hysteresis,
minimum runtime and speed and the collector's enabled operational minimum
are conditional on positively resolved live native store/collector
sensor and solar output references and active mode. The firmware's readable
defaults do not establish an installed store. No inferred SystemId mapping,
topology edit, emergency-limit change or automatic write. Exact archive
binding and developer tests cover layout and conditional rejection; no
physical solar installation has yet been qualified.

### Native operating thresholds and speed control (development)

The BX Plus 2.03 manual (pp. 17, 57–58) establishes minimum/maximum speed as native output adjustments when speed control is enabled, and heat exchange/solid fuel source minimum and sink maximum as independent operational thresholds. Exact 2.08 metadata and completed field capture corroborate the wire layout. The existing unique controller/EM registration, live speed-enabled/Auto state, exact parameter identity and ordered 20–100% pair remain mandatory. An unresolved volatile role does not block the native output; explicit contrary role or other lookup failure does. No cross-namespace ordinal inference. Operational source/sink thresholds and rise, plus already-enabled solid-fuel target/mixer and function-block differential, use exact bounds, active function, live option dependencies, selected-unit parent/child equality where applicable and the same save/readback writer. The output scope is native speed adjustment, not inferred hydraulic ownership. No automatic adjustment, topology changes or certified protection overrides. Installer settings can affect heat transfer; use only validated installation-specific values. Physical writes remain unverified. Development, not published.

### Automatic Discovery (next development batch)

After a confirmed supported controller and parameter access, initial read-only Discovery starts outside setup using the pinned catalogue acquisition path. A previously completed legacy prefix is extended automatically; an explicitly paused extension remains paused. Completed captures are not rescanned at ordinary startup. Existing recovery, foreground priority, manual button, checkpoint and write gates remain unchanged. Failed catalogue acquisition or unverified controller identity is reported in diagnostics, without an automatic retry loop; explicit retry remains available. An incompatible checkpoint is not automatically replaced. Development, not a published feature yet.

### Discovery TEST operation

1.2.13 development selects the catalogue by observed controller address and live changeset, not gateway firmware. On the first user-triggered Discovery, reuse the verified local archive or acquire the exact pinned public RPT archive from the existing qualified Mastersolar mirror (HTTPS, size cap, SHA256 and XML validation before atomic local cache). No site data is sent to the download host. The original RESOL [RPT page](https://www.resol.de/en/produkt/169) remains the authoritative source; the exact direct vendor archive URL is not established. If download is unavailable, an exact manually supplied archive at `/config/deltasol/BXPLUS_2.08.zip` remains supported. Unknown firmware and invalid archives do not initiate parameter writes or silently replace metadata. The archive is used locally as data only; no vendor binaries are loaded or redistributed. No firmware update is required. Configure VBus access, then press the native **Discover configuration (start/pause/resume)** button. Press again to pause at the next batch boundary (or immediately during a recovery delay); press again to resume. 1.2.12 TEST adds automatic read-only recovery: retry the same uncommitted batch after 2, 5, 10, 20, 30 and 60 seconds, with a fresh serialized session each time. A successful batch resets the consecutive failure budget. Prolonged outages stop as incomplete after six retries; explicit authentication, controller identity/layout/changeset failures stop immediately. Native status shows recovering and retry details; writes are never retried. No extra poll or scheduler. Download native integration diagnostics to see the capture and per-parameter status. A second completed capture in the same loaded entry includes a comparison. 1.2.13 development adds a private, versioned Home Assistant Store checkpoint: committed observations only, no vendor metadata duplication; revalidate controller identity, changeset and catalogue digest before loading. Save every 128 committed candidates and on completion, pause or unload. An interrupted active scan resumes automatically on compatible reload; manual pause and exhausted retries stay paused until explicitly resumed. Abrupt power loss may repeat up to 127 candidates. Keep downloaded snapshots for your records; historical observations are not write authorization. Discovery does not run during setup, does not send writes, and does not replace the controller's autonomous program. Catalogue coverage is not proof of installed function applicability or write permission.

### Configuration Expansion TEST 1.2.16 (DS-PAR-01/02/03/04/05, issue #10)

Owner authorized this bounded TEST on 27 November 2026 before completion of the full product. Scope: expanded Discovery/compound-parent evidence and compatible snapshots, native installed-function inventory and device association, documented conditional mixer, HQM, heating, differential, thermostat, loading, timing and direct-role pump controls. All available controls must perform real validated native actions with exact current firmware/identity, readback, constraints and save semantics; existing 1.2.15 behavior and IDs remain. The compound selected-unit parent and save contracts are TEST candidates supported by reference menu/optimizer evidence and live equality checks, not physical write qualification. Site acceptance remains open. The new alternative pump assignment contract is retained as source evidence but not authorized for live controls until AktorNr/RelaisNr namespaces are corroborated; direct positively identified pump-role controls remain enabled. Weekly schedule payload decoding/editing, solar topology and remaining conditional functions stay open in #10. This is not Full Configuration completion or STABLE.

Owner approved continuation of the complete configuration batch on 1.2.15. Full scope: actual installed controller/module configuration, precise metadata and applicability, native typed controls for every positively corroborated adjustable parameter, exact storage/readback, snapshot/change detection, and lifecycle/negative tests. Reuse existing catalogue, checkpoint, native entity adapters, single VBus owner and writer; no second database or unrestricted editor. Configuration families include solar, heating, arrangement functions (including differential/solid fuel/mixer), operational pump speeds, timers, domestic hot water and HQM. Keep topology, safety limits, credentials, counters, reset commands and unverified settings read-only. A catalogue entry or successful read does not prove write permission; old firmware optimizer hints do not establish current layout. Resolve compound parent/child selectors, menu masks, type inheritance, quantization and active installed-function dependencies from exact reference evidence. Never trial-write for discovery. Account for every safe catalogue candidate with distinct observation, type, applicability, access and storage evidence. Preserve current entities, checkpoint, transport stability and native telemetry. Candidate must pass actual HA typed actions and authoritative readback, constraint/negative cases, no duplicate save, persistence, upgrade/restart/rollback to exact 1.2.15, and existing load/recovery tests. No publication of a partial configuration release without explicit owner scope decision. Field 1.2.15 snapshot: 11,325/11,325 legacy candidates completed; 8,209 read, 3,116 unresolved volatile parameters; 224/228 transport sessions successful, zero release failures. Native control availability confirmed at snapshot; physical actuation and continuous coexistence remain separate. Preserve legacy checkpoint when extending read-only proxy coverage. Expose installed native function inventory and reference assignments as diagnostic evidence, with raw ordinal provenance and unknown/unresolved distinctions. Never infer physical actuator roles, site names, hydraulic policy or project automations. Pump write eligibility must not depend exclusively on unresolved AktorFunktion; establish function assignment proof and live conditions before enabling alternative ownership evidence. Preserve immutable snapshot semantics and existing devices/entities. Wire scaling and display rounding are different; disclose conversion basis and avoid granting write access from decoding.

The parameter engine is site-independent, not firmware-independent: controller and exact changeset select a verified catalogue/profile. Native function slots are discovered from catalogue type selectors; neither slot count nor installed function type is inferred from a site. Selected-function menu evidence classifies parameters and raw references, while unresolved masks remain unverified. New native function settings use logical controller-function devices with stable slot identifiers; do not move existing sensors, relay modes or basic settings. For the BX Plus profile, HQM settings are associated with the existing native meter device (RESOL VSF 20240922, blob `4b3058e2`: HQM #n = `0x7130 + n`; physical evidence for Wmz1 in #8/#10). The meter must be uniquely observed on the controller's bus with the same gateway identity; missing or ambiguous devices do not grant controls. Writes still target the controller and entity identifiers remain stable. Compound writes and output assignment alternatives require live corroboration in the existing owner; all competing contracts are rechecked at action and reconnect. Unknown model, changed program, ambiguous identity, invalid sibling, unresolved conditions or unproven encoding must not become a write permission. No site-specific logic, address, name, wiring, heat-source priority or automation is part of the integration.

Weekly program Discovery retains the existing scalar observation and checkpoint ordering. For positively allocated and initialized native day definitions, read two additional correlated 32-bit subindices within the same serialized session and bounded batch, distinguishing a refused word from zero and checking each bounded subindex independently. Preserve those raw words in the same checkpoint and distinguish changed overlapping observed words from changed coverage. No guessed time-window decode or raw write service: exact RPT 2.08 Blob has no size/encoding metadata; the manual describes 15-minute UI slots, not the wire bit layout. The actual reference capture has no allocated week programs. Solar_SystemId likewise has only min/max/default and unresolved native presence masks; the older optimizer's thresholds are not current-layout proof. Complete timer and solar configuration require corroborating native protocol/menu evidence from a compatible controller with those functions already configured. Do not change a live installation to probe this.

## Development

If you want to support the development, you can find AI generated development [documentation](https://deepwiki.com/dm82m/hass-Deltasol-KM2) that will help you start contributing to this project.

## Credits

A huge thank you to all the contributors and also thank you to everyone who contributed ideas.

[![contributors](https://contrib.rocks/image?repo=dm82m/hass-Deltasol-KM2)](https://github.com/dm82m/hass-Deltasol-KM2/graphs/contributors)
