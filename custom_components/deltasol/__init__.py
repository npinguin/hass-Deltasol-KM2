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
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
    IntegrationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .catalog import (
    CatalogError,
    CatalogMissing,
    download_catalog,
    load_catalog,
    source_for,
)
from .configuration import WEEKLY_OWNERS, encode_action
from .const import (
    CONF_VBUS_PASSWORD,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .deltasolapi import DeltasolApi, DeltasolEndpoint
from .discovery import ConfigurationCapture, capture_batches, capture_identity
from .parameters import (
    device_inventory,
    find_profile,
    refresh_relay_modes,
    run_parameter_job,
)
from .vbus import VBusError, VBusTransientError

_LOGGER = logging.getLogger(__name__)
DISCOVERY_BATCH_GAP_SECONDS = 1

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TEXT,
]

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
    capture_wakeup: asyncio.Event = dataclass_field(default_factory=asyncio.Event)
    capture_listeners: set = dataclass_field(default_factory=set)
    capture_store: Store | None = None
    checkpoint_error: str | None = None
    resume_on_load: bool = False
    auto_start_task: asyncio.Task | None = None
    closing: bool = False

    def notify_capture(self):
        for listener in tuple(self.capture_listeners):
            listener()


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

    # Reject a second parameter owner on the same physical gateway, including
    # aliases of the same IP. No new owner or sensor registry is introduced.
    if coordinator.api.supports_parameter_access:
        serial = coordinator.api.product_details.get("serial")
        for other in hass.config_entries.async_entries(DOMAIN):
            existing = getattr(other, "runtime_data", None)
            if (
                other.entry_id != config_entry.entry_id
                and other.state
                in (
                    ConfigEntryState.LOADED,
                    ConfigEntryState.SETUP_IN_PROGRESS,
                    ConfigEntryState.UNLOAD_IN_PROGRESS,
                    ConfigEntryState.FAILED_UNLOAD,
                )
                and existing
                and existing.coordinator.api.supports_parameter_access
                and existing.coordinator.api.product_details.get("serial") == serial
            ):
                raise ConfigEntryError("RESOL gateway already has a parameter owner")

    # Add the coordinator and update listener to config runtime data to make
    # accessible throughout your integration
    config_entry.runtime_data = RuntimeData(
        coordinator,
        capture_store=Store(
            hass, 1, f"deltasol.{config_entry.entry_id}.discovery", private=True
        ),
    )

    config_entry.async_on_unload(config_entry.add_update_listener(_async_reload_entry))

    # Setup platforms (based on the list of entity types in PLATFORMS defined above)
    # This calls the async_setup method in each of your entity type files.
    runtime = config_entry.runtime_data
    if coordinator.api.supports_parameter_access:
        runtime.relay_coordinator = DeltasolRelayCoordinator(hass, coordinator, runtime)
        config_entry.async_on_unload(
            coordinator.async_add_listener(
                runtime.relay_coordinator.async_update_listeners
            )
        )
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    if runtime.relay_coordinator:
        relay = runtime.relay_coordinator
        await relay.async_refresh()
        if relay.last_update_success:
            await _async_restore_and_resume(hass, config_entry, runtime)
        else:
            runtime.checkpoint_error = "checkpoint_waiting_for_controller"
            restore_pending = True

            @callback
            def restore_when_ready():
                nonlocal restore_pending
                if restore_pending and relay.last_update_success:
                    restore_pending = False
                    config_entry.async_create_background_task(
                        hass,
                        _async_restore_and_resume(hass, config_entry, runtime),
                        "deltasol_restore_capture",
                    )

            config_entry.async_on_unload(relay.async_add_listener(restore_when_ready))

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
    runtime.closing = True
    async with runtime.capture_lock:
        if runtime.capture_task and not runtime.capture_task.done():
            runtime.resume_on_load = not runtime.stop_capture
            runtime.stop_capture = True
            runtime.capture_wakeup.set()
            await runtime.capture_task
        if runtime.capture:
            await _async_save_capture(runtime)
    if runtime.relay_coordinator:
        await runtime.relay_coordinator.async_close()
    unloaded = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    if not unloaded:
        runtime.closing = False
        if runtime.relay_coordinator:
            runtime.relay_coordinator.closing = False
    return unloaded


