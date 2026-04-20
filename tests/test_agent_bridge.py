from types import SimpleNamespace
from pathlib import Path
import shutil
import uuid

from acp.helpers import image_block, resource_link_block, text_block, update_agent_message, update_agent_thought
from src.agent.agent import (
    AcpClient,
    WeixinAcpContext,
    clear_bridge_state,
    notify_reply_activity,
    set_active_session_id,
    set_agent_connection,
    set_weixin_context,
)
from src.channel_runtime_impl import build_default_channel_runtime
from src.api.types import MessageItem, MessageItemType, TextItem, WeixinMessage
from src.messaging.process_message import process_one_message


class TestAcpClientSessionUpdate:
    def setup_method(self):
        clear_bridge_state()

    async def test_agent_message_chunk_sends_weixin_text(self, monkeypatch):
        sent_messages = []

        async def fake_send_message_weixin(to, text, opts):
            sent_messages.append((to, text, opts))
            return {"message_id": "m1"}

        monkeypatch.setattr("src.agent.agent.send_message_weixin", fake_send_message_weixin)
        set_weixin_context(
            WeixinAcpContext(
                to_user_id="user-1",
                base_url="https://example.test",
                token="token",
                context_token="ctx-1",
            )
        )

        client = AcpClient()
        await client.session_update("session-1", update_agent_message(text_block("hello from agent")))

        assert sent_messages == [
            (
                "user-1",
                "hello from agent",
                {
                    "base_url": "https://example.test",
                    "token": "token",
                    "context_token": "ctx-1",
                },
            )
        ]

    async def test_agent_thought_chunk_does_not_send_weixin_text(self, monkeypatch):
        sent_messages = []

        async def fake_send_message_weixin(to, text, opts):
            sent_messages.append((to, text, opts))
            return {"message_id": "m2"}

        monkeypatch.setattr("src.agent.agent.send_message_weixin", fake_send_message_weixin)
        set_weixin_context(WeixinAcpContext(to_user_id="user-2", base_url="https://example.test"))

        client = AcpClient()
        await client.session_update("session-1", update_agent_thought(text_block("thinking aloud")))

        assert sent_messages == []

    async def test_non_text_updates_do_not_trigger_weixin_output(self, monkeypatch):
        sent_messages = []

        async def fake_send_message_weixin(to, text, opts):
            sent_messages.append((to, text, opts))
            return {"message_id": "m3"}

        monkeypatch.setattr("src.agent.agent.send_message_weixin", fake_send_message_weixin)
        set_weixin_context(
            WeixinAcpContext(
                to_user_id="user-3",
                base_url="https://example.test",
                token="token",
                typing_ticket="ticket-1",
            )
        )

        client = AcpClient()
        await client.session_update("session-1", update_agent_thought(text_block("thinking")))

        assert sent_messages == []

    async def test_missing_context_skips_outbound_send(self, monkeypatch):
        sent_messages = []

        async def fake_send_message_weixin(to, text, opts):
            sent_messages.append((to, text, opts))
            return {"message_id": "m4"}

        monkeypatch.setattr("src.agent.agent.send_message_weixin", fake_send_message_weixin)

        client = AcpClient()
        await client.session_update("session-1", update_agent_message(text_block("orphaned message")))

        assert sent_messages == []

    async def test_agent_message_chunk_sends_weixin_media(self, monkeypatch):
        downloaded = []
        sent_media = []
        state_dir = Path("build") / "test-state" / f"agent-media-{uuid.uuid4().hex}"
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

        async def fake_download_remote_image_to_temp(url, output_dir):
            downloaded.append((url, output_dir))
            return str(Path(output_dir) / "reply.png")

        async def fake_send_weixin_media_file(file_path, to, text, opts, cdn_base_url):
            sent_media.append((file_path, to, text, opts, cdn_base_url))
            return {"message_id": "media-1"}

        monkeypatch.setattr("src.agent.agent.download_remote_image_to_temp", fake_download_remote_image_to_temp)
        monkeypatch.setattr("src.agent.agent.send_weixin_media_file", fake_send_weixin_media_file)
        set_weixin_context(
            WeixinAcpContext(
                to_user_id="user-media",
                base_url="https://example.test",
                token="token",
                context_token="ctx-media",
                cdn_base_url="https://cdn.example.test",
            )
        )

        client = AcpClient()
        await client.session_update(
            "session-1",
            update_agent_message(
                image_block(
                    data="aGVsbG8=",
                    mime_type="image/png",
                    uri="https://example.test/reply.png",
                )
            ),
        )

        assert downloaded and downloaded[0][0] == "https://example.test/reply.png"
        assert sent_media == [
            (
                str(Path(downloaded[0][1]) / "reply.png"),
                "user-media",
                "",
                {
                    "base_url": "https://example.test",
                    "token": "token",
                    "context_token": "ctx-media",
                },
                "https://cdn.example.test",
            )
        ]

    async def test_media_send_failure_sends_error_notice(self, monkeypatch):
        notices = []
        state_dir = Path("build") / "test-state" / f"agent-media-error-{uuid.uuid4().hex}"
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

        async def fake_send_weixin_media_file(*args, **kwargs):
            raise RuntimeError("upload failed")

        async def fake_send_weixin_error_notice(to, context_token, message, base_url, token=None, err_log=None):
            notices.append((to, context_token, message, base_url, token))

        monkeypatch.setattr("src.agent.agent.send_weixin_media_file", fake_send_weixin_media_file)
        monkeypatch.setattr("src.agent.agent.send_weixin_error_notice", fake_send_weixin_error_notice)
        set_weixin_context(
            WeixinAcpContext(
                to_user_id="user-error",
                base_url="https://example.test",
                token="token",
                context_token="ctx-error",
                cdn_base_url="https://cdn.example.test",
            )
        )

        client = AcpClient()
        await client.session_update(
            "session-1",
            update_agent_message(
                resource_link_block(
                    name="report",
                    uri="E:/tmp/report.pdf",
                    mime_type="application/pdf",
                    title="report title",
                )
            ),
        )

        assert notices == [
            (
                "user-error",
                "ctx-error",
                "抱歉，刚才的回复发送失败了，请稍后重试。",
                "https://example.test",
                "token",
            )
        ]


