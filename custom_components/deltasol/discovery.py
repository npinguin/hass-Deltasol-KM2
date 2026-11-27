"""On-demand inventory using the existing VBus owner, never a second writer."""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property

from .catalog import (
    BX_PLUS_CHANGESET,
    configuration_evidence,
    decoded_value,
    explicit_options,
    is_sensitive,
)
from .parameters import device_inventory, find_profile
from .vbus import ERROR_CODES, ParameterObservation, VBusError, VBusTransientError

BATCH_SIZE = 16
CHECKPOINT_INTERVAL = 128
RETRY_DELAYS = (2, 5, 10, 20, 30, 60)


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
    retry_count: int = 0
    consecutive_failures: int = 0
    retry_in_seconds: int | None = None
    last_failure: dict | None = None

    @cached_property
    def sensitive_indices(self):
        return {
            d.index
            for d in self.catalog.definitions
            if d.index is not None and is_sensitive(d)
        }

    @cached_property
    def base_candidates(self):
        """Retain the exact pre-existing candidate order for checkpoint parity."""

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

    @cached_property
    def candidates(self):
        """Append verified read-only compound parents after legacy coverage."""
        safe_children = {
            d.metadata.get("compound_ref")
            for d in self.base_candidates
        }
        parents = tuple(
            d
            for d in self.catalog.definitions
            if d.excluded == "non_value_definition"
            and d.metadata.get("compound_parent_read_candidate") is True
            and d.name in safe_children
            and not is_sensitive(d)
            and d.index not in self.sensitive_indices
        )
        return self.base_candidates + parents

    @property
    def progress_percent(self):
        """Committed coverage, not the percentage of readable values."""
        if self.status == "complete":
            return 100
        total = len(self.candidates)
        return min(99.99, round(100 * self.offset / total, 2)) if total else 0

    def summary(self):
        """Shared cached progress projection, without full metadata serialization."""
        total = len(self.candidates)
        counts = {}
        for result in self.results.values():
            counts[result.status] = counts.get(result.status, 0) + 1
        return {
            "scanned_count": self.offset,
            "candidate_count": total,
            "progress_percent": self.progress_percent,
            "read_count": counts.get("read", 0),
            "not_resolved_count": counts.get("not_resolved", 0),
            "other_unread_count": self.offset
            - counts.get("read", 0)
            - counts.get("not_resolved", 0),
            "remaining_count": total - self.offset,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": self.retry_count,
            "consecutive_failures": self.consecutive_failures,
            "retry_in_seconds": self.retry_in_seconds,
            "last_failure": self.last_failure,
        }

    def identity_digest(self):
        identity = self.controller
        data = (identity.channel, identity.address, identity.source, identity.serial)
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()

    def checkpoint(self):
        """Keep observations only; reconstruct trusted metadata from the catalogue."""
        error = self.error
        if error and error not in ERROR_CODES:
            error = "parameter_error"
        return {
            "identity": self.identity_digest(),
            "catalog_digest": self.catalog.digest,
            "changeset": self.changeset,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "offset": self.offset,
            "retry_count": self.retry_count,
            "consecutive_failures": self.consecutive_failures,
            "error": error,
            "last_failure": self.last_failure,
            "observations": [
                [
                    d.name,
                    self.results[d.name].index,
                    self.results[d.name].value,
                    self.results[d.name].status,
                ]
                for d in self.candidates[: self.offset]
            ],
        }

    @classmethod
    def restore(cls, data, catalog, controller, changeset):
        """Reject stale, corrupt or incompatible checkpoints, never use for writes."""
        capture = cls(catalog, controller, changeset)
        if (
            data["identity"] != capture.identity_digest()
            or data["catalog_digest"] != catalog.digest
            or data["changeset"] != changeset
        ):
            raise ValueError("Discovery checkpoint identity or layout changed")
        offset = data["offset"]
        rows = data["observations"]
        # Previous releases never attempted abstract compound parents.
        # A previously complete legacy prefix is now a paused, extendable
        # capture, not corrupt data or falsely complete expanded coverage.
        legacy_complete = (
            data["status"] == "complete"
            and offset == len(capture.base_candidates)
            and len(capture.candidates) > offset
        )
        if (
            type(offset) is not int
            or not 0 <= offset <= len(capture.candidates)
            or len(rows) != offset
            or data["status"]
            not in ("running", "recovering", "paused", "incomplete", "complete")
            or (
                not legacy_complete
                and (data["status"] == "complete") != (offset == len(capture.candidates))
            )
            or type(data["retry_count"]) is not int
            or data["retry_count"] < 0
        ):
            raise ValueError("Invalid Discovery checkpoint coverage")
        for definition, row in zip(capture.candidates[:offset], rows, strict=True):
            name, index, value, status = row
            if name != definition.name or status not in (
                "read",
                "not_resolved",
                "read_refused",
                "layout_mismatch",
            ):
                raise ValueError("Invalid Discovery checkpoint observation")
            if status == "read":
                if (
                    index != definition.index
                    or type(value) is not int
                    or not -(2**31) <= value < 2**31
                    or index in capture.sensitive_indices
                ):
                    raise ValueError("Invalid Discovery checkpoint value")
            elif value is not None:
                raise ValueError("Unread checkpoint contains value")
            if index is not None and (type(index) is not int or not 0 < index <= 65535):
                raise ValueError("Invalid Discovery checkpoint index")
            if status == "not_resolved" and index is not None:
                raise ValueError("Unresolved checkpoint contains index")
            capture.results[name] = ParameterObservation(index, value, status)
        for key in ("started_at", "finished_at"):
            value = data[key]
            if value is not None and datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("Invalid Discovery checkpoint timestamp")
            setattr(capture, key, value)
        if not capture.started_at or (
            data["status"] == "complete" and not capture.finished_at
        ):
            raise ValueError("Missing Discovery checkpoint timestamp")
        capture.offset = offset
        capture.retry_count = data["retry_count"]
        consecutive = data.get("consecutive_failures", 0)
        error = data.get("error")
        if (
            type(consecutive) is not int
            or not 0 <= consecutive <= capture.retry_count + 1
            or (error is not None and error not in ERROR_CODES)
        ):
            raise ValueError("Invalid Discovery checkpoint error")
        capture.consecutive_failures = consecutive
        capture.error = error
        failure = data.get("last_failure")
        if failure is not None:
            # Never restore arbitrary message/payload data into HA state.
            phase = failure["phase"]
            command = failure["command"]
            index = failure["reference_index"]
            batch_offset = failure["batch_offset"]
            timestamp = failure["observed_at"]
            if (
                phase
                not in (
                    "connect",
                    "handshake",
                    "acquire",
                    "send",
                    "response",
                    "receive",
                    "release",
                    "telemetry",
                    "availability",
                )
                or (
                    command is not None
                    and (
                        not isinstance(command, str)
                        or len(command) != 6
                        or not command.startswith("0x")
                        or not 0 <= int(command, 16) <= 65535
                    )
                )
                or (
                    index is not None
                    and (type(index) is not int or not 0 <= index <= 65535)
                )
                or index in capture.sensitive_indices
                or type(batch_offset) is not int
                or not 0 <= batch_offset <= offset
                or datetime.fromisoformat(timestamp).tzinfo is None
            ):
                raise ValueError("Invalid Discovery checkpoint failure")
            capture.last_failure = {
                "phase": phase,
                "command": command,
                "reference_index": index,
                "batch_offset": batch_offset,
                "observed_at": timestamp,
            }
        capture.status = (
            data["status"]
            if not legacy_complete and data["status"] in ("complete", "incomplete")
            else "paused"
        )
        if legacy_complete:
            capture.finished_at = None
        return capture

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
        definitions = {d.name: d for d in self.catalog.definitions}
        parent_names = {d.name for d in self.candidates[len(self.base_candidates):]}
        for definition in self.catalog.definitions:
            if is_sensitive(definition) or definition.index in self.sensitive_indices:
                counts["sensitive_redacted"] = counts.get("sensitive_redacted", 0) + 1
                continue
            observed = self.results.get(definition.name)
            status = (
                observed.status if observed else "not_scanned"
            ) if definition.name in parent_names else (
                definition.excluded or (observed.status if observed else "not_scanned")
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
            item.update(configuration_evidence(definition, definitions))
            if definition.name in parent_names:
                item["write_access_reason"] = "compound_parent_write_unverified"
                item["read_evidence"] = "exact_proxy_parent"
            parameters[definition.name] = item
            if observed is None:
                continue
            if not current:
                item["last_observation"] = {"status": observed.status}
                if observed.index not in self.sensitive_indices:
                    item["last_observation"]["resolved_index"] = observed.index
                    if observed.status == "read":
                        item["last_observation"]["raw_value"] = observed.value
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
                    "explicit_options": explicit_options(definition),
                    "access": "read_observed",
                }
            )
        counts.setdefault("sensitive_redacted", 0)
        return {
            "status": self.status if current else "stale",
            "capture_status": self.status,
            "export_format": 3,
            "metadata_scope": "read_values_only",
            "unread_access": "unknown",
            "unread_write_access": "not_verified",
            "value_semantics": "observations_during_capture_not_live_poll",
            "catalog_digest": self.catalog.digest,
            "controller_address": f"0x{self.controller.address:04X}",
            "changeset": f"{self.changeset:08X}",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "retry_count": self.retry_count,
            "consecutive_failures": self.consecutive_failures,
            "retry_in_seconds": self.retry_in_seconds,
            "last_failure": self.last_failure,
            "catalog_count": len(self.catalog.definitions),
            "summary": self.summary(),
            "scanned_count": self.offset,
            "coverage": "all_catalogue_candidates_attempted"
            if self.status == "complete"
            else "partial",
            "candidate_count": len(self.candidates),
            "compound_parent_candidate_count": len(parent_names),
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
    if profile is None:
        raise VBusError("Unsupported or ambiguous discovery controller")
    return controller


async def capture_batches(runtime, checkpoint=None):
    """Retry only uncommitted read batches through the existing session owner."""
    capture = runtime.capture
    relay = runtime.relay_coordinator
    try:
        while not runtime.stop_capture:
            batch = capture.next_batch()
            if not batch:
                capture.apply((), {})
                return
            try:
                observations = await relay.async_read_configuration_batch(
                    capture,
                    {definition.name: definition.index for definition in batch},
                )
            except VBusTransientError as error:
                capture.consecutive_failures += 1
                capture.error = str(error)
                capture.last_failure = {
                    **error.evidence(),
                    "batch_offset": capture.offset,
                    "observed_at": datetime.now(UTC).isoformat(),
                }
                if runtime.stop_capture:
                    break
                if capture.consecutive_failures > len(RETRY_DELAYS):
                    capture.status = "incomplete"
                    return
                delay = RETRY_DELAYS[capture.consecutive_failures - 1]
                capture.retry_count += 1
                capture.retry_in_seconds = delay
                capture.status = "recovering"
                runtime.notify_capture()
                try:
                    await asyncio.wait_for(runtime.capture_wakeup.wait(), timeout=delay)
                except TimeoutError:
                    pass
                capture.retry_in_seconds = None
                continue
            capture.apply(batch, observations)
            capture.consecutive_failures = 0
            capture.error = None
            capture.retry_in_seconds = None
            if capture.status != "complete":
                capture.status = "running"
            runtime.notify_capture()
            if capture.status == "complete":
                return
            if checkpoint and capture.offset % CHECKPOINT_INTERVAL == 0:
                await checkpoint()
            await asyncio.sleep(0)
        capture.status = "paused"
    except VBusError as error:
        # Authentication rejection, identity/layout changes and explicit hard
        # failures require intervention. Do not advance or disguise as absence.
        capture.status = "incomplete"
        capture.error = str(error)
    except asyncio.CancelledError:
        if capture.status in ("running", "recovering"):
            # HA can cancel background tasks before config-entry unload.
            # Only committed batches survive; preserve automatic resume intent.
            runtime.resume_on_load = runtime.resume_on_load or not runtime.stop_capture
            capture.status = "paused"
            capture.error = "capture_cancelled"
        raise
    finally:
        capture.retry_in_seconds = None
        if checkpoint:
            await checkpoint()
        runtime.notify_capture()
