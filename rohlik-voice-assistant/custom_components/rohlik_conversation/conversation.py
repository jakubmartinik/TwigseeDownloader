"""Conversation agent platform for Rohlik Voice Assistant."""
from __future__ import annotations

import logging
from typing import Literal
from datetime import datetime
import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationInput, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, entity_registry as er, area_registry as ar
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .config_flow import CONF_ROHLIK_MCP_URL, CONF_SYSTEM_PROMPT, CONF_MONTHLY_CAP_USD

_LOGGER = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"
STORAGE_KEY = "rohlik_conversation_usage"
STORAGE_VERSION = 1

# Sonnet 4.6 pricing per token
COST_PER_INPUT_TOKEN = 3.0 / 1_000_000   # $3 per million
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000  # $15 per million


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation agent from config entry."""
    agent = RohlikConversationAgent(hass, config_entry)
    await agent.async_init_storage()
    async_add_entities([agent])


class RohlikConversationAgent(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Rohlik-aware conversation agent backed by Claude + MCP."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = None
        self._conversations: dict[str, list[dict]] = {}
        self._store: Store | None = None
        # Usage tracking: { "month": "2026-04", "cost_usd": 1.23, "requests": 42 }
        self._usage: dict = {}

    async def async_init_storage(self) -> None:
        """Load persisted usage data from HA storage."""
        self._store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)
        data = await self._store.async_load()
        current_month = datetime.now().strftime("%Y-%m")
        if data and data.get("month") == current_month:
            self._usage = data
        else:
            # New month — reset counter
            self._usage = {"month": current_month, "cost_usd": 0.0, "requests": 0}
            await self._store.async_save(self._usage)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    @property
    def monthly_cost(self) -> float:
        """Return current month's cost in USD."""
        return self._usage.get("cost_usd", 0.0)

    @property
    def monthly_cap(self) -> float:
        """Return configured monthly cap in USD."""
        return self.entry.data.get(CONF_MONTHLY_CAP_USD, 5.0)

    async def async_process(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a sentence from the voice pipeline."""

        # Reset counter if we've rolled into a new month
        current_month = datetime.now().strftime("%Y-%m")
        if self._usage.get("month") != current_month:
            self._usage = {"month": current_month, "cost_usd": 0.0, "requests": 0}

        # Enforce monthly cap BEFORE calling the API
        if self.monthly_cost >= self.monthly_cap:
            _LOGGER.warning(
                "Monthly cap of $%.2f reached (spent $%.2f). Blocking request.",
                self.monthly_cap,
                self.monthly_cost,
            )
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(
                f"Sorry, the monthly API budget of ${self.monthly_cap:.0f} has been reached. "
                "I'll be available again next month."
            )
            return ConversationResult(
                response=intent_response,
                conversation_id=user_input.conversation_id or "default",
            )

        api_key = self.entry.data[CONF_API_KEY]
        mcp_url = self.entry.data[CONF_ROHLIK_MCP_URL]
        system_prompt = self.entry.data.get(CONF_SYSTEM_PROMPT, "")
        conversation_id = user_input.conversation_id or "default"

        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = []
        history = self._conversations[conversation_id]

        ha_context = await self._build_ha_context()
        full_system = f"{system_prompt}\n\n{ha_context}"

        history.append({"role": "user", "content": user_input.text})

        try:
            response_text, input_tokens, output_tokens = await self._call_claude(
                api_key=api_key,
                mcp_url=mcp_url,
                system=full_system,
                messages=history,
            )
            # Track cost
            cost = (input_tokens * COST_PER_INPUT_TOKEN) + (output_tokens * COST_PER_OUTPUT_TOKEN)
            self._usage["cost_usd"] = round(self._usage.get("cost_usd", 0.0) + cost, 6)
            self._usage["requests"] = self._usage.get("requests", 0) + 1
            if self._store:
                await self._store.async_save(self._usage)
            _LOGGER.debug(
                "Request cost: $%.4f | Month total: $%.4f / $%.2f cap (%d requests)",
                cost, self._usage["cost_usd"], self.monthly_cap, self._usage["requests"],
            )
        except Exception as err:
            _LOGGER.error("Error calling Claude API: %s", err)
            response_text = "Sorry, I couldn't process that request right now."

        history.append({"role": "assistant", "content": response_text})
        if len(history) > 20:
            self._conversations[conversation_id] = history[-20:]

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)
        return ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )

    async def _call_claude(
        self,
        api_key: str,
        mcp_url: str,
        system: str,
        messages: list[dict],
    ) -> tuple[str, int, int]:
        """Call Claude API. Returns (response_text, input_tokens, output_tokens)."""
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "mcp-client-2025-04-04",
            "content-type": "application/json",
        }

        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "mcp_servers": [
                {
                    "type": "url",
                    "url": mcp_url,
                    "name": "rohlik",
                }
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Claude API error {resp.status}: {body}")
                data = await resp.json()

        text_parts = [
            block["text"]
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return " ".join(text_parts).strip(), input_tokens, output_tokens

    async def _build_ha_context(self) -> str:
        """Build a concise summary of exposed HA entities for Claude."""
        try:
            entity_reg = er.async_get(self.hass)
            area_reg = ar.async_get(self.hass)

            lines = ["## Home Assistant State\n"]

            # Group exposed entities by area
            areas: dict[str, list[str]] = {}

            for entity in entity_reg.entities.values():
                # Only include entities exposed to voice
                if not entity.options.get("conversation", {}).get("should_expose", False):
                    continue

                state = self.hass.states.get(entity.entity_id)
                if state is None:
                    continue

                area_name = "Unknown area"
                if entity.area_id:
                    area = area_reg.async_get_area(entity.area_id)
                    if area:
                        area_name = area.name

                friendly = state.attributes.get("friendly_name", entity.entity_id)
                state_str = f"{friendly}: {state.state}"

                # Add useful attributes
                if "temperature" in state.attributes:
                    state_str += f" ({state.attributes['temperature']}°C)"
                if "brightness" in state.attributes and state.state == "on":
                    pct = round(state.attributes["brightness"] / 255 * 100)
                    state_str += f" ({pct}% brightness)"

                areas.setdefault(area_name, []).append(state_str)

            for area_name, entities in sorted(areas.items()):
                lines.append(f"**{area_name}:**")
                for e in entities:
                    lines.append(f"  - {e}")

            if len(lines) == 1:
                return "## Home Assistant State\nNo entities currently exposed to voice assistant."

            return "\n".join(lines)

        except Exception as err:
            _LOGGER.warning("Could not build HA context: %s", err)
            return ""
