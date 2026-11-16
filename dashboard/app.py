"""Local-only dashboard API; importing this module never opens a ledger."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from jsonschema import Draft202012Validator, FormatChecker, ValidationError as SchemaValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from dashboard.reader import LedgerReader, ReadError, metadata
from dashboard.telemetry import TelemetryReader, unknown_health
from dashboard.history import HistoryReader, demo_page
from dashboard.assets import verify_frontend


ROOT = Path(__file__).resolve().parent
MESSAGES = {
    "ledger_missing": (503, "Recorded account data is unavailable.", True),
    "ledger_invalid": (409, "Recorded account data failed validation.", False),
    "schema_unsupported": (409, "Recorded account schema is unsupported.", False),
    "source_unavailable": (503, "Recorded account data is temporarily unavailable.", True),
    "invalid_request": (400, "The dashboard request is invalid.", False),
    "snapshot_changed": (409, "Recorded history changed. Refresh the snapshot before continuing.", True),
}


def create_app(database=None, scenario="healthy_recorded", clock=None, static_directory=None, telemetry_path=None, process_check=None, serve_frontend=True,
               intelligence_database=None, intelligence_demo=False, analysis_password=None, analysis_profiles=(), investment_database=None):
    if database is not None and investment_database is not None:
        raise ValueError("Select legacy trading or investment storage, not both")
    if serve_frontend:
        static_directory = verify_frontend(static_directory)
    clock = clock or (lambda: datetime.now(timezone.utc))
    reader = LedgerReader(database) if database is not None else None
    telemetry = TelemetryReader(telemetry_path or reader.database.with_name("bot_telemetry.json"), process_check) if reader else None
    fixtures = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8")) if reader is None else None
    selected = next((item for item in fixtures["scenarios"] if item["name"] == scenario), None) if fixtures else None
    if reader is None and selected is None:
        raise ValueError("Unknown synthetic scenario")
    validator = Draft202012Validator(
        json.loads((ROOT / "response.schema.json").read_text(encoding="utf-8")), format_checker=FormatChecker())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    intelligence = None
    if intelligence_database is not None or intelligence_demo:
        if intelligence_demo and (database is not None or investment_database is not None or intelligence_database is not None):
            raise ValueError("Synthetic intelligence must not bind operational storage")
        from dashboard.intelligence import IntelligenceWorkspace
        intelligence = IntelligenceWorkspace(intelligence_database, analysis_profiles, analysis_password, intelligence_demo)
    mutation_paths = {"/api/v1/intelligence/login", "/api/v1/intelligence/controls"}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def error(code, status=None):
        default_status, message, retryable = MESSAGES[code]
        meta = metadata(clock())
        meta["source_mode"] = "recorded" if reader or investment_database is not None else "demo"
        if code in ("ledger_invalid", "schema_unsupported"):
            meta["ledger"]["freshness"] = "invalid"
        return JSONResponse(status_code=status or default_status, content={
            "api_version": "1", "resource": "error", "meta": meta,
            "data": {"code": code, "message": message, "retryable": retryable},
        })

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        origin = request.headers.get("origin")
        if hostname not in ("127.0.0.1", "localhost"):
            response = error("invalid_request", 400)
        elif origin and origin != f"{request.url.scheme}://{host}":
            response = error("invalid_request", 403)
        elif request.method not in ("GET", "HEAD") and not (request.method == "POST" and request.url.path in mutation_paths and intelligence and intelligence.controls_enabled):
            response = error("invalid_request", 405)
        elif request.method == "POST" and (origin != f"{request.url.scheme}://{host}" or request.headers.get("sec-fetch-site") not in (None,"same-origin")):
            response = error("invalid_request", 403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return response

    @app.exception_handler(ReadError)
    async def read_error(request: Request, exc: ReadError):
        return error(exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error("invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error("invalid_request", exc.status_code)

    @app.get("/api/v1/settings")
    def settings():
        return {"mode": "recorded" if reader or investment_database is not None else "demo",
                "scenario": None if reader or investment_database is not None else scenario}

    @app.get("/api/investment/v1/snapshot")
    def investment_snapshot():
        from investment import InvestmentError
        from investment.service import demo_snapshot, snapshot

        if investment_database is None and reader is not None:
            return JSONResponse(status_code=503, content={"error": "investment_not_configured"})
        try:
            report = snapshot(investment_database, now=clock()) if investment_database is not None else demo_snapshot(now=clock())
            schema_name = ("response-v2.schema.json" if isinstance(report, dict)
                           and report.get("schema_version") == 2 else "response.schema.json")
            schema = json.loads((ROOT.parent / "investment" / schema_name).read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
            return report
        except (InvestmentError, OSError) as exc:
            return JSONResponse(status_code=503, content={"error": "investment_unavailable", "message": str(exc)})
        except SchemaValidationError:
            return JSONResponse(status_code=409, content={"error": "investment_invalid", "message": "Investment report failed schema validation."})

    @app.get("/api/v1/intelligence")
    def intelligence_snapshot():
        if intelligence is None:
            return JSONResponse(status_code=503,content={"error":"intelligence_not_configured"})
        try:
            return intelligence.snapshot()
        except Exception as exc:
            return JSONResponse(status_code=503,content={"error":"intelligence_unavailable",
                "diagnostic":getattr(exc,"code",type(exc).__name__) if intelligence.demo else None})

    @app.get("/api/v1/intelligence/session")
    def intelligence_session(request:Request):
        if intelligence is None or not intelligence.controls_enabled:
            return {"enabled":False,"authenticated":False,"csrf":None}
        token,record=intelligence.security.session(request.cookies.get("analysis_session"),clock(),create=True)
        response=JSONResponse({"enabled":True,"authenticated":record["authenticated"],"csrf":record["csrf"]})
        response.set_cookie("analysis_session",token,httponly=True,samesite="strict",secure=request.url.scheme=="https",path="/api/v1/intelligence",max_age=900)
        return response

    async def bounded_json(request):
        if request.headers.get("content-type","").split(";",1)[0]!="application/json":
            raise ValueError("JSON only")
        body=b""
        async for chunk in request.stream():
            body+=chunk
            if len(body)>4096:
                raise ValueError("Command too large")
        return json.loads(body)

    @app.post("/api/v1/intelligence/login")
    async def intelligence_login(request:Request):
        try:
            payload=await bounded_json(request)
            if type(payload) is not dict or set(payload)!={"password"} or not intelligence.security.login(request.cookies.get("analysis_session"),request.headers.get("x-csrf-token"),payload["password"],clock()):
                return error("invalid_request",403)
            return {"authenticated":True}
        except Exception:
            return error("invalid_request",400)

    @app.post("/api/v1/intelligence/controls")
    async def intelligence_controls(request:Request):
        if not intelligence.security.authorize(request.cookies.get("analysis_session"),request.headers.get("x-csrf-token"),clock()):
            return error("invalid_request",403)
        try:
            from market_intelligence.store import encode
            receipt=intelligence.enqueue(await bounded_json(request),clock())
            return JSONResponse(status_code=202,content=json.loads(encode(receipt)))
        except Exception:
            return error("invalid_request",409)

    def respond(resource, revision, database_id=None, run_id=None):
        if investment_database is not None:
            return error("source_unavailable")
        now = clock()
        if reader:
            snapshot = reader.snapshot(now)
            telemetry.augment(snapshot, now)
            if revision is not None and revision != snapshot["meta"]["ledger_revision"]:
                raise ReadError("source_unavailable")
            if database_id is not None and database_id != snapshot["meta"]["database_id"]:
                raise ReadError("source_unavailable")
            if run_id is not None and run_id != snapshot["meta"]["telemetry_run_id"]:
                raise ReadError("source_unavailable")
            envelope = {"api_version": "1", "resource": resource, "meta": snapshot["meta"], "data": snapshot[resource]}
        else:
            if "error" in selected["responses"]:
                return error(selected["responses"]["error"]["code"])
            if resource not in selected["responses"] and resource != "health":
                raise ReadError("source_unavailable")
            meta = copy.deepcopy(fixtures["meta"])
            meta.update(copy.deepcopy(selected["meta_overrides"]))
            anchor = datetime.fromisoformat(fixtures["generated_at"].replace("Z", "+00:00"))
            offset = now - anchor

            def shift(value):
                if isinstance(value, dict):
                    return {key: shift(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [shift(item) for item in value]
                if isinstance(value, str) and value.endswith("Z"):
                    try:
                        return (datetime.fromisoformat(value.replace("Z", "+00:00")) + offset).isoformat().replace("+00:00", "Z")
                    except ValueError:
                        return value
                return value

            data = selected["responses"].get(resource)
            if resource == "performance" and scenario == "healthy_recorded":
                data = json.loads((ROOT / "history-fixture.json").read_text(encoding="utf-8"))
            if data is None:
                data = unknown_health({"overview": selected["responses"].get("overview", {"block_reasons": []})})
            envelope = shift({"api_version": "1", "resource": resource, "meta": meta,
                              "data": copy.deepcopy(data)})
        if not validator.is_valid(envelope):
            raise ReadError("ledger_invalid")
        return envelope

    @app.get("/api/v1/overview")
    def overview():
        return respond("overview", None)

    @app.get("/api/v1/positions")
    def positions(revision: int | None = None, database_id: str | None = None, run_id: str | None = None):
        return respond("positions", revision, database_id, run_id)

    @app.get("/api/v1/health")
    def health(revision: int | None = None, database_id: str | None = None, run_id: str | None = None):
        return respond("health", revision, database_id, run_id)

    def collection(resource, limit, cursor, after=0, start=None, end=None, symbol=None):
        if reader:
            envelope = HistoryReader(reader.database).page(resource, limit, cursor, after, start, end, symbol, clock())
            if not validator.is_valid(envelope):
                raise ReadError("ledger_invalid")
            return envelope
        if limit < 1 or limit > 1000 or after < 0:
            raise ReadError("invalid_request")
        envelope = respond(resource, None)
        if isinstance(envelope, JSONResponse):
            return envelope
        data = envelope["data"]
        if resource == "events":
            rows = [row for row in data["items"] if row["sequence"] > after]
            data["items"] = rows
        elif resource == "performance":
            from datetime import date
            try:
                for value in (start, end):
                    if value and date.fromisoformat(value).isoformat() != value:
                        raise ValueError("date")
                if start and end and start > end:
                    raise ValueError("range")
            except ValueError as exc:
                raise ReadError("invalid_request") from exc
            data["daily"] = [row for row in data["daily"] if (not start or row["day"] >= start) and (not end or row["day"] <= end)]
            data["exits"] = [row for row in data["exits"] if (not start or row["accounting_day"] and row["accounting_day"] >= start) and (not end or row["accounting_day"] and row["accounting_day"] <= end) and (not symbol or row["symbol"] == symbol)]
            ids = {row["execution_id"] for row in data["exits"]}
            data["corrections"] = [row for row in data["corrections"] if row["execution_id"] in ids]
        envelope["data"] = demo_page(data, resource, limit, cursor, [start, end, symbol, after], after)
        if not validator.is_valid(envelope):
            raise ReadError("ledger_invalid")
        return envelope

    @app.get("/api/v1/events")
    def events(after: int = 0, limit: int = 100, cursor: str | None = None):
        return collection("events", limit, cursor, after)

    @app.get("/api/v1/orders")
    def orders(limit: int = 100, cursor: str | None = None):
        return collection("orders", limit, cursor)

    @app.get("/api/v1/performance")
    def performance(request: Request, limit: int = 100, cursor: str | None = None, symbol: str | None = None):
        return collection("performance", limit, cursor, start=request.query_params.get("from"), end=request.query_params.get("to"), symbol=symbol)

    if serve_frontend:
        app.mount("/assets", StaticFiles(directory=static_directory / "assets"), name="assets")

        @app.get("/")
        @app.get("/positions")
        @app.get("/health")
        @app.get("/activity")
        @app.get("/history")
        @app.get("/intelligence")
        @app.get("/guide")
        @app.get("/investment")
        @app.get("/investment-guide")
        def index():
            return FileResponse(static_directory / "index.html")

    return app