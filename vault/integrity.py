import hashlib
import json


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def simulated_representation(object_id, size, seed, version):
    return json.dumps({"object_id": object_id, "size": size, "seed": seed, "version": version},
                      sort_keys=True, separators=(",", ":")).encode()
