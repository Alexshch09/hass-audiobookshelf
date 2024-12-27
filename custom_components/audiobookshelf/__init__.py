# __init__.py
"""Custom component for Audiobookshelf."""

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_SCAN_INTERVAL, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
import aiohttp  # Import aiohttp

from custom_components.audiobookshelf.config_flow import validate_config
from custom_components.audiobookshelf.const import (
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    VERSION,
    HTTP_OK,
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_API_KEY): cv.string,
                vol.Required(CONF_URL): cv.string,
                vol.Optional(CONF_SCAN_INTERVAL, default=300): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


def clean_config(data: dict[str, str]) -> dict[str, str]:
    """Remove the api key from config."""
    try:
        if bool(data[CONF_API_KEY]):
            data[CONF_API_KEY] = "<redacted>"
    except Exception:
        _LOGGER.exception("Failed to clean config, most likely not valid")
    return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Audiobookshelf from a config entry."""
    if entry.data is None:
        error_message = "Configuration not found"
        raise ConfigEntryNotReady(error_message)

    _LOGGER.info(
        """
            -------------------------------------------------------------------
            Audiobookshelf
            Version: %s
            This is a custom integration!
            If you have any issues with this you need to open an issue here:
            %s
            -------------------------------------------------------------------
            """,
        VERSION,
        ISSUE_URL,
    )

    _LOGGER.debug(
        "Setting up Audiobookshelf with config: %s", clean_config(entry.data.copy())
    )

    # Test the API connection here
    conf = entry.data
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {conf[CONF_API_KEY]}"}
            async with session.get(
                f"{conf[CONF_URL]}/api/libraries", headers=headers
            ) as response:
                if response.status != HTTP_OK:
                    msg = f"Failed to connect to Audiobookshelf API: {response.status}"
                    _LOGGER.error(msg)
                    raise ConfigEntryNotReady(msg)
    except aiohttp.ClientError as e:
        _LOGGER.error(f"Error connecting to Audiobookshelf API: {e}")
        raise ConfigEntryNotReady(f"Error connecting to Audiobookshelf API: {e}")

    validate_config(entry.data.copy())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
