import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import cli


class TestCliBridgeStartup:
    async def test_resolve_agent_launch_config_prefers_npx_over_binary(self, monkeypatch):
        monkeypatch.setattr(
            cli,
            "_ensure_npx_agent_installed",
            lambda *args, **kwargs: asyncio.sleep(0),
        )
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        launch = await cli.resolve_agent_launch_config(
            "codex-acp",
            {
                "id": "codex-acp",
                "name": "Codex CLI",
                "distribution": {
                    "binary": {
                        "windows-x86_64": {
                            "archive": "https://example.test/codex.zip",
                            "cmd": "./codex-acp.exe",
                        }
                    },
                    "npx": {"package": "@zed-industries/codex-acp@0.11.1"},
                },
            },
        )

        assert launch["distribution"] == "npx"
        assert launch["command"] == "npx.cmd"
        assert launch["args"] == ["--no-install", "@zed-industries/codex-acp"]

    async def test_resolve_agent_launch_config_supports_uvx(self):
        launch = await cli.resolve_agent_launch_config(
            "fast-agent",
            {
                "id": "fast-agent",
                "name": "fast-agent",
                "distribution": {
                    "uvx": {
                        "package": "fast-agent-acp==0.6.19",
                        "args": ["-x"],
                    }
                },
            },
        )

        assert launch["distribution"] == "uvx"
        assert launch["command"] in {"uvx", "uvx.exe"}
        assert launch["args"] == ["fast-agent-acp==0.6.19", "-x"]

    async def test_resolve_agent_launch_config_reuses_installed_binary(self, monkeypatch):
        command_path = Path(__file__).resolve()
        monkeypatch.setattr(cli, "_detect_platform_key", lambda: "windows-x86_64")
        monkeypatch.setattr(
            cli,
            "_expected_binary_command_path",
            lambda *args, **kwargs: command_path,
        )
        monkeypatch.setattr(cli, "_ensure_executable", lambda *_: None)

        launch = await cli.resolve_agent_launch_config(
            "goose",
            {
                "id": "goose",
                "name": "goose",
                "version": "1.31.1",
                "distribution": {
                    "binary": {
                        "windows-x86_64": {
                            "archive": "https://example.test/goose.zip",
                            "cmd": "./goose-package\\goose.exe",
                            "args": ["acp"],
                        }
                    }
                },
            },
        )

        assert launch["distribution"] == "binary"
        assert launch["command"] == str(command_path)
        assert launch["args"] == ["acp"]

    async def test_spawn_external_agent_initializes_acp_bridge(self, monkeypatch):
        captured = {}

        async def fake_connect_external_agent(command, args, cwd=None, env=None):
            captured["command"] = command
            captured["args"] = args
            captured["cwd"] = cwd
            captured["env"] = env
            return SimpleNamespace(), SimpleNamespace(returncode=None)

        monkeypatch.setattr("src.agent.agent.connect_external_agent", fake_connect_external_agent)
        monkeypatch.setattr(
            cli,
            "_ensure_npx_agent_installed",
            lambda *args, **kwargs: asyncio.sleep(0),
        )
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        proc = await cli.spawn_external_agent(
            "claude-acp",
            {
                "id": "claude-acp",
                "name": "Claude Agent",
                "distribution": {
                    "npx": {
                        "package": "@agentclientprotocol/claude-agent-acp@0.30.0",
                    }
                },
                "env": {"FOO": "bar"},
            },
            cwd="E:/workplace/wechat-clawbot-acp",
        )

        assert proc.returncode is None
        assert captured["command"] == "npx.cmd"
        assert captured["args"] == ["--no-install", "@agentclientprotocol/claude-agent-acp"]
        assert captured["cwd"] == "E:/workplace/wechat-clawbot-acp"

    async def test_main_async_stops_before_wechat_when_acp_start_fails(self, monkeypatch):
        async def fake_spawn_external_agent(*args, **kwargs):
            raise RuntimeError("acp failed")

        ensure_called = False

        async def fake_ensure_weixin_login(*args, **kwargs):
            nonlocal ensure_called
            ensure_called = True

        monkeypatch.setattr(cli, "spawn_external_agent", fake_spawn_external_agent)
        monkeypatch.setattr(cli, "ensure_weixin_login", fake_ensure_weixin_login)
        monkeypatch.setattr(
            cli,
            "load_agents_config",
            lambda *_: {
                "agents": {
                    "claude-acp": {
                        "id": "claude-acp",
                        "name": "Claude Agent",
                        "distribution": {"npx": {"package": "@agentclientprotocol/claude-agent-acp@0.30.0"}},
                    }
                }
            },
        )

        with pytest.raises(RuntimeError, match="acp failed"):
            await cli.main_async(
                SimpleNamespace(
                    config=None,
                    list_agents=False,
                    agent="claude-acp",
                    verbose=False,
                    cwd=None,
                    login=False,
                )
            )

        assert ensure_called is False

    async def test_start_cli_monitors_injects_default_channel_runtime(self, monkeypatch):
        captured = {}

        async def fake_monitor_weixin_provider(opts):
            captured["channel_runtime"] = opts["channel_runtime"]

        monkeypatch.setattr(
            cli,
            "_load_logged_in_accounts",
            lambda: [("acc-1", {"token": "t1", "baseUrl": "https://example.test"})],
        )
        monkeypatch.setattr("src.monitor.monitor.monitor_weixin_provider", fake_monitor_weixin_provider)

        tasks = await cli.start_cli_monitors()
        await asyncio.gather(*tasks)

        assert captured["channel_runtime"]
        assert "reply" in captured["channel_runtime"]
        assert "routing" in captured["channel_runtime"]


