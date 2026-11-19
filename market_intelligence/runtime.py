"""Concrete operator-configured providers and restart-safe analysis spending."""

from dataclasses import dataclass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import threading

from market_intelligence.analysis import (
    DailyAnalysisBudget, JSONSummaryProvider, ProviderResponse, AnalysisError, strict_json_loads,
)
from market_intelligence.providers import PinnedHTTPSConnection, ProviderError
from proxy_config import configured_proxy_url, parse_proxy_url


_HOSTED_ENDPOINTS = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "gemini": "generativelanguage.googleapis.com",
}


class PersistentAnalysisBudget(DailyAnalysisBudget):
    def __init__(self, store, limits):
        super().__init__(limits)
        self.store = store

    def reserve_request(self, now, input_bytes, context):
        return self.store.reserve_analysis(now, input_bytes, self.limits, context)


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    kind: str
    model: str
    key_env: str | None = None
    port: int = 11434

    def __post_init__(self):
        from market_intelligence.contracts import identifier
        identifier(self.name)
        if self.kind not in (*_HOSTED_ENDPOINTS, "local") or not isinstance(self.model, str) or not 1 <= len(self.model) <= 128:
            raise ValueError("Invalid provider profile")
        if self.kind == "gemini" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.model):
            raise ValueError("Gemini models require a bare model identifier")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("Invalid local provider port")
        if self.kind in _HOSTED_ENDPOINTS and (not isinstance(self.key_env, str) or not self.key_env.startswith("TRADER_AI_") or not self.key_env.replace("_", "").isalnum()):
            raise ValueError("Hosted keys must use an operator-owned TRADER_AI_ environment variable")
        if self.kind == "local" and self.key_env is not None:
            raise ValueError("Local profiles do not accept secrets")

    def _request(self, request):
        if self.kind == "anthropic":
            return "/v1/messages", {
                "model": self.model, "system": request.system_prompt,
                "messages": [{"role": "user", "content": request.evidence_json}],
                "max_tokens": request.max_output_tokens, "stream": False,
            }
        if self.kind == "gemini":
            return f"/v1beta/models/{self.model}:generateContent", {
                "system_instruction": {"parts": [{"text": request.system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": request.evidence_json}]}],
                "generationConfig": {"candidateCount": 1, "maxOutputTokens": request.max_output_tokens,
                                     "responseMimeType": "application/json"},
            }
        return "/v1/chat/completions", {
            "model": self.model, "messages": [{"role": "system", "content": request.system_prompt},
                                               {"role": "user", "content": request.evidence_json}],
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_output_tokens, "stream": False,
        }

    def _response(self, result):
        if self.kind == "anthropic":
            content = result["content"]
            if (result.get("type") != "message" or result.get("role") != "assistant"
                    or result.get("stop_reason") != "end_turn" or type(content) is not list
                    or len(content) != 1 or type(content[0]) is not dict
                    or content[0].get("type") != "text"):
                raise ValueError("Unsupported Anthropic response")
            usage = result.get("usage")
            tokens = None
            if usage is not None:
                if (type(usage) is not dict or type(usage.get("input_tokens")) is not int
                        or type(usage.get("output_tokens")) is not int
                        or usage["input_tokens"] < 0 or usage["output_tokens"] < 0):
                    raise ValueError("Invalid Anthropic usage")
                tokens = usage["input_tokens"] + usage["output_tokens"]
            return ProviderResponse(content[0]["text"], tokens)
        if self.kind == "gemini":
            candidates = result["candidates"]
            if type(candidates) is not list or len(candidates) != 1 or type(candidates[0]) is not dict:
                raise ValueError("Unsupported Gemini response")
            candidate = candidates[0]
            content = candidate["content"]
            if (candidate.get("finishReason") != "STOP" or type(content) is not dict
                    or content.get("role", "model") != "model"):
                raise ValueError("Incomplete Gemini response")
            parts = content["parts"]
            if (type(parts) is not list or len(parts) != 1 or type(parts[0]) is not dict
                    or set(parts[0]) - {"text", "thought", "thoughtSignature"}
                    or parts[0].get("thought", False) is not False):
                raise ValueError("Unsupported Gemini content")
            usage = result.get("usageMetadata")
            if usage is not None and type(usage) is not dict:
                raise ValueError("Invalid Gemini usage")
            return ProviderResponse(parts[0]["text"], usage.get("totalTokenCount") if usage is not None else None)
        choices = result["choices"]
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError("Unsupported completion response")
        choice = choices[0]
        message = choice["message"]
        if (choice.get("finish_reason") != "stop" or type(message) is not dict
                or message.get("role") != "assistant" or message.get("refusal") is not None
                or message.get("tool_calls") not in (None, []) or message.get("function_call") is not None):
            raise ValueError("Incomplete or unsupported completion")
        return ProviderResponse(message["content"], result.get("usage", {}).get("total_tokens"))

    def complete(self, request, cancelled):
        if cancelled.is_set():
            raise AnalysisError("analysis_cancelled")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"}
        if self.kind in _HOSTED_ENDPOINTS:
            key = os.getenv(self.key_env, "")
            if not key or len(key) > 4096 or any(not 33 <= ord(character) <= 126 for character in key):
                raise AnalysisError("credential_unavailable")
            if self.kind == "anthropic":
                headers["x-api-key"] = key
                headers["anthropic-version"] = "2023-06-01"
            elif self.kind == "gemini":
                headers["x-goog-api-key"] = key
            else:
                headers["Authorization"] = "Bearer " + key
        connection = None
        response = None
        active_socket = None

        def abort():
            cancelled.set()
            active = active_socket or (connection.sock if connection is not None else None)
            if active is not None:
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if connection is not None:
                connection.close()

        timer = threading.Timer(25.0, abort)
        timer.daemon = True
        timer.start()
        try:
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            if self.kind in _HOSTED_ENDPOINTS:
                host = _HOSTED_ENDPOINTS[self.kind]
                addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
                if not addresses or len(addresses)>32:
                    raise AnalysisError("provider_unavailable")
                for address in addresses:
                    host_address = address[4][0]
                    ip=ipaddress.ip_address(host_address)
                    if (address[0] not in (socket.AF_INET, socket.AF_INET6)
                            or address[1] != socket.SOCK_STREAM or address[4][1] != 443
                            or "%" in host_address or not ip.is_global or ip.is_reserved or ip.is_multicast
                            or (address[0] == socket.AF_INET and ip.version != 4)
                            or (address[0] == socket.AF_INET6 and ip.version != 6)
                            or (address[0] == socket.AF_INET6 and len(address[4]) > 3 and address[4][3] != 0)):
                        raise AnalysisError("provider_unavailable")
                if cancelled.is_set():
                    raise AnalysisError("analysis_cancelled")
                connection=PinnedHTTPSConnection(host,addresses[0][4][0],3.0,proxy=parse_proxy_url(configured_proxy_url()))
            else:
                connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=3.0)
            connection.cancel_event=cancelled
            path, body = self._request(request)
            encoded = json.dumps(body, ensure_ascii=True).encode()
            if len(encoded) > 131072:
                raise AnalysisError("input_too_large")
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            connection.request("POST", path, encoded, headers)
            active_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                raise AnalysisError("provider_unavailable")
            length = response.getheader("Content-Length")
            if (length is not None and (not isinstance(length, str) or not length.isascii()
                    or not length.isdigit() or len(length) > 6 or int(length) > 131072)):
                raise AnalysisError("output_invalid")
            chunks = []
            total = 0
            while not cancelled.is_set():
                chunk = response.read1(min(16384, 131073-total))
                if not chunk:
                    break
                total += len(chunk)
                if total > 131072:
                    raise AnalysisError("output_invalid")
                chunks.append(chunk)
            if length is not None and total != int(length):
                raise AnalysisError("output_invalid")
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            result = strict_json_loads(b"".join(chunks))
            return self._response(result)
        except (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError,
                http.client.HTTPException, ProviderError):
            raise AnalysisError("provider_unavailable") from None
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def provider(self):
        return JSONSummaryProvider(self.name, self.model, self.complete)


def load_profiles(path):
    path = Path(path)
    if path.suffix.lower() != ".json" or path.is_symlink():
        raise ValueError("Provider profiles require a nonsymlink JSON configuration")
    with path.open("rb") as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise ValueError("Provider profile file exceeds limit")
    data = strict_json_loads(raw)
    if type(data) is not list or not 1 <= len(data) <= 10:
        raise ValueError("Provider profiles must be a bounded list")
    profiles = {}
    for item in data:
        if (type(item) is not dict or not {"name", "kind", "model"} <= set(item)
                or set(item) - {"name", "kind", "model", "key_env", "port"}):
            raise ValueError("Profile configuration may not contain secrets or arbitrary endpoints")
        profile = ProviderProfile(**item)
        if profile.name in profiles:
            raise ValueError("Duplicate provider profiles")
        profiles[profile.name] = profile.provider()
    return profiles