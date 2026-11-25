"""On-demand inventory using the existing VBus owner, never a second writer."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property

from .catalog import BX_PLUS_CHANGESET, decoded_value, is_sensitive
from .parameters import device_inventory, find_profile
from .vbus import ParameterObservation, VBusError

BATCH_SIZE = 16


@dataclass
class ConfigurationCapture:
    """One bounded, resumable capture and its comparison baseline."""

    catalog: object
    controller: object
    changeset: int = BX_PLUS_CHANGESET
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "running"
    finished_at: str | None = None
    error: str | None = None
    results: dict = field(default_factory=dict)
    offset: int = 0

    @cached_property
    def sensitive_indices(self):
        return {d.index for d in self.catalog.definitions if is_sensitive(d)}

    @cached_property
    def candidates(self):
        """Useful physical/configuration evidence first; keep full safe coverage."""

        def priority(definition):
            name = definition.name
            if name == "Schema":
                return 0
            if name.startswith(("Modul1_", "Modul2_")):
                return 1
            if name.startswith("Relais_") and name.endswith(
                ("_Handbetrieb", "_ODrehzahl")
            ):
                return 2
            if name.startswith("Error_Modul"):
                return 3
            if definition.storage == "persistent":
                return 4
            if definition.storage == "unspecified":
                return 5
            return 6

        return tuple(
            sorted(
                (
                    d
                    for d in self.catalog.definitions
                    if d.excluded is None
                    and not is_sensitive(d)
                    and d.index not in self.sensitive_indices
                ),
                key=priority,
            )
        )

    def next_batch(self):
        return self.candidates[self.offset : self.offset + BATCH_SIZE]

    def apply(self, batch, observations):
        for definition in batch:
            if is_sensitive(definition) or definition.index in self.sensitive_indices:
                continue
            result = observations[definition.name]
            if result.index is not None and (
                result.index in self.sensitive_indices
                or result.index != definition.index
            ):
                result = ParameterObservation(result.index, None, "layout_mismatch")
            self.results[definition.name] = result
        self.offset += len(batch)
        if self.offset == len(self.candidates):
            self.status = "complete"
            self.finished_at = datetime.now(UTC).isoformat()

    def export(self, current):
        parameters = {}
        counts = {}
        for definition in self.catalog.definitions:
            if is_sensitive(definition) or definition.index in self.sensitive_indices:
                counts["sensitive_redacted"] = counts.get("sensitive_redacted", 0) + 1
                continue
            observed = self.results.get(definition.name)
            status = definition.excluded or (
                observed.status if observed else "not_scanned"
            )
            if observed and not current:
                status = "stale"
            counts[status] = counts.get(status, 0) + 1
            item = {
                "status": status,
                "reference_index": definition.index,
                "storage": definition.storage,
                "type": definition.type_id,
            }
            parameters[definition.name] = item
            if observed is None or not current:
                continue
            if observed.index not in self.sensitive_indices:
                item["resolved_index"] = observed.index
            if observed.status != "read" or observed.index in self.sensitive_indices:
                continue
            raw = observed.value
            decoded = decoded_value(definition, raw)
            item.update(
                {
                    "raw_value": raw,
                    "decoded_value": decoded,
                    "decode_status": (
                        "decoded"
                        if decoded is not None
                        else "raw_only"
                        if raw is not None
                        else "not_available"
                    ),
                    "metadata": definition.metadata,
                    "access": "read_observed",
                    "write_access": "not_assessed",
                    "applicability": "not_assessed",
                }
            )
        counts.setdefault("sensitive_redacted", 0)
        return {
            "status": self.status if current else "stale",
            "export_format": 2,
            "metadata_scope": "read_values_only",
            "unread_access": "unknown",
            "unread_write_access": "not_assessed",
            "value_semantics": "observations_during_capture_not_live_poll",
            "catalog_digest": self.catalog.digest,
            "controller_address": f"0x{self.controller.address:04X}",
            "changeset": f"{self.changeset:08X}",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "catalog_count": len(self.catalog.definitions),
            "scanned_count": self.offset,
            "coverage": "all_catalogue_candidates_attempted"
            if self.status == "complete"
            else "partial",
            "candidate_count": len(self.candidates),
            "counts": counts,
            "parameters": parameters,
        }


def compare_captures(previous, current):
    """Compare captured values and changes in availability separately."""
    if (
        previous is None
        or previous.status != "complete"
        or current.status != "complete"
        or previous.controller != current.controller
        or previous.changeset != current.changeset
        or previous.catalog.digest != current.catalog.digest
    ):
        return {"status": "not_comparable", "changes": {}}
    changes = {}
    for definition in current.candidates:
        name = definition.name
        observation = current.results.get(name)
        old = previous.results.get(name)
        if observation is None or observation.index in current.sensitive_indices:
            continue
        if old and old.index in previous.sensitive_indices:
            continue
        if old != observation:
            changes[name] = {
                "before_status": old.status if old else "not_scanned",
                "after_status": observation.status,
                "before_raw": old.value if old else None,
                "after_raw": observation.value,
                "before_index": old.index if old else None,
                "after_index": observation.index,
            }
    return {"status": "compared", "changes": changes}


def capture_identity(telemetry):
    """Only the exact known controller family; do not match on changeset alone."""
    profile, controller = find_profile(device_inventory(telemetry))
    if profile is None or profile.address != 0x7112:
        raise VBusError("Unsupported or ambiguous discovery controller")
    return controller