class TestProcessOneMessageBridge:
    def setup_method(self):
        clear_bridge_state()

    async def test_process_message_prompts_active_acp_session(self, monkeypatch):
        state_dir = Path("build") / "test-state" / f"agent-bridge-{uuid.uuid4().hex}"
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

        prompted = []
        captured_contexts = []

        async def fake_prompt_active_session(prompt_text):
            prompted.append(prompt_text)
            await notify_reply_activity()

        def fake_set_weixin_context(context):
            captured_contexts.append(context)

        def fake_is_user_allowed(user_id, account_id):
            return True  # Allow all users for this test

        monkeypatch.setattr("src.agent.agent.prompt_active_session", fake_prompt_active_session)
        monkeypatch.setattr("src.agent.agent.set_weixin_context", fake_set_weixin_context)
        monkeypatch.setattr("src.auth.pairing.is_user_allowed", fake_is_user_allowed)

        set_agent_connection(SimpleNamespace())
        set_active_session_id("session-1")

        full = WeixinMessage(
            from_user_id="wx-user",
            context_token="ctx-2",
            item_list=[
                MessageItem(
                    type=MessageItemType.TEXT,
                    text_item=TextItem(text="hello from wechat"),
                )
            ],
        )

        await process_one_message(
            full,
            {
                "account_id": "acc-1",
                "channel_runtime": {},
                "base_url": "https://example.test",
                "cdn_base_url": "https://cdn.example.test",
                "token": "token",
                "typing_ticket": "ticket-2",
            },
        )

        assert prompted == ["hello from wechat"]
        assert len(captured_contexts) == 1
        assert captured_contexts[0].to_user_id == "wx-user"
        assert captured_contexts[0].context_token == "ctx-2"
        assert captured_contexts[0].typing_ticket == "ticket-2"

    async def test_unauthorized_sender_skips_acp_prompt(self, monkeypatch):
        prompted = []
        state_dir = Path("build") / "test-state" / f"agent-unauthorized-{uuid.uuid4().hex}"
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

        async def fake_prompt_active_session(prompt_text):
            prompted.append(prompt_text)

        def fake_is_user_allowed(user_id, account_id):
            return False

        monkeypatch.setattr("src.agent.agent.prompt_active_session", fake_prompt_active_session)
        monkeypatch.setattr("src.auth.pairing.is_user_allowed", fake_is_user_allowed)

        set_agent_connection(SimpleNamespace())
        set_active_session_id("session-1")

        full = WeixinMessage(
            from_user_id="blocked-user",
            context_token="ctx-blocked",
            item_list=[
                MessageItem(
                    type=MessageItemType.TEXT,
                    text_item=TextItem(text="hello from blocked user"),
                )
            ],
        )

        await process_one_message(
            full,
            {
                "account_id": "acc-1",
                "channel_runtime": {},
                "base_url": "https://example.test",
                "cdn_base_url": "https://cdn.example.test",
                "token": "token",
            },
        )

        assert prompted == []

class TestReplyDispatcherBridge:
    def setup_method(self):
        clear_bridge_state()

    async def test_runtime_dispatcher_delivers_acp_payloads(self, monkeypatch):
        delivered = []
        runtime = build_default_channel_runtime()

        async def fake_prompt_active_session(prompt_text):
            del prompt_text
            client = AcpClient()
            await client.session_update("session-1", update_agent_message(text_block("via dispatcher")))
            return SimpleNamespace(stop_reason="end_turn")

        monkeypatch.setattr("src.channel_runtime_impl.prompt_active_session", fake_prompt_active_session, raising=False)
        monkeypatch.setattr("src.agent.agent.prompt_active_session", fake_prompt_active_session)

        set_agent_connection(SimpleNamespace())
        set_weixin_context(
            WeixinAcpContext(
                to_user_id="wx-user",
                base_url="https://example.test",
                context_token="ctx-dispatch",
                cdn_base_url="https://cdn.example.test",
            )
        )

        bundle = runtime["reply"]["create_reply_dispatcher_with_typing"](
            {
                "deliver": lambda payload: delivered.append(payload),
            }
        )

        await runtime["reply"]["dispatch_reply_from_config"](
            {
                "ctx": SimpleNamespace(body="hello"),
                "dispatcher": bundle["dispatcher"],
                "reply_options": bundle["reply_options"],
            }
        )

        assert len(delivered) == 1
        assert delivered[0].text == "via dispatcher"
