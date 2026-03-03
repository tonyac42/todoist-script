import os
import requests
from datetime import datetime, timedelta, timezone
import re
import time

TOKEN = os.environ["TODOIST_TOKEN"]
BASE_URL = "https://api.todoist.com/api/v1"

RESTRICTED_LABELS = {"Work", "Floating Horarium", "Reminder"}
ROLL_LABEL = "Roll"


def api_get(path, params=None):
    r = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        params=params,
    )
    r.raise_for_status()
    return r.json()


def api_post(path, payload):
    r = requests.post(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json=payload,
    )
    r.raise_for_status()


def get_tasks():
    data = api_get("/tasks")
    return data["results"]


def update_task(task_id, **fields):
    api_post(f"/tasks/{task_id}", fields)


def is_due_today(task):
    if not task.get("due"):
        return False

    due_str = task["due"]["date"]
    due_date = datetime.fromisoformat(due_str).date()

    return due_date == datetime.now().date()


def is_overdue(task):
    if not task.get("due"):
        return False

    due_str = task["due"]["date"]  # "YYYY-MM-DD"
    due_date = datetime.fromisoformat(due_str).date()

    return due_date < datetime.now().date()


def clean_name(name):
    return re.sub(r"\s*\[.*?(remaining|overdue).*?\]$", "", name)


def time_remaining(task):
    if not task.get("due") or not task["due"].get("datetime"):
        return ""
    due = datetime.fromisoformat(task["due"]["datetime"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = due - now
    if delta.total_seconds() <= 0:
        return " [overdue]"
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    return f" [{hours}h {minutes}m remaining]"


def roll_tasks(tasks):
    tomorrow = datetime.now() + timedelta(days=1)
    new_due = tomorrow.replace(hour=21, minute=0, second=0, microsecond=0)

    for t in tasks:
        if ROLL_LABEL in t.get("labels", []) and is_due_today(t):
            update_task(
                t["id"],
                due_datetime=new_due.isoformat()
            )


def enforce():
    tasks = get_tasks()

    roll_tasks(tasks)
    time.sleep(1)
    tasks = get_tasks()

    # Clear priority if not due today
    for t in tasks:
        if not is_due_today(t) and t["priority"] != 1:
            update_task(t["id"], priority=1)

    # Overdue restricted → P1
    for t in tasks:
        if is_overdue(t) and any(l in RESTRICTED_LABELS for l in t.get("labels", [])):
            if t["priority"] != 4:
                update_task(t["id"], priority=4)

    time.sleep(1)
    tasks = get_tasks()

    while True:
        p1 = [t for t in tasks if t["priority"] == 4]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]
        p4 = [t for t in tasks if t["priority"] == 1]

        non_restricted_p1 = [
            t for t in p1 if not any(l in RESTRICTED_LABELS for l in t.get("labels", []))
        ]

        if not non_restricted_p1 and p2:
            for t in p2:
                update_task(t["id"], priority=4)

        if not p2 and p3:
            for t in p3:
                update_task(t["id"], priority=3)

        if not p3 and p4:
            next_due = sorted(
                [t for t in p4 if t.get("due")],
                key=lambda x: x["due"]["date"]
            )
            if next_due:
                update_task(next_due[0]["id"], priority=2)

        time.sleep(1)
        tasks = get_tasks()

        p1 = [t for t in tasks if t["priority"] == 4]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]

        if p1 and p2 and p3:
            break

    # Append time remaining
    for t in tasks:
        new_content = clean_name(t["content"]) + time_remaining(t)
        update_task(t["id"], content=new_content)


if __name__ == "__main__":
    enforce()
