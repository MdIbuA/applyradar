# My 10x Solution - Mohamed Ibrahim Abdullah

**Project:** ApplyRadar, every application deadline in one place
**Repository:** https://github.com/MdIbuA/applyradar

## The problem

Students chasing internships, scholarships and hackathons get opportunities from everywhere: forwarded WhatsApp messages, college emails, LinkedIn posts, portal pages. Each one is a paragraph in which the application deadline sits next to an event date, a results date and a start date, so it's easy to note the wrong one or not note it at all. Weeks later the only way to know what's due is to scroll back through every chat. That's how deadlines get discovered the day after they passed.

**Who it's for:** a student (me, first) juggling 5–15 open applications at a time.

**The 10x claim:** logging an opportunity goes from *read the whole message, find the right date, type it into a sheet* to *paste it*. Knowing what's due goes from *remember to check, then scroll* to *a PDF that is already waiting every morning*.

**Non-goal:** helping write the applications themselves (essays, CV versions). ApplyRadar only tracks what's open, its deadline, and its status.

## How I built it

ApplyRadar is a Python web API (FastAPI) with a SQLite database. You sign up, log in, and get a token that every private route checks. You can add an opportunity by hand, or paste the announcement and let an AI model pull out the title, organisation, type, **application deadline** and link. The server checks the AI's answer before saving it: the deadline must be a real date, the type must come from a fixed list, and the link must actually appear in the text you pasted. If the answer fails twice, nothing is saved and you get a clear error. Every morning a background job builds a one-page PDF for each user: what's overdue, what's due in the next 14 days, and what still has no deadline.

### The six concepts

1. **API endpoints**: 13 routes with input validation and correct status codes (`app/main.py`).
2. **Database**: SQLite with constraints and an index; data survives restarts, and every query is limited to the logged-in user (`app/db.py`).
3. **Authentication**: bcrypt-hashed passwords and expiring JWTs. Other users' items return 404 (`app/auth.py`).
4. **Background job**: APScheduler builds everyone's digest daily at 07:30 India time (`app/jobs.py`).
5. **Reporting (PDF)**: ReportLab digest with overdue, due soon and undated sections (`app/reports.py`).
6. **LLM integration**: one narrow job (announcement → structured fields), with schema validation, a source-text check, one repair, a quarantine log, a cost log, a timeout, retries, a stub mode and a kill switch (`app/llm/`, `prompts/extract-v1.md`).

**Swaps:** none. All six are from the original list. Caching wasn't needed at this size.

Reused from my assignments: the CRUD structure and validation (A1/A2), the bearer-token guard pattern (Auth), and the LLM client with timeout and retry policy (AI API assignment).

### Proof it works

- 21 automated tests, including forged tokens, cross-user access, an AI that invents a link, and an AI that returns an impossible date twice.
- Real model run (qwen3.5 on Ollama): from *"Event on 14th Dec… Last date to register 6th dec"*, it picked **6 Dec**, the registration deadline, not the event date. A guest-lecture notice was correctly classified as *not an opportunity*.

### Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # set JWT_SECRET
python seed.py                # demo user + sample deadlines
uvicorn app.main:app          # open http://localhost:8000/docs
```

Log in as `demo@applyradar.local` / `demo-password-123`. The README has a step-by-step 5-minute demo path.
