"""Concrete operator-configured providers and restart-safe analysis spending."""

from dataclasses import dataclass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import threading

from market_intelligence.analysis import DailyAnalysisBudget, JSONSummaryProvider, ProviderResponse, AnalysisError
from market_intelligence.providers import PinnedHTTPSConnection
from proxy_config import configured_proxy_url, parse_proxy_url


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
        if self.kind not in ("openai", "local") or not isinstance(self.model, str) or not 1 <= len(self.model) <= 128:
            raise ValueError("Invalid provider profile")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("Invalid local provider port")
        if self.kind == "openai" and (not isinstance(self.key_env, str) or not self.key_env.startswith("TRADER_AI_") or not self.key_env.replace("_", "").isalnum()):
            raise ValueError("Hosted keys must use an operator-owned TRADER_AI_ environment variable")
        if self.kind == "local" and self.key_env is not None:
            raise ValueError("Local profiles do not accept secrets")

    def complete(self, request, cancelled):
        if cancelled.is_set():
            raise AnalysisError("analysis_cancelled")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"}
        if self.kind == "openai":
            key = os.getenv(self.key_env, "")
            if not key or len(key) > 4096 or any(character.isspace() for character in key):
                raise AnalysisError("credential_unavailable")
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
            if self.kind=="openai":
                addresses=socket.getaddrinfo("api.openai.com",443,type=socket.SOCK_STREAM)
                if not addresses or len(addresses)>32:
                    raise AnalysisError("provider_unavailable")
                for address in addresses:
                    ip=ipaddress.ip_address(address[4][0])
                    if not ip.is_global or ip.is_reserved or ip.is_multicast:
                        raise AnalysisError("provider_unavailable")
                if cancelled.is_set():
                    raise AnalysisError("analysis_cancelled")
                connection=PinnedHTTPSConnection("api.openai.com",addresses[0][4][0],3.0,proxy=parse_proxy_url(configured_proxy_url()))
            else:
                connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=3.0)
            connection.cancel_event=cancelled
            body = {"model": self.model, "messages": [{"role": "system", "content": request.system_prompt},
                      {"role": "user", "content": request.evidence_json}], "response_format": {"type": "json_object"},
                    "max_tokens": request.max_output_tokens, "stream": False}
            encoded = json.dumps(body, ensure_ascii=True).encode()
            if len(encoded) > 131072:
                raise AnalysisError("input_too_large")
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            connection.request("POST", "/v1/chat/completions", encoded, headers)
            active_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                raise AnalysisError("provider_unavailable")
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
            if cancelled.is_set():
                raise AnalysisError("analysis_cancelled")
            result = json.loads(b"".join(chunks))
            content = result["choices"][0]["message"]["content"]
            return ProviderResponse(content, result.get("usage", {}).get("total_tokens"))
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
            raise AnalysisError("provider_unavailable") from exc
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
    data = json.loads(raw)
    if type(data) is not list or not 1 <= len(data) <= 10:
        raise ValueError("Provider profiles must be a bounded list")
    profiles = {}
    for item in data:
        if type(item) is not dict or set(item) - {"name", "kind", "model", "key_env", "port"}:
            raise ValueError("Profile configuration may not contain secrets or arbitrary endpoints")
        profile = ProviderProfile(**item)
        if profile.name in profiles:
            raise ValueError("Duplicate provider profiles")
        profiles[profile.name] = profile.provider()
    return profiles