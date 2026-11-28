"""Native controls for positively discovered RESOL relay capabilities."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .configuration_entity import ConfigurationEntity, register_configuration_entities
from .const import DOMAIN
from .parameters import OBSERVED_MODES, device_inventory


def relay_name(hass, telemetry, capability):
    """Reuse an unambiguous existing output sensor name, including user overrides."""
    keys = device_inventory(telemetry).get(capability.device, ())
    names = {f"relay_{capability.number}", f"relais_{capability.number}"}
    matches = [
        key
        for key in keys
        if telemetry[key].name.lower() in names and telemetry[key].unit == "%"
    ]
    if len(matches) == 1:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, matches[0])
        entity = registry.async_get(entity_id) if entity_id else None
        if entity and entity.name:
            return f"{entity.name} (R{capability.number}) mode"
    return f"Relay R{capability.number} mode"


async def async_setup_entry(hass, entry, async_add_entities):
    """Add controls only after discovery; recover on later polls."""
    telemetry = entry.runtime_data.coordinator
    coordinator = entry.runtime_data.relay_coordinator
    if coordinator is None:
        return
    register_configuration_entities(
        entry, async_add_entities, "select", ConfigurationSelect
    )
    registered = set()

    @callback
    def discover_entities():
        if not coordinator.last_update_success or not coordinator.data:
            return
        new = [
            DeltasolRelaySelect(
                coordinator, capability, relay_name(hass, telemetry.data, capability)
            )
            for key, capability in coordinator.data.capabilities.items()
            if key not in registered
        ]
        registered.update(entity.unique_id for entity in new)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(discover_entities))
    discover_entities()


class DeltasolRelaySelect(CoordinatorEntity, SelectEntity):
    """Expose the actual manual mode, not the instantaneous relay output."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, capability, name):
        super().__init__(coordinator)
        self._attr_unique_id = capability.unique_id
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{capability.device.serial}_{capability.device.source}")
            },
        )

    @property
    def capability(self):
        if (
            not self.coordinator.last_update_success
            or not self.coordinator.telemetry.last_update_success
            or not self.coordinator.data
        ):
            return None
        capability = self.coordinator.data.capabilities.get(self.unique_id)
        inventory = self.coordinator.telemetry.observed_inventory
        return capability if capability and capability.matches(inventory) else None

    @property
    def available(self):
        return self.capability is not None

    @property
    def options(self):
        cap = self.capability
        return list(cap.options) if cap else []

    @property
    def current_option(self):
        cap = self.capability
        return cap.current_option if cap else None

    @property
    def extra_state_attributes(self):
        cap = self.capability
        if cap is None:
            return {}
        return {
            "observed_mode": OBSERVED_MODES[cap.value],
            "observed_mode_value": cap.value,
            "speed_control": cap.speed_control,
            "registration_source": cap.registration_source,
            "changeset": f"{cap.changeset & 0xFFFFFFFF:08X}",
            "parameter": cap.name,
        }

    async def async_select_option(self, option):
        await self.coordinator.async_set_mode(self.unique_id, option)


class ConfigurationSelect(ConfigurationEntity, SelectEntity):
    """Native typed configuration selection, independent of relay mode."""

    @property
    def options(self):
        return [label for label, _ in self.setting.options]

    @property
    def current_option(self):
        capability = self.capability
        return capability.value if capability else None

    async def async_select_option(self, option):
        await self.async_set_value(option)
