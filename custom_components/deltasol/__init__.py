"""The Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB component.

Gets sensor data from Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB using api
Author: dm82m
https://github.com/dm82m/hass-Deltasol-KM2"""

import asyncio
import logging
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import timedelta

import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA_BASE
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    IntegrationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .catalog import CatalogError, load_catalog
from .const import (
    CONF_VBUS_PASSWORD,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    MIN_SCAN_INTERVAL,
)
from .deltasolapi import DeltasolApi, DeltasolEndpoint
from .discovery import ConfigurationCapture, capture_identity
from .vbus import VBusError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON]

PLATFORM_SCHEMA = PLATFORM_SCHEMA_BASE.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
        ),
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT): cv.port,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_API_KEY): cv.string,
    }
)

type DeltasolConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class RuntimeData:
    """Class to hold your data."""

    coordinator: DataUpdateCoordinator
    relay_coordinator: "DeltasolRelayCoordinator | None" = None
    capture: ConfigurationCapture | None = None
    previous_capture: ConfigurationCapture | None = None
    capture_task: asyncio.Task | None = None
    capture_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)
    stop_capture: bool = False


async def async_setup(hass: HomeAssistant, config):
    """Set up yaml entry."""

    # This function can be removed in a future version when migration to config flow completed.
    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry: DeltasolConfigEntry
) -> bool:
    """Set up Example Integration from a config entry."""

    # Initialise the coordinator that manages data updates from your api.
    # This is defined in below
    coordinator = DeltasolCoordinator(hass, config_entry)

    # Perform an initial data load from api.
    # async_config_entry_first_refresh() is special in that it does not log errors if it fails
    await coordinator.async_config_entry_first_refresh()

    # Test to see if api initialised correctly, else raise ConfigNotReady to make HA retry setup
    if not coordinator.api.product_details:
        raise ConfigEntryNotReady

    # Add the coordinator and update listener to config runtime data to make
    # accessible throughout your integration
    config_entry.runtime_data = RuntimeData(coordinator)

    config_entry.async_on_unload(config_entry.add_update_listener(_async_reload_entry))

    # Setup platforms (based on the list of entity types in PLATFORMS defined above)
    # This calls the async_setup method in each of your entity type files.
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # Return true to denote a successful setup.
    return True


async def _async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: DeltasolConfigEntry
) -> bool:
    """Unload a config entry."""
    # This is called when you remove your integration or shutdown HA.
    # If you have created any custom services, they need to be removed here too.

    # Unload platforms and return result
    runtime = config_entry.runtime_data
    async with runtime.capture_lock:
        runtime.stop_capture = True
        if runtime.capture_task and not runtime.capture_task.done():
            await runtime.capture_task
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_toggle_configuration_capture(hass, entry):
    """Start/resume or request a stop at the next batch boundary."""
    runtime = entry.runtime_data
    async with runtime.capture_lock:
        await _async_toggle_capture_locked(hass, entry, runtime)


async def _async_toggle_capture_locked(hass, entry, runtime):
    if runtime.capture_task and not runtime.capture_task.done():
        runtime.stop_capture = True
        return
    telemetry = runtime.coordinator
    if not telemetry.last_update_success or not telemetry.api.supports_parameter_access:
        raise HomeAssistantError("Controller parameter access is unavailable")
    try:
        controller = capture_identity(telemetry.data)
        catalog = await hass.async_add_executor_job(
            load_catalog, hass.config.path("deltasol", "BXPLUS_2.08.zip")
        )
    except (CatalogError, VBusError) as err:
        raise HomeAssistantError(str(err)) from err
    capture = runtime.capture
    if (
        capture is None
        or capture.status == "complete"
        or capture.controller != controller
        or capture.catalog.digest != catalog.digest
    ):
        if capture and capture.status == "complete":
            runtime.previous_capture = capture
        capture = ConfigurationCapture(catalog, controller)
        runtime.capture = capture
    capture.status = "running"
    capture.error = None
    runtime.stop_capture = False
    runtime.capture_task = entry.async_create_background_task(
        hass, _async_capture_configuration(runtime), "deltasol_configuration_capture"
    )


