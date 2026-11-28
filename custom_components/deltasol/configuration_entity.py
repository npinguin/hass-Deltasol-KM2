"""Native entity projection of the existing parameter coordinator."""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .parameters import device_inventory, find_device


def register_configuration_entities(entry, async_add_entities, kind, entity_type):
    coordinator = entry.runtime_data.relay_coordinator
    if coordinator is None:
        return
    registered = set()

    @callback
    def add_discovered():
        if not coordinator.last_update_success or not coordinator.data:
            return
        new = [
            entity_type(coordinator, capability)
            for uid, capability in coordinator.data.configuration.items()
            if uid not in registered and capability.setting.kind == kind
        ]
        registered.update(entity.unique_id for entity in new)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(add_discovered))
    add_discovered()


def configuration_device_info(capability):
    """Preserve existing hardware identities; group new native function controls.

    HQM uses the verified, observed vendor telemetry device, not a synthetic
    meter. Configuration transactions still target its owning controller.
    """
    physical = (DOMAIN, f"{capability.device.serial}_{capability.device.source}")
    slot = capability.setting.native_slot
    if slot is None or slot[1] == "Wmz":
        return DeviceInfo(identifiers={physical})
    slot_id, family, number = slot
    controller = capability.controller
    label = {
        "Anlage_Wf": "Arrangement function",
        "Solar_Wf": "Solar optional function",
        "Solar_Sp": "Solar store",
        "Solar_Kol": "Solar collector",
        "Solar_Bypass": "Solar bypass",
        "Solar_CsBypass": "Solar CS bypass",
        "Solar_Zieltemperatur": "Solar target temperature",
        "Solar_Roehrenkollektor": "Solar tube collector",
        "Solar_ExtWT": "Solar external heat exchanger",
        "Heizung_Wf": "Heating optional function",
        "Heizung_Heizkreis": "Heating circuit",
    }[family]
    return DeviceInfo(
        identifiers={
            (
                DOMAIN,
                (
                    f"{controller.serial}_{controller.channel:02X}_"
                    f"{controller.address:04X}_function_{slot_id}"
                ),
            )
        },
        name=f"RESOL {label} {number}",
        manufacturer="RESOL",
        model="Native controller function",
        via_device=(DOMAIN, f"{controller.serial}_{controller.source}"),
    )


class ConfigurationEntity(CoordinatorEntity):
    """One native configuration projection of a positively discovered setting."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, capability):
        super().__init__(coordinator)
        self.setting = capability.setting
        self._attr_unique_id = capability.unique_id
        self._attr_name = self.setting.label
        self._attr_device_info = configuration_device_info(capability)

    @property
    def capability(self):
        if (
            not self.coordinator.last_update_success
            or not self.coordinator.telemetry.last_update_success
            or not self.coordinator.data
        ):
            return None
        capability = self.coordinator.data.configuration.get(self.unique_id)
        if capability is None:
            return None
        inventory = device_inventory(self.coordinator.telemetry.data)
        if (
            find_device(inventory, capability.controller.address)
            != capability.controller
            or find_device(inventory, capability.device.address) != capability.device
        ):
            return None
        return capability

    @property
    def available(self):
        return self.capability is not None

    @property
    def extra_state_attributes(self):
        capability = self.capability
        attributes = {
            "parameter": self.setting.name,
            "native_function_slot": (
                self.setting.native_slot[0] if self.setting.native_slot else None
            ),
            "storage_action": "save" if self.setting.save else "volatile",
            "changeset": f"{capability.changeset:08X}" if capability else None,
        }
        if self.setting.weekly_slot is not None:
            attributes.update(
                weekly_program=self.setting.weekly_slot,
                observed_references=list(capability.owners) if capability else [],
                active_references=list(capability.active_owners) if capability else [],
                reference_coverage="known_live_links_full_check_on_edit",
            )
        return attributes

    async def async_set_value(self, value):
        await self.coordinator.async_set_configuration(self.unique_id, value)
