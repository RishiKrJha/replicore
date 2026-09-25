import json
import threading
import uuid
from datetime import datetime, timezone

from .integrity import sha256_bytes, simulated_representation
from .storage import NodeStorage

ONLINE = "ONLINE"
FAILED = "FAILED"
HEALTHY = "HEALTHY"
CREATING = "CREATING"
MISSING = "MISSING"
CORRUPTED = "CORRUPTED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
REPAIRING = "REPAIRING"
REPAIR_FAILED = "REPAIR_FAILED"
DEGRADED = "DEGRADED"


def now():
    return datetime.now(timezone.utc).isoformat()


class VaultService:
    def __init__(self, db, storage_root):
        self.db = db
        self.storage = NodeStorage(storage_root)
        self.lock = threading.RLock()

    def ensure_nodes(self, count):
        with self.db.lock, self.db.connect() as con:
            for index in range(1, count + 1):
                con.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?)", (f"node-{index}", ONLINE, now()))

    def nodes(self):
        with self.db.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM nodes ORDER BY id")]

    def events(self, object_id=None, limit=50):
        query = "SELECT * FROM operation_events"
        params = []
        if object_id:
            query += " WHERE object_id=?"
            params.append(object_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.db.connect() as con:
            return [dict(row) for row in con.execute(query, params)]

    def operation_summary(self, object_id=None):
        events = list(reversed(self.events(object_id, 8)))
        if not events:
            return None
        parts = []
        for event in events:
            detail = event["message"]
            if event["source_node"] or event["destination_node"]:
                detail += " (source: %s, destination: %s)" % (
                    event["source_node"] or "none", event["destination_node"] or "none"
                )
            parts.append("%s: %s" % (event["event_type"], detail))
        return " | ".join(parts)

    @staticmethod
    def _record_event(con, event_type, message, object_id=None, node_id=None,
                      previous_state=None, resulting_state=None, source_node=None,
                      destination_node=None, expected_checksum=None, actual_checksum=None):
        con.execute(
            """INSERT INTO operation_events
               (object_id, node_id, event_type, previous_state, resulting_state,
                source_node, destination_node, expected_checksum, actual_checksum,
                message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (object_id, node_id, event_type, previous_state, resulting_state,
             source_node, destination_node, expected_checksum, actual_checksum,
             message, now()),
        )

    def objects(self):
        with self.db.connect() as con:
            ids = [row["id"] for row in con.execute("SELECT id FROM objects ORDER BY created_at DESC")]
        for object_id in ids:
            self.repair(object_id)
        with self.db.connect() as con:
            rows = [dict(row) for row in con.execute("SELECT * FROM objects ORDER BY created_at DESC")]
            for obj in rows:
                obj["replicas"] = [dict(row) for row in con.execute(
                    "SELECT r.*, n.state AS node_state FROM replicas r JOIN nodes n ON n.id=r.node_id "
                    "WHERE object_id=? ORDER BY node_id", (obj["id"],))]
                obj["verified_replica_count"] = sum(
                    replica["state"] == HEALTHY and replica["node_state"] == ONLINE
                    for replica in obj["replicas"]
                )
                obj["physical_size"] = sum(
                    self.storage.path(replica["node_id"], obj["id"]).stat().st_size
                    for replica in obj["replicas"]
                    if self.storage.exists(replica["node_id"], obj["id"])
                )
            return rows

    def get_object(self, object_id):
        with self.db.connect() as con:
            row = con.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone()
            if not row:
                raise KeyError(object_id)
            obj = dict(row)
            obj["replicas"] = [dict(r) for r in con.execute(
                "SELECT r.*, n.state AS node_state FROM replicas r JOIN nodes n ON n.id=r.node_id "
                "WHERE object_id=? ORDER BY node_id", (object_id,))]
            obj["verified_replica_count"] = sum(
                replica["state"] == HEALTHY and replica["node_state"] == ONLINE
                for replica in obj["replicas"]
            )
            obj["physical_size"] = sum(
                self.storage.path(replica["node_id"], object_id).stat().st_size
                for replica in obj["replicas"]
                if self.storage.exists(replica["node_id"], object_id)
            )
            return obj

    def _node_state(self, con, node_id):
        row = con.execute("SELECT state FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise KeyError(node_id)
        return row["state"]

    def _content(self, obj):
        if obj["kind"] == "simulated":
            return simulated_representation(obj["id"], obj["size"], obj["seed"], obj["version"])
        return obj["_data"]

    def create_real(self, filename, data, replication_factor):
        self._validate_create(filename, replication_factor)
        object_id = uuid.uuid4().hex
        obj = {"id": object_id, "filename": filename, "kind": "real", "size": len(data),
               "checksum": sha256_bytes(data), "version": 1, "replication_factor": replication_factor,
               "state": "DEGRADED", "seed": None, "_data": data, "created_at": now()}
        self._create(obj)
        return self.get_object(object_id)

    def create_simulated(self, filename, size, replication_factor):
        self._validate_create(filename, replication_factor)
        if not isinstance(size, int) or size < 0:
            raise ValueError("size must be a non-negative integer")
        object_id = uuid.uuid4().hex
        seed = uuid.uuid5(uuid.NAMESPACE_URL, object_id).hex
        representation = simulated_representation(object_id, size, seed, 1)
        obj = {"id": object_id, "filename": filename, "kind": "simulated", "size": size,
               "checksum": sha256_bytes(representation), "version": 1,
               "replication_factor": replication_factor, "state": "DEGRADED", "seed": seed,
               "_data": representation, "created_at": now()}
        self._create(obj)
        return self.get_object(object_id)

    @staticmethod
    def _validate_create(filename, replication_factor):
        if not filename or "/" in filename or "\\" in filename:
            raise ValueError("filename must be a non-empty basename")
        if not isinstance(replication_factor, int) or replication_factor < 1:
            raise ValueError("replication factor must be positive")

    def _create(self, obj):
        with self.lock, self.db.lock, self.db.connect() as con:
            con.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        tuple(obj[key] for key in ("id", "filename", "kind", "size", "checksum", "version",
                                                   "replication_factor", "state", "seed", "created_at")))
            online = [r["id"] for r in con.execute("SELECT id FROM nodes WHERE state=? ORDER BY id", (ONLINE,))]
            for node_id in online[:obj["replication_factor"]]:
                con.execute("INSERT INTO replicas(object_id,node_id,state,version,updated_at) VALUES(?,?,?,?,?)",
                            (obj["id"], node_id, CREATING, obj["version"], now()))
                try:
                    self.storage.write(node_id, obj["id"], obj["_data"])
                    con.execute("UPDATE replicas SET state=?, checksum=?, updated_at=? WHERE object_id=? AND node_id=?",
                                (HEALTHY, obj["checksum"], now(), obj["id"], node_id))
                except OSError:
                    con.execute("UPDATE replicas SET state=?, updated_at=? WHERE object_id=? AND node_id=?",
                                (REPAIR_FAILED, now(), obj["id"], node_id))
            con.commit()
        self.repair(obj["id"])

    def _verify_replica(self, con, obj, replica):
        return self._replica_status(con, obj, replica)[0]

    def _replica_status(self, con, obj, replica):
        if self._node_state(con, replica["node_id"]) != ONLINE:
            return UNAVAILABLE, None
        if not self.storage.exists(replica["node_id"], obj["id"]):
            return MISSING, None
        try:
            actual = sha256_bytes(self.storage.read(replica["node_id"], obj["id"]))
        except OSError:
            return MISSING, None
        if actual != obj["checksum"] or replica["version"] != obj["version"]:
            return (STALE if replica["version"] != obj["version"] else CORRUPTED), actual
        return HEALTHY, actual

    def verify(self, object_id):
        with self.lock, self.db.lock, self.db.connect() as con:
            row = con.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone()
            if not row:
                raise KeyError(object_id)
            obj = dict(row)
            rows = list(con.execute("SELECT * FROM replicas WHERE object_id=?", (object_id,)))
            for row in rows:
                state, actual = self._replica_status(con, obj, row)
                con.execute("UPDATE replicas SET state=?, updated_at=? WHERE id=?", (state, now(), row["id"]))
                self._record_event(
                    con, "VERIFICATION", "Replica verified as %s" % state,
                    object_id, row["node_id"], row["state"], state,
                    expected_checksum=obj["checksum"], actual_checksum=actual,
                )
            good = sum(self._verify_replica(con, obj, row) == HEALTHY for row in rows)
            state = HEALTHY if good >= obj["replication_factor"] else (DEGRADED if good else UNAVAILABLE)
            con.execute("UPDATE objects SET state=? WHERE id=?", (state, object_id))
            con.commit()
        return self.get_object(object_id)

    def repair(self, object_id):
        with self.lock, self.db.lock, self.db.connect() as con:
            obj = dict(con.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone())
            rows = list(con.execute("SELECT * FROM replicas WHERE object_id=?", (object_id,)))
            healthy = []
            for row in rows:
                state, actual = self._replica_status(con, obj, row)
                if state != HEALTHY:
                    con.execute("UPDATE replicas SET state=?, updated_at=? WHERE id=?", (state, now(), row["id"]))
                    self._record_event(
                        con, "VERIFICATION", "Replica detected as %s" % state,
                        object_id, row["node_id"], row["state"], state,
                        expected_checksum=obj["checksum"], actual_checksum=actual,
                    )
                else:
                    healthy.append(row)
            destinations = [r["id"] for r in con.execute("SELECT id FROM nodes WHERE state=? ORDER BY id", (ONLINE,))
                            if not any(x["node_id"] == r["id"] and self._verify_replica(con, obj, x) == HEALTHY for x in rows)]
            source = healthy[0] if healthy else None
            needed = max(0, obj["replication_factor"] - len(healthy))
            for node_id in destinations[:needed]:
                existing = next((r for r in rows if r["node_id"] == node_id), None)
                try:
                    if not source:
                        raise OSError("no verified healthy source")
                    previous_state = existing["state"] if existing else None
                    con.execute("INSERT INTO replicas(object_id,node_id,state,version,updated_at) VALUES(?,?,?,?,?) "
                                "ON CONFLICT(object_id,node_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                                (object_id, node_id, REPAIRING, obj["version"], now()))
                    self.storage.copy(source["node_id"], node_id, object_id)
                    if not self.storage.exists(node_id, object_id) or sha256_bytes(self.storage.read(node_id, object_id)) != obj["checksum"]:
                        raise OSError("post-write verification failed")
                    con.execute("UPDATE replicas SET state=?, checksum=?, version=?, updated_at=? WHERE object_id=? AND node_id=?",
                                (HEALTHY, obj["checksum"], obj["version"], now(), object_id, node_id))
                    self._record_event(
                        con, "REPAIR", "Replica repaired and verified",
                        object_id, node_id, previous_state, HEALTHY,
                        source["node_id"], node_id, obj["checksum"], obj["checksum"],
                    )
                    if existing:
                        for index, item in enumerate(rows):
                            if item["node_id"] == node_id:
                                rows[index] = dict(item, state=HEALTHY, version=obj["version"])
                except (OSError, KeyError) as error:
                    self.storage.remove(node_id, object_id)
                    con.execute("UPDATE replicas SET state=?, updated_at=? WHERE object_id=? AND node_id=?",
                                (REPAIR_FAILED, now(), object_id, node_id))
                    self._record_event(
                        con, "REPAIR_FAILED", str(error), object_id, node_id,
                        existing["state"] if existing else None, REPAIR_FAILED,
                        source["node_id"] if source else None, node_id,
                        obj["checksum"], None,
                    )
            if len(destinations) < needed:
                self._record_event(
                    con, "REPAIR_FAILED", "insufficient eligible online destinations",
                    object_id, resulting_state=DEGRADED if healthy else UNAVAILABLE,
                    expected_checksum=obj["checksum"],
                )
            fresh = list(con.execute("SELECT * FROM replicas WHERE object_id=?", (object_id,)))
            good = sum(self._verify_replica(con, obj, row) == HEALTHY for row in fresh)
            state = "HEALTHY" if good >= obj["replication_factor"] else ("DEGRADED" if good else "UNAVAILABLE")
            con.execute("UPDATE objects SET state=? WHERE id=?", (state, object_id))
            if state != obj["state"]:
                self._record_event(con, "OBJECT_STATE", "Object state is %s" % state,
                                   object_id, previous_state=obj["state"], resulting_state=state)
            con.commit()
        return self.get_object(object_id)

    def retrieve(self, object_id):
        obj = self.verify(object_id)
        obj = self.repair(object_id)
        with self.db.connect() as con:
            for replica in obj["replicas"]:
                if replica["state"] == HEALTHY and replica["node_state"] == ONLINE:
                    return self.storage.read(replica["node_id"], object_id), obj
        raise FileNotFoundError("no verified replica is available")

    def set_node(self, node_id, state):
        with self.db.lock, self.db.connect() as con:
            row = con.execute("SELECT state FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not row:
                raise KeyError(node_id)
            con.execute("UPDATE nodes SET state=? WHERE id=?", (state, node_id))
            self._record_event(con, "NODE_STATE", "Node changed to %s" % state,
                               node_id=node_id, previous_state=row["state"], resulting_state=state)
            object_ids = [r["object_id"] for r in con.execute("SELECT object_id FROM replicas WHERE node_id=?", (node_id,))]
            con.commit()
        for object_id in set(object_ids):
            self.repair(object_id)

    def inject(self, object_id, node_id, action):
        with self.lock, self.db.lock, self.db.connect() as con:
            obj = dict(con.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone())
            replica = con.execute("SELECT * FROM replicas WHERE object_id=? AND node_id=?", (object_id, node_id)).fetchone()
            if not replica:
                raise KeyError("replica")
            if action == "remove":
                self.storage.remove(node_id, object_id)
            elif action == "corrupt":
                self.storage.corrupt(node_id, object_id)
            elif action == "stale":
                data = self.storage.read(node_id, object_id)
                stale_version = max(0, obj["version"] - 1)
                if obj["kind"] == "simulated":
                    value = json.loads(data.decode())
                    value["version"] = stale_version
                    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                else:
                    data = b"stale-representation"
                self.storage.write(node_id, object_id, data)
                con.execute("UPDATE replicas SET version=?, updated_at=? WHERE object_id=? AND node_id=?",
                            (stale_version, now(), object_id, node_id))
            else:
                raise ValueError(action)
            self._record_event(con, "FAULT_INJECTED", "Injected %s fault" % action,
                               object_id, node_id, replica["state"], None,
                               expected_checksum=obj["checksum"])
            con.commit()
        return self.repair(object_id)

    def maintenance_scan(self):
        with self.db.connect() as con:
            ids = [r["id"] for r in con.execute("SELECT id FROM objects")]
        for object_id in ids:
            self.repair(object_id)
