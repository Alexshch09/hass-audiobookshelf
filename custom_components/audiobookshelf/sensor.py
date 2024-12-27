# sensor.py
"""Module containing the sensor platform for the Audiobookshelf integration."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import aiohttp
from dacite import from_dict
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_SCAN_INTERVAL, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.audiobookshelf import clean_config
from custom_components.audiobookshelf.const import DOMAIN, HTTP_OK, VERSION

_LOGGER = logging.getLogger(__name__)


def count_active_users(data: dict) -> int:
    """Take in an object with an array of users and counts the active ones."""
    _LOGGER.debug("Entering count_active_users with data: %s", data)
    count = 0
    for user in data["users"]:
        if user["isActive"] and user["username"] != "hass":
            count += 1
    _LOGGER.debug("Exiting count_active_users, returning: %s", count)
    return count


def clean_user_attributes(data: dict) -> dict:
    """Remove the token and some extra data from users."""
    _LOGGER.debug("Entering clean_user_attributes with data: %s", data)
    for user in data["users"]:
        user["token"] = "<redacted>"  # noqa: S105
    _LOGGER.debug("Exiting clean_user_attributes, returning: %s", data)
    return data


def count_open_sessions(data: dict) -> int:
    """Count the number of open stream sessions."""
    _LOGGER.debug("Entering count_open_sessions with data: %s", data)
    count = len(data["openSessions"])
    _LOGGER.debug("Exiting count_open_sessions, returning: %s", count)
    return count


def count_libraries(data: dict) -> int:
    """Count the number libraries."""
    _LOGGER.debug("Entering count_libraries with data: %s", data)
    count = len(data["libraries"])
    _LOGGER.debug("Exiting count_libraries, returning: %s", count)
    return count


def extract_library_details(data: dict) -> dict:
    """Extract the details from the library."""
    _LOGGER.debug("Entering extract_library_details with data: %s", data)
    details = {}
    for library in data.get("libraries", []):
        library_id = library.get("id")
        if library_id:
            details[library_id] = {
                "mediaType": library.get("mediaType"),
                "provider": library.get("provider"),
            }
            _LOGGER.debug(
                "Extracted details for library ID %s: %s",
                library_id,
                details[library_id],
            )
        else:
            _LOGGER.warning("Library ID not found in library data: %s", library)
    _LOGGER.debug("Exiting extract_library_details, returning: %s", details)
    return details


def get_total_duration(total_duration: float | None) -> float | None:
    """Calculate the total duration in hours and round it to 0 decimal places."""
    _LOGGER.debug("Entering get_total_duration with total_duration: %s", total_duration)
    if total_duration is None:
        _LOGGER.debug("Total duration is None, returning None")
        return None
    duration_hours = round(total_duration / 60.0 / 60.0, 0)
    _LOGGER.debug("Calculated duration in hours: %s", duration_hours)
    _LOGGER.debug("Exiting get_total_duration, returning: %s", duration_hours)
    return duration_hours


def get_total_size(total_size: float | None) -> float | None:
    """Convert the size to human readable."""
    _LOGGER.debug("Entering get_total_size with total_size: %s", total_size)
    if total_size is None:
        _LOGGER.debug("Total size is None, returning None")
        return None
    size_gb = round(total_size / 1024.0 / 1024.0 / 1024.0, 2)
    _LOGGER.debug("Calculated size in GB: %s", size_gb)
    _LOGGER.debug("Exiting get_total_size, returning: %s", size_gb)
    return size_gb


def get_library_stats(data: dict) -> dict:
    """Get statistics for each library."""
    _LOGGER.debug("Entering get_library_stats with data: %s", data)
    library_stats = extract_library_details(data)
    _LOGGER.debug("Exiting get_library_stats, returning: %s", library_stats)
    return library_stats


def do_nothing(data: dict) -> dict:
    """Return the input data without any modifications."""
    _LOGGER.debug("Entering do_nothing with data: %s", data)
    _LOGGER.debug("Exiting do_nothing, returning input data")
    return data


def extract_server_version(data: dict) -> str | None:
    """Extract the server version from the authorize endpoint."""
    _LOGGER.debug("Entering extract_server_version with data: %s", data)
    try:
        version = data["serverSettings"]["version"]
        _LOGGER.debug("Extracted server version: %s", version)
        _LOGGER.debug("Exiting extract_server_version, returning: %s", version)
        return version
    except KeyError:
        _LOGGER.warning("Server version not found in API response.")
        _LOGGER.debug("Exiting extract_server_version, returning None")
        return None


type Sensor = dict[str, Any]

# simple polling sensors
sensors: dict[str, Sensor] = {
    "users": {
        "endpoint": "api/users",
        "name": "Audiobookshelf Users",
        "data_function": count_active_users,
        "attributes_function": clean_user_attributes,
        "unit": "users",
    },
    "sessions": {
        "endpoint": "api/users/online",
        "name": "Audiobookshelf Open Sessions",
        "data_function": count_open_sessions,
        "attributes_function": do_nothing,
        "unit": "sessions",
    },
    "libraries": {
        "endpoint": "api/libraries",
        "name": "Audiobookshelf Libraries",
        "data_function": count_libraries,
        "attributes_function": get_library_stats,
        "unit": "libraries",
    },
    "server_version": {
        "endpoint": "api/authorize",
        "name": "Audiobookshelf Server Version",
        "data_function": extract_server_version,
        "attributes_function": do_nothing,
        "unit": "version",
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    _LOGGER.debug("Entering async_setup_entry")
    conf = entry.data

    _LOGGER.debug("Configuration data: %s", clean_config(conf.copy()))

    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {conf[CONF_API_KEY]}"}
        _LOGGER.debug("Headers for API request: %s", headers)
        url = f"{conf[CONF_URL]}/api/libraries"
        _LOGGER.debug("Fetching libraries from: %s", url)
        try:
            async with session.get(url, headers=headers) as response:
                _LOGGER.debug("Response status from API: %s", response.status)
                if response.status != HTTP_OK:
                    msg = f"Failed to connect to API: {response.status}"
                    _LOGGER.error("%s", msg)
                    raise ConfigEntryNotReady(msg)
                _LOGGER.debug("Successfully connected to API")
        except aiohttp.ClientError as e:
            _LOGGER.error("AIOHTTP error during API request: %s", e)
            raise ConfigEntryNotReady(f"Failed to connect to API: {e}")

    coordinator = AudiobookshelfDataUpdateCoordinator(hass, entry)
    _LOGGER.debug("AudiobookshelfDataUpdateCoordinator initialized: %s", coordinator)

    libraries: list[Library] = await coordinator.get_libraries()
    _LOGGER.debug("Retrieved libraries: %s", libraries)
    coordinator.generate_library_sensors(libraries)
    _LOGGER.debug("Generated library sensors")

    await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug("Initial data fetch completed")

    entities = [
        AudiobookshelfSensor(coordinator, sensor) for sensor in sensors.values()
    ]
    _LOGGER.debug("Created sensor entities: %s", entities)

    _LOGGER.debug("Adding entities to Home Assistant: %s", entities)
    async_add_entities(entities, update_before_add=True)
    _LOGGER.debug("Exiting async_setup_entry")


@dataclass
class LibraryFolder:
    """Class representing a folder in an Audiobookshelf library."""

    id: str
    full_path: str | None
    library_id: str
    added_at: int | None


@dataclass
class LibrarySettings:
    """Class representing settings for an Audiobookshelf library."""

    cover_aspect_ratio: int | None
    disable_watcher: bool | None
    auto_scan_cron_expression: str | None
    skip_matching_media_with_asin: bool | None
    skip_matching_media_with_isbn: bool | None
    audiobooks_only: bool | None
    epubs_allow_scripted_content: bool | None
    hide_single_book_series: bool | None
    only_show_later_books_in_continue_series: bool | None
    metadata_precedence: list[str] | None
    mark_as_finished_percent_complete: int | None
    mark_as_finished_time_remaining: int | None


@dataclass
class Library:
    """Class representing an Audiobookshelf library."""

    id: str
    name: str
    folders: list[LibraryFolder] | None
    display_order: int | None
    icon: str | None
    media_type: str | None
    provider: str | None
    settings: LibrarySettings | None
    last_scan: int | None
    last_scan_version: str | None
    created_at: int | None
    last_update: int | None


def camel_to_snake(data: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """Convert camelCase keys to snake_case."""
    _LOGGER.debug("Entering camel_to_snake with data: %s", data)

    def _convert_key(key: str) -> str:
        snake_case_key = "".join(
            ["_" + char.lower() if char.isupper() else char for char in key]
        ).lstrip("_")
        _LOGGER.debug("Converted key '%s' to '%s'", key, snake_case_key)
        return snake_case_key

    if isinstance(data, dict):
        converted_data = {
            _convert_key(key): camel_to_snake(value)
            if isinstance(value, (dict, list))
            else value
            for key, value in data.items()
        }
        _LOGGER.debug("Converted dictionary: %s", converted_data)
        _LOGGER.debug("Exiting camel_to_snake, returning: %s", converted_data)
        return converted_data
    if isinstance(data, list):
        converted_list = [
            camel_to_snake(item) if isinstance(item, (dict, list)) else item
            for item in data
        ]
        _LOGGER.debug("Converted list: %s", converted_list)
        _LOGGER.debug("Exiting camel_to_snake, returning: %s", converted_list)
        return converted_list
    _LOGGER.debug("Exiting camel_to_snake, returning: %s", data)
    return data


class AudiobookshelfDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Audiobookshelf data from the API."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        _LOGGER.debug("Entering AudiobookshelfDataUpdateCoordinator.__init__")
        self.config_entry = config_entry
        self.conf = self.config_entry.data
        if self.config_entry is None:
            msg = "Config is none on coordinator"
            _LOGGER.error(msg)
            raise ConfigEntryNotReady(msg)

        super().__init__(
            hass,
            _LOGGER,
            name="audiobookshelf",
            update_interval=timedelta(seconds=self.conf[CONF_SCAN_INTERVAL]),
        )
        _LOGGER.debug("Exiting AudiobookshelfDataUpdateCoordinator.__init__")

    async def get_libraries(self) -> list[Library]:
        """Fetch library id list from API."""
        _LOGGER.debug("Entering AudiobookshelfDataUpdateCoordinator.get_libraries")
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.conf[CONF_API_KEY]}"}
            _LOGGER.debug("Headers for API request: %s", headers)
            url = f"{self.conf[CONF_URL]}/api/libraries"
            _LOGGER.debug("Fetching libraries from: %s", url)
            try:
                async with session.get(url, headers=headers) as response:
                    _LOGGER.debug("Response status from API: %s", response.status)
                    if response.status == HTTP_OK:
                        data: dict[str, Any] = await response.json()
                        _LOGGER.debug("Received library data: %s", data)
                        libraries_data = data.get("libraries", [])
                        _LOGGER.debug("Libraries data: %s", libraries_data)
                        libraries = [
                            from_dict(
                                data_class=Library, data=(dict(camel_to_snake(lib)))
                            )
                            for lib in libraries_data
                        ]
                        _LOGGER.debug("Converted libraries: %s", libraries)
                        _LOGGER.debug(
                            "Exiting AudiobookshelfDataUpdateCoordinator.get_libraries, returning %s",
                            libraries,
                        )
                        return libraries
                    else:
                        _LOGGER.error(
                            "Failed to fetch libraries, status: %s", response.status
                        )
                        _LOGGER.debug(
                            "Exiting AudiobookshelfDataUpdateCoordinator.get_libraries, returning empty list"
                        )
                        return []
            except aiohttp.ClientError as e:
                _LOGGER.error("AIOHTTP error fetching libraries: %s", e)
                _LOGGER.debug(
                    "Exiting AudiobookshelfDataUpdateCoordinator.get_libraries, returning empty list"
                )
                return []

    def generate_library_sensors(self, libraries: list[Library]) -> None:
        """Generate sensor configs for each library."""
        _LOGGER.debug(
            "Entering AudiobookshelfDataUpdateCoordinator.generate_library_sensors with libraries: %s",
            libraries,
        )
        for library in libraries:
            base_id = f"library_{library.id}"
            sensors.update(
                {
                    f"{base_id}_size": {
                        "endpoint": f"api/libraries/{library.id}/stats",
                        "name": f"Audiobookshelf {library.name} Size",
                        "unique_id": f"{base_id}_size",
                        "data_function": lambda data: get_total_size(
                            data.get("totalSize")
                        ),
                        "unit": "GB",
                        "attributes_function": do_nothing,
                    },
                    f"{base_id}_items": {
                        "endpoint": f"api/libraries/{library.id}/stats",
                        "name": f"Audiobookshelf {library.name} Items",
                        "unique_id": f"{base_id}_items",
                        "data_function": lambda data: data.get("totalItems"),
                        "unit": "items",
                        "attributes_function": do_nothing,
                    },
                    f"{base_id}_duration": {
                        "endpoint": f"api/libraries/{library.id}/stats",
                        "name": f"Audiobookshelf {library.name} Duration",
                        "unique_id": f"{base_id}_duration",
                        "data_function": lambda data: get_total_duration(
                            data.get("totalDuration")
                        ),
                        "unit": "hours",
                        "attributes_function": do_nothing,
                    },
                }
            )
        _LOGGER.debug(
            "Exiting AudiobookshelfDataUpdateCoordinator.generate_library_sensors, updated sensors: %s",
            sensors,
        )

    async def _async_update_data(self) -> dict:
        """Fetch data from API endpoint."""
        _LOGGER.debug("Entering AudiobookshelfDataUpdateCoordinator._async_update_data")
        headers = {"Authorization": f"Bearer {self.conf[CONF_API_KEY]}"}
        data = {}
        unique_endpoints: set[str] = {sensor["endpoint"] for sensor in sensors.values()}
        _LOGGER.debug("Fetching data for unique endpoints: %s", unique_endpoints)
        try:
            async with aiohttp.ClientSession() as session:
                for endpoint in unique_endpoints:
                    url = f"{self.conf[CONF_URL]}/{endpoint}"
                    _LOGGER.debug("Fetching data from: %s", url)
                    try:
                        async with session.get(url, headers=headers) as response:
                            _LOGGER.debug(
                                "Response status for %s: %s", endpoint, response.status
                            )
                            if response.status != HTTP_OK:
                                error_message = f"Error fetching data for {endpoint}: {response.status}"
                                _LOGGER.error(error_message)
                                raise UpdateFailed(error_message)
                            response_data = await response.json()
                            data[endpoint] = response_data
                            _LOGGER.debug(
                                "Data received for %s: %s", endpoint, response_data
                            )
                    except aiohttp.ClientError as e:
                        _LOGGER.error("AIOHTTP error fetching %s: %s", endpoint, e)
                        raise UpdateFailed(f"Error fetching data for {endpoint}: {e}")
            _LOGGER.debug(
                "Exiting AudiobookshelfDataUpdateCoordinator._async_update_data, returning data: %s",
                data,
            )
            return data
        except UpdateFailed as err:
            _LOGGER.error("Error during data update: %s", err)
            _LOGGER.debug(
                "Exiting AudiobookshelfDataUpdateCoordinator._async_update_data with failure"
            )
            raise


class AudiobookshelfSensor(RestoreEntity, Entity):
    """Representation of a sensor."""

    def __init__(
        self, coordinator: AudiobookshelfDataUpdateCoordinator, sensor: Sensor
    ) -> None:
        """Initialize the sensor."""
        _LOGGER.debug("Entering AudiobookshelfSensor.__init__ with sensor: %s", sensor)
        self._name = sensor["name"]
        self._unique_id = sensor.get("unique_id", self._name)
        self._attr_unit_of_measurement = sensor.get("unit", None)
        self._endpoint = sensor["endpoint"]
        self.coordinator = coordinator
        self._state: str | None = None
        self._attr_extra_state_attributes = {}
        self._process_data = sensor["data_function"]
        self._process_attributes = sensor["attributes_function"]
        self.conf = self.coordinator.conf
        _LOGGER.debug("Exiting AudiobookshelfSensor.__init__")

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        _LOGGER.debug("Entering AudiobookshelfSensor.async_added_to_hass")
        await super().async_added_to_hass()
        if (
            self._endpoint != "api/authorize"
            and (last_state := await self.async_get_last_state()) is not None
        ):
            self._state = last_state.state
            self._attributes = last_state.attributes
            _LOGGER.debug(
                "Restored state: %s, attributes: %s", self._state, self._attributes
            )

        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        _LOGGER.debug("Exiting AudiobookshelfSensor.async_added_to_hass")

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> Any:
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return self._attr_extra_state_attributes

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self.conf[CONF_URL])},
            "name": "Audiobookshelf",
            "manufacturer": "advplyr",
            "sw_version": VERSION,
            "configuration_url": self.conf[CONF_URL],
        }

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        # Create unique IDs for each sensor that include the API URL
        return f"{self.conf[CONF_URL]}_{self._endpoint}_{self._name}"

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        _LOGGER.debug("Entering AudiobookshelfSensor.async_update")
        data = self.coordinator.data
        if data:
            endpoint_data = data.get(self._endpoint, {})
            _LOGGER.debug("Data for endpoint %s: %s", self._endpoint, endpoint_data)
            if isinstance(endpoint_data, dict):
                processed_attributes = self._process_attributes(endpoint_data)
                self._attr_extra_state_attributes.update(processed_attributes)
                _LOGGER.debug(
                    "Updated extra state attributes: %s",
                    self._attr_extra_state_attributes,
                )
                new_state = self._process_data(data=endpoint_data)
                _LOGGER.debug("Calculated new state: %s", new_state)
                if new_state not in (0, None) or self._state in (0, None):
                    self._state = new_state
                    _LOGGER.debug("Sensor state updated to: %s", self._state)
            else:
                _LOGGER.error(
                    "Expected endpoint_data to be a dictionary for %s, got %s",
                    self._endpoint,
                    type(endpoint_data),
                )
                _LOGGER.debug("Data: %s", endpoint_data)
        _LOGGER.debug("Exiting AudiobookshelfSensor.async_update")
