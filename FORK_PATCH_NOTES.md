# Fork patch notes — 1.2.5

Maintainer: [npinguin](https://github.com/npinguin)  
Upstream: [dm82m/hass-Deltasol-KM2](https://github.com/dm82m/hass-Deltasol-KM2)  
Based on upstream 1.2.4 (same Home Assistant integration domain: `deltasol`).

## Fix: missing VBus endpoints

When a previously discovered VBus field disappears from a successful poll, the
registered Home Assistant entity must not raise `KeyError` while its state is
calculated. The affected field is temporarily marked unavailable until it returns.

- `available` is determined per field, not only by coordinator-wide success.
- `native_value` returns `None` if the field is missing; valid `0`,
  `False` and other reported values are preserved.
- `device_class` uses the originally discovered endpoint metadata and remains
  valid during temporary field loss.
- `Last Updated` does not advance for a missing field.
- The entity is updated when the field disappears or recovers.

No changes to discovery IDs, unique IDs, entity registry entries, device links,
or Home Assistant configuration. Custom entity names are retained.

## Validation

- Regression tests: `python -m unittest discover -s tests -v`
- GitHub Actions: Sensor regression tests, Ruff, YAML lint, HACS, Hassfest.
- Live Home Assistant / physical Resol runtime proof remains to be performed.

## HACS deployment (after publishing GitHub release 1.2.5)

This fork uses the same domain as upstream; **do not install both copies in parallel**.

1. Back up Home Assistant configuration and note the currently installed integration.
2. Register `npinguin/hass-Deltasol-KM2` as a HACS custom integration repository.
3. Install the fork's **1.2.5 release** via HACS, replacing the upstream-managed
   installation rather than creating a second `deltasol` directory.
4. Restart Home Assistant. Do not delete/re-create the configured DeltaSol entry
   or its entities.
5. Verify normal readings, unavailable behavior when a field is missing,
   recovery after the field returns, and absence of `KeyError` logs.

Note: Missing fields can be caused by changing VBus data, filters or packet
availability; this patch protects HA state handling but does not manufacture
missing measurements or change the data source.
