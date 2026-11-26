"""Native numeric projection for verified configuration settings."""
from homeassistant.components.number import NumberEntity, NumberMode

from .configuration_entity import ConfigurationEntity, register_configuration_entities


async def async_setup_entry(hass, entry, async_add_entities):
    register_configuration_entities(entry, async_add_entities, "number", ConfigurationNumber)


class ConfigurationNumber(ConfigurationEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, capability):
        super().__init__(coordinator, capability)
        minimum, maximum, step, _ = self.setting.limits
        self._attr_native_min_value = float(minimum)
        self._attr_native_max_value = float(maximum)
        self._attr_native_step = float(step)
        self._attr_native_unit_of_measurement = self.setting.unit

    @property
    def native_value(self):
        capability = self.capability
        return capability.value if capability else None

    async def async_set_native_value(self, value):
        await self.async_set_value(value)
