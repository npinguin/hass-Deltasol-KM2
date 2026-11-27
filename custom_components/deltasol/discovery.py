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
    function_selector,
    is_sensitive,
    ordinal_options,
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
    base_finished_at: str | None = None
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
        safe_children = {d.metadata.get("compound_ref") for d in self.base_candidates}
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
            "base_finished_at": self.base_finished_at,
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

    def storage_checkpoint(self):
        """Keep the legacy prefix readable by 1.2.15 after rollback."""
        full = self.checkpoint()
        base_count = len(self.base_candidates)
        if (
            len(self.candidates) == base_count
            or self.offset < base_count
            or not self.base_finished_at
        ):
            return full
        extension = {**full, "observations": full["observations"][base_count:]}
        return {
            **full,
            "status": "complete",
            "finished_at": self.base_finished_at,
            "offset": base_count,
            "observations": full["observations"][:base_count],
            "error": None,
            "consecutive_failures": 0,
            "last_failure": None,
            "extension": extension,
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
        if "extension" in data:
            extension = data["extension"]
            base_count = len(capture.base_candidates)
            if (
                not isinstance(extension, dict)
                or data["status"] != "complete"
                or data["offset"] != base_count
                or len(data["observations"]) != base_count
                or any(
                    extension.get(key) != data[key]
                    for key in ("identity", "catalog_digest", "changeset", "started_at")
                )
                or extension.get("base_finished_at") != data["finished_at"]
            ):
                raise ValueError("Invalid Discovery checkpoint extension")
            data = {
                **extension,
                "observations": data["observations"] + extension["observations"],
            }
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
                and (data["status"] == "complete")
                != (offset == len(capture.candidates))
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
        base_finished_at = data.get("base_finished_at")
        if legacy_complete:
            base_finished_at = capture.finished_at
        if base_finished_at is not None and (
            offset < len(capture.base_candidates)
            or datetime.fromisoformat(base_finished_at).tzinfo is None
        ):
            raise ValueError("Invalid Discovery prefix timestamp")
        capture.base_finished_at = base_finished_at
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
        if self.offset >= len(self.base_candidates) and not self.base_finished_at:
            self.base_finished_at = datetime.now(UTC).isoformat()
        if self.offset == len(self.candidates):
            self.status = "complete"
            self.finished_at = datetime.now(UTC).isoformat()

    def _observed_raw(self, definition):
        """Only exact, nonsensitive observations may feed derived views."""
        if definition is None:
            return None
        observed = self.results.get(definition.name)
        if (
            is_sensitive(definition)
            or definition.index in self.sensitive_indices
            or observed is None
            or observed.status != "read"
            or observed.index != definition.index
            or type(observed.value) is not int
        ):
            return None
        return observed.value

    def compound_parameters(self):
        """Consolidate temperature variants without guessing the write target."""
        definitions = {d.name: d for d in self.catalog.definitions}
        groups = {}
        for definition in self.base_candidates:
            parent = definition.metadata.get("compound_ref")
            if parent:
                groups.setdefault(parent, []).append(definition)
        selector = definitions.get("TemperatureHysteresisSelector")
        selected = self._observed_raw(selector)
        result = {}
        for name, children in groups.items():
            parent = definitions.get(name)
            if (
                parent is None
                or is_sensitive(parent)
                or parent.index in self.sensitive_indices
                or parent.metadata.get("compound_parent_read_candidate") is not True
            ):
                continue
            variants = {}
            for child in children:
                chain = child.metadata.get("type_chain", ())
                unit = next(
                    (
                        symbol
                        for type_id, symbol in (
                            ("TemperatureCelsius", "°C"),
                            ("TemperatureFahrenheit", "°F"),
                            ("HysteresisKelvin", "K"),
                            ("HysteresisRankine", "°R"),
                        )
                        if type_id in chain
                    ),
                    None,
                )
                if unit is None:
                    continue
                variants.setdefault(unit, []).append(child)
            if set(variants) not in ({"°C", "°F"}, {"K", "°R"}) or any(
                len(items) != 1 for items in variants.values()
            ):
                continue
            units = ("K", "°R") if "K" in variants else ("°C", "°F")
            active = variants[units[selected]][0] if selected in (0, 1) else None
            raw = self._observed_raw(active)
            parent_raw = self._observed_raw(parent)
            value = decoded_value(active, raw) if active else None
            evidence = (
                "selector_unknown"
                if active is None
                else "child_not_decoded"
                if value is None
                else "parent_not_observed"
                if parent_raw is None
                else "parent_matches_selected_child"
                if parent_raw == raw
                else "parent_encoding_unverified"
            )
            result[name] = {
                "selected_parameter": active.name if active else None,
                "unit": units[selected] if selected in (0, 1) else None,
                "value": value,
                "parent_reference_index": parent.index,
                "parent_raw_value": parent_raw,
                "selected_raw_value": raw,
                "selector_raw": selected,
                "evidence": evidence,
                "write_access": "not_verified",
            }
        return result

    @cached_property
    def function_slots(self):
        """Index catalogue-defined slots and their members once per capture."""
        slots = {}
        for definition in self.catalog.definitions:
            selector = function_selector(definition)
            if selector:
                name, slot, family = selector
                slots[name] = (slot, family, definition)
        members = {name: [] for name in slots}
        for definition in self.catalog.definitions:
            # A slot owns its prefix only, not slot 10 through slot 19.
            parts = definition.name.split("_")
            for end in range(1, len(parts)):
                name = "_".join(parts[:end])
                if name in slots:
                    members[name].append(definition)
                    break
        return slots, members

    def function_context(self):
        """One catalogue-derived projection used by inventory and parameter rows.

        Menu membership and captured selectors establish context, not live
        writability. A mask or extraRef is retained as an unresolved condition
        until its vendor semantics are independently established.
        """
        definitions = {d.name: d for d in self.catalog.definitions}
        slots, members = self.function_slots
        functions = {}
        contexts = {}
        for name, (slot, family, selector) in slots.items():
            raw = self._observed_raw(selector)
            options = ordinal_options(selector)
            kind = options.get(raw)
            activity = self._observed_raw(definitions.get(f"{name}_Aktiviert_Modus"))
            status = (
                "unknown"
                if kind is None
                else "unused"
                if kind == "Frei"
                else "configured"
            )
            item = {
                "family": family,
                "slot": slot,
                "type_parameter": selector.name,
                "type_raw": raw,
                "type": kind,
                "status": status,
                "activity_raw": activity,
                "activity_mode": {
                    0: "disabled",
                    1: "enabled",
                    2: "switch_controlled",
                }.get(activity),
                "assignments": {},
            }
            typed_menus = {
                f"{name}_{option}" for option in options.values() if option != "Frei"
            }
            for definition in members[name]:
                if (
                    is_sensitive(definition)
                    or definition.index in self.sensitive_indices
                ):
                    continue
                parent = definitions.get(definition.metadata.get("compound_ref"))
                refs = definition.metadata.get("menu_refs", ())
                if not refs and parent and not is_sensitive(parent):
                    refs = parent.metadata.get("menu_refs", ())
                selected = []
                other = []
                common = []
                for ref in refs:
                    menu = ref.get("menu")
                    if menu in typed_menus:
                        (selected if menu == f"{name}_{kind}" else other).append(ref)
                    elif menu and (menu == name or menu.startswith(name + "_")):
                        common.append(ref)
                if definition.name == selector.name:
                    applicability = "function_selector"
                elif status == "unknown":
                    applicability = "function_unknown"
                elif status == "unused":
                    applicability = "function_unused"
                elif activity == 0:
                    applicability = "function_disabled"
                elif selected:
                    applicability = (
                        "conditional_unverified"
                        if any(
                            ref.get("mask") or ref.get("extraRef") for ref in selected
                        )
                        else "selected_function_reference"
                    )
                elif other and not common:
                    applicability = "other_function_type"
                elif common:
                    applicability = "slot_menu_unverified"
                else:
                    applicability = "not_assessed"
                contexts[definition.name] = {
                    "function_slot": name,
                    "function_type": kind,
                    "applicability": applicability,
                }
                chain = definition.metadata.get("type_chain", ())
                reference_types = {
                    "AktorNr",
                    "RelaisNr",
                    "AusgangNr",
                    "SensorNr",
                    "SensorSchalterNr",
                    "SensorSchalterExtNr",
                    "SensorVolumenstromNr",
                    "SensorVolumenstromImpulsNr",
                    "RelaisOderAusgangAlleNr",
                }
                if (
                    status != "configured"
                    or applicability
                    not in (
                        "selected_function_reference",
                        "conditional_unverified",
                        "slot_menu_unverified",
                    )
                    or not reference_types.intersection(chain)
                ):
                    continue
                value = self._observed_raw(definition)
                if value is None or value == 0:
                    continue
                item["assignments"][definition.name] = {
                    "reference": value,
                    "reference_type": chain[-1],
                    "applicability": applicability,
                }
            functions[name] = item
        return functions, contexts

    def native_functions(self):
        functions, _ = self.function_context()
        return {
            "value_semantics": "observations_during_capture_not_live_poll",
            "functions": functions,
        }

    def export(self, current):
        parameters = {}
        counts = {}
        definitions = {d.name: d for d in self.catalog.definitions}
        parent_names = {d.name for d in self.candidates[len(self.base_candidates) :]}
        functions, contexts = self.function_context()
        for definition in self.catalog.definitions:
            if is_sensitive(definition) or definition.index in self.sensitive_indices:
                counts["sensitive_redacted"] = counts.get("sensitive_redacted", 0) + 1
                continue
            observed = self.results.get(definition.name)
            status = (
                (observed.status if observed else "not_scanned")
                if definition.name in parent_names
                else (
                    definition.excluded
                    or (observed.status if observed else "not_scanned")
                )
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
            if context := contexts.get(definition.name):
                item.update(context)
                if not current:
                    item["applicability"] = "stale"
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
                    "ordinal_options": ordinal_options(definition),
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
            "native_function_inventory": {
                "functions": functions,
                "value_semantics": "observations_during_capture_not_live_poll",
                "status": "capture_observation" if current else "stale",
            },
            "compound_parameters": {
                "status": "capture_observation" if current else "stale",
                "value_semantics": "observations_during_capture_not_live_poll",
                "parameters": self.compound_parameters(),
            },
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
