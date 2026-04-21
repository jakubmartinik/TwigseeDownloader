# Rohlik Voice Assistant — HA Custom Component

Custom Home Assistant conversation agent that connects **Claude AI** with your
**Rohlik MCP server**, so you can shop by voice through your HA voice pipeline.

---

## How it works

```
ATOM Echo (mic) → Whisper (STT) → Claude API + Rohlik MCP → Piper (TTS) → speaker
```

Claude receives your speech as text, calls your Rohlik MCP server for shopping
actions, and speaks the response back via Piper TTS.

---

## Installation

### 1. Copy the custom component

```bash
# On your Pi, from your HA config directory:
cp -r rohlik_conversation /config/custom_components/
```

Or if using Samba/SMB: copy the `rohlik_conversation/` folder into
`config/custom_components/` on your Pi.

### 2. Restart Home Assistant

Settings → System → Restart

### 3. Add the integration

Settings → Devices & Services → Add Integration → search **"Rohlik Voice Assistant"**

Fill in:
- **Anthropic API Key** — from https://console.anthropic.com/settings/keys
- **Rohlik MCP URL** — your Cloudflare tunnel URL (already pre-filled)
- **System Prompt** — customize the assistant personality (optional)

### 4. Install STT and TTS add-ons

In Add-on Store, install:
- **Whisper** — use `small` model to keep Pi load manageable
- **Piper** — pick `cs_CZ` for Czech or `en_GB`/`en_US` for English

Start both add-ons.

### 5. Create the voice pipeline

Settings → Voice Assistants → Add Assistant:

| Setting | Value |
|---|---|
| Speech-to-text | Whisper (local) |
| Conversation agent | **Rohlik Voice Assistant** ← your new agent |
| Text-to-speech | Piper (local) |
| Wake word | Hey Jarvis (or any) |

### 6. Flash ATOM Echo

- Buy M5Stack ATOM Echo (~$13 on AliExpress/TME)
- In HA go to Settings → Devices → Add Device → ESPHome
- Use the "Ready-made Projects" page to flash voice assistant firmware
- Assign it to the pipeline from step 5

---

## Usage examples

**Shopping:**
> "Hey Jarvis, add milk to my Rohlik cart"
> "Hey Jarvis, what's in my Rohlik cart?"
> "Hey Jarvis, search for Greek yogurt on Rohlik"
> "Hey Jarvis, what are today's discounts on Rohlik?"

**Smart home:**
> "Hey Jarvis, turn off the living room lights"
> "Hey Jarvis, what's the temperature in the bedroom?"

---

## Tips

### Two wake words = two pipelines
HA supports mapping two wake words to separate pipelines. You can have:
- **"Hey Jarvis"** → fast local Assist pipeline for simple device commands
- **"Hey Claude"** → this Rohlik pipeline for shopping + complex requests

### Pi load
If the Pi struggles with Whisper, switch STT to **Nabu Casa** (HA Cloud ~$7/mo).
Only the conversation call hits the Claude API — STT/TTS stay local.

### Conversation history
The agent keeps the last 20 messages per conversation, so you can say:
> "add that to my cart" after asking about a product.

### Costs
Claude API costs ~$0.003 per voice interaction. For typical household use
(10–20 voice requests/day) expect roughly **$1–2/month**.

---

## File structure

```
custom_components/
└── rohlik_conversation/
    ├── __init__.py        # Integration setup
    ├── manifest.json      # HA integration metadata
    ├── config_flow.py     # UI configuration flow
    ├── conversation.py    # The conversation agent (main logic)
    └── strings.json       # UI labels
```
