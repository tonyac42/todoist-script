#!/usr/bin/env python3
"""
Todoist Priority Manager
- Clears priority on tasks not due today
- Sets overdue Work/Floating Horarium/Reminder tasks to p1
- Cascades priorities up until p1 (non-special), p2, and p3 are all filled
- Appends time remaining to every task description
- Rolls "Roll" label tasks to next day 9 PM
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────
API_TOKEN   = os.environ["TODOIST_API_KEY"]
BASE_URL    = "https://api.todoist.com/rest/v2"
HEADERS     = {"Authorization": f"Bearer {API_TOKEN}"}
SPECIAL_LABELS = {"Work", "Floating Horarium", "Reminder"}
USER_TZ     = ZoneInfo("America/New_York")   # ← change to your timezone
HOURGLASS   = "⌛"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_tasks() -> list[dict]:
    r = requests.get(f"{BASE_URL}/tasks", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def update_task(task_id: str, **kwargs) -> None:
    r = requests.post(
        f"{BASE_URL}/tasks/{task_id}",
        headers=HEADERS,
        json=kwargs,
    )
    r.raise_for_status()


def parse_due(task: dict) -> datetime | None:
    """Return an aware datetime for the task's due date (user TZ)."""
    due = task.get("due")
    if not due:
        return None

    dt_str = due.get("datetime") or due.get("date")
    if not dt_str:
        return None

    if "T" in dt_str:
        # datetime string – may be UTC or local
        if dt_str.endswith("Z"):
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        elif "+" in dt_str[10:] or (len(dt_str) > 19 and dt_str[19] == "-"):
            dt = datetime.fromisoformat(dt_str)
        else:
            # Treat as local (no offset present)
            dt = datetime.fromisoformat(dt_str).replace(tzinfo=USER_TZ)
        return dt.astimezone(USER_TZ)
    else:
        # date-only → count to midnight of that date
        d = datetime.strptime(dt_str, "%Y-%m-%d")
        return d.replace(hour=0, minute=0, second=0, tzinfo=USER_TZ) + timedelta(days=1)