async def async_remove_entry(hass, entry):
    """Remove only this entry's private discovery checkpoint."""
    await Store(
        hass, 1, f"deltasol.{entry.entry_id}.discovery", private=True
    ).async_remove()


async def _async_save_capture(runtime):
    if runtime.capture is None:
        return
    try:
        await runtime.capture_store.async_save(
            {
                "capture": runtime.capture.storage_checkpoint(),
                "previous": runtime.previous_capture.storage_checkpoint()
                if runtime.previous_capture
                else None,
                "resume": runtime.capture.status not in ("complete", "incomplete")
                and (
                    runtime.resume_on_load
                    or runtime.capture.status in ("running", "recovering")
                ),
            }
        )
        runtime.checkpoint_error = None
    except (OSError, ValueError, HomeAssistantError):
        runtime.checkpoint_error = "checkpoint_save_failed"
        _LOGGER.warning("Could not save RESOL Discovery checkpoint")


def _catalog_identity(runtime):
    relay = runtime.relay_coordinator
    controller = capture_identity(runtime.coordinator.data)
    if (
        not runtime.coordinator.last_update_success
        or not relay
        or not relay.last_update_success
        or not relay.data
        or relay.data.controller != controller
        or relay.data.changeset is None
    ):
        raise CatalogError("Controller catalogue identity not confirmed")
    return controller, source_for(controller.address, relay.data.changeset)


async def _async_catalog(hass, source, *, acquire=False):
    path = hass.config.path("deltasol", source.filename)
    try:
        return await hass.async_add_executor_job(load_catalog, path, source)
    except CatalogMissing:
        if not acquire:
            raise
        return await hass.async_add_executor_job(download_catalog, path, source)


async def _async_restore_capture(hass, runtime, *, catalog=None):
    """Restore only against the current verified controller and exact local archive."""
    if not runtime.coordinator.api.supports_parameter_access:
        return False
    try:
        stored = await runtime.capture_store.async_load()
        if stored is None:
            runtime.checkpoint_error = None
            return False
        controller, source = _catalog_identity(runtime)
        if catalog is None:
            catalog = await _async_catalog(hass, source)
        capture = ConfigurationCapture.restore(
            stored["capture"], catalog, controller, source.changeset
        )
        previous = stored.get("previous")
        runtime.previous_capture = (
            ConfigurationCapture.restore(
                previous, catalog, controller, source.changeset
            )
            if previous
            else None
        )
        runtime.capture = capture
        runtime.resume_on_load = (
            stored.get("resume") is True or capture.extension_pending
        )
        runtime.checkpoint_error = None
        return True
    except CatalogMissing:
        runtime.checkpoint_error = "checkpoint_catalogue_missing"
        return False
    except (
        CatalogError,
        VBusError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        HomeAssistantError,
    ):
        runtime.checkpoint_error = "checkpoint_incompatible"
        _LOGGER.warning("RESOL Discovery checkpoint unavailable or incompatible")
        return False


async def _async_restore_and_resume(hass, entry, runtime):
    """Restore once or schedule initial Discovery after confirmed access."""
    async with runtime.capture_lock:
        if runtime.closing or runtime.capture is not None:
            return
        if await _async_restore_capture(hass, runtime):
            if runtime.resume_on_load and runtime.capture.status == "paused":
                await _start_capture(hass, entry, runtime)
            runtime.notify_capture()
        elif (
            runtime.checkpoint_error in (None, "checkpoint_catalogue_missing")
            and runtime.auto_start_task is None
        ):
            runtime.auto_start_task = entry.async_create_background_task(
                hass,
                _async_start_initial_capture(hass, entry, runtime),
                "deltasol_initial_discovery",
            )
        else:
            runtime.notify_capture()


