"""Durable single-host SQLite journal with cross-process advisory locking.

The lock has no unsafe expiry. OS releases it on process death. Multi-host state
is deliberately unsupported: this file must reside on the controller's local disk.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from onedeploy.errors import OnedeployError


class StateStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS deployments (
              id TEXT PRIMARY KEY, generation INTEGER NOT NULL, desired TEXT);
            CREATE TABLE IF NOT EXISTS releases (
              id TEXT PRIMARY KEY, deployment TEXT NOT NULL, generation INTEGER NOT NULL,
              data TEXT NOT NULL, UNIQUE(deployment,generation));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY, release TEXT, state TEXT, time REAL);
            ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def locked(self):
        with open(self.lock_path, "a+b") as lock:
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0)
                    if not lock.read(1):
                        lock.write(b"0"); lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise OnedeployError("locked", "Another AutoDeploy operation owns the controller state.") from None
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def get(self, release_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None:
            raise OnedeployError("invalid_config", "Unknown AutoDeploy release.")
        return json.loads(row[0])

    def deployment(self, deployment_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
            releases = db.execute("SELECT data FROM releases WHERE deployment=? ORDER BY generation DESC", (deployment_id,)).fetchall()
        return {"deployment_id": deployment_id, "generation": row["generation"] if row else 0,
                "desired_release": row["desired"] if row else None,
                "releases": [json.loads(r[0]) for r in releases]}

    def add(self, record):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT data FROM releases WHERE id=?", (record["release_id"],)).fetchone()
            if prior:
                return json.loads(prior[0])
            row = db.execute("SELECT generation FROM deployments WHERE id=?", (record["deployment_id"],)).fetchone()
            generation = (row[0] if row else 0) + 1
            record = {**record, "generation": generation}
            db.execute("INSERT INTO deployments VALUES (?,?,NULL) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation", (record["deployment_id"], generation))
            db.execute("INSERT INTO releases VALUES (?,?,?,?)", (record["release_id"], record["deployment_id"], generation, json.dumps(record, sort_keys=True)))
            db.execute("INSERT INTO events(release,state,time) VALUES (?,?,?)", (record["release_id"], record["state"], time.time()))
        return record

    def save(self, record):
        with self.connect() as db:
            changed = db.execute("UPDATE releases SET data=? WHERE id=?", (json.dumps(record, sort_keys=True), record["release_id"])).rowcount
            if not changed:
                raise OnedeployError("invalid_config", "Unknown AutoDeploy release.")
            db.execute("INSERT INTO events(release,state,time) VALUES (?,?,?)", (record["release_id"], record["state"], time.time()))
        return record

    def promote(self, record):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE deployments SET desired=? WHERE id=? AND generation=?", (record["release_id"], record["deployment_id"], record["generation"])).rowcount
            if not changed:
                raise OnedeployError("stale_plan", "A newer release superseded this rollout.")
            record = {**record, "state": "promoted"}
            db.execute("UPDATE releases SET data=? WHERE id=?", (json.dumps(record, sort_keys=True), record["release_id"]))
            db.execute("INSERT INTO events(release,state,time) VALUES (?,?,?)", (record["release_id"], "promoted", time.time()))
        return record
