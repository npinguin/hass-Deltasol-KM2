"""On-demand inventory using the existing VBus owner, never a second writer."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .catalog import BX_PLUS_CHANGESET, decoded_value
from .parameters import device_inventory, find_profile
from .vbus import VBusError

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

    @property
    def candidates(self):
        return tuple(d for d in self.catalog.definitions if d.excluded is None)

    def next_batch(self):
        return self.candidates[self.offset : self.offset + BATCH_SIZE]

    def apply(self, batch, observations):
        for definition in batch:
            result = observations[definition.name]
            self.results[definition.name] = result
        self.offset += len(batch)
        if self.offset == len(self.candidates):
            self.status = "complete"
            self.finished_at = datetime.now(UTC).isoformat()

    def export(self, current):
        parameters = {}
        for definition in self.catalog.definitions:
            if definition.excluded == "sensitive":
                continue
            observed = self.results.get(definition.name)
            status = definition.excluded or (
                observed.status if observed else "not_scanned"
            )
            if observed and not current:
                status = "stale"
            raw = observed.value if observed and current else None
            parameters[definition.name] = {
                "status": status,
                "reference_index": definition.index,
                "resolved_index": observed.index if observed and current else None,
                "raw_value": raw,
                "decoded_value": decoded_value(definition, raw),
                "type": definition.type_id,
                "storage": definition.storage,
                "metadata": definition.metadata,
                "access": "read_observed"
                if observed and current and observed.status == "read"
                else "unknown",
                "write_access": "not_assessed",
                "applicability": "not_assessed",
            }
        counts = {}
        for item in parameters.values():
            status = item["status"]
            counts[status] = counts.get(status, 0) + 1
        counts["sensitive_redacted"] = sum(
            d.excluded == "sensitive" for d in self.catalog.definitions
        )
        return {
            "status": self.status if current else "stale",
            "catalog_digest": self.catalog.digest,
            "controller_address": f"0x{self.controller.address:04X}",
            "changeset": f"{self.changeset:08X}",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "catalog_count": len(self.catalog.definitions),
            "scanned_count": self.offset,
            "candidate_count": len(self.candidates),
            "counts": counts,
            "parameters": parameters,
        }


def compare_captures(previous, current):
    """Compare comparable successful reads, not missing/stale as changed values."""
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
    for name, observation in current.results.items():
        old = previous.results.get(name)
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
