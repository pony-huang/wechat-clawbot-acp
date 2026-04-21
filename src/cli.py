"""CLI entry point for wechat-clawbot-agent.

Supports running the WeChat ACP bridge with configurable external ACP agents.

Usage:
    wca --agent "claude-acp"
    wca --agent "gemini"
    wca --agent "github-copilot-cli"
    wca --list-agents
    wca --cwd /path/to/dir
"""
import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from src.channel_runtime_impl import build_default_channel_runtime
from src.api.client import get_updates
from src.api.session_guard import SESSION_EXPIRED_ERRCODE
from src.auth.accounts import (
    DEFAULT_BASE_URL,
    clear_stale_accounts_for_user_id,
    list_indexed_weixin_account_ids,
    load_weixin_account,
    normalize_account_id_filesystem as normalize_account_id,
    register_weixin_account_id,
    save_weixin_account,
)
from src.auth.login_qr import (
    DEFAULT_ILINK_BOT_TYPE,
    get_qr_code_ascii,
    start_weixin_login_with_qr,
    wait_for_weixin_login,
)
from src.storage.state_dir import resolve_state_dir
from src.util.logger import logger


DEFAULT_AGENTS_CONFIG = Path(__file__).parent / "config" / "agents.json"
DEFAULT_AGENT_ID = "claude-acp"


