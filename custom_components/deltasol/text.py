"""Native daily schedule projection of verified controller parameters."""

from homeassistant.components.text import TextEntity, TextMode

from .configuration_entity import ConfigurationEntity, register_configuration_entities


async def async_setup_entry(hass, entry, async_add_entities):
    register_configuration_entities(entry, async_add_entities, "text", ConfigurationText)


class ConfigurationText(ConfigurationEntity, TextEntity):
    """Edit up to six native quarter-hour windows without a new writer."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 76

    @property
    def native_value(self):
        capability = self.capability
        return capability.value if capability else None

    async def async_set_value(self, value):
        await self.coordinator.async_set_configuration(self.unique_id, value)