class TestCliAgentConfig:
    async def test_ensure_npx_agent_installed_runs_npm_install_when_missing(self, monkeypatch):
        install_calls = []
        monkeypatch.setattr(cli, "_is_npx_package_installed", lambda *args, **kwargs: asyncio.sleep(0, result=False))

        async def fake_run(command, args, cwd=None, env=None, log_prefix=None):
            install_calls.append((command, args, cwd, env, log_prefix))
            return 0, "", ""

        monkeypatch.setattr(cli, "_run_command_checked", fake_run)
        monkeypatch.setattr(cli, "_normalize_spawn_command", lambda command: "npm.cmd" if command == "npm" else command)

        await cli._ensure_npx_agent_installed(
            "claude-acp",
            {"id": "claude-acp", "name": "Claude Agent"},
            "@agentclientprotocol/claude-agent-acp@0.30.0",
        )

        assert len(install_calls) == 1
        assert install_calls[0][0] == "npm.cmd"
        assert install_calls[0][1] == [
            "install",
            "-g",
            "@agentclientprotocol/claude-agent-acp@0.30.0",
        ]
        assert install_calls[0][2] is None
        assert isinstance(install_calls[0][3], dict)
        assert install_calls[0][4] == "[cli][install][claude-acp]"

    async def test_ensure_npx_agent_installed_skips_install_when_present(self, monkeypatch):
        install_calls = []

        monkeypatch.setattr(cli, "_is_npx_package_installed", lambda *args, **kwargs: asyncio.sleep(0, result=True))

        async def fake_run(*args, **kwargs):
            install_calls.append((args, kwargs))
            return 0, "", ""

        monkeypatch.setattr(cli, "_run_command_checked", fake_run)

        await cli._ensure_npx_agent_installed(
            "claude-acp",
            {"id": "claude-acp", "name": "Claude Agent"},
            "@agentclientprotocol/claude-agent-acp@0.30.0",
        )

        assert install_calls == []

    def test_load_agents_config_indexes_registry_agents(self, monkeypatch):
        config_path = Path(__file__).resolve()
        monkeypatch.setattr(
            cli,
            "_load_json_file",
            lambda *_: {
                "version": "1.0.0",
                "agents": [
                    {
                        "id": "amp-acp",
                        "name": "Amp",
                        "distribution": {
                            "binary": {
                                "windows-x86_64": {
                                    "archive": "https://example.test/amp.zip",
                                    "cmd": "amp-acp.exe",
                                }
                            }
                        },
                    },
                    {
                        "id": "gemini",
                        "name": "Gemini CLI",
                        "distribution": {
                            "npx": {
                                "package": "@google/gemini-cli@0.38.2",
                                "args": ["--acp"],
                            }
                        },
                    },
                ],
            },
        )

        config = cli.load_agents_config(config_path)

        assert list(config["agents"].keys()) == ["amp-acp", "gemini"]
        assert config["agents"]["gemini"]["name"] == "Gemini CLI"

    def test_list_available_agent_entries_returns_ids_with_names(self, monkeypatch):
        config_path = Path(__file__).resolve()
        monkeypatch.setattr(
            cli,
            "_load_json_file",
            lambda *_: {
                "agents": [
                    {
                        "id": "claude-acp",
                        "name": "Claude Agent",
                        "distribution": {
                            "npx": {
                                "package": "@agentclientprotocol/claude-agent-acp@0.30.0"
                            }
                        },
                    }
                ]
            },
        )

        assert cli.list_available_agent_entries(config_path) == [("claude-acp", "Claude Agent")]
