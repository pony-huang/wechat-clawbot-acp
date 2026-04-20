"""CLI entry point for wechat-clawbot-agent.

Supports running the WeChat ACP bridge with configurable external ACP agents.

Usage:
    wca --agent "Claude Code"
    wca --agent "Gemini CLI"
    wca --agent "GitHub Copilot"
    wca --list-agents
    wca --cwd /path/to/dir
"""
import argparse
import asyncio
import sys
import json
import os
from pathlib import Path
from typing import Optional

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
from src.util.logger import logger


# Default agents configuration path
DEFAULT_AGENTS_CONFIG = Path(__file__).parent / "config" / "agents.json"


def load_agents_config(config_path: Optional[Path] = None) -> dict:
    """Load agents configuration from JSON file."""
    path = config_path or DEFAULT_AGENTS_CONFIG
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def list_available_agents(config_path: Optional[Path] = None) -> list[str]:
    """Get list of available agent names."""
    config = load_agents_config(config_path)
    return list(config.get("agents", {}).keys())


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
        logger.info(f"Wechat account not")
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


def _build_monitor_runtime() -> dict:
    return {
        "log": _cli_runtime_log,
        "error": _cli_runtime_error,
    }


def _load_logged_in_accounts() -> list[tuple[str, dict]]:
    accounts: list[tuple[str, dict]] = []
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
    from src.monitor.monitor import monitor_weixin_provider

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
    agent_name: str,
    agent_config: dict,
    cwd: Optional[str] = None,
) -> asyncio.subprocess.Process:
    """Start and initialize the external ACP agent bridge."""
    from src.agent.agent import connect_external_agent

    command = agent_config.get("command")
    args = agent_config.get("args", [])
    env = agent_config.get("env", {})

    if not command:
        logger.error(f"[cli] No command specified for agent: {agent_name}")
        sys.exit(1)

    # Use provided cwd or current directory
    work_dir = cwd or os.getcwd()

    logger.info(f"[cli] Spawning external agent: {agent_name}")
    logger.info(f"[cli] Command: {command} {' '.join(args)}")
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
        logger.error(f"[cli] Failed to start ACP client for {agent_name}: {err}")
        raise

    logger.info(f"[cli] ACP client ready for {agent_name}")
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
        default="Claude Code",
        choices=["Claude Code", "Gemini CLI", "GitHub Copilot", "Qwen Code", "Auggie CLI", "Qoder CLI", "Codex CLI", "OpenCode", "OpenClaw"],
        help="Agent to use (default: Claude Code)",
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
        agents = list_available_agents(config_path)
        logger.info("Available agents:")
        for name in agents:
            logger.info(f"  - {name}")
        return

    agent_name = args.agent
    monitor_tasks: list[asyncio.Task] = []

    try:
        agent_config = agents_config.get("agents", {}).get(agent_name)
        if not agent_config:
            logger.error(f"[cli] Agent '{agent_name}' not found in config")
            logger.error(f"[cli] Use --list-agents to see available agents")
            sys.exit(1)

        agent_proc = await spawn_external_agent(agent_name, agent_config, cwd=args.cwd)
        await ensure_weixin_login(verbose=args.verbose, force_login=args.login)
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
            clear_bridge_state = None  # type: ignore
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

    # Run async main
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("[cli] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[cli] Error: {e}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            import traceback
            traceback.logger.info_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
