"""Config flow for Rohlik Conversation."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY

DOMAIN = "rohlik_conversation"

CONF_ROHLIK_MCP_URL = "rohlik_mcp_url"
CONF_SYSTEM_PROMPT = "system_prompt"
CONF_MONTHLY_CAP_USD = "monthly_cap_usd"

DEFAULT_MCP_URL = "https://home.jakubmartinik.cz/api/webhook/mcp_e4377878d72fd24fafbf866a067f3ae6"

DEFAULT_SYSTEM_PROMPT = """You are a helpful smart home and shopping assistant.
You have access to the user's Home Assistant smart home and their Rohlik.cz grocery account.

For shopping requests (add to cart, search products, check cart, place order),
use the Rohlik MCP tools available to you.

For smart home requests (lights, temperature, sensors etc.),
use the Home Assistant context provided.

Always respond in the same language the user speaks.
Keep responses short and clear — this is a voice assistant.
"""


class RohlikConversationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rohlik Conversation."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title="Rohlik Voice Assistant",
                data=user_input,
            )

        schema = vol.Schema({
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_ROHLIK_MCP_URL, default=DEFAULT_MCP_URL): str,
            vol.Required(CONF_MONTHLY_CAP_USD, default=5.0): vol.Coerce(float),
            vol.Optional(CONF_SYSTEM_PROMPT, default=DEFAULT_SYSTEM_PROMPT): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
