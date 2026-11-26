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


class ConfigurationEntity(CoordinatorEntity):
    """One existing-device projection of a positively discovered setting."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, capability):
        super().__init__(coordinator)
        self.setting = capability.setting
        self._attr_unique_id = capability.unique_id
        self._attr_name = self.setting.label
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{capability.device.serial}_{capability.device.source}")
            }
        )

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
        return {
            "parameter": self.setting.name,
            "storage_action": "save" if self.setting.save else "volatile",
            "changeset": f"{capability.changeset:08X}" if capability else None,
        }

    async def async_set_value(self, value):
        await self.coordinator.async_set_configuration(self.unique_id, value)
