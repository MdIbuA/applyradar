"""Create a demo account with a realistic spread of deadlines, relative to today.

    python seed.py            # demo@applyradar.local / demo-password-123

Safe to re-run: the demo user's opportunities are replaced each time.
All data is made up or public; no real personal data.
"""

from datetime import timedelta

from app import auth, db, reports

EMAIL, PASSWORD = "demo@applyradar.local", "demo-password-123"

# (title, organisation, kind, days from today or None, status, url)
DEMO = [
    ("Hostel fee and exam registration", "College office", "other", -2, "interested", None),
    ("Smart Campus Hackathon", "IEEE Student Branch", "hackathon", 1, "applying", "https://example.org/hack"),
    ("Global Internship Program - Batch 2", "HENNGE", "internship", 5, "applying", "https://hennge.com/global/gip/"),
    ("MS/PhD Fall 2027", "KAUST", "scholarship", 12, "applying", "https://admissions.kaust.edu.sa"),
    ("Summer Research Fellowship", "Indian Academy of Sciences", "program", 40, "interested", None),
    ("AI-Enabled Application Support Intern", "Blue Yonder", "internship", -10, "applied", None),
    ("RL Gym Hackathon", "HackerRank", "hackathon", 9, "interested", None),
    ("Open-source mentorship (GSoC 2027)", "Google", "program", None, "interested", "https://summerofcode.withgoogle.com"),
]


def main() -> None:
    db.init()
    today = reports.local_today()
    with db.session() as conn:
        user = db.get_user_for_login(conn, EMAIL)
        if user is None:
            user = db.create_user(conn, EMAIL, auth.hash_password(PASSWORD))
        conn.execute("DELETE FROM opportunities WHERE user_id = ?", (user["id"],))
        for title, org, kind, days, status, url in DEMO:
            deadline = (today + timedelta(days=days)).isoformat() if days is not None else None
            db.create_opportunity(conn, user["id"], {"title": title, "organisation": org, "kind": kind,
                                                     "deadline": deadline, "status": status, "url": url})
    print(f"Seeded {len(DEMO)} opportunities for {EMAIL} (password: {PASSWORD}), deadlines relative to {today}.")


if __name__ == "__main__":
    main()
