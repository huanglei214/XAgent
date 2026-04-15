import unittest

from xagent.channel.feishu.client import FeishuLongConnectionClient


class _FakeBuilder:
    def __init__(self) -> None:
        self.registered = None

    def register_p2_im_message_receive_v1(self, handler):
        self.registered = handler
        return self

    def build(self):
        return {"registered": self.registered}


class _FakeEventDispatcherHandler:
    last_builder = None

    @staticmethod
    def builder(_encrypt_key: str, _verification_token: str):
        builder = _FakeBuilder()
        _FakeEventDispatcherHandler.last_builder = builder
        return builder


class _FakeWsClient:
    last_init = None

    def __init__(self, app_id, app_secret, *, event_handler, domain, auto_reconnect):
        _FakeWsClient.last_init = {
            "app_id": app_id,
            "app_secret": app_secret,
            "event_handler": event_handler,
            "domain": domain,
            "auto_reconnect": auto_reconnect,
        }
        self.started = False

    def start(self) -> None:
        self.started = True


class _FakeSdk:
    EventDispatcherHandler = _FakeEventDispatcherHandler

    class ws:
        Client = _FakeWsClient


class _Config:
    app_id = "app-id"
    app_secret = "app-secret"
    api_base_url = "https://open.feishu.cn"


class FeishuTransportTests(unittest.TestCase):
    def test_long_connection_client_uses_official_sdk_shapes(self) -> None:
        seen = []

        def _handler(event) -> None:
            seen.append(event)

        client = FeishuLongConnectionClient(_Config(), _handler, sdk_module=_FakeSdk)
        client.start()

        self.assertEqual(_FakeWsClient.last_init["app_id"], "app-id")
        self.assertEqual(_FakeWsClient.last_init["app_secret"], "app-secret")
        self.assertEqual(_FakeWsClient.last_init["domain"], "https://open.feishu.cn")
        self.assertTrue(_FakeWsClient.last_init["auto_reconnect"])
        self.assertIs(_FakeEventDispatcherHandler.last_builder.registered, _handler)
