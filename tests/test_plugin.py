"""The plugin's `.mcp.json` — also the server's shared `--mcp-config` — names every provider the motor serves."""
from __future__ import annotations

import json
from pathlib import Path

from motor.providers import PROVIDERS

ROOT = Path(__file__).resolve().parent.parent


def test_every_provider_is_in_the_plugin():
    servers = json.loads((ROOT / "plugin" / ".mcp.json").read_text())["mcpServers"]
    assert sorted(servers) == sorted(p.name for p in PROVIDERS)
    for name, server in servers.items():
        assert server == {
            "type": "http",
            "url": "${MOTOR_URL:-https://motor.gang.su}/" + name,
            "headers": {"Authorization": "Bearer ${MOTOR_TOKEN}"},
        }


def test_marketplace_points_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [(plugin["name"], "./plugin")]