def fmt_delta(delta: timedelta) -> str:
    """Format a timedelta as '2d 4h 30m'. Negative deltas show 'Overdue Xd Xh Xm'."""
    negative = delta.total_seconds() < 0
    total_sec = abs(int(delta.total_seconds()))
    days, rem  = divmod(total_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins       = rem // 60

    parts = []
    if days:  parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    label = " ".join(parts)
    return f"Overdue {label}" if negative else label


def strip_old_time(description: str) -> str:
    """Remove a previously appended '⌛ <time>' segment."""
    # Matches ' ⌛ <anything to end of string>' including multiline safety
    return re.sub(r"\s*" + re.escape(HOURGLASS) + r".*$", "", description, flags=re.DOTALL).rstrip()


def is_today(dt: datetime | None, now: datetime) -> bool:
    if dt is None:
        return False
    return dt.date() == now.date()


def is_overdue(dt: datetime | None, now: datetime) -> bool:
    if dt is None:
        return False
    return dt < now


def has_special_label(task: dict) -> bool:
    return bool(set(task.get("labels", [])) & SPECIAL_LABELS)


def only_special_p1(tasks: list[dict]) -> bool:
    """True if every p1 task carries a special label (or there are no p1 tasks)."""
    p1 = [t for t in tasks if t["priority"] == 4]  # API: p1=4, p2=3, p3=2, p4=1
    return all(has_special_label(t) for t in p1)


# ── Priority cascade ──────────────────────────────────────────────────────────

def cascade_priorities(tasks: list[dict], now: datetime) -> list[dict]:
    """
    Mutates task dicts in-place (priority field) and queues API updates.
    Returns the list of (task_id, new_priority) pairs to update.
    """
    updates: list[tuple[str, int]] = []

    def refresh_counts():
        p1 = [t for t in tasks if t["priority"] == 4]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]
        return p1, p2, p3

    for _ in range(20):  # safety loop cap
        p1, p2, p3 = refresh_counts()
        non_special_p1 = [t for t in p1 if not has_special_label(t)]

        # Condition: need at least one non-special p1, one p2, one p3
        needs_p1 = len(non_special_p1) == 0
        needs_p2 = len(p2) == 0
        needs_p3 = len(p3) == 0

        if not needs_p1 and not needs_p2 and not needs_p3:
            break

        promoted_any = False

        # ── Rule: if no non-special p1, promote all p2 tasks due TODAY → p1
        if needs_p1:
            today_p2 = [t for t in p2 if is_today(parse_due(t), now)]
            if today_p2:
                for t in today_p2:
                    t["priority"] = 4
                    updates.append((t["id"], 4))
                promoted_any = True
            else:
                # No p2 tasks due today — promote all p2 regardless
                if p2:
                    for t in p2:
                        t["priority"] = 4
                        updates.append((t["id"], 4))
                    promoted_any = True

        # Refresh after p1 promotions
        p1, p2, p3 = refresh_counts()
        needs_p2 = len(p2) == 0
        needs_p3 = len(p3) == 0

        # ── Rule: no p2 → promote all p3 → p2
        if needs_p2 and p3:
            for t in p3:
                t["priority"] = 3
                updates.append((t["id"], 3))
            promoted_any = True

        # Refresh again
        p1, p2, p3 = refresh_counts()
        p4 = [t for t in tasks if t["priority"] == 1]
        needs_p3 = len(p3) == 0

        # ── Rule: no p3 → promote the next-due p4 task → p3
        if needs_p3 and p4:
            # pick earliest due, fall back to first in list
            due_p4 = sorted(
                p4,
                key=lambda t: parse_due(t) or datetime.max.replace(tzinfo=USER_TZ)
            )
            due_p4[0]["priority"] = 2
            updates.append((due_p4[0]["id"], 2))
            promoted_any = True

        if not promoted_any:
            break

    return updates


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(tz=USER_TZ)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    tasks = get_tasks()
    priority_updates:  list[tuple[str, int]] = []
    description_updates: list[tuple[str, str]] = []

    for task in tasks:
        tid    = task["id"]
        due_dt = parse_due(task)
        labels = task.get("labels", [])

        # ── Roll label: reschedule to next day 9 PM ───────────────────────────
        if "Roll" in labels and due_dt and is_today(due_dt, now):
            next_day = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            update_task(tid, due_string=f"{next_day} at 21:00")
            # Refresh due_dt for time calculation below
            due_dt = datetime.fromisoformat(f"{next_day}T21:00:00").replace(tzinfo=USER_TZ)

        # ── Priority: clear priority for tasks not due today ─────────────────
        if due_dt is None or not is_today(due_dt, now):
            if task["priority"] != 1:   # 1 = no priority in API
                task["priority"] = 1
                priority_updates.append((tid, 1))
        else:
            # ── Overdue special-label tasks → p1 ─────────────────────────────
            if is_overdue(due_dt, now) and has_special_label(task):
                if task["priority"] != 4:
                    task["priority"] = 4
                    priority_updates.append((tid, 4))

    # ── Cascade priorities ────────────────────────────────────────────────────
    cascade_updates = cascade_priorities(tasks, now)
    priority_updates.extend(cascade_updates)

    # ── Apply priority updates ────────────────────────────────────────────────
    seen_priority = set()
    for tid, pri in priority_updates:
        if tid not in seen_priority:
            update_task(tid, priority=pri)
            seen_priority.add(tid)

    # ── Append time remaining to descriptions ─────────────────────────────────
    # Re-fetch so we have the latest state
    tasks = get_tasks()

    for task in tasks:
        tid    = task["id"]
        due_dt = parse_due(task)
        raw_desc = task.get("description") or ""

        base_desc = strip_old_time(raw_desc)

        if due_dt:
            delta     = due_dt - now
            time_str  = fmt_delta(delta)
            new_desc  = f"{base_desc} {HOURGLASS} {time_str}".strip()
        else:
            new_desc = f"{base_desc} {HOURGLASS} No due date".strip()

        if new_desc != raw_desc:
            description_updates.append((tid, new_desc))

    for tid, desc in description_updates:
        update_task(tid, description=desc)

    print(
        f"Done. {len(seen_priority)} priority updates, "
        f"{len(description_updates)} description updates."
    )


if __name__ == "__main__":
    main()
