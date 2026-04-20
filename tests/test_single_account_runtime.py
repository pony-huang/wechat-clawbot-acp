from types import SimpleNamespace

from src.api.types import MessageItem, MessageItemType, TextItem, WeixinMessage
from src.messaging.inbound import (
    clear_all_context_tokens,
    get_context_token,
)
from src.messaging.process_message import process_one_message


class TestInboundContextTokenPersistence:
    def setup_method(self):
        clear_all_context_tokens()

    async def test_process_message_persists_context_token(self, monkeypatch):
        def fake_is_user_allowed(_user_id, _account_id):
            return False

        async def fake_record_inbound_session(_params):
            return None

        async def fake_resolve_agent_route(_ctx):
            return SimpleNamespace(route="default", session_key="acc-1:wx-user", agent_id=None)

        monkeypatch.setattr("src.auth.pairing.is_user_allowed", fake_is_user_allowed)

        full = WeixinMessage(
            from_user_id="wx-user",
            context_token="ctx-persisted",
            item_list=[
                MessageItem(
                    type=MessageItemType.TEXT,
                    text_item=TextItem(text="hello"),
                )
            ],
        )

        await process_one_message(
            full,
            {
                "account_id": "acc-1",
                "channel_runtime": {
                    "routing": {"resolve_agent_route": fake_resolve_agent_route},
                    "session": {"record_inbound_session": fake_record_inbound_session},
                    "reply": {"finalize_inbound_context": lambda ctx: ctx},
                    "media": {},
                },
                "base_url": "https://example.test",
                "cdn_base_url": "https://cdn.example.test",
                "token": "token",
            },
        )

        assert get_context_token("acc-1", "wx-user") == "ctx-persisted"
