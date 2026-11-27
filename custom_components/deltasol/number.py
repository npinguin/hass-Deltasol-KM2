"""Native numeric projection for verified configuration settings."""

from homeassistant.components.number import NumberEntity, NumberMode

from .configuration_entity import ConfigurationEntity, register_configuration_entities


async def async_setup_entry(hass, entry, async_add_entities):
    register_configuration_entities(
        entry, async_add_entities, "number", ConfigurationNumber
    )


class ConfigurationNumber(ConfigurationEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, capability):
        super().__init__(coordinator, capability)
        # Compound controls keep one entity ID when the native display unit
        # changes. Use the currently verified capability's bounds and unit.
        # While unavailable, retain the last registered metadata only.

    @property
    def _native_setting(self):
        capability = self.capability
        return capability.setting if capability else self.setting

    @property
    def native_min_value(self):
        return float(self._native_setting.limits[0])

    @property
    def native_max_value(self):
        return float(self._native_setting.limits[1])

    @property
    def native_step(self):
        return float(self._native_setting.limits[2])

    @property
    def native_unit_of_measurement(self):
        return self._native_setting.unit

    @property
    def native_value(self):
        capability = self.capability
        return capability.value if capability else None

    async def async_set_native_value(self, value):
        await self.async_set_value(value)
