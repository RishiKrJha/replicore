import sqlite3
import threading


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_schema(self):
        with self.lock, self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS objects (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
                    size INTEGER NOT NULL, checksum TEXT NOT NULL, version INTEGER NOT NULL,
                    replication_factor INTEGER NOT NULL, state TEXT NOT NULL, seed TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replicas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, object_id TEXT NOT NULL,
                    node_id TEXT NOT NULL, state TEXT NOT NULL, checksum TEXT,
                    version INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(object_id, node_id), FOREIGN KEY(object_id) REFERENCES objects(id),
                    FOREIGN KEY(node_id) REFERENCES nodes(id)
                );
                CREATE TABLE IF NOT EXISTS operation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_id TEXT,
                    node_id TEXT,
                    event_type TEXT NOT NULL,
                    previous_state TEXT,
                    resulting_state TEXT,
                    source_node TEXT,
                    destination_node TEXT,
                    expected_checksum TEXT,
                    actual_checksum TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(object_id) REFERENCES objects(id),
                    FOREIGN KEY(node_id) REFERENCES nodes(id)
                );
                CREATE INDEX IF NOT EXISTS idx_replicas_object_id ON replicas(object_id);
                CREATE INDEX IF NOT EXISTS idx_events_object_id ON operation_events(object_id);
                CREATE INDEX IF NOT EXISTS idx_events_created_at ON operation_events(created_at DESC);
                """
            )
