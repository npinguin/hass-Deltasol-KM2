"""Native on-demand engineering action; no configuration writes."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from . import async_toggle_configuration_capture


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.coordinator.api.supports_parameter_access:
        async_add_entities([DiscoveryButton(hass, entry)])


class DiscoveryButton(ButtonEntity):
    """Start, pause or resume the configuration capture."""

    _attr_name = "Discover configuration (start/pause/resume)"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_configuration_discovery"

    async def async_press(self):
        await async_toggle_configuration_capture(self.hass, self.entry)
