"""ApplyRadar API: every application deadline in one place.

Paste an announcement -> AI pulls out the deadline -> it lands in your list
-> every morning a PDF digest tells you what's due.
"""

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app import auth, config, db, jobs, reports
from app.auth import AuthFailure, current_user
from app.llm import client as llm_client
from app.llm.extract import ExtractionDisabled, ExtractionFailed, extract


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(config.JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET must be set to at least 32 random characters (see .env.example)")
    db.init()
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(
    title="ApplyRadar",
    version="1.0",
    description="Track internship, scholarship and hackathon deadlines. Paste an announcement, "
                "let the AI pull out the deadline, get a daily PDF digest.",
    lifespan=lifespan,
)

Kind = Literal["internship", "job", "scholarship", "hackathon", "competition", "program", "other"]
Status = Literal["interested", "applying", "applied", "rejected", "accepted", "dropped"]


# ---------- schemas ----------

class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: StrictStr = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: StrictStr = Field(min_length=8, max_length=128)


def _real_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("must be a real date written as YYYY-MM-DD") from None


class OpportunityIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "title": "KAUST MS/PhD Fall 2027", "organisation": "KAUST", "kind": "scholarship",
        "deadline": "2027-01-15", "url": "https://admissions.kaust.edu.sa"}]})

    title: StrictStr = Field(min_length=3, max_length=100)
    organisation: StrictStr | None = Field(default=None, max_length=80)
    kind: Kind = "other"
    deadline: StrictStr | None = None
    url: StrictStr | None = Field(default=None, max_length=500)
    status: Status = "interested"

    @field_validator("deadline")
    @classmethod
    def deadline_is_real_date(cls, value: str | None) -> str | None:
        return _real_date(value)


class OpportunityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"status": "applied"}]})

    title: StrictStr | None = Field(default=None, min_length=3, max_length=100)
    organisation: StrictStr | None = Field(default=None, max_length=80)
    kind: Kind | None = None
    deadline: StrictStr | None = None
    url: StrictStr | None = Field(default=None, max_length=500)
    status: Status | None = None

    @field_validator("deadline")
    @classmethod
    def deadline_is_real_date(cls, value: str | None) -> str | None:
        return _real_date(value)


class Opportunity(BaseModel):
    id: int
    title: str
    organisation: str | None
    kind: Kind
    deadline: str | None
    url: str | None
    status: Status
    source: Literal["manual", "ai"]
    created_at: str
    updated_at: str


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"text": (
        "Applications are open for the Microsoft Research India PhD Fellowship 2027! "
        "Submit your proposal by 30 January 2027 via https://aka.ms/msr-india-fellowship. "
        "Results announced in April.")}]})

    text: StrictStr = Field(min_length=20, max_length=5000)


class ExtractResponse(BaseModel):
    saved: bool
    confidence: float
    opportunity: Opportunity | None
    extracted: dict


class ErrorMessage(BaseModel):
    error: str


E = lambda code, desc: {code: {"model": ErrorMessage, "description": desc}}  # noqa: E731
UNAUTH = E(401, "Missing, invalid or expired token")
NOT_FOUND = E(404, "No such opportunity in your account")
BAD = E(400, "Invalid input")


# ---------- errors ----------

def error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


