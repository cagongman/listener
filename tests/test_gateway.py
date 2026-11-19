import json
import threading
import unittest
from unittest.mock import Mock, patch

from websockets.sync.server import serve

from listener_hermes.gateway import GatewayClient, GatewayError, OutcomeUnknown


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.headers = []
        self.drop_submit = False
        def handler(ws):
            self.headers.append(ws.request.headers.get("Sec-WebSocket-Protocol"))
            try:
                for raw in ws:
                    request = json.loads(raw)
                    self.received.append(request)
                    if request["method"] == "prompt.submit" and self.drop_submit:
                        ws.close()
                        return
                    result = {"profile_id": "test-profile"} if request["method"] == "runtime.describe" else {"admission_id": "receipt-1", "status": "queued"}
                    # Events may precede the RPC response; the reader must keep draining.
                    ws.send(json.dumps({"method": "session.event", "params": {
                        "session_id": "session-1", "type": "message.start", "payload": {}}}))
                    ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))
            except Exception:
                return
        self.server = serve(handler, "127.0.0.1", 0, subprotocols=["hermes-gateway-v1"])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.socket.getsockname()[1]
        self.grant = {"state": "ready", "url": f"ws://127.0.0.1:{port}/api/ws",
                      "protocol": "hermes-gateway-v1",
                      "protocols": ["hermes-gateway-v1", "hermes-gateway-ticket.test-secret"]}
        self.launcher = Mock()
        self.launcher._prefix.return_value = ["hermes.exe"]
        self.launcher.profile_args.return_value = []
        self.launcher._environment.return_value = {}
        self.bootstrap = patch("listener_hermes.gateway.subprocess.run", return_value=Mock(
            returncode=0, stdout=json.dumps(self.grant)))
        self.bootstrap.start()
        self.client = GatewayClient(self.launcher)

    def tearDown(self):
        self.client.close()
        self.bootstrap.stop()
        self.server.shutdown()
        self.thread.join(3)

    def test_ticket_subprotocol_and_json_rpc_with_interleaved_events(self):
        self.assertEqual(self.client.connect()["profile_id"], "test-profile")
        result = self.client.call("prompt.submit", session_id="session-1", input_id="voice-1", text='한국어 "quotes" $(literal)')
        self.assertEqual(result["status"], "queued")
        self.assertIn("hermes-gateway-ticket.test-secret", self.headers[0])
        self.assertEqual(self.received[-1]["params"]["text"], '한국어 "quotes" $(literal)')
        self.assertEqual(self.client.events.get(timeout=1)["type"], "message.start")

    def test_unknown_outcome_is_not_automatically_retried(self):
        self.client.connect()
        self.drop_submit = True
        with self.assertRaises(OutcomeUnknown):
            self.client.call("prompt.submit", session_id="session-1", text="one", input_id="stable", timeout=2)
        self.assertEqual(sum(r["method"] == "prompt.submit" for r in self.received), 1)

    def test_nonlocal_bootstrap_is_refused_before_connect(self):
        self.bootstrap.stop()
        grant = {**self.grant, "url": "ws://example.com/api/ws"}
        with patch("listener_hermes.gateway.subprocess.run", return_value=Mock(returncode=0, stdout=json.dumps(grant))):
            with self.assertRaises(GatewayError):
                self.client.connect()
        self.assertEqual(self.received, [])
