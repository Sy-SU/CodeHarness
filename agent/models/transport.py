"""Allowlisted HTTP observations; never retain headers, URLs or exception prose."""
from datetime import datetime, timezone
import ssl
import time

import httpx


class TransportObservation:
    def __init__(self, *, timeout, request_bytes, started):
        self.started = started
        self.data = {"schema_version": "provider_transport_v1", "started_at": datetime.now(timezone.utc).isoformat(),
            "timeouts": dict(timeout), "overall_request_deadline_seconds": None,
            "model_response_streaming": False, "automatic_retries": 0,
            "request_body_bytes": request_bytes, "request_send_completed": None,
            "http_status": None, "response_headers_observed": False,
            "first_byte_observed": None, "first_byte_observed_at": None,
            "body_bytes_observed": 0, "body_complete": False,
            "body_bytes_kind": "raw_transport_or_preconsumed_content",
            "network_phase": None, "events": []}

    def trace(self, name, info):
        # The info object can contain Authorization and TLS objects. Only names,
        # elapsed time and a known integer status may enter persisted evidence.
        self.data["network_phase"] = name
        self.data["events"].append({"name": name, "elapsed_ms": round((time.monotonic()-self.started)*1000)})
        if name.endswith("send_request_body.complete"):
            self.data["request_send_completed"] = True
        if name.endswith("receive_response_headers.complete"):
            value = info.get("return_value")
            if isinstance(value, tuple) and len(value) > 1 and isinstance(value[1], int):
                self.headers(value[1])

    def headers(self, status):
        self.data.update(http_status=status, response_headers_observed=True, first_byte_observed=True)
        if self.data["first_byte_observed_at"] is None:
            self.data["first_byte_observed_at"] = datetime.now(timezone.utc).isoformat()
            self.data["headers_elapsed_ms"] = round((time.monotonic()-self.started)*1000)
            self.data["first_byte_observation_kind"] = "completed_response_headers_not_exact_packet_ttfb"

    def body(self, size):
        self.data["body_bytes_observed"] += size

    def finish(self, *, category="success", error=None):
        self.data.update(category=category, finished_at=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=round((time.monotonic()-self.started)*1000),
            exception_class=type(error).__name__ if error is not None else None,
            partial_body_observed=self.data["body_bytes_observed"] > 0 and not self.data["body_complete"],
            provider_generation_may_have_completed=category not in {"success", "connect_timeout", "connect_failure", "tls_connect_failure", "pool_timeout"},
            unknown_charge_possible=category not in {"success", "connect_timeout", "connect_failure", "tls_connect_failure", "pool_timeout"})
        return dict(self.data)

    def error_category(self, error):
        if isinstance(error, httpx.ConnectTimeout):
            return "connect_timeout"
        if isinstance(error, httpx.ReadTimeout):
            return "read_timeout_during_body" if self.data["response_headers_observed"] else "read_timeout_before_response_headers"
        if isinstance(error, httpx.WriteTimeout):
            return "write_timeout"
        if isinstance(error, httpx.PoolTimeout):
            return "pool_timeout"
        if isinstance(error, httpx.ConnectError):
            causes = []
            current = error
            while current is not None and len(causes) < 8 and current not in causes:
                causes.append(current)
                current = current.__cause__ or current.__context__
            tls = any(isinstance(cause, ssl.SSLError) for cause in causes) or any(
                "start_tls.failed" in event["name"] for event in self.data["events"])
            return "tls_connect_failure" if tls else "connect_failure"
        return "transport_failure"
