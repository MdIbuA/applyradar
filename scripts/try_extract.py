"""Run the AI extraction on a few sample announcements and print what comes back.

    python scripts/try_extract.py

Uses the real model from .env (no server, no database). Samples are made up.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.extract import ExtractionFailed, extract  # noqa: E402

SAMPLES = [
    "Applications are open for the Microsoft Research India PhD Fellowship 2027! Submit your proposal by "
    "30 January 2027 via https://aka.ms/msr-india-fellowship. Results announced in April.",
    "*Fwd* 🚀 CodeStorm 24-hr hackathon @ PSG Tech, prizes worth 1L! Event on 14th Dec. Team of 2-4. "
    "Last date to register 6th dec. Reg link: bit.ly/codestorm26 . Contact Arun for doubts",
    "Dear students, the department will conduct a guest lecture on cloud security on Friday. Attendance "
    "is mandatory for third years.",
]

for text in SAMPLES:
    try:
        result = extract(text, today=date(2026, 11, 24))
        print(json.dumps(result.model_dump(), ensure_ascii=False))
    except ExtractionFailed as exc:
        print("FAILED:", exc)
