"""Long-poll monitor: getUpdates main loop (mirrors TypeScript monitor.ts)."""
import asyncio
import time

from src.channel_runtime_impl import build_default_channel_runtime
from src.api.client import get_updates
from src.api.config_cache import WeixinConfigManager
from src.api.session_guard import SESSION_EXPIRED_ERRCODE
from src.messaging.process_message import process_one_message
from src.storage.sync_buf import get_sync_buf_file_path, load_get_updates_buf, save_get_updates_buf
from src.util.logger import logger

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_MS = 30_000
RETRY_DELAY_MS = 2_000


async def monitor_weixin_provider(opts: dict) -> None:
    """Long-poll getUpdates loop: getUpdates → normalize → dispatch.

    Runs until abort.
    """
    base_url = opts["base_url"]
    cdn_base_url = opts["cdn_base_url"]
    token = opts.get("token")
    account_id = opts["account_id"]
    config = opts.get("config", {})
    abort_signal = opts.get("abort_signal")
    long_poll_timeout_ms = opts.get("long_poll_timeout_ms", DEFAULT_LONG_POLL_TIMEOUT_MS)
    set_status = opts.get("set_status")
    runtime = opts.get("runtime", {})
    channel_runtime = opts.get("channel_runtime") or build_default_channel_runtime()
    log = runtime.get("log", lambda x: None)
    err_log = runtime.get("error", lambda x: log(x))

    a_log = logger.withAccount(account_id)

    log(f"[monitor] weixin monitor started ({base_url}, account={account_id})")
    a_log.info(f"[monitor] Monitor started: baseUrl={base_url} timeoutMs={long_poll_timeout_ms}")

    sync_file_path = get_sync_buf_file_path(account_id)
    previous_buf = load_get_updates_buf(sync_file_path)
    get_updates_buf = previous_buf or ""

    if previous_buf:
        log(f"[weixin] resuming from previous sync buf ({len(get_updates_buf)} bytes)")
        a_log.debug(f"Using previous get_updates_buf ({len(get_updates_buf)} bytes)")
    else:
        log("[weixin] no previous sync buf, starting fresh")
        a_log.info("No previous get_updates_buf found, starting fresh")

    config_manager = WeixinConfigManager({"base_url": base_url, "token": token}, log)

    next_timeout_ms = long_poll_timeout_ms
    consecutive_failures = 0

    while not (abort_signal and abort_signal.aborted):
        try:
            a_log.debug(f"getUpdates: get_updates_buf={get_updates_buf[:50]}..., timeoutMs={next_timeout_ms}")
            resp = await get_updates(
                base_url=base_url,
                token=token,
                get_updates_buf=get_updates_buf,
                timeout_ms=next_timeout_ms,
            )
            a_log.debug(
                f"getUpdates response: ret={resp.ret}, msgs={len(resp.msgs or [])}, "
                f"get_updates_buf_length={len(resp.get_updates_buf or '')}"
            )

            if resp.longpolling_timeout_ms and resp.longpolling_timeout_ms > 0:
                next_timeout_ms = resp.longpolling_timeout_ms
                a_log.debug(f"Updated next poll timeout: {next_timeout_ms}ms")

            is_api_error = (resp.ret is not None and resp.ret != 0) or (resp.errcode is not None and resp.errcode != 0)

            if is_api_error:
                is_session_expired = (
                    resp.errcode == SESSION_EXPIRED_ERRCODE or resp.ret == SESSION_EXPIRED_ERRCODE
                )

                if is_session_expired:
                    from src.api.session_guard import pause_session, get_remaining_pause_ms

                    pause_session(account_id)
                    pause_ms = get_remaining_pause_ms(account_id)
                    err_log(
                        f"weixin getUpdates: session expired (errcode {SESSION_EXPIRED_ERRCODE}), "
                        f"pausing bot for {int(pause_ms / 60_000)} min"
                    )
                    a_log.error(
                        f"getUpdates: session expired (errcode={resp.errcode} ret={resp.ret}), "
                        f"pausing all requests for {int(pause_ms / 60_000)} min"
                    )
                    consecutive_failures = 0
                    await _sleep(pause_ms, abort_signal)
                    continue

                consecutive_failures += 1
                err_log(
                    f"weixin getUpdates failed: ret={resp.ret} errcode={resp.errcode} "
                    f"errmsg={resp.errmsg or ''} ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                )
                a_log.error(
                    f"getUpdates failed: ret={resp.ret} errcode={resp.errcode} "
                    f"errmsg={resp.errmsg} response={resp}"
                )

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    err_log(f"weixin getUpdates: {MAX_CONSECUTIVE_FAILURES} consecutive failures, backing off 30s")
                    a_log.error(f"getUpdates: {MAX_CONSECUTIVE_FAILURES} consecutive failures, backing off 30s")
                    consecutive_failures = 0
                    await _sleep(BACKOFF_DELAY_MS, abort_signal)
                else:
                    await _sleep(RETRY_DELAY_MS, abort_signal)
                continue

            consecutive_failures = 0
            set_status and set_status({"accountId": account_id, "lastEventAt": int(time.time() * 1000)})

            if resp.get_updates_buf and resp.get_updates_buf != "":
                save_get_updates_buf(sync_file_path, resp.get_updates_buf)
                get_updates_buf = resp.get_updates_buf
                a_log.debug(f"Saved new get_updates_buf ({len(get_updates_buf)} bytes)")

            for full in resp.msgs or []:
                a_log.info(
                    f"inbound message: from={full.from_user_id} "
                    f"types={','.join(str(i.type) for i in full.item_list or []) or 'none'}"
                )
                log(
                    f"[weixin] inbound message: account={account_id} "
                    f"from={full.from_user_id or ''} "
                    f"context={bool(full.context_token)} "
                    f"items={len(full.item_list or [])}"
                )

                now_ms = int(time.time() * 1000)
                set_status and set_status({"accountId": account_id, "lastEventAt": now_ms, "lastInboundAt": now_ms})

                from_user_id = full.from_user_id or ""
                cached_config = await config_manager.get_for_user(from_user_id, full.context_token)

                await process_one_message(full, {
                    "account_id": account_id,
                    "config": config,
                    "channel_runtime": channel_runtime,
                    "base_url": base_url,
                    "cdn_base_url": cdn_base_url,
                    "token": token,
                    "typing_ticket": cached_config.typing_ticket,
                    "log": log,
                    "err_log": err_log,
                })

        except asyncio.CancelledError:
            a_log.info("Monitor stopped (aborted)")
            return
        except Exception as err:
            if abort_signal and abort_signal.aborted:
                a_log.info("Monitor stopped (aborted)")
                return
            consecutive_failures += 1
            err_log(f"weixin getUpdates error ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {err}")
            a_log.error(f"getUpdates error: {err}")

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                err_log(f"weixin getUpdates: {MAX_CONSECUTIVE_FAILURES} consecutive failures, backing off 30s")
                a_log.error(f"getUpdates: {MAX_CONSECUTIVE_FAILURES} consecutive failures, backing off 30s")
                consecutive_failures = 0
                await _sleep(BACKOFF_DELAY_MS, abort_signal)
            else:
                await _sleep(RETRY_DELAY_MS, abort_signal)

    a_log.info("Monitor ended")


async def _sleep(ms: int, signal=None) -> None:
    """Sleep with abort support."""
    await asyncio.sleep(ms / 1000)
