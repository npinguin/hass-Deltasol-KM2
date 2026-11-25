> [!NOTE]
> **npinguin development fork.** `master` mirrors upstream exactly; fork changes live on `dev`. Existing HACS releases are preserved. Install only a validated fork release from `npinguin/hass-Deltasol-KM2` (same `deltasol` domain; do not install both). Upstream now incorporates the UTC and missing-field fixes. See [open work](https://github.com/npinguin/hass-Deltasol-KM2/issues).
> Fork publication is explicit: an authorized `release: ...` commit on `dev`, with a new manifest version, must pass the existing release checks. Ordinary development pushes only validate. TEST releases are GitHub prereleases; enable prereleases in Marketplace. The shipped ZIP must pass the isolated Home Assistant lifecycle and network-protocol contract checks, including upgrade and rollback to the retained predecessor artifact (1.2.8 for 1.2.9; 1.2.6 remains available). Isolated protocol-peer evidence is not physical RESOL acceptance. Do not test manual overrides on a hot solid-fuel installation.

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

Field timing feedback (#5/#8): all parameter reads and writes share one serialized session owner and a conservative 1.5-second reconnect guard after session close, including errors. This is a candidate timing value, not a measured KM2 minimum. No blind write retry; an uncertain write requires fresh readback. Diagnostics distinguish connection, handshake, acquisition, command response, uncertain write and release failures. Physical repeated-write qualification remains required.

Acceptance: existing sensor and controller controls retain identity; one or more registered modules, missing/disabled modules, ambiguous identity, unknown profile, mixed output types, independent write/readback and diagnostics, lifecycle, and rollback to 1.2.8. Physical actuation remains separate from isolated HA qualification.

### Parameter capability discovery and configuration (issue #10)

Owner-approved sequencing (25 Nov 2026): 1.2.10 Discovery TEST delivers the complete read-only inventory/snapshot/comparison journey while preserving all existing controls. 1.2.11 is the owner-approved corrective Discovery TEST; 1.2.12 (next planned version) is the Configuration TEST for uniform validated writes; DS-PAR-03/04 remain open on the same issue. No partial Discovery release. Follow the existing metadata-driven telemetry pattern: gateway response → native endpoint → stable device/entity; parameter catalog → resolved live observation → typed metadata → native diagnostics, with a shared VBus session owner. Use locally supplied vendor definitions if redistribution rights are not established; do not bundle proprietary metadata or backups. No raw index scan. Existing sensor, relay and EM identity/availability behavior stays intact.

**Read first, write uniformly.** Inventory all readable parameters of the actual controller firmware/changeset, not just already-supported controls. The parameter catalog is not live capability evidence. Keep the semantic ID, resolved index, actual raw/decoded result, type/constraints, access, storage and provenance distinct; distinguish missing, refused, invalid, stale and transport failure, and explicitly report incomplete discovery. Discovery is bounded, resumable and on-demand outside setup; release the bus between batches. One metadata-driven validated writer handles every positively verified writable type, not individual use-case-specific writers or a handpicked parameter list. Preserve safety, installed program, permissions and dependency constraints; never probe write access by changing a live parameter. Read-first is the owner-approved release sequence, not a reduction of the full product scope.

- **DS-PAR-01:** discover the readable parameters of the supported firmware profile against the observed controller and registered devices; resolve semantic IDs dynamically and verify layout, type, units and constraints. Do not infer availability merely from a vendor catalog.
- **DS-PAR-02:** provide an on-demand, redacted current configuration snapshot, with typed values, storage/access metadata, defaults where defined, and explicit missing/stale status. Compare snapshots without automatic restore or an additional state store.
- **DS-PAR-03:** expose all positively verified writable parameter settings with native HA entities appropriate to their types and the existing single serialized writer. Validate range, step, active function and conditional dependencies. Preserve installed program topology, safety limits and credentials; these are read-only or excluded if sensitive.
- **DS-PAR-04:** keep volatile write and persistent save distinct within the same transport. Require authoritative readback, no repeat writes for unchanged values, no blind retry after uncertain outcome. Verify persistence after reconnect and isolated peer restart; physical power-cycle retention is separate site evidence.
- **DS-PAR-05:** preserve all existing telemetry and relay controls/identities; test discovery, validation, rejection, readback, persistence, reconnect/recovery and rollback to actual 1.2.9. No public raw parameter writer, second bus engine, new service or automatic optimization.

### Discovery field corrections (issue #10, DS-PAR-01/02/05)

1.2.11 TEST corrects the 1.2.10 field findings. Exclude sensitive semantic identifiers (including Bedienercode and related reset parameters) before lookup/read and from exports/comparisons. Never read a resolved index inconsistent with the verified catalogue or a sensitive reference index. Prioritize Schema, known controller/module registration and relay parameters, then configuration storage before UI/runtime values, retaining every other safe candidate. Reuse the same serialized bus owner, with a single layout/changeset validation sequence per batch and unchanged release/recovery limits. Expose native Discovery status and count/progress attributes without a new poll or entity per parameter. Diagnostics retain the complete candidate inventory and per-result status, but export detailed metadata only for positively read values; non-readable/pending definitions retain identity, index, type and storage. The pinned local archive remains the source of full metadata. Include read-only EM registration evidence (Aktiviert, Moeglich, Vorhanden, Status and actual relay observations); do not weaken existing write gating based solely on telemetry. Verify no credential reads, no new writes, bounded coverage, compact export, progress/recovery and existing relay/telemetry identity. Rollback to exact 1.2.10. Physical performance and EM control acceptance remain open.

### Configuration TEST engineering (DS-PAR-03/04/05)

The controller owns its installed program; read access and catalogue storage are not proof of write permission or active applicability. For each candidate distinguish type encoding, static limits/step, conditional limits and visibility, storage semantics and independent write-access evidence. Do not infer that unspecified storage means persistent or that forceUse means writable. Preserve explicit enum values and reject ambiguous or incomplete encodings. A shared typed validation path must reject fractional raw values, invalid enum values, limits, dependencies and unknown access before transport. Existing verified relay control is retained; any new native writable entity requires positively verified metadata and live identity/readback. Persistent saves must be independently verified after reconnect without a second save; uncertain outcomes are not retried. Exact archive tests report metadata coverage and missing permission/constraint evidence; the isolated HA lifecycle and actual predecessor ZIP are required before release. No raw generic writer or unverified thermal safety changes.

### Discovery TEST operation

Download the BX Plus 2.08 RPT import archive from RESOL's [RPT page](https://www.resol.de/en/produkt/169) and put the exact supported ZIP at `/config/deltasol/BXPLUS_2.08.zip` (SHA256 above). The archive is used locally as data only; no vendor binaries are loaded or redistributed. No firmware update is required. Configure VBus access, then press the native **Discover configuration (start/pause/resume)** button. Press again to pause at the next batch boundary; press again to resume. Download native integration diagnostics to see the capture and per-parameter status. A second completed capture in the same loaded entry includes a comparison. A restart/reload discards the in-memory capture; keep downloaded snapshots for your records. Discovery does not run during setup, does not send writes, and does not replace the controller's autonomous program. Catalogue coverage is not proof of installed function applicability or write permission.

## Development

If you want to support the development, you can find AI generated development [documentation](https://deepwiki.com/dm82m/hass-Deltasol-KM2) that will help you start contributing to this project.

## Credits

A huge thank you to all the contributors and also thank you to everyone who contributed ideas.

[![contributors](https://contrib.rocks/image?repo=dm82m/hass-Deltasol-KM2)](https://github.com/dm82m/hass-Deltasol-KM2/graphs/contributors)
