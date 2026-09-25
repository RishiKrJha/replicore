from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, session, url_for
from io import BytesIO

bp = Blueprint("portal", __name__)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("portal.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/admin/login", methods=["GET", "POST"])
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == current_app.config["ADMIN_USERNAME"] and request.form.get("password") == current_app.config["ADMIN_PASSWORD"]:
            session["admin"] = True
            return redirect(request.args.get("next") or url_for("portal.admin_dashboard"))
        flash("Invalid administrator credentials", "error")
    return render_template("login.html")


@bp.post("/admin/logout")
@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("portal.login"))


@bp.route("/")
def dashboard():
    service = current_app.extensions["vault"]
    return render_template("dashboard.html", nodes=service.nodes(), objects=service.objects(), admin=False)


@bp.route("/admin")
@admin_required
def admin_dashboard():
    service = current_app.extensions["vault"]
    return render_template("dashboard.html", nodes=service.nodes(), objects=service.objects(),
                           events=service.events(), admin=True)


@bp.post("/objects/upload")
def upload():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a file", "error")
    else:
        service = current_app.extensions["vault"]
        factor = int(request.form.get("replication_factor", current_app.config["DEFAULT_REPLICATION_FACTOR"]))
        obj = service.create_real(upload.filename, upload.read(), factor)
        flash(f"Uploaded {obj['filename']} as {obj['id']}", "success")
    return redirect(url_for("portal.dashboard"))


@bp.post("/objects/simulated")
def simulated():
    service = current_app.extensions["vault"]
    obj = service.create_simulated(
        request.form.get("filename", "simulated.bin"),
        int(request.form.get("size", 0)),
        int(request.form.get("replication_factor", current_app.config["DEFAULT_REPLICATION_FACTOR"])),
    )
    flash(f"Created simulated object {obj['id']}", "success")
    return redirect(url_for("portal.dashboard"))


@bp.get("/objects/<object_id>/download")
def download(object_id):
    data, obj = current_app.extensions["vault"].retrieve(object_id)
    return send_file(BytesIO(data), as_attachment=True, download_name=obj["filename"])


@bp.post("/admin/nodes/<node_id>/<action>")
@bp.post("/nodes/<node_id>/<action>")
@admin_required
def node_action(node_id, action):
    if action not in {"fail", "restore"}:
        return ("Unknown node action", 400)
    service = current_app.extensions["vault"]
    service.set_node(node_id, "FAILED" if action == "fail" else "ONLINE")
    flash(service.operation_summary() or "Node operation completed", "success")
    return redirect(url_for("portal.admin_dashboard"))


@bp.post("/admin/objects/<object_id>/replicas/<node_id>/<action>")
@bp.post("/objects/<object_id>/replicas/<node_id>/<action>")
@admin_required
def replica_action(object_id, node_id, action):
    if action not in {"remove", "corrupt", "stale"}:
        return ("Unknown replica action", 400)
    service = current_app.extensions["vault"]
    service.inject(object_id, node_id, action)
    flash(service.operation_summary(object_id) or "Replica operation completed", "success")
    return redirect(url_for("portal.admin_dashboard"))


@bp.post("/admin/objects/<object_id>/verify")
@admin_required
def verify_object(object_id):
    service = current_app.extensions["vault"]
    service.verify(object_id)
    service.repair(object_id)
    flash(service.operation_summary(object_id) or "Verification completed", "success")
    return redirect(url_for("portal.admin_dashboard"))