async def _async_capture_configuration(runtime):
    """Release the shared VBus session between batches."""
    capture = runtime.capture
    telemetry = runtime.coordinator
    try:
        while not runtime.stop_capture:
            if not telemetry.last_update_success:
                raise VBusError("Controller telemetry is unavailable")
            batch = capture.next_batch()
            if not batch:
                capture.apply((), {})
                return
            observations = await telemetry.hass.async_add_executor_job(
                telemetry.api.read_configuration_batch,
                telemetry.data,
                capture.controller,
                [definition.name for definition in batch],
                capture.changeset,
            )
            capture.apply(batch, observations)
            if capture.status == "complete":
                return
            await asyncio.sleep(0)
        capture.status = "paused"
    except VBusError as err:
        capture.status = "incomplete"
        capture.error = str(err)
    except asyncio.CancelledError:
        capture.status = "incomplete"
        capture.error = "capture_cancelled"
        raise


class DeltasolCoordinator(DataUpdateCoordinator):
    """Coordinator class."""

    data: dict[str, DeltasolEndpoint]

    def __init__(self, hass: HomeAssistant, config: DeltasolConfigEntry) -> None:
        """Initialize coordinator."""
        self.hass = hass
        self.api = DeltasolApi(
            host=config.data.get(CONF_HOST),
            port=config.data.get(CONF_PORT),
            username=config.data.get(CONF_USERNAME),
            password=config.data.get(CONF_PASSWORD),
            api_key=config.data.get(CONF_API_KEY),
            vbus_password=config.options.get(CONF_VBUS_PASSWORD),
        )
        super().__init__(
            hass,
            _LOGGER,
            name="deltasol_sensor",
            update_method=self.async_update_data,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(
                seconds=max(
                    config.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    MIN_SCAN_INTERVAL,
                )
            ),
        )

    async def async_update_data(self):
        """Fetch data from the Resol KM1/KM2, DL2/DL2Plus/DL3, VBus/LAN, VBus/USB."""
        async with asyncio.timeout(DEFAULT_TIMEOUT):
            try:
                data = await self.hass.async_add_executor_job(self.api.fetch_data)
            except IntegrationError as error:
                _LOGGER.error(
                    "Stopping Resol integration due to previous error: %s", error
                )
                raise
            else:
                return data


class DeltasolRelayCoordinator(DataUpdateCoordinator):
    """Independent native refresh for the slower VBus parameter channel.

    Shares the existing API and observed telemetry identity. A parameter
    outage must not delay or invalidate HTTP sensor updates.
    """

    def __init__(self, hass, telemetry):
        self.telemetry = telemetry
        self.api = telemetry.api
        self._lock = asyncio.Lock()
        super().__init__(
            hass,
            _LOGGER,
            name="deltasol_relay_modes",
            update_interval=max(telemetry.update_interval, timedelta(seconds=60)),
        )

    async def _async_update_data(self):
        async with self._lock:
            if not self.telemetry.last_update_success:
                raise UpdateFailed("Controller telemetry is unavailable")
            try:
                return await self.hass.async_add_executor_job(
                    self.api.fetch_relay_modes, self.telemetry.data
                )
            except VBusError as error:
                raise UpdateFailed(str(error)) from error

    async def async_set_mode(self, unique_id, option):
        """Do not accept calls that bypass entity availability."""
        async with self._lock:
            if (
                not self.last_update_success
                or not self.telemetry.last_update_success
                or not self.data
                or unique_id not in self.data.capabilities
            ):
                raise HomeAssistantError("Relay capability is unavailable")
            try:
                capability = await self.hass.async_add_executor_job(
                    self.api.set_relay_mode,
                    self.telemetry.data,
                    self.data.capabilities[unique_id],
                    option,
                )
            except VBusError as error:
                self.async_set_update_error(error)
                raise HomeAssistantError(str(error)) from error
            self.async_set_updated_data(
                replace(
                    self.data,
                    capabilities={**self.data.capabilities, unique_id: capability},
                )
            )
