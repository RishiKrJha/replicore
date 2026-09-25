# Vault v0.1

Local distributed object-storage prototype built with Flask. It stores real uploads
in isolated `storage/node-*` directories and represents simulated large objects with
small deterministic manifests. SQLite stores metadata only.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

Open `http://127.0.0.1:5000` for the public Vault interface. The authenticated Admin Portal
is at `/admin`; development credentials are `admin` / `admin`. Set `ADMIN_USERNAME` and
`ADMIN_PASSWORD` in `.env` for local use. Fault controls perform real operations: failed
nodes reject storage access, missing replicas remove files, and corruption changes stored
representations. Repair runs synchronously after verification, fault injection, retrieval,
and startup maintenance. The Admin Portal keeps persistent operation history showing fault
detection, checksums, repair source/destination, and final verification results.

Real uploads are stored in separate node directories. Simulated objects store only a small
deterministic manifest while retaining their declared logical size. The portal reports both
logical and physical sizes and shows verified replica counts.

Run the acceptance tests with `pytest`.
