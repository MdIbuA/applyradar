"""Background work: every morning, build a fresh digest PDF for every user.

APScheduler runs inside the API process on its own thread, so nothing extra
has to be installed or started. One user's failure is logged and skipped;
it never stops the others or crashes the server.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import config, db, reports

log = logging.getLogger("uvicorn.error")
_scheduler: BackgroundScheduler | None = None


def run_daily_digest() -> dict:
    with db.session() as conn:
        user_ids = db.all_user_ids(conn)
    made, failed = 0, 0
    for user_id in user_ids:
        try:
            reports.generate_for_user(user_id, trigger="schedule")
            made += 1
        except Exception:  # noqa: BLE001 - isolate each user's failure
            failed += 1
            log.exception("digest failed for user %s", user_id)
    log.info("daily digest: %s reports, %s failures", made, failed)
    return {"reports": made, "failures": failed}


def start() -> None:
    global _scheduler
    if not config.SCHEDULER_ENABLED or _scheduler is not None:
        return
    minute, hour = config.DIGEST_CRON.split()
    _scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
    _scheduler.add_job(run_daily_digest, CronTrigger(minute=minute, hour=hour, timezone=config.TIMEZONE),
                       id="daily-digest", max_instances=1, coalesce=True, misfire_grace_time=3600)
    _scheduler.start()
    log.info("scheduler started: daily digest at %s:%s %s", hour.zfill(2), minute.zfill(2), config.TIMEZONE)


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run() -> str | None:
    job = _scheduler.get_job("daily-digest") if _scheduler else None
    return job.next_run_time.isoformat() if job and job.next_run_time else None
