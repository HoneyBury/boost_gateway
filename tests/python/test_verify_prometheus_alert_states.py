import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.gates.production.verify_prometheus_alert_states import verify_sequences


class SequencedPrometheusHandler(BaseHTTPRequestHandler):
    responses: list[dict] = []
    request_count = 0

    def do_GET(self):  # noqa: N802
        index = min(type(self).request_count, len(type(self).responses) - 1)
        type(self).request_count += 1
        body = json.dumps(type(self).responses[index]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


def alerts_response(state=None):
    alerts = []
    if state:
        alerts.append({"labels": {"alertname": "GatewayDown"}, "state": state})
    return {"status": "success", "data": {"alerts": alerts}}


class PrometheusServer:
    def __init__(self, responses):
        SequencedPrometheusHandler.responses = responses
        SequencedPrometheusHandler.request_count = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SequencedPrometheusHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class VerifyPrometheusAlertStatesTest(unittest.TestCase):
    def verify(self, url, sequence, api="alerts"):
        return verify_sequences(
            [("GatewayDown", sequence)],
            prometheus_url=url,
            api=api,
            poll_interval_seconds=0.01,
            state_timeout_seconds=0.2,
            overall_timeout_seconds=0.5,
            request_timeout_seconds=0.2,
        )

    def test_alerts_api_observes_pending_firing_and_resolved(self):
        with PrometheusServer(
            [alerts_response("pending"), alerts_response("firing"), alerts_response()]
        ) as url:
            summary = self.verify(url, ["pending", "firing", "resolved"])

        self.assertTrue(summary["overall_pass"])
        self.assertEqual(summary["summary_version"], 2)
        self.assertEqual(
            [item["state"] for item in summary["checks"][0]["matched_states"]],
            ["pending", "firing", "resolved"],
        )

    def test_fails_when_firing_skips_required_pending(self):
        with PrometheusServer([alerts_response("firing")]) as url:
            summary = self.verify(url, ["pending", "firing"])

        self.assertFalse(summary["overall_pass"])
        self.assertIn("before required pending", summary["checks"][0]["failure"])

    def test_rules_api_parses_alerting_rule_state(self):
        response = {
            "status": "success",
            "data": {
                "groups": [
                    {
                        "rules": [
                            {"type": "alerting", "name": "GatewayDown", "state": "firing"}
                        ]
                    }
                ]
            },
        }
        with PrometheusServer([response]) as url:
            summary = self.verify(url, ["firing"], api="rules")

        self.assertTrue(summary["overall_pass"])
        self.assertEqual(summary["api_endpoints_used"], ["rules"])


if __name__ == "__main__":
    unittest.main()
