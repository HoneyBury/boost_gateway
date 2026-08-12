"""Performance baseline responsibility module: perf_business_protocol."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.server
import json
import os
import platform
import re
import shutil
import statistics
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from scripts.lib.perf_statistics import (
    distribution,
    latency_percentile,
    linear_slope,
    metric_distribution,
)

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None  # type: ignore[assignment]

from scripts.lib.perf_process_affinity import *  # noqa: F401,F403
from scripts.lib.perf_process_runtime import *  # noqa: F401,F403
from scripts.lib.perf_otel_runtime import *  # noqa: F401,F403
from scripts.lib.perf_bench_runtime import *  # noqa: F401,F403
BUSINESS_OPERATION_SEQUENCES = {
    "matchmaking": (
        ("match_join", 6001, 6002),
        ("match_status", 6006, 6007),
        ("match_leave", 6004, 6005),
    ),
    "leaderboard": (
        ("leaderboard_submit", 7001, 7002),
        ("leaderboard_top", 7003, 7004),
        ("leaderboard_rank", 7005, 7006),
    ),
}

def encode_business_packet(
    message_id: int,
    request_id: int,
    body: str,
    *,
    version: int = 1,
    flags: int = 0,
) -> bytes:
    encoded_body = body.encode("utf-8")
    payload_length = 16 + len(encoded_body)
    return struct.pack("!IBHIIiB", payload_length, version, message_id, request_id, 0, 0, flags) + encoded_body


def recv_business_packet(sock: socket.socket, deadline: float | None = None) -> dict[str, Any]:
    payload_length = struct.unpack("!I", recv_exact(sock, 4, deadline))[0]
    if payload_length < 16 or payload_length > 1024 * 1024:
        raise ValueError(f"invalid gateway frame length: {payload_length}")
    payload = recv_exact(sock, payload_length, deadline)
    version, message_id, request_id, sequence_number, error_code, flags = struct.unpack(
        "!BHIIiB", payload[:16]
    )
    return {
        "version": version,
        "message_id": message_id,
        "request_id": request_id,
        "sequence_number": sequence_number,
        "error_code": error_code,
        "flags": flags,
        "body_bytes": payload[16:],
        "body": payload[16:].decode("utf-8", errors="replace") if flags == 0 else "",
    }

class BusinessOperationClient:
    PUSH_MESSAGE_IDS = {1003, 1004, 3009, 4005, 4006, 6003}

    def __init__(self, host: str, port: int, timeout_seconds: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout_seconds)
        self.sock.settimeout(timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.next_request_id = 1

    def close(self) -> None:
        with suppress(OSError):
            self.sock.close()

    def request(
        self,
        message_id: int,
        expected_message_id: int,
        body: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_timeout = timeout_seconds or self.timeout_seconds
        deadline = time.monotonic() + request_timeout
        self.sock.settimeout(request_timeout)
        request_id = self.next_request_id
        self.next_request_id += 1
        self.sock.sendall(encode_business_packet(message_id, request_id, body))
        while True:
            response = recv_business_packet(self.sock, deadline)
            if response["version"] != 1:
                raise ValueError(f"unsupported protocol version: {response['version']}")
            if response["flags"] & ~0x01:
                raise ValueError(
                    f"unsupported response flags: 0x{response['flags']:02x}"
                )
            if response["flags"] & 0x01:
                compressed = response["body_bytes"]
                if len(compressed) < 4:
                    raise ValueError("invalid compressed response: missing original length")
                expected_length = int.from_bytes(compressed[:4], "little")
                try:
                    decoded = zlib.decompress(compressed[4:])
                except zlib.error as exc:
                    raise ValueError(f"invalid compressed response: {exc}") from exc
                if len(decoded) != expected_length:
                    raise ValueError(
                        f"invalid compressed response length: expected {expected_length}, got {len(decoded)}"
                    )
                response["body"] = decoded.decode("utf-8", errors="strict")
            if response["message_id"] in self.PUSH_MESSAGE_IDS:
                continue
            if response["request_id"] != request_id:
                raise ValueError(
                    f"unexpected request id {response['request_id']}, expected {request_id}"
                )
            response["ok"] = (
                response["message_id"] == expected_message_id
                and response["error_code"] == 0
            )
            return response

def business_operation_body(
    scenario: str,
    operation: str,
    user_id: str,
    client_index: int,
    iteration: int,
) -> str:
    if scenario == "matchmaking":
        mmr = 1000 + (client_index % 20)
        return f"{user_id}|{mmr if operation == 'match_join' else 0}|1v1"
    if operation == "leaderboard_submit":
        score = 1_000_000_000 + client_index * 100_000 + iteration
        return f"{user_id}|perf-{client_index}|{score}"
    if operation == "leaderboard_top":
        return "20"
    return user_id

def run_business_operation_worker(
    host: str,
    port: int,
    scenario: str,
    client_index: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
) -> dict[str, Any]:
    if scenario != "leaderboard":
        raise ValueError("generic business operation worker only supports leaderboard")
    user_id = f"perf_{scenario}_{run_id}_{client_index}"
    records: list[dict[str, Any]] = []
    client: BusinessOperationClient | None = None
    try:
        client = BusinessOperationClient(host, port, timeout_seconds)
        login = client.request(2001, 2002, f"{user_id}|token:{user_id}|{user_id}")
        if not login["ok"]:
            return {"client_index": client_index, "setup_error": f"login failed: {login['body'][:200]}", "records": []}

        for iteration in range(iterations):
            for operation, request_id, response_id in BUSINESS_OPERATION_SEQUENCES[scenario]:
                body = business_operation_body(scenario, operation, user_id, client_index, iteration)
                started = time.perf_counter()
                try:
                    response = client.request(request_id, response_id, body)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    records.append({
                        "operation": operation,
                        "ok": response["ok"],
                        "latency_ms": latency_ms,
                        "error": "" if response["ok"] else f"error={response['error_code']} body={response['body'][:200]}",
                    })
                except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
                    records.append({
                        "operation": operation,
                        "ok": False,
                        "latency_ms": (time.perf_counter() - started) * 1000.0,
                        "error": str(exc)[:200],
                    })
        return {"client_index": client_index, "setup_error": "", "records": records}
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return {"client_index": client_index, "setup_error": str(exc)[:200], "records": records}
    finally:
        if client is not None:
            client.close()

def setup_matchmaking_client(
    host: str,
    port: int,
    client_index: int,
    timeout_seconds: float,
    run_id: str,
    iteration: int,
) -> dict[str, Any]:
    user_id = f"perf_matchmaking_{run_id}_{iteration}_{client_index}"
    client: BusinessOperationClient | None = None
    try:
        client = BusinessOperationClient(host, port, timeout_seconds)
        login = client.request(2001, 2002, f"{user_id}|token:{user_id}|{user_id}")
        if not login["ok"]:
            raise ValueError(f"login failed: {login['body'][:200]}")
        return {
            "client_index": client_index,
            "user_id": user_id,
            "client": client,
            "error": "",
            "retryable": False,
        }
    except (ConnectionError, OSError, TimeoutError) as exc:
        if client is not None:
            client.close()
        return {
            "client_index": client_index,
            "user_id": user_id,
            "client": None,
            "error": str(exc)[:200],
            "retryable": True,
        }
    except ValueError as exc:
        if client is not None:
            client.close()
        return {
            "client_index": client_index,
            "user_id": user_id,
            "client": None,
            "error": str(exc)[:200],
            "retryable": False,
        }

def setup_matchmaking_cohort(
    host: str,
    port: int,
    clients: int,
    timeout_seconds: float,
    run_id: str,
    iteration: int,
) -> tuple[list[dict[str, Any]], int]:
    for attempt in range(2):
        with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
            entries = list(executor.map(
                lambda index: setup_matchmaking_client(
                    host,
                    port,
                    index,
                    timeout_seconds,
                    f"{run_id}_setup{attempt + 1}",
                    iteration,
                ),
                range(clients),
            ))
        failures = [entry for entry in entries if entry["error"]]
        if not failures or attempt == 1 or any(not entry["retryable"] for entry in failures):
            return entries, attempt

        # Matchmaking requires an even, complete cohort. Rebuild every connection
        # before measuring operations so a transient setup timeout cannot leave an
        # unmatched player in the queue or hide a failed measured request.
        for entry in entries:
            if entry["client"] is not None:
                entry["client"].close()

    raise AssertionError("unreachable")

def execute_match_request(
    entry: dict[str, Any],
    operation: str,
    request_id: int,
    response_id: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        body = business_operation_body("matchmaking", operation, entry["user_id"], entry["client_index"], 0)
        response = entry["client"].request(request_id, response_id, body, timeout_seconds)
        return {
            "operation": operation,
            "ok": response["ok"],
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "error": "" if response["ok"] else f"error={response['error_code']} body={response['body'][:200]}",
        }
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return {
            "operation": operation,
            "ok": False,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "error": str(exc)[:200],
        }

def poll_until_matched(
    entry: dict[str, Any],
    match_started: float,
    match_deadline: float,
) -> dict[str, Any]:
    polls = 0
    while time.monotonic() < match_deadline:
        remaining = match_deadline - time.monotonic()
        try:
            body = business_operation_body("matchmaking", "match_status", entry["user_id"], entry["client_index"], 0)
            response = entry["client"].request(6006, 6007, body, remaining)
            polls += 1
            if not response["ok"]:
                raise ValueError(f"error={response['error_code']} body={response['body'][:200]}")
            status = json.loads(response["body"])
            if not isinstance(status, dict):
                raise ValueError("match status response is not a JSON object")
            if status.get("matched") is True:
                return {
                    "operation": "match_status",
                    "ok": True,
                    "latency_ms": (time.perf_counter() - match_started) * 1000.0,
                    "poll_attempts": polls,
                    "error": "",
                }
        except (ConnectionError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "operation": "match_status",
                "ok": False,
                "latency_ms": (time.perf_counter() - match_started) * 1000.0,
                "poll_attempts": polls,
                "error": str(exc)[:200],
            }
        time.sleep(min(0.05, max(0.0, match_deadline - time.monotonic())))
    return {
        "operation": "match_status",
        "ok": False,
        "latency_ms": (time.perf_counter() - match_started) * 1000.0,
        "poll_attempts": polls,
        "error": "matchmaking deadline exceeded before matched=true",
    }
