"""Native boolean projection for verified configuration settings."""
from homeassistant.components.switch import SwitchEntity

from .configuration_entity import ConfigurationEntity, register_configuration_entities


async def async_setup_entry(hass, entry, async_add_entities):
    register_configuration_entities(entry, async_add_entities, "switch", ConfigurationSwitch)


class ConfigurationSwitch(ConfigurationEntity, SwitchEntity):
    @property
    def is_on(self):
        capability = self.capability
        return capability.value if capability else None

    async def async_turn_on(self, **kwargs):
        await self.async_set_value(True)

    async def async_turn_off(self, **kwargs):
        await self.async_set_value(False)
