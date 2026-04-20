from src.api.config_cache import WeixinConfigManager


class TestWeixinConfigManager:
    async def test_cache_is_scoped_by_context_token(self, monkeypatch):
        calls = []

        class Resp:
            def __init__(self, ret, typing_ticket):
                self.ret = ret
                self.typing_ticket = typing_ticket

        async def fake_get_config(*, base_url, token, ilink_user_id, context_token):
            calls.append(
                {
                    "base_url": base_url,
                    "token": token,
                    "ilink_user_id": ilink_user_id,
                    "context_token": context_token,
                }
            )
            return Resp(ret=0, typing_ticket=f"ticket-for-{context_token or 'none'}")

        monkeypatch.setattr("src.api.config_cache.get_config", fake_get_config)

        manager = WeixinConfigManager({"base_url": "https://example.test", "token": "token"}, lambda _: None)

        cfg1 = await manager.get_for_user("user-1", "ctx-a")
        cfg2 = await manager.get_for_user("user-1", "ctx-b")

        assert cfg1.typing_ticket == "ticket-for-ctx-a"
        assert cfg2.typing_ticket == "ticket-for-ctx-b"
        assert [call["context_token"] for call in calls] == ["ctx-a", "ctx-b"]
