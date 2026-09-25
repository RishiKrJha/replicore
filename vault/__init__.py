from pathlib import Path

from flask import Flask

from .config import Config
from .db import Database
from .routes import bp
from .service import VaultService


def create_app(test_config=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["STORAGE_ROOT"]).mkdir(parents=True, exist_ok=True)
    db = Database(app.config["DATABASE_PATH"])
    db.init_schema()
    service = VaultService(db, app.config["STORAGE_ROOT"])
    service.ensure_nodes(app.config["NODE_COUNT"])
    service.maintenance_scan()
    app.extensions["vault_db"] = db
    app.extensions["vault"] = service
    app.register_blueprint(bp)
    return app