def _load_json_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_legacy_agents(agents: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    is_windows = platform.system() == "Windows"
    for agent_id, agent in agents.items():
        record = dict(agent)
        command = record.get("command", "")
        if is_windows and command == "npx":
            record["command"] = "npx.cmd"
        elif not is_windows and command == "npx.cmd":
            record["command"] = "npx"
        record.setdefault("id", agent_id)
        record.setdefault("name", agent_id)
        normalized[agent_id] = record
    return normalized


def _normalize_registry_agents(agents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for agent in agents:
        agent_id = str(agent.get("id", "")).strip()
        if not agent_id:
            continue
        normalized[agent_id] = agent
    return normalized


def load_agents_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Load agent configuration from JSON file."""
    path = config_path or DEFAULT_AGENTS_CONFIG
    if not path.exists():
        return {"agents": {}}

    config = _load_json_file(path)
    agents = config.get("agents")
    if isinstance(agents, list):
        return {**config, "agents": _normalize_registry_agents(agents)}
    if isinstance(agents, dict):
        return {**config, "agents": _normalize_legacy_agents(agents)}
    return {"agents": {}}


def list_available_agents(config_path: Optional[Path] = None) -> list[str]:
    """Get the list of available agent ids."""
    config = load_agents_config(config_path)
    return list(config.get("agents", {}).keys())


def list_available_agent_entries(config_path: Optional[Path] = None) -> list[tuple[str, str]]:
    """Get available agent ids with display names."""
    config = load_agents_config(config_path)
    entries: list[tuple[str, str]] = []
    for agent_id, agent in config.get("agents", {}).items():
        entries.append((agent_id, str(agent.get("name", agent_id))))
    return entries


def _detect_platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in {"amd64", "x86_64", "x64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "aarch64"
    else:
        raise RuntimeError(f"Unsupported architecture: {platform.machine()}")

    if system == "darwin":
        return f"darwin-{arch}"
    if system == "linux":
        return f"linux-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    raise RuntimeError(f"Unsupported operating system: {platform.system()}")


def _normalize_spawn_command(command: str) -> str:
    is_windows = platform.system() == "Windows"
    if is_windows and command == "npx":
        return "npx.cmd"
    if is_windows and command == "npm":
        return "npm.cmd"
    if is_windows and command == "uvx":
        return "uvx.exe"
    if not is_windows and command == "npx.cmd":
        return "npx"
    if not is_windows and command == "npm.cmd":
        return "npm"
    return command


def _preferred_distribution(agent_config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    distribution = agent_config.get("distribution")
    if not isinstance(distribution, dict):
        if "command" in agent_config:
            return "legacy", agent_config
        raise RuntimeError(f"Agent '{agent_config.get('id', 'unknown')}' has no distribution")

    for kind in ("npx", "uvx", "binary"):
        value = distribution.get(kind)
        if isinstance(value, dict):
            return kind, value
    raise RuntimeError(f"Agent '{agent_config.get('id', 'unknown')}' has no supported distribution")


def _normalize_binary_relative_path(command: str) -> Path:
    cleaned = command.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    while cleaned.startswith("/"):
        cleaned = cleaned[1:]
    return Path(*[part for part in cleaned.split("/") if part and part != "."])


def _binary_install_root(agent_id: str, version: str, platform_key: str) -> Path:
    return Path(resolve_state_dir()) / "agents" / agent_id / version / platform_key


def _expected_binary_command_path(agent_id: str, version: str, platform_key: str, command: str) -> Path:
    return _binary_install_root(agent_id, version, platform_key) / _normalize_binary_relative_path(command)


def _binary_archive_name(url: str) -> str:
    path = urlparse(url).path
    name = Path(path).name
    return name or "agent-archive"


def _split_package_spec(package_spec: str) -> tuple[str, str]:
    package_spec = package_spec.strip()
    if not package_spec:
        raise RuntimeError("Package spec cannot be empty")

    if package_spec.startswith("@"):
        match = re.match(r"^(@[^/]+/[^@]+)(?:@(.+))?$", package_spec)
        if not match:
            raise RuntimeError(f"Unsupported package spec: {package_spec}")
        return match.group(1), match.group(2) or "latest"

    if "@" in package_spec:
        name, version = package_spec.rsplit("@", 1)
        return name, version or "latest"
    return package_spec, "latest"


def _sanitize_package_name(package_name: str) -> str:
    return package_name.replace("@", "").replace("/", "__")


async def _run_command_checked(
    command: str,
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    log_prefix: str = "[cli]",
) -> tuple[int, str, str]:
    logger.info(f"{log_prefix} Running command: {command} {' '.join(args)}")
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    proc = await asyncio.create_subprocess_exec(
        command,
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _drain_stream(stream: asyncio.StreamReader | None, level: str) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if not text:
                continue
            if level == "error":
                stderr_chunks.append(text)
            else:
                stdout_chunks.append(text)
            if level == "error":
                logger.error(f"{log_prefix} {text}")
            else:
                logger.info(f"{log_prefix} {text}")

    await asyncio.gather(
        _drain_stream(proc.stdout, "info"),
        _drain_stream(proc.stderr, "error"),
    )
    return_code = await proc.wait()
    if return_code != 0:
        raise RuntimeError(f"Command failed ({return_code}): {command} {' '.join(args)}")
    logger.info(f"{log_prefix} Command finished successfully")
    return return_code, "\n".join(stdout_chunks), "\n".join(stderr_chunks)


async def _is_npx_package_installed(package_name: str) -> bool:
    npm_command = _normalize_spawn_command("npm")
    proc = await asyncio.create_subprocess_exec(
        npm_command,
        "list",
        "-g",
        package_name,
        "--depth=0",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    return await proc.wait() == 0


async def _ensure_npx_agent_installed(
    agent_id: str,
    agent_config: dict[str, Any],
    package_spec: str,
) -> None:
    package_name, version = _split_package_spec(package_spec)
    if not await _is_npx_package_installed(package_name):
        npm_command = _normalize_spawn_command("npm")
        logger.info(f"[cli] Installing npx agent: {agent_config.get('name', agent_id)} ({agent_id})")
        logger.info(f"[cli] Package: {package_spec}")
        await _run_command_checked(
            npm_command,
            ["install", "-g", package_spec],
            env=os.environ.copy(),
            log_prefix=f"[cli][install][{agent_id}]",
        )
        logger.info(f"[cli] Installed npx agent: {agent_config.get('name', agent_id)} ({agent_id})")
    else:
        logger.info(f"[cli] Using installed npx agent: {agent_config.get('name', agent_id)} ({agent_id})")


def _extract_archive(archive_path: Path, destination: Path) -> None:
    lower_name = archive_path.name.lower()
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(destination)
        return
    if any(lower_name.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar")):
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(destination)
        return
    raise RuntimeError(f"Unsupported archive format: {archive_path.name}")


def _ensure_executable(path: Path) -> None:
    if platform.system() == "Windows" or not path.exists():
        return
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR)


async def _download_binary_archive(url: str, destination: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(
                    f"Download failed: status={response.status} url={url} body={body[:200]}"
                )
            destination.write_bytes(await response.read())


async def _ensure_binary_agent_installed(
    agent_id: str,
    agent_config: dict[str, Any],
    binary_distribution: dict[str, Any],
) -> tuple[str, list[str], dict[str, str]]:
    version = str(agent_config.get("version", "unknown")).strip() or "unknown"
    platform_key = _detect_platform_key()
    platform_distribution = binary_distribution.get(platform_key)
    if not isinstance(platform_distribution, dict):
        raise RuntimeError(f"Agent '{agent_id}' does not support platform '{platform_key}'")

    command_rel = str(platform_distribution.get("cmd", "")).strip()
    if not command_rel:
        raise RuntimeError(f"Agent '{agent_id}' binary distribution for '{platform_key}' is missing 'cmd'")

    install_root = _binary_install_root(agent_id, version, platform_key)
    command_path = _expected_binary_command_path(agent_id, version, platform_key, command_rel)
    if command_path.exists():
        _ensure_executable(command_path)
        return str(command_path), list(platform_distribution.get("args", [])), dict(platform_distribution.get("env", {}))

    archive_url = str(platform_distribution.get("archive", "")).strip()
    if not archive_url:
        raise RuntimeError(
            f"Agent '{agent_id}' binary distribution for '{platform_key}' is missing 'archive'"
        )

    install_root.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f"{agent_id}-{version}-", dir=str(install_root.parent))
    )
    archive_path = temp_dir / _binary_archive_name(archive_url)

    try:
        logger.info(f"[cli] Installing binary agent: {agent_config.get('name', agent_id)} ({agent_id})")
        logger.info(f"[cli] Downloading archive: {archive_url}")
        await _download_binary_archive(archive_url, archive_path)
        _extract_archive(archive_path, temp_dir)

        extracted_command = temp_dir / _normalize_binary_relative_path(command_rel)
        if not extracted_command.exists():
            raise RuntimeError(
                f"Installed archive for agent '{agent_id}' did not contain expected command '{command_rel}'"
            )

        if install_root.exists():
            shutil.rmtree(install_root)
        shutil.move(str(temp_dir), str(install_root))
        command_path = install_root / _normalize_binary_relative_path(command_rel)
        _ensure_executable(command_path)
        logger.info(f"[cli] Installed binary agent: {agent_config.get('name', agent_id)} ({agent_id})")
        return str(command_path), list(platform_distribution.get("args", [])), dict(platform_distribution.get("env", {}))
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def resolve_agent_launch_config(agent_id: str, agent_config: dict[str, Any]) -> dict[str, Any]:
    distribution_kind, distribution_value = _preferred_distribution(agent_config)
    display_name = str(agent_config.get("name", agent_id))

    if distribution_kind == "legacy":
        return {
            "agent_id": agent_id,
            "display_name": display_name,
            "distribution": distribution_kind,
            "command": _normalize_spawn_command(str(agent_config.get("command", "")).strip()),
            "args": list(agent_config.get("args", [])),
            "env": dict(agent_config.get("env", {})),
        }

    if distribution_kind == "binary":
        command, args, env = await _ensure_binary_agent_installed(
            agent_id,
            agent_config,
            distribution_value,
        )
        return {
            "agent_id": agent_id,
            "display_name": display_name,
            "distribution": distribution_kind,
            "command": command,
            "args": args,
            "env": env,
        }

    package = str(distribution_value.get("package", "")).strip()
    if not package:
        raise RuntimeError(
            f"Agent '{agent_id}' distribution '{distribution_kind}' is missing 'package'"
        )
    if distribution_kind == "npx":
        await _ensure_npx_agent_installed(agent_id, agent_config, package)
        package_name, _version = _split_package_spec(package)
        command = _normalize_spawn_command("npx")
    else:
        command = _normalize_spawn_command("uvx")
    return {
        "agent_id": agent_id,
        "display_name": display_name,
        "distribution": distribution_kind,
        "command": command,
        "args": (
            ["--no-install", package_name] if distribution_kind == "npx" else [package]
        ) + list(distribution_value.get("args", [])),
        "env": dict(distribution_value.get("env", {})),
    }


async def validate_weixin_account(account_id: str) -> tuple[bool, str]:
    """Validate a stored Weixin account by probing the upstream session."""
    account_data = load_weixin_account(account_id)
    if not account_data:
        return False, "missing-account"

    token = str(account_data.get("token", "") or "").strip()
    if not token:
        return False, "missing-token"

    base_url = str(account_data.get("baseUrl", "") or "").strip() or DEFAULT_BASE_URL

    try:
        resp = await get_updates(
            base_url=base_url,
            token=token,
            timeout_ms=1_000,
        )
    except Exception as err:
        raise RuntimeError(f"微信登录态校验失败(account={account_id}): {err}") from err

    if resp.errcode == SESSION_EXPIRED_ERRCODE or resp.ret == SESSION_EXPIRED_ERRCODE:
        return False, "expired"
    if (resp.ret or 0) != 0:
        raise RuntimeError(
            f"微信登录态校验失败(account={account_id}): "
            f"ret={resp.ret} errcode={resp.errcode} errmsg={resp.errmsg}"
        )

    return True, "ok"


async def perform_weixin_qr_login(
    account_id: str | None = None,
    *,
    verbose: bool = False,
) -> str:
    """Run an interactive QR login flow and persist the resulting account."""
    saved_account = load_weixin_account(account_id) if account_id else None
    api_base_url = str(saved_account.get("baseUrl", "") or "").strip() if saved_account else ""

    start_result = await start_weixin_login_with_qr(
        account_id=account_id,
        api_base_url=api_base_url or DEFAULT_BASE_URL,
        bot_type=DEFAULT_ILINK_BOT_TYPE,
        force=bool(account_id),
        verbose=verbose,
    )

    qrcode_url = start_result.get("qrcode_url")
    if not qrcode_url:
        raise RuntimeError(start_result.get("message", "获取二维码失败"))

    logger.info("使用微信扫描以下二维码，以完成连接：")
    try:
        qr_ascii = await get_qr_code_ascii(qrcode_url, scale=2)
    except Exception as err:
        logger.warn(f"[cli] QR render failed, falling back to raw content: {err}")
        qr_ascii = None
    if qr_ascii:
        logger.info("\n" + qr_ascii)
        logger.info("如果二维码未能成功展示，请用浏览器打开以下链接扫码:")
        logger.info(qrcode_url)
    else:
        logger.info("二维码渲染失败，请用浏览器打开以下链接扫码:")
        logger.info(qrcode_url)

    logger.info("等待连接结果...")
    wait_result = await wait_for_weixin_login(
        session_key=start_result["session_key"],
        api_base_url=api_base_url or DEFAULT_BASE_URL,
        timeout_ms=480_000,
        verbose=verbose,
        bot_type=DEFAULT_ILINK_BOT_TYPE,
    )

    if not (wait_result.get("connected") and wait_result.get("bot_token") and wait_result.get("account_id")):
        raise RuntimeError(wait_result.get("message", "登录失败"))

    normalized_id = normalize_account_id(wait_result["account_id"])
    save_weixin_account(normalized_id, {
        "token": wait_result["bot_token"],
        "base_url": wait_result.get("base_url"),
        "user_id": wait_result.get("user_id"),
    })
    register_weixin_account_id(normalized_id)
    if wait_result.get("user_id"):
        clear_stale_accounts_for_user_id(normalized_id, wait_result["user_id"])

    logger.info(f"\n✅ 与微信连接成功：{normalized_id}\n")
    return normalized_id


async def ensure_weixin_login(verbose: bool = False, force_login: bool = False) -> None:
    """Ensure at least one Weixin account is present and still logged in."""
    if force_login:
        logger.info("Force login flag set, initiating QR login...")
        await perform_weixin_qr_login(verbose=verbose)
        return

    account_ids = list_indexed_weixin_account_ids()
    if not account_ids:
        logger.info("No logged-in WeChat account detected, initiating login...")
        logger.info("Wechat account not")
        await perform_weixin_qr_login(verbose=verbose)
        return

    for account_id in account_ids:
        is_valid, reason = await validate_weixin_account(account_id)
        if is_valid:
            logger.info(f"[cli] Wechat account has been Login：{account_id}")
            continue

        logger.info(f"[cli] Wechat account is invalid：{account_id} ({reason})")
        await perform_weixin_qr_login(account_id=account_id, verbose=verbose)


def _cli_runtime_log(message: str) -> None:
    logger.info(message)


def _cli_runtime_error(message: str) -> None:
    logger.info(f"[weixin] {message}")


def _build_monitor_runtime() -> dict[str, Any]:
    return {
        "log": _cli_runtime_log,
        "error": _cli_runtime_error,
    }


def _load_logged_in_accounts() -> list[tuple[str, dict[str, Any]]]:
    accounts: list[tuple[str, dict[str, Any]]] = []
    for account_id in list_indexed_weixin_account_ids():
        account_data = load_weixin_account(account_id)
        if not account_data:
            continue
        token = str(account_data.get("token", "") or "").strip()
        if not token:
            continue
        accounts.append((account_id, account_data))
    return accounts


async def start_cli_monitors() -> list[asyncio.Task]:
    """Start background Weixin monitors so inbound receipt is visible in the terminal."""
    from src.polling.monitor import monitor_weixin_provider

    tasks: list[asyncio.Task] = []
    for account_id, account_data in _load_logged_in_accounts():
        base_url = str(account_data.get("baseUrl", "") or "").strip() or DEFAULT_BASE_URL
        token = str(account_data.get("token", "") or "").strip()
        logger.info(f"[cli] Starting WeChat monitor: {account_id} ({base_url})")
        task = asyncio.create_task(
            monitor_weixin_provider(
                {
                    "base_url": base_url,
                    "cdn_base_url": "",
                    "token": token,
                    "account_id": account_id,
                    "config": {},
                    "runtime": _build_monitor_runtime(),
                    "channel_runtime": build_default_channel_runtime(),
                }
            ),
            name=f"weixin-monitor:{account_id}",
        )
        tasks.append(task)
    return tasks


async def spawn_external_agent(
    agent_id: str,
    agent_config: dict[str, Any],
    cwd: Optional[str] = None,
) -> asyncio.subprocess.Process:
    """Start and initialize the external ACP agent bridge."""
    from src.agent.agent import connect_external_agent

    launch_config = await resolve_agent_launch_config(agent_id, agent_config)
    command = str(launch_config.get("command", "")).strip()
    args = list(launch_config.get("args", []))
    env = dict(launch_config.get("env", {}))
    display_name = str(launch_config.get("display_name", agent_id))

    if not command:
        logger.error(f"[cli] No command specified for agent: {display_name} ({agent_id})")
        sys.exit(1)

    work_dir = cwd or os.getcwd()

    logger.info(f"[cli] Spawning external agent: {display_name} ({agent_id})")
    logger.info(
        f"[cli] Distribution: {launch_config.get('distribution')} | Command: {command} {' '.join(args)}"
    )
    logger.info(f"[cli] Working directory: {work_dir}")

    full_env = {**os.environ, **env}
    try:
        _conn, proc = await connect_external_agent(
            command,
            args,
            cwd=work_dir,
            env=full_env,
        )
    except Exception as err:
        logger.error(f"[cli] Failed to start ACP client for {display_name} ({agent_id}): {err}")
        raise

    logger.info(f"[cli] ACP client ready for {display_name} ({agent_id})")
    return proc


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="wca",
        description="WeChat ACP Bridge - Connect WeChat to ACP agents",
    )

    parser.add_argument(
        "--agent",
        "-a",
        type=str,
        default=DEFAULT_AGENT_ID,
        help=f"Official agent id to use (default: {DEFAULT_AGENT_ID})",
    )

    parser.add_argument(
        "--list-agents",
        "-l",
        action="store_true",
        help="List available agents and exit",
    )

    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Path to agents.json config file",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="Working directory for the agent (default: current directory)",
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Force re-login with a new QR code",
    )

    return parser


async def main_async(args: argparse.Namespace) -> None:
    """Async main function."""
    config_path = Path(args.config) if args.config else DEFAULT_AGENTS_CONFIG
    agents_config = load_agents_config(config_path)
    agent_proc: asyncio.subprocess.Process | None = None

    if args.list_agents:
        logger.info("Available agents:")
        for agent_id, name in list_available_agent_entries(config_path):
            logger.info(f"  - {agent_id}: {name}")
        return

    agent_id = str(args.agent).strip()
    monitor_tasks: list[asyncio.Task] = []

    try:
        agent_config = agents_config.get("agents", {}).get(agent_id)
        if not agent_config:
            logger.error(f"[cli] Agent '{agent_id}' not found in config")
            logger.error("[cli] Use --list-agents to see available agents")
            sys.exit(1)

        agent_proc = await spawn_external_agent(agent_id, agent_config, cwd=args.cwd)
        await ensure_weixin_login(verbose=args.verbose, force_login=getattr(args, "login", False))
        monitor_tasks = await start_cli_monitors()
        if agent_proc.returncode is None:
            await agent_proc.wait()
        if agent_proc.returncode != 0:
            logger.error(f"[cli] Agent exited with code: {agent_proc.returncode}")
            sys.exit(agent_proc.returncode)
        if monitor_tasks:
            await asyncio.gather(*monitor_tasks)
    finally:
        try:
            from src.agent.agent import clear_bridge_state
        except Exception:
            clear_bridge_state = None  # type: ignore[assignment]
        for task in monitor_tasks:
            task.cancel()
        if monitor_tasks:
            await asyncio.gather(*monitor_tasks, return_exceptions=True)
        if agent_proc is not None and agent_proc.returncode is None:
            agent_proc.terminate()
            await agent_proc.wait()
        if clear_bridge_state:
            clear_bridge_state()


def main() -> None:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("[cli] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[cli] Error: {e}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
