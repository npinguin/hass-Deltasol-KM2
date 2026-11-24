"""Native controls for positively discovered RESOL relay capabilities."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DeltasolRelayCoordinator
from .const import DOMAIN
from .parameters import MODE_VALUES, OBSERVED_MODES, controller_identity


async def async_setup_entry(hass, entry, async_add_entities):
    """Add controls only after successful discovery; recover on later polls."""
    telemetry = entry.runtime_data.coordinator
    if not telemetry.api.supports_parameter_access:
        return
    coordinator = DeltasolRelayCoordinator(hass, telemetry)
    registered = set()

    @callback
    def discover_entities():
        if not coordinator.last_update_success or not coordinator.data:
            return
        new = [
            DeltasolRelaySelect(coordinator, capability)
            for key, capability in coordinator.data.items()
            if key not in registered
        ]
        registered.update(entity.unique_id for entity in new)
        if new:
            async_add_entities(new)

    # Keep discovery running even if the first attempt cannot find controls.
    entry.async_on_unload(coordinator.async_add_listener(discover_entities))
    entry.async_on_unload(
        telemetry.async_add_listener(coordinator.async_update_listeners)
    )
    await coordinator.async_refresh()


class DeltasolRelaySelect(CoordinatorEntity, SelectEntity):
    """Expose the actual manual mode, not the instantaneous relay output."""

    _attr_has_entity_name = True
    _attr_options = list(MODE_VALUES)

    def __init__(self, coordinator, capability):
        super().__init__(coordinator)
        self._attr_unique_id = capability.unique_id
        self._attr_name = f"Relay R{capability.number} mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{capability.serial}_{capability.source}")},
        )

    @property
    def available(self):
        """Keep identity/history but deny writes when capability is stale."""
        if (
            not self.coordinator.last_update_success
            or not self.coordinator.telemetry.last_update_success
            or not self.coordinator.data
        ):
            return False
        capability = self.coordinator.data.get(self.unique_id)
        return capability is not None and controller_identity(
            self.coordinator.telemetry.data
        ) == (capability.source, capability.serial)

    @property
    def current_option(self):
        if not self.available:
            return None
        return self.coordinator.data[self.unique_id].current_option

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {}
        capability = self.coordinator.data[self.unique_id]
        return {
            "observed_mode": OBSERVED_MODES[capability.value],
            "changeset": f"{capability.changeset & 0xFFFFFFFF:08X}",
            "parameter": capability.name,
        }

    async def async_select_option(self, option):
        await self.coordinator.async_set_mode(self.unique_id, option)
