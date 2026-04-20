from types import SimpleNamespace

from src.api.types import GetUpdatesResp, MessageItem, MessageItemType, TextItem, WeixinMessage
from src.monitor.monitor import monitor_weixin_provider


class TestMonitorWeixinProvider:
    async def test_passes_channel_runtime_to_process_message(self, monkeypatch):
        captured = {}
        abort_signal = SimpleNamespace(aborted=False)
        channel_runtime = {
            "reply": {"dispatch_reply_from_config": object()},
            "routing": {"resolve_agent_route": object()},
        }

        class FakeConfigManager:
            def __init__(self, *_args, **_kwargs):
                pass

            async def get_for_user(self, *_args, **_kwargs):
                return SimpleNamespace(typing_ticket="ticket-1")

        async def fake_get_updates(**_kwargs):
            return GetUpdatesResp(
                ret=0,
                msgs=[
                    WeixinMessage(
                        from_user_id="wx-user",
                        context_token="ctx-1",
                        item_list=[
                            MessageItem(
                                type=MessageItemType.TEXT,
                                text_item=TextItem(text="hello"),
                            )
                        ],
                    )
                ],
                get_updates_buf="buf-1",
            )

        async def fake_process_one_message(full, deps):
            captured["from_user_id"] = full.from_user_id
            captured["typing_ticket"] = deps["typing_ticket"]
            captured["channel_runtime"] = deps["channel_runtime"]
            abort_signal.aborted = True

        monkeypatch.setattr("src.monitor.monitor.WeixinConfigManager", FakeConfigManager)
        monkeypatch.setattr("src.monitor.monitor.get_updates", fake_get_updates)
        monkeypatch.setattr("src.monitor.monitor.process_one_message", fake_process_one_message)
        monkeypatch.setattr("src.monitor.monitor.load_get_updates_buf", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.monitor.monitor.save_get_updates_buf", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("src.monitor.monitor.get_sync_buf_file_path", lambda *_args, **_kwargs: "sync.json")

        await monitor_weixin_provider(
            {
                "base_url": "https://example.test",
                "cdn_base_url": "https://cdn.example.test",
                "token": "token",
                "account_id": "acc-1",
                "channel_runtime": channel_runtime,
                "abort_signal": abort_signal,
            }
        )

        assert captured["from_user_id"] == "wx-user"
        assert captured["typing_ticket"] == "ticket-1"
        assert captured["channel_runtime"] is channel_runtime
