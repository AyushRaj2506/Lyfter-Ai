import logging
import sqlite3
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import Database, SessionLocks
from .llm import AllProvidersUnavailable, Completion, Gateway, configure_cache, litellm
from .schemas import ChatRequest, ChatResponse, HealthResponse, Metadata, SessionHistory
from .simulation import parse_simulation

logger = logging.getLogger("lyftr")


class JsonCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code == 400:
            headers = {key: value for key, value in response.headers.items() if key not in ("content-length", "content-type")}
            return JSONResponse({"detail": "origin not allowed"}, status_code=400, headers=headers)
        return response


def create_app(settings: Settings | None = None, completion: Completion | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    db = Database(settings.database_path)
    locks = SessionLocks()
    gateway = Gateway(settings, completion=completion)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
        for name in ("lyftr", "app"):
            logging.getLogger(name).setLevel(settings.log_level)
        try:
            db.start()
        except (OSError, sqlite3.Error) as error:
            logger.error("SQLite initialization failed (%s)", type(error).__name__)

        previous_cache = litellm.cache
        cache = configure_cache(settings)
        litellm.cache = cache
        try:
            yield
        finally:
            litellm.cache = previous_cache
            await cache.disconnect()
            db.close()

    app = FastAPI(
        title="Lyftr Chat API",
        lifespan=lifespan,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error("request_failed error=%s", type(error).__name__)
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        metadata = getattr(request.state, "metadata", None)
        route = request.scope.get("route")
        logger.info(
            "request method=%s route=%s status=%s source=%s key_id=%s fallbacks=%s latency_ms=%s",
            request.method,
            getattr(route, "path", "-"),
            response.status_code,
            metadata.resolution_source if metadata else "-",
            metadata.key_id if metadata and metadata.key_id else "-",
            metadata.fallback_attempts if metadata else getattr(request.state, "failed_keys", 0),
            max(1, round((perf_counter() - started) * 1000)),
        )
        return response

    app.add_middleware(
        JsonCORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=settings.allowed_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError):
        return JSONResponse(
            {"detail": [{field: entry[field] for field in ("type", "loc", "msg")} for entry in error.errors()]},
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        if error.status_code == 400 and request.method == "POST" and request.url.path == "/chat":
            return JSONResponse(
                {"detail": [{"type": "json_invalid", "loc": ["body"], "msg": "Invalid JSON body"}]},
                status_code=422,
            )
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)

    @app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
    def health(response: Response) -> HealthResponse:
        healthy = db.ready() and len(settings.api_keys) >= 2 and litellm.cache is not None
        if not healthy:
            response.status_code = 503
        return HealthResponse(
            status="ok" if healthy else "degraded",
            keys_configured=len(settings.api_keys),
            cache_enabled=litellm.cache is not None,
        )

    @app.post("/chat", response_model=ChatResponse)
    def chat(
        data: ChatRequest,
        request: Request,
        x_simulate_failure: Annotated[str | None, Header(alias="X-Simulate-Failure")] = None,
    ) -> ChatResponse:
        started = perf_counter()
        simulation = (
            parse_simulation(x_simulate_failure, len(settings.api_keys))
            if settings.enable_failure_simulation else {}
        )
        with locks.hold(data.session_id):
            stored_prompt, history = db.context(data.session_id, settings.max_history_messages)
            system_prompt = data.system_prompt or stored_prompt or settings.default_system_prompt
            messages = [
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": data.message},
            ]
            try:
                result = gateway.answer(messages, simulation)
            except AllProvidersUnavailable:
                request.state.failed_keys = len(settings.api_keys)
                raise HTTPException(status_code=503, detail="all providers unavailable") from None

            metadata = Metadata(
                resolution_source="LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
                model=settings.model,
                latency_ms=max(1, round((perf_counter() - started) * 1000)),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                key_id=result.key_id,
                fallback_attempts=result.fallback_attempts,
            )
            db.save_turn(data.session_id, system_prompt, data.message, result.reply, metadata)
        request.state.metadata = metadata
        return ChatResponse(session_id=data.session_id, reply=result.reply, metadata=metadata)

    @app.get("/sessions/{session_id:path}/messages", response_model=SessionHistory)
    def session_history(session_id: str) -> SessionHistory:
        messages = db.history(session_id)
        if not messages:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionHistory(session_id=session_id, messages=messages)

    return app


app = create_app()
