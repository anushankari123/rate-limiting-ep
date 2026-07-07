import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()

# --- Config ---------------------------------------------------------------

EMAIL = os.environ.get("USER_EMAIL", "your-login-email@example.com")

ALLOWED_ORIGINS = [
    "https://app-oeacc6.example.com",   # assigned origin
    # Add the exact origin of the grader/exam page below, e.g.:
    # "https://exam.someplatform.com",
]

RATE_LIMIT = 13          # requests
WINDOW_SECONDS = 10       # per this many seconds

# client_id -> deque of request timestamps (monotonic)
_buckets = defaultdict(deque)
_lock = Lock()


# --- Middleware 1: Request context ----------------------------------------

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# --- Middleware 3: Per-client rate limiting --------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_id = request.headers.get("X-Client-Id")
        if client_id is None:
            client_id = request.client.host if request.client else "anonymous"

        now = time.monotonic()
        with _lock:
            bucket = _buckets[client_id]
            while bucket and now - bucket[0] > WINDOW_SECONDS:
                bucket.popleft()

            if len(bucket) >= RATE_LIMIT:
                request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded", "request_id": request_id},
                )

            bucket.append(now)

        return await call_next(request)


# --- Register middleware ---------------------------------------------------
# NOTE: Starlette treats the LAST added middleware as the OUTERMOST layer.
# We want execution order (outer -> inner) = CORS -> RequestContext -> RateLimit
# so add them in this order: RateLimit, RequestContext, CORS (last = outermost).

app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# --- Route -------------------------------------------------------------

@app.get("/ping")
async def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}


@app.get("/")
async def root():
    return {"status": "ok"}