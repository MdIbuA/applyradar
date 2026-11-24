"""End-to-end tests through the HTTP API: auth, isolation between users,
CRUD + validation, AI extraction (with a scripted fake model), digest maths,
the PDF report, and the scheduled job. No network needed. Run: pytest -q
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, jobs, reports
from app.llm import client as llm_client

GOOD = ('{"title": "MSR India PhD Fellowship 2027", "organisation": "Microsoft Research", "kind": "scholarship", '
        '"deadline": "2027-01-30", "url": "https://aka.ms/msr-india-fellowship", "confidence": 0.9}')
ANNOUNCEMENT = ("Applications are open for the Microsoft Research India PhD Fellowship 2027! Submit your proposal "
                "by 30 January 2027 via https://aka.ms/msr-india-fellowship. Results announced in April.")


class FakeModel:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = self.completions = self

    def create(self, model, messages, temperature, **kw):
        self.calls.append(messages)
        msg = type("M", (), {"content": self.replies.pop(0)})()
        usage = type("U", (), {"prompt_tokens": 50, "completion_tokens": 10})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()], "usage": usage, "model": model})()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "JWT_SECRET", "x" * 40)
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    monkeypatch.setattr(config, "LLM_STUB", False)
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    for k, v in {"LLM_BASE_URL": "http://llm", "LLM_API_KEY": "k", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(k, v)
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(api, email="ibu@example.com", password="password123"):
    api.post("/auth/signup", json={"email": email, "password": password})
    token = api.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def fake(monkeypatch, replies):
    model = FakeModel(replies)
    monkeypatch.setattr(llm_client, "_client", lambda: model)
    return model


# ---------- auth ----------

def test_signup_login_and_duplicates(api):
    assert api.post("/auth/signup", json={"email": "A@x.com", "password": "password123"}).status_code == 201
    assert api.post("/auth/signup", json={"email": "a@x.com", "password": "password123"}).status_code == 409
    assert api.post("/auth/login", json={"email": "a@x.com", "password": "wrong-password"}).status_code == 401
    assert api.post("/auth/login", json={"email": "nobody@x.com", "password": "password123"}).status_code == 401
    assert api.post("/auth/signup", json={"email": "bad", "password": "password123"}).status_code == 400
    assert api.post("/auth/signup", json={"email": "b@x.com", "password": "short"}).status_code == 400


@pytest.mark.parametrize("path", ["/opportunities", "/digest", "/reports/latest", "/me"])
def test_protected_routes_need_a_valid_token(api, path):
    assert api.get(path).status_code == 401
    assert api.get(path, headers={"Authorization": "Bearer nonsense"}).status_code == 401
    import jwt
    forged = jwt.encode({"sub": "1", "exp": 9999999999}, "another-secret-that-is-long-enough", algorithm="HS256")
    assert api.get(path, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_users_cannot_see_each_others_data(api):
    a, b = login(api, "a@x.com"), login(api, "b@x.com")
    opp = api.post("/opportunities", json={"title": "Secret plan", "kind": "job"}, headers=a).json()
    assert api.get("/opportunities", headers=b).json() == []
    assert api.get(f"/opportunities/{opp['id']}", headers=b).status_code == 404
    assert api.patch(f"/opportunities/{opp['id']}", json={"status": "dropped"}, headers=b).status_code == 404
    assert api.delete(f"/opportunities/{opp['id']}", headers=b).status_code == 404
    assert api.get(f"/opportunities/{opp['id']}", headers=a).status_code == 200


# ---------- CRUD ----------

def test_crud_and_sorting(api):
    h = login(api)
    later = api.post("/opportunities", json={"title": "Later one", "deadline": "2027-05-01"}, headers=h).json()
    api.post("/opportunities", json={"title": "No date"}, headers=h)
    api.post("/opportunities", json={"title": "Sooner one", "deadline": "2027-01-01"}, headers=h)
    assert [o["title"] for o in api.get("/opportunities", headers=h).json()] == ["Sooner one", "Later one", "No date"]
    r = api.patch(f"/opportunities/{later['id']}", json={"status": "applied"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert [o["title"] for o in api.get("/opportunities?status=applied", headers=h).json()] == ["Later one"]
    assert api.delete(f"/opportunities/{later['id']}", headers=h).status_code == 204


@pytest.mark.parametrize("body", [{}, {"title": "ab"}, {"title": "Fine", "deadline": "2027-02-30"},
                                  {"title": "Fine", "kind": "party"}, {"title": "Fine", "extra": 1}])
def test_create_validation_400(api, body):
    assert api.post("/opportunities", json=body, headers=login(api)).status_code == 400


def test_patch_rules(api):
    h = login(api)
    opp = api.post("/opportunities", json={"title": "Thing"}, headers=h).json()
    assert api.patch(f"/opportunities/{opp['id']}", json={}, headers=h).status_code == 400
    assert api.patch(f"/opportunities/{opp['id']}", json={"status": None}, headers=h).status_code == 400
    assert api.patch(f"/opportunities/{opp['id']}", json={"status": "won"}, headers=h).status_code == 400
    assert api.patch("/opportunities/999", json={"status": "applied"}, headers=h).status_code == 404


# ---------- AI extraction ----------

def test_extract_saves_ai_opportunity(api, monkeypatch):
    model = fake(monkeypatch, [GOOD])
    r = api.post("/opportunities/extract", json={"text": ANNOUNCEMENT}, headers=login(api))
    assert r.status_code == 201 and r.json()["saved"] is True
    assert r.json()["opportunity"]["deadline"] == "2027-01-30" and r.json()["opportunity"]["source"] == "ai"
    system, user = model.calls[0]
    assert ANNOUNCEMENT not in system["content"] and ANNOUNCEMENT in user["content"]


def test_extract_preview_does_not_save(api, monkeypatch):
    fake(monkeypatch, [GOOD])
    h = login(api)
    r = api.post("/opportunities/extract?save=false", json={"text": ANNOUNCEMENT}, headers=h)
    assert r.status_code == 200 and r.json()["saved"] is False
    assert api.get("/opportunities", headers=h).json() == []


def test_invented_url_is_repaired(api, monkeypatch):
    invented = GOOD.replace("https://aka.ms/msr-india-fellowship", "https://msr.example/apply")
    model = fake(monkeypatch, [invented, GOOD])
    r = api.post("/opportunities/extract", json={"text": ANNOUNCEMENT}, headers=login(api))
    assert r.status_code == 201 and len(model.calls) == 2
    assert "does not appear" in model.calls[1][-1]["content"]


def test_impossible_date_twice_is_422_and_quarantined(api, monkeypatch, tmp_path):
    bad = GOOD.replace("2027-01-30", "2027-02-30")
    fake(monkeypatch, [bad, bad])
    r = api.post("/opportunities/extract", json={"text": ANNOUNCEMENT}, headers=login(api))
    assert r.status_code == 422 and "2027-02-30" not in r.text
    assert (tmp_path / "logs" / "quarantine.jsonl").exists()
    assert (tmp_path / "logs" / "llm-calls.jsonl").read_text().count('"repairs": 1') == 1


def test_extract_kill_switch_and_short_input(api, monkeypatch):
    h = login(api)
    model = fake(monkeypatch, [])
    assert api.post("/opportunities/extract", json={"text": "too short"}, headers=h).status_code == 400
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert api.post("/opportunities/extract", json={"text": ANNOUNCEMENT}, headers=h).status_code == 503
    assert model.calls == []


# ---------- digest, PDF, scheduled job ----------

def test_digest_buckets_and_day_counts(api):
    h = login(api)
    today = reports.local_today()
    for title, days, status in [("Overdue", -1, "interested"), ("Today", 0, "applying"), ("Soon", 3, "interested"),
                                ("Far", 30, "interested"), ("Already applied", 2, "applied")]:
        api.post("/opportunities", json={"title": title, "status": status,
                                         "deadline": (today + timedelta(days=days)).isoformat()}, headers=h)
    api.post("/opportunities", json={"title": "Undated"}, headers=h)
    d = api.get("/digest", headers=h).json()
    assert [(i["title"], i["days_left"]) for i in d["overdue"]] == [("Overdue", -1)]
    assert [(i["title"], i["days_left"]) for i in d["due_soon"]] == [("Today", 0), ("Soon", 3)]
    assert [i["title"] for i in d["no_deadline"]] == ["Undated"]


def test_pdf_report_roundtrip(api):
    h = login(api)
    assert api.get("/reports/latest", headers=h).status_code == 404
    api.post("/opportunities", json={"title": "A <b>&</b> B", "deadline": date.today().isoformat()}, headers=h)
    made = api.post("/reports/digest", headers=h)
    assert made.status_code == 201 and made.json()["items"] == 1
    pdf = api.get("/reports/latest", headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_scheduled_job_makes_one_report_per_user(api):
    login(api, "a@x.com")
    login(api, "b@x.com")
    assert jobs.run_daily_digest() == {"reports": 2, "failures": 0}
    assert api.get("/reports/latest", headers=login(api, "a@x.com")).status_code == 200
