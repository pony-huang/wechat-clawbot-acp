import asyncio
from types import SimpleNamespace

import pytest

from src import cli


class TestCliBridgeStartup:
    async def test_spawn_external_agent_initializes_acp_bridge(self, monkeypatch):
        captured = {}

        async def fake_connect_external_agent(command, args, cwd=None, env=None):
            captured["command"] = command
            captured["args"] = args
            captured["cwd"] = cwd
            captured["env"] = env
            return SimpleNamespace(), SimpleNamespace(returncode=None)

        monkeypatch.setattr("src.agent.agent.connect_external_agent", fake_connect_external_agent)

        proc = await cli.spawn_external_agent(
            "Claude Code",
            {
                "command": "npx.cmd",
                "args": ["@zed-industries/claude-code-acp@latest"],
                "env": {"FOO": "bar"},
            },
            cwd="E:/workplace/wechat-clawbot-acp",
        )

        assert proc.returncode is None
        assert captured["command"] == "npx.cmd"
        assert captured["args"] == ["@zed-industries/claude-code-acp@latest"]
        assert captured["cwd"] == "E:/workplace/wechat-clawbot-acp"
        assert captured["env"]["FOO"] == "bar"

    async def test_main_async_stops_before_wechat_when_acp_start_fails(self, monkeypatch):
        async def fake_spawn_external_agent(*args, **kwargs):
            raise RuntimeError("acp failed")

        ensure_called = False

        async def fake_ensure_weixin_login(*args, **kwargs):
            nonlocal ensure_called
            ensure_called = True

        monkeypatch.setattr(cli, "spawn_external_agent", fake_spawn_external_agent)
        monkeypatch.setattr(cli, "ensure_weixin_login", fake_ensure_weixin_login)
        monkeypatch.setattr(cli, "load_agents_config", lambda *_: {"agents": {"Claude Code": {"command": "npx.cmd"}}})

        with pytest.raises(RuntimeError, match="acp failed"):
            await cli.main_async(
                SimpleNamespace(
                    config=None,
                    list_agents=False,
                    agent="Claude Code",
                    verbose=False,
                    cwd=None,
                )
            )

        assert ensure_called is False

    async def test_start_cli_monitors_injects_default_channel_runtime(self, monkeypatch):
        captured = {}

        async def fake_monitor_weixin_provider(opts):
            captured["channel_runtime"] = opts["channel_runtime"]

        monkeypatch.setattr(cli, "_load_logged_in_accounts", lambda: [("acc-1", {"token": "t1", "baseUrl": "https://example.test"})])
        monkeypatch.setattr("src.monitor.monitor.monitor_weixin_provider", fake_monitor_weixin_provider)

        tasks = await cli.start_cli_monitors()
        await asyncio.gather(*tasks)

        assert captured["channel_runtime"]
        assert "reply" in captured["channel_runtime"]
        assert "routing" in captured["channel_runtime"]
