"""Daily automatic job.

  1. Read Gmail: stop sequences for anyone who replied, honour opt-outs, catch bounces
  2. Research new leads (Claude + web) -> verify contacts -> dedupe -> store
  3. Auto-approve High/Medium leads whose email is confirmed on their own website
  4. Send due follow-ups + new first emails (daily cap, natural pacing)
  5. Flag follow-ups, build the daily report

Usage:
    python run_daily.py                 # everything above
    python run_daily.py --no-research   # replies + sending + report only
    python run_daily.py --no-send       # research + report, send nothing
    python run_daily.py --dry-run       # show what would be emailed, send nothing
    python run_daily.py --report-only
"""
import argparse
import sys
from datetime import datetime

import config
import db
import outreach
import report


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", type=int, default=None)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--no-research", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db.init_db()
    conn = db.connect()
    stats = None

    if not args.report_only:
        try:
            import inbox
            log(f"inbox: {inbox.check_replies(conn)}")
        except Exception as e:  # never let inbox problems stop the day's run
            log(f"inbox check failed: {e}")

        if not args.no_research:
            import research
            stats = research.run_research(conn, args.leads)
            log(f"research: added {len(stats['added_ids'])}, duplicates {stats['duplicates_skipped']}, "
                f"rejected {stats['rejected']}")

        if not args.no_send:
            log(f"auto-approved: {outreach.auto_approve(conn) if config.AUTO_SEND else 'off'}")
            log(f"email: {outreach.send_all(conn, dry_run=args.dry_run)}")

    n = db.refresh_follow_ups(conn)
    conn.commit()
    log(f"follow-ups due: {n}")
    data = report.build_report(conn, run_stats=stats)
    log(f"report saved: {report.save_html(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