async def _async_start_initial_capture(hass, entry, runtime):
    """Acquire the pinned catalogue once outside setup; never replace a checkpoint."""
    async with runtime.capture_lock:
        if runtime.closing or runtime.capture is not None:
            return
        restore_required = runtime.checkpoint_error == "checkpoint_catalogue_missing"
        try:
            controller, source = _catalog_identity(runtime)
        except (CatalogError, VBusError):
            runtime.checkpoint_error = "discovery_controller_unverified"
            runtime.notify_capture()
            return
        try:
            catalog = await _async_catalog(hass, source, acquire=True)
        except (CatalogError, OSError, ValueError):
            runtime.checkpoint_error = "discovery_catalogue_unavailable"
            runtime.notify_capture()
            return
        if runtime.closing:
            return
        # Acquisition may take time; repeat identity verification before
        # committing the capture, rather than scanning a changed controller.
        try:
            current_controller, current_source = _catalog_identity(runtime)
        except (CatalogError, VBusError):
            runtime.checkpoint_error = "discovery_controller_unverified"
            runtime.notify_capture()
            return
        if (current_controller, current_source) != (controller, source):
            runtime.checkpoint_error = "discovery_controller_unverified"
            runtime.notify_capture()
            return
        if restore_required:
            if (
                await _async_restore_capture(hass, runtime, catalog=catalog)
                and runtime.resume_on_load
                and runtime.capture.status == "paused"
            ):
                await _start_capture(hass, entry, runtime)
            runtime.notify_capture()
            return
        runtime.capture = ConfigurationCapture(catalog, controller, source.changeset)
        await _start_capture(hass, entry, runtime)


async def async_toggle_configuration_capture(hass, entry):
    """Start/resume or request a stop at the next batch boundary."""
    runtime = entry.runtime_data
    async with runtime.capture_lock:
        if runtime.closing:
            raise HomeAssistantError("Controller entry is unloading")
        await _async_toggle_capture_locked(hass, entry, runtime)


async def _async_toggle_capture_locked(hass, entry, runtime):
    if runtime.capture_task and not runtime.capture_task.done():
        runtime.stop_capture = True
        runtime.capture_wakeup.set()
        runtime.notify_capture()
        return
    telemetry = runtime.coordinator
    if not telemetry.last_update_success or not telemetry.api.supports_parameter_access:
        raise HomeAssistantError("Controller parameter access is unavailable")
    try:
        controller, source = _catalog_identity(runtime)
        catalog = await _async_catalog(hass, source, acquire=True)
    except (CatalogError, VBusError) as err:
        raise HomeAssistantError(str(err)) from err
    if runtime.capture is None:
        await _async_restore_capture(hass, runtime)
    capture = runtime.capture
    if (
        capture is None
        or capture.status == "complete"
        or capture.controller != controller
        or capture.catalog.digest != catalog.digest
        or capture.changeset != source.changeset
    ):
        runtime.previous_capture = (
            capture
            if capture
            and capture.status == "complete"
            and capture.controller == controller
            and capture.catalog.digest == catalog.digest
            and capture.changeset == source.changeset
            else None
        )
        capture = ConfigurationCapture(catalog, controller, source.changeset)
        runtime.capture = capture
    await _start_capture(hass, entry, runtime)


