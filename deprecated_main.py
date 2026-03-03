import os
import requests
from datetime import datetime, timedelta, timezone
import re
import time

TOKEN = os.environ["TODOIST_TOKEN"]
BASE_URL = "https://api.todoist.com/api/v1"

RESTRICTED_LABELS = {"Work", "Floating Horarium", "Reminder"}
ROLL_LABEL = "Roll"


############################################################
# API Helpers
############################################################

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


############################################################
# Helpers
############################################################

def is_due_today(task):
    if not task.get("due"):
        return False
    due = datetime.fromisoformat(task["due"]["date"]).date()
    return due == datetime.now().date()


def is_overdue(task):
    if not task.get("due"):
        return False
    due = datetime.fromisoformat(task["due"]["date"]).date()
    return due < datetime.now().date()


def clean_name(name):
    # Removes previous brackets like "[3h 20m remaining]"
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


############################################################
# Rule: Roll tasks with "Roll" label due today → tomorrow @ 9pm
############################################################

def roll_tasks(tasks):
    tomorrow = datetime.now() + timedelta(days=1)
    new_due = tomorrow.replace(hour=21, minute=0, second=0, microsecond=0)

    for t in tasks:
        if ROLL_LABEL in t.get("labels", []) and is_due_today(t):
            update_task(t["id"], due_datetime=new_due.isoformat())


############################################################
# MAIN RULE ENFORCEMENT LOGIC
############################################################

def enforce():
    # Always begin fresh
    tasks = get_tasks()

    ############################################################
    # Step 1 — Roll tasks first
    ############################################################
    roll_tasks(tasks)
    time.sleep(1)
    tasks = get_tasks()

    ############################################################
    # Step 2 — Clear ALL tasks not due today → priority = 1 (P4)
    ############################################################
    for t in tasks:
        if not is_due_today(t) and t["priority"] != 1:
            update_task(t["id"], priority=1)

    time.sleep(1)
    tasks = get_tasks()

    ############################################################
    # Step 3 — Overdue restricted tasks → P1 (priority 4)
    ############################################################
    for t in tasks:
        if is_overdue(t) and any(lbl in RESTRICTED_LABELS for lbl in t.get("labels", [])):
            if t["priority"] != 4:
                update_task(t["id"], priority=4)

    time.sleep(1)
    tasks = get_tasks()

    ############################################################
    # Step 4 — Cascading Priority Rules
    ############################################################

    while True:
        tasks = get_tasks()

        p1 = [t for t in tasks if t["priority"] == 4]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]
        p4 = [t for t in tasks if t["priority"] == 1]

        non_restricted_p1 = [
            t for t in p1
            if not any(lbl in RESTRICTED_LABELS for lbl in t.get("labels", []))
        ]

        ############################################################
        # Rule A: If no valid P1 → promote ALL P2 → P1
        ############################################################
        if (not p1 or not non_restricted_p1) and p2:
            for t in p2:
                update_task(t["id"], priority=4)
            time.sleep(1)
            continue

        ############################################################
        # Rule B: If no P2 → promote ALL P3 → P2
        ############################################################
        if not p2 and p3:
            for t in p3:
                update_task(t["id"], priority=3)
            time.sleep(1)
            continue

        ############################################################
        # Rule C: If no P3 → promote next due P4 → P3
        ############################################################
        if not p3 and p4:
            dated = [t for t in p4 if t.get("due")]
            if dated:
                next_due = sorted(
                    dated,
                    key=lambda x: x["due"]["date"]
                )[0]
                update_task(next_due["id"], priority=2)
                time.sleep(1)
                continue

        ############################################################
        # Rule D: Stop when we have:
        #   - ≥1 non-restricted P1
        #   - ≥1 P2
        #   - ≥1 P3
        ############################################################
        if non_restricted_p1 and p2 and p3:
            break

        # Safety break (should never hit)
        break

    ############################################################
    # Step 5 — Append time remaining to all tasks
    ############################################################
    tasks = get_tasks()
    for t in tasks:
        new_content = clean_name(t["content"]) + time_remaining(t)
        update_task(t["id"], content=new_content)


############################################################

if __name__ == "__main__":
    enforce()
