import os
import requests
from datetime import datetime, timedelta, timezone
import time
import re

TOKEN = os.environ["TODOIST_TOKEN"]
BASE_URL = "https://api.todoist.com/rest/v2"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

RESTRICTED_LABELS = {"Work", "Floating Horarium", "Reminder"}
ROLL_LABEL = "Roll"

def get_tasks():
    r = requests.get(
        "https://api.todoist.com/api/v1/tasks",
        headers={
            "Authorization": f"Bearer {TOKEN}",
        }
    )
    r.raise_for_status()
    return r.json()

def update_task(task_id, **kwargs):
    r = requests.post(
        f"https://api.todoist.com/api/v1/tasks/{task_id}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
        },
        json=kwargs
    )
    r.raise_for_status()

def is_due_today(task):
    if not task.get("due"):
        return False
    return task["due"].get("date") == datetime.now().date().isoformat()

def is_overdue(task):
    if not task.get("due"):
        return False
    due_str = task["due"].get("datetime") or task["due"].get("date")
    if not due_str:
        return False
    try:
        due = datetime.fromisoformat(due_str.replace("Z",""))
    except:
        return False
    return due < datetime.now()

def has_label(task, label):
    return label in task.get("labels", [])

def has_restricted_label(task):
    return any(label in RESTRICTED_LABELS for label in task.get("labels", []))

def clean_task_name(name):
    return re.sub(r"\s*\[.*?(remaining|overdue).*?\]$", "", name)

def time_remaining_text(task):
    if not task.get("due") or not task["due"].get("datetime"):
        return ""
    due = datetime.fromisoformat(task["due"]["datetime"].replace("Z","")).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = due - now
    if delta.total_seconds() <= 0:
        return " [overdue]"
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    return f" [{hours}h {minutes}m remaining]"

def roll_tasks(tasks):
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).date()
    new_due = datetime.combine(tomorrow, datetime.strptime("21:00", "%H:%M").time())

    for t in tasks:
        if has_label(t, ROLL_LABEL) and is_due_today(t):
            update_task(
                t["id"],
                due_datetime=new_due.isoformat()
            )

def enforce_rules():
    tasks = get_tasks()

    # ---- NEW STEP 0: Roll logic ----
    roll_tasks(tasks)

    time.sleep(1)
    tasks = get_tasks()

    while True:

        # Rule 1: Clear priority if not due today
        for t in tasks:
            if not is_due_today(t):
                if t["priority"] != 1:
                    update_task(t["id"], priority=1)

        # Rule 2: Restricted overdue → P1
        for t in tasks:
            if has_restricted_label(t) and is_overdue(t):
                if t["priority"] != 4:
                    update_task(t["id"], priority=4)

        time.sleep(1)
        tasks = get_tasks()

        p1 = [t for t in tasks if t["priority"] == 4]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]
        p4 = [t for t in tasks if t["priority"] == 1]

        non_restricted_p1 = [t for t in p1 if not has_restricted_label(t)]

        # Rule 3
        if not p1 or not non_restricted_p1:
            for t in p2:
                update_task(t["id"], priority=4)

        # Rule 4
        if not p2:
            for t in p3:
                update_task(t["id"], priority=3)

        # Rule 5
        if not p3:
            next_due = sorted(
                [t for t in p4 if t.get("due")],
                key=lambda x: x["due"]["date"]
            )
            if next_due:
                update_task(next_due[0]["id"], priority=2)

        time.sleep(1)
        tasks = get_tasks()

        p1 = [t for t in tasks if t["priority"] == 4 and not has_restricted_label(t)]
        p2 = [t for t in tasks if t["priority"] == 3]
        p3 = [t for t in tasks if t["priority"] == 2]

        if p1 and p2 and p3:
            break

    # Rule 8: Append time remaining
    for t in tasks:
        clean_name = clean_task_name(t["content"])
        suffix = time_remaining_text(t)
        update_task(t["id"], content=clean_name + suffix)

if __name__ == "__main__":
    enforce_rules()
