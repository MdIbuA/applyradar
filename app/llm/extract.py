"""One narrow AI job: pull title / organisation / kind / deadline / url out of a
pasted announcement.

model -> parse -> validate schema -> check facts against the source text
      -> one repair if anything failed -> else quarantine + ExtractionFailed

The model may only *copy* facts. Code then checks what code can check:
the deadline is a real calendar date, and the url literally appears in the text.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from app import config
from app.llm import client

PROMPT_VERSION = "extract-v1"
PROMPT_FILE = config.ROOT / "prompts" / f"{PROMPT_VERSION}.md"
log = logging.getLogger("uvicorn.error")

Kind = Literal["internship", "job", "scholarship", "hackathon", "competition", "program", "other"]


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=3, max_length=100)
    organisation: StrictStr | None = Field(default=None, max_length=80)
    kind: Kind
    deadline: StrictStr | None = None
    url: StrictStr | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0, le=1)

    @field_validator("deadline")
    @classmethod
    def real_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("deadline must be a real date written as YYYY-MM-DD, or null") from None


STUB = Extraction(title="Stub opportunity (LLM_STUB=1)", organisation=None, kind="other",
                  deadline=None, url=None, confidence=0.0)


class ExtractionFailed(Exception):
    pass


class ExtractionDisabled(Exception):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse(raw: str) -> Extraction:
    text = re.sub(r"<think>.*?</think>", "", _FENCE.sub("", raw.strip()), flags=re.DOTALL).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object found in the reply")
    try:
        return Extraction.model_validate_json(text[start : end + 1])
    except ValidationError as exc:
        raise ValueError("; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'object'}: {e['msg'].removeprefix('Value error, ')}"
            for e in exc.errors()
        )) from None


def _check_against_source(result: Extraction, source: str) -> None:
    """Facts the model claims must be backed by the text it was given."""
    if result.url and result.url.rstrip("/.") not in source:
        raise ValueError(f"url: '{result.url}' does not appear in the announcement; copy it exactly or use null")


def _log(name: str, record: dict) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOG_DIR / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract(announcement: str, today: date | None = None) -> Extraction:
    if not config.LLM_ENABLED:
        raise ExtractionDisabled()
    if config.LLM_STUB:
        return STUB

    today = today or date.today()
    messages = [
        {"role": "system", "content": PROMPT_FILE.read_text(encoding="utf-8")},
        # Pasted text can contain instructions; it travels as JSON data in the user turn.
        {"role": "user", "content": json.dumps({"announcement": announcement, "today": today.isoformat()},
                                               ensure_ascii=False)},
    ]
    started, tokens_in, tokens_out, repairs, outcome = time.perf_counter(), 0, 0, 0, "ok"
    model = client.model_name()

    def attempt(msgs):
        nonlocal tokens_in, tokens_out, model
        reply = client.complete(msgs)
        tokens_in += reply.input_tokens
        tokens_out += reply.output_tokens
        model = reply.model
        return reply.text

    try:
        first = attempt(messages)
        try:
            result = _parse(first)
            _check_against_source(result, announcement)
            return result
        except ValueError as first_error:
            repairs = 1
            second = attempt(messages + [
                {"role": "assistant", "content": first},
                {"role": "user", "content": f"Your reply was rejected: {first_error}. "
                                            "Reply again with only the corrected JSON object."},
            ])
            try:
                result = _parse(second)
                _check_against_source(result, announcement)
                return result
            except ValueError as second_error:
                outcome = "quarantined"
                _log("quarantine.jsonl", {
                    "ts": _now(), "prompt_version": PROMPT_VERSION, "model": model, "input": announcement,
                    "first_output": first, "first_error": str(first_error),
                    "repair_output": second, "repair_error": str(second_error),
                })
                raise ExtractionFailed(str(second_error)) from None
    except (client.LLMTimeout, client.LLMUnavailable) as exc:
        outcome = type(exc).__name__
        raise
    finally:
        record = {"ts": _now(), "prompt_version": PROMPT_VERSION, "model": model, "input_tokens": tokens_in,
                  "output_tokens": tokens_out, "duration_ms": round((time.perf_counter() - started) * 1000),
                  "repairs": repairs, "outcome": outcome}
        _log("llm-calls.jsonl", record)
        log.info("llm_call %s", json.dumps(record))