async def _start_capture(hass, entry, runtime):
    capture = runtime.capture
    capture.status = "running"
    capture.error = None
    runtime.resume_on_load = False
    runtime.stop_capture = False
    runtime.capture_wakeup.clear()
    capture.consecutive_failures = 0
    await _async_save_capture(runtime)
    runtime.notify_capture()

    async def save():
        await _async_save_capture(runtime)

    runtime.capture_task = entry.async_create_background_task(
        hass, capture_batches(runtime, save), "deltasol_configuration_capture"
    )


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

    def __init__(self, hass, telemetry, runtime):
        self.telemetry = telemetry
        self.runtime = runtime
        self.api = telemetry.api
        self._lock = asyncio.Lock()
        self.closing = False
        self.initial_refresh = True
        super().__init__(
            hass,
            _LOGGER,
            name="deltasol_relay_modes",
            update_interval=max(telemetry.update_interval, timedelta(seconds=60)),
        )

    def _check_open(self):
        if self.closing:
            raise VBusError("Parameter owner is unloading")

    async def async_close(self):
        self.closing = True
        # Drain any already-running command; do not let a replacement owner
        # reconnect before the physical gateway cooldown has elapsed.
        async with self._lock:
            await asyncio.sleep(self.api.parameter_cooldown_remaining())

    def _configuration_catalog(self):
        """Use the existing capture's verified catalogue, no second cache."""
        capture = self.runtime.capture
        if capture is None:
            return None
        _, controller = find_profile(device_inventory(self.telemetry.data))
        if capture.controller != controller:
            return None
        if not capture.catalog.matches(capture.controller.address, capture.changeset):
            return None
        return capture.catalog

    def _configuration_hints(self):
        profile, controller = find_profile(device_inventory(self.telemetry.data))
        if not profile:
            return {}
        contracts = profile.settings_for(
            profile.configuration_changeset, self._configuration_catalog()
        )
        selectors = {
            setting.installed_by.name for setting in contracts if setting.installed_by
        }
        if any(setting.weekly_slot is not None for setting in contracts):
            selectors.update(owner.name for owner in WEEKLY_OWNERS)
        hints = {}
        # The previous completed capture avoids temporarily losing controls
        # during a new scan. The newer observations replace previous hints.
        for capture in (self.runtime.previous_capture, self.runtime.capture):
            if (
                capture
                and capture.controller == controller
                and capture.changeset == profile.configuration_changeset
            ):
                hints.update(
                    (name, capture.results[name])
                    for name in selectors
                    if name in capture.results
                )
        return hints

    async def _async_update_data(self):
        try:
            result = await refresh_relay_modes(
                self.telemetry,
                self._lock,
                guard=self._check_open,
                initial=self.initial_refresh,
                configuration_hints=self._configuration_hints(),
                catalog=self._configuration_catalog(),
                previous=self.data,
            )
        except VBusError as error:
            # Retry ordinary polling sooner while unavailable, not by issuing
            # additional writes or keeping the bus occupied.
            self.update_interval = timedelta(seconds=30)
            raise UpdateFailed(str(error)) from error
        else:
            self.update_interval = max(
                self.telemetry.update_interval, timedelta(seconds=60)
            )
            return result
        finally:
            self.initial_refresh = False

    async def async_read_configuration_batch(self, capture, names):
        """Give foreground work a scheduling window before each batch."""
        await asyncio.sleep(DISCOVERY_BATCH_GAP_SECONDS)
        if not self.last_update_success:
            await self.async_refresh()
            if not self.last_update_success:
                cause = getattr(self.last_exception, "__cause__", None)
                if isinstance(cause, VBusError):
                    raise cause
                raise VBusTransientError(
                    "Controller parameter access is unavailable", "availability"
                )
        async with self._lock:
            self._check_open()
            if not self.telemetry.last_update_success:
                raise VBusTransientError(
                    "Controller telemetry is unavailable", "telemetry"
                )
            return await run_parameter_job(
                self.hass,
                self.api.read_configuration_batch,
                self.telemetry.data,
                capture.controller,
                names,
                capture.changeset,
                {
                    name: capture.packed_days[name]
                    for name in names
                    if name in capture.packed_days
                },
            )

    async def _async_action_failed(self, error):
        """Refresh capabilities, not the failed command; preserve real errors."""
        if str(error).startswith("VBus write outcome uncertain") or isinstance(error, VBusTransientError) or str(error) in (
            "VBus write outcome uncertain",
            "VBus write acknowledgement differs",
            "VBus write readback differs",
            "Configuration reconnect readback differs",
            "Configuration parameter is no longer supported",
            "Configuration conditional constraint rejected",
        ):
            await self.async_refresh()

    async def async_set_configuration(self, unique_id, value):
        """Native entities use one live validated configuration action."""
        try:
            refresh_dependents = await self._async_set_configuration(unique_id, value)
        except VBusError as error:
            await self._async_action_failed(error)
            raise HomeAssistantError(str(error)) from error
        if refresh_dependents:
            await self.async_refresh()

    async def _async_set_configuration(self, unique_id, value):
        async with self._lock:
            if (
                self.closing
                or not self.last_update_success
                or not self.telemetry.last_update_success
                or not self.data
                or unique_id not in self.data.configuration
            ):
                raise HomeAssistantError("Configuration capability is unavailable")
            capability = self.data.configuration[unique_id]
            try:
                encode_action(capability.setting, value)
            except VBusError as error:
                raise HomeAssistantError(str(error)) from error
            await self.telemetry.async_refresh()
            if not self.telemetry.last_update_success:
                raise HomeAssistantError("Controller telemetry is unavailable")
            try:
                verified = await run_parameter_job(
                    self.hass,
                    self.api.set_configuration,
                    self.telemetry.data,
                    capability,
                    value,
                    self._configuration_catalog(),
                )
            except VBusError as error:
                self.async_set_update_error(error)
                raise
            # A changed selector invalidates dependent controls before the
            # read-only refresh. Never retain a capability derived from an old
            # condition, even if the next refresh fails.
            changed = verified.raw != capability.raw
            dependents = (
                {
                    setting.name
                    for setting in self.data.profile.settings_for(verified.changeset)
                    if setting.name != verified.setting.name
                    and verified.setting.name in setting.expected
                }
                if changed and self.data.profile
                else set()
            )
            remaining = {
                uid: cap
                for uid, cap in self.data.configuration.items()
                if cap.setting.name not in dependents
            }
            self.async_set_updated_data(
                replace(
                    self.data,
                    configuration={**remaining, unique_id: verified},
                )
            )
            return bool(dependents)

    async def async_set_mode(self, unique_id, option):
        """Do not accept calls that bypass entity availability."""
        try:
            refresh_dependents = await self._async_set_mode(unique_id, option)
        except VBusError as error:
            await self._async_action_failed(error)
            raise HomeAssistantError(str(error)) from error
        if refresh_dependents:
            await self.async_refresh()

    async def _async_set_mode(self, unique_id, option):
        async with self._lock:
            if (
                self.closing
                or not self.last_update_success
                or not self.telemetry.last_update_success
                or not self.data
                or unique_id not in self.data.capabilities
            ):
                raise HomeAssistantError("Relay capability is unavailable")
            capability = self.data.capabilities[unique_id]
            if capability.registration_source == "activation_and_observed_module":
                # The controller cannot resolve the presence flag on this exact
                # profile. Obtain a fresh gateway observation before each action.
                await self.telemetry.async_refresh()
                if not self.telemetry.last_update_success:
                    raise HomeAssistantError("Controller telemetry is unavailable")
            try:
                capability = await run_parameter_job(
                    self.hass,
                    self.api.set_relay_mode,
                    self.telemetry.data,
                    capability,
                    option,
                )
            except VBusError as error:
                self.async_set_update_error(error)
                raise
            # Manual mode is a prerequisite of pump settings. Invalidate
            # dependent controls immediately rather than leave writable stale
            # projections until the next scheduled parameter refresh.
            dependents = {
                uid: cap
                for uid, cap in self.data.configuration.items()
                if capability.name in cap.setting.expected
            }
            remaining = {
                uid: cap
                for uid, cap in self.data.configuration.items()
                if uid not in dependents
            }
            self.async_set_updated_data(
                replace(
                    self.data,
                    capabilities={**self.data.capabilities, unique_id: capability},
                    configuration=remaining,
                )
            )
            # Returning to Auto can restore settings invalidated by a previous
            # manual operation, but do not add extra polls to unrelated relays.
            if dependents:
                return True
            hints = self._configuration_hints()
            return bool(
                self.data.profile
                and option == "Auto"
                and any(
                    (setting.installed_by is None or setting.installed_by.name in hints)
                    and capability.name in setting.expected
                    for setting in self.data.profile.settings_for(capability.changeset)
                )
            )
