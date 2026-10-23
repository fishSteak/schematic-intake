"""Detached workers and verified lifecycle recovery."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import psutil

from .store import ACTIVE, TERMINAL, Store, ROOT, now, dumps


def process_alive(run):
    if not run.get("pid"):
        return False
    try:
        proc = psutil.Process(int(run["pid"]))
        if run.get("process_started") and abs(proc.create_time() - run["process_started"]) > 0.1:
            return False
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        # A transient observation failure must never turn a live task terminal.
        return None


def reconcile(store: Store):
    for run in store.runs(active=True):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(run["created_at"])).total_seconds()
        alive = process_alive(run)
        if alive is False and age > 10:
            store.finish(run["id"], "stopped" if run.get("stop_requested") else "interrupted", errors=[] if run.get("stop_requested") else ["执行进程已退出；可按有效检查点续跑"])


def launch(store: Store, ident):
    run = store.run(ident)
    if run["status"] in TERMINAL or process_alive(run):
        return run
    with store.connection(write=True) as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
        data = json.loads(row["data"])
        if data.get("launch_reserved"):
            return store._run(row)
        data["launch_reserved"] = now()
        c.execute("UPDATE runs SET data=? WHERE id=?", (dumps(data), ident))
    workspace = Path(run["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SCHEMATIC_WORKBENCH_HOME"] = str(store.home)
    env["PYTHONPATH"] = str(ROOT)
    with (workspace / "worker.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "web_workbench.worker", ident], cwd=ROOT, env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=os.name != "nt",
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    try:
        started = psutil.Process(child.pid).create_time()
    except psutil.NoSuchProcess:
        started = None
    store.update_run(ident, pid=child.pid, process_started=started)
    # Reap our own children while they remain independent of browser requests.
    threading.Thread(target=child.wait, daemon=True).start()
    return store.run(ident)


def stop(store: Store, ident, *, grace=5):
    run = store.request_stop(ident)
    if run["status"] in TERMINAL:
        return run

    def terminate_if_needed():
        time.sleep(grace)
        current = store.run(ident)
        if current["status"] in TERMINAL:
            return
        if process_alive(current) is True:
            try:
                proc = psutil.Process(current["pid"])
                children = proc.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                proc.terminate()
                _, alive = psutil.wait_procs([*children, proc], timeout=3)
                for child in alive:
                    child.kill()
                psutil.wait_procs(alive, timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if process_alive(current) is False:
            store.finish(ident, "stopped", errors=[])

    threading.Thread(target=terminate_if_needed, daemon=True).start()
    return run
