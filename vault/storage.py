import os
import shutil
import uuid
from pathlib import Path


class NodeStorage:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, node_id, object_id):
        return self.root / node_id / f"{object_id}.data"

    def write(self, node_id, object_id, data):
        directory = self.root / node_id
        directory.mkdir(parents=True, exist_ok=True)
        target = self.path(node_id, object_id)
        temporary = directory / f".{object_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, node_id, object_id):
        with self.path(node_id, object_id).open("rb") as stream:
            return stream.read()

    def remove(self, node_id, object_id):
        self.path(node_id, object_id).unlink(missing_ok=True)

    def exists(self, node_id, object_id):
        return self.path(node_id, object_id).is_file()

    def copy(self, source_node, destination_node, object_id):
        self.write(destination_node, object_id, self.read(source_node, object_id))

    def corrupt(self, node_id, object_id):
        path = self.path(node_id, object_id)
        with path.open("ab") as stream:
            stream.write(b"\ncorrupted-by-vault")

    def clear(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
