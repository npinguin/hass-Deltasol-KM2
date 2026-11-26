"""Sensor entities."""

import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import DeltasolConfigEntry, DeltasolCoordinator
from .const import DOMAIN
from .deltasolapi import DeltasolEndpoint
from .diagnostics import ERROR_CODES
from .parameters import device_inventory, find_profile

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: DeltasolConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
):
    """Platform setup for Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB sensors."""
    # This function can be removed in a future version when migration to config flow completed.
    if (
        not hass.config_entries.async_entries(DOMAIN)
        and config.get("platform") == DOMAIN
    ):
        # No config entry exists and configuration.yaml config exists, trigger the import flow.
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=config,
            )
        )

    if config.get("platform") == DOMAIN:
        _LOGGER.error(
            "The %s integration has been migrated to use a config flow setup.  Please delete the entry in configuration.yaml",
            DOMAIN,
        )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config: DeltasolConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    coordinator = config.runtime_data.coordinator
    registered = set()

    @callback
    def discover_entities():
        if not coordinator.last_update_success or not coordinator.data:
            return
        new = [
            DeltasolSensor(coordinator, unique_id, endpoint)
            for unique_id, endpoint in coordinator.data.items()
            if unique_id not in registered
        ]
        registered.update(entity.unique_id for entity in new)
        if new:
            async_add_entities(new)

    config.async_on_unload(coordinator.async_add_listener(discover_entities))
    discover_entities()
    if coordinator.api.supports_parameter_access:
        _, controller = find_profile(device_inventory(coordinator.data))
        if controller:
            async_add_entities(
                [
                    DiscoveryStatusSensor(config, controller),
                    DiscoveryProgressSensor(config, controller),
                ]
            )


class DiscoverySensor(SensorEntity):
    """Push-only projection of the existing capture; no extra bus traffic."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry, controller, suffix):
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_configuration_discovery_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{controller.serial}_{controller.source}")}
        )

    async def async_added_to_hass(self):
        self.runtime.capture_listeners.add(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.runtime.capture_listeners.discard(self.async_write_ha_state)
        )


class DiscoveryStatusSensor(DiscoverySensor):
    """Capture lifecycle status with existing diagnostic attributes."""

    _attr_name = "Configuration discovery status"

    def __init__(self, entry, controller):
        super().__init__(entry, controller, "status")

    @property
    def native_value(self):
        capture = self.runtime.capture
        if not capture:
            return "not_started"
        if self.runtime.stop_capture and capture.status in ("running", "recovering"):
            return "pausing"
        return capture.status

    @property
    def extra_state_attributes(self):
        capture = self.runtime.capture
        if not capture:
            return {}
        return {
            **capture.summary(),
            "last_error": ERROR_CODES.get(capture.error, "parameter_error")
            if capture.error
            else None,
        }


class DiscoveryProgressSensor(DiscoverySensor):
    """Numeric progress for native dashboards and completion automations."""

    _attr_name = "Configuration discovery progress"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:progress-check"

    def __init__(self, entry, controller):
        super().__init__(entry, controller, "progress")

    @property
    def native_value(self):
        capture = self.runtime.capture
        return capture.progress_percent if capture else 0


class DeltasolSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Resol sensor."""

    icon_mapper: ClassVar[defaultdict[str, str]] = defaultdict(
        lambda: "mdi:alert-circle",
        {
            "°C": "mdi:thermometer",
            "%": "mdi:flash",
            "l/h": "mdi:hydro-power",
            "bar": "mdi:car-brake-low-pressure",
            "%RH": "mdi:water-percent",
            "s": "mdi:timer",
            "Wh": "mdi:solar-power-variant-outline",
            "W": "mdi:flash",
        },
    )

    def __init__(
        self,
        coordinator: DeltasolCoordinator,
        unique_id: str,
        endpoint: DeltasolEndpoint,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        # Set correct type for coordinator
        self.coordinator: DeltasolCoordinator = coordinator

        self._attr_unique_id = unique_id
        self._endpoint = endpoint

        self._last_updated = dt_util.now()
        self._attr_unique_id = unique_id
        self._attr_name = endpoint.name
        self._attr_icon = DeltasolSensor.icon_mapper[endpoint.unit]

        self._unit = endpoint.unit
        self._state = None

        # Set entity category to diagnostic and disabled for sensors with no unit
        if not endpoint.unit:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False

    @property
    def available(self):
        """Return whether the last successful poll contains this sensor."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.unique_id in self.coordinator.data
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.available:
            self._last_updated = dt_util.now()
        _LOGGER.debug("Updating %s", self.name)
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the latest value, or None when the source field is missing."""
        if not self.available:
            return None

        value = self.coordinator.data[self.unique_id].value
        # ggf. hier noch die anderen sprachen?
        if self._attr_name in ("system_time", "heure"):
            hours, minutes = divmod(value, 60)
            value = f"{hours:02d}:{minutes:02d}"
        return None if value == "" else value

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement of this entity, if any."""
        if self._endpoint.unit == UnitOfVolumeFlowRate.LITERS_PER_HOUR.lower():
            return UnitOfVolumeFlowRate.LITERS_PER_HOUR
        else:
            return None if self._endpoint.unit == "" else self._endpoint.unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the device class of this entity, if any."""
        if self._unit == UnitOfTemperature.CELSIUS:
            return SensorDeviceClass.TEMPERATURE
        if self._unit == PERCENTAGE:
            return SensorDeviceClass.POWER_FACTOR
        if self._unit == UnitOfEnergy.WATT_HOUR:
            return SensorDeviceClass.ENERGY
        if self._unit == UnitOfPower.WATT:
            return SensorDeviceClass.POWER
        if self._unit == UnitOfVolumeFlowRate.LITERS_PER_HOUR.lower():
            return SensorDeviceClass.VOLUME_FLOW_RATE
        # Use the current value when the field is present. Fall back to the creation-time value when it disappears.
        current = (
            self.coordinator.data.get(self.unique_id) if self.coordinator.data else None
        )
        value = current.value if current is not None else self._endpoint.value
        if isinstance(value, date):
            return SensorDeviceClass.DATE
        return None

    @property
    def state_class(self) -> SensorStateClass | str | None:
        """Return the state class of this entity, if any."""
        if self._unit in (
            UnitOfTemperature.CELSIUS,
            UnitOfPower.WATT,
            UnitOfVolumeFlowRate.LITERS_PER_HOUR.lower(),
        ):
            return SensorStateClass.MEASUREMENT
        if self._unit == UnitOfEnergy.WATT_HOUR:
            return SensorStateClass.TOTAL_INCREASING
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device specific attributes."""
        # Device unique identifier is the serial + bus_src
        product_details = self._endpoint.product_details
        return DeviceInfo(
            identifiers={
                (DOMAIN, product_details["serial"] + "_" + self._endpoint.bus_src)
            },
            name=self._endpoint.bus_src,
            manufacturer=product_details["vendor"],
            model=product_details["name"],
            sw_version=product_details["version"],
            serial_number=product_details["serial"],
            hw_version=product_details["build"],
            model_id=product_details["features"],
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes of this device."""
        attr = {}
        if self._last_updated is not None:
            attr["Last Updated"] = self._last_updated
        return attr