@app.exception_handler(AuthFailure)
async def _auth_failure(request: Request, exc: AuthFailure):
    return JSONResponse(status_code=401, content={"error": exc.message}, headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(RequestValidationError)
async def _bad_input(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
    message = first["msg"].removeprefix("Value error, ")
    return error(400, f"{field}: {message}" if field else message)


# ---------- auth ----------

@app.post("/auth/signup", status_code=201, responses={**BAD, **E(409, "Email already registered")}, tags=["auth"])
def signup(body: Credentials):
    with db.session() as conn:
        user = db.create_user(conn, body.email.strip().lower(), auth.hash_password(body.password))
    if user is None:
        return error(409, "An account with this email already exists")
    return user


@app.post("/auth/login", responses={**BAD, **E(401, "Wrong email or password")}, tags=["auth"])
def login(body: Credentials):
    user = auth.login(body.email.strip().lower(), body.password)
    if user is None:
        return error(401, "Invalid email or password")
    token, expires_in = auth.issue_token(user["id"])
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@app.get("/me", responses=UNAUTH, tags=["auth"])
def me(user: dict = Depends(current_user)):
    return user


# ---------- opportunities ----------

@app.get("/opportunities", response_model=list[Opportunity], responses=UNAUTH, tags=["opportunities"],
         summary="Your opportunities, soonest deadline first")
def list_opps(status: Status | None = None, kind: Kind | None = None, user: dict = Depends(current_user)):
    with db.session() as conn:
        return db.list_opportunities(conn, user["id"], status, kind)


@app.post("/opportunities", status_code=201, response_model=Opportunity, responses={**UNAUTH, **BAD},
          tags=["opportunities"], summary="Add one by hand")
def create_opp(body: OpportunityIn, user: dict = Depends(current_user)):
    with db.session() as conn:
        return db.create_opportunity(conn, user["id"], body.model_dump())


@app.post("/opportunities/extract", status_code=201, response_model=ExtractResponse,
          responses={**UNAUTH, **BAD, **E(422, "AI answer failed validation twice"),
                     **E(502, "AI provider unavailable"), **E(503, "AI switched off"), **E(504, "AI provider timed out")},
          tags=["opportunities"], summary="Paste an announcement; AI fills in title, kind, deadline, link")
def extract_opp(body: ExtractRequest, response: Response,
                save: bool = Query(True, description="false = preview only, nothing is stored"),
                user: dict = Depends(current_user)):
    try:
        found = extract(body.text, today=reports.local_today())
    except ExtractionDisabled:
        return error(503, "AI extraction is switched off; add the opportunity by hand")
    except ExtractionFailed:
        return error(422, "The AI's answer didn't pass validation; it has been logged. Add it by hand.")
    except llm_client.LLMTimeout:
        return error(504, "The AI provider took too long to answer")
    except llm_client.LLMUnavailable:
        return error(502, "The AI provider is unavailable right now")

    data = found.model_dump(exclude={"confidence"})
    if not save:
        response.status_code = 200
        return {"saved": False, "confidence": found.confidence, "opportunity": None, "extracted": data}
    with db.session() as conn:
        opp = db.create_opportunity(conn, user["id"], data, source="ai")
    return {"saved": True, "confidence": found.confidence, "opportunity": opp, "extracted": data}


@app.get("/opportunities/{opp_id}", response_model=Opportunity, responses={**UNAUTH, **NOT_FOUND},
         tags=["opportunities"])
def get_opp(opp_id: int, user: dict = Depends(current_user)):
    with db.session() as conn:
        opp = db.get_opportunity(conn, user["id"], opp_id)
    return opp or error(404, f"Opportunity {opp_id} not found")


@app.patch("/opportunities/{opp_id}", response_model=Opportunity, responses={**UNAUTH, **NOT_FOUND, **BAD},
           tags=["opportunities"], summary="Change status, deadline or any field")
def patch_opp(opp_id: int, body: OpportunityPatch, user: dict = Depends(current_user)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return error(400, "send at least one field to change")
    if any(changes.get(k) is None for k in ("title", "kind", "status") if k in changes):
        return error(400, "title, kind and status can't be null")
    with db.session() as conn:
        opp = db.update_opportunity(conn, user["id"], opp_id, changes)
    return opp or error(404, f"Opportunity {opp_id} not found")


@app.delete("/opportunities/{opp_id}", status_code=204, responses={**UNAUTH, **NOT_FOUND}, tags=["opportunities"])
def delete_opp(opp_id: int, user: dict = Depends(current_user)):
    with db.session() as conn:
        deleted = db.delete_opportunity(conn, user["id"], opp_id)
    return Response(status_code=204) if deleted else error(404, f"Opportunity {opp_id} not found")


# ---------- digest & reports ----------

@app.get("/digest", responses=UNAUTH, tags=["reports"], summary="Overdue, due in 14 days, and undated items")
def digest(user: dict = Depends(current_user)):
    with db.session() as conn:
        return reports.build_digest(conn, user["id"])


@app.post("/reports/digest", status_code=201, responses=UNAUTH, tags=["reports"],
          summary="Build a PDF digest now (the scheduler also does this every morning)")
def make_report(user: dict = Depends(current_user)):
    rec = reports.generate_for_user(user["id"], trigger="manual")
    return {"id": rec["id"], "items": rec["items"], "created_at": rec["created_at"], "download": "/reports/latest"}


@app.get("/reports/latest", responses={**UNAUTH, **E(404, "No report yet")}, tags=["reports"],
         response_class=FileResponse, summary="Download your most recent digest PDF")
def latest_report(user: dict = Depends(current_user)):
    with db.session() as conn:
        rec = db.latest_report(conn, user["id"])
    if rec is None or not Path(rec["path"]).exists():
        return error(404, "No digest yet. POST /reports/digest to make one.")
    return FileResponse(rec["path"], media_type="application/pdf", filename=Path(rec["path"]).name)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "next_digest_run": jobs.next_run(), "llm": (
        "stub" if config.LLM_STUB else "on" if config.LLM_ENABLED else "off")}
