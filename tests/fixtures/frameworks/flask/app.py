from flask import Flask, Blueprint

app = Flask(__name__)
api = Blueprint("api", __name__, url_prefix="/api")
admin = Blueprint("admin", __name__)


@app.route("/")
def index():
    return "x"


@api.route("/users", methods=["GET", "POST"])
def users():
    return "u"


@api.get("/users/<int:uid>")
def user(uid):
    return "u"


@admin.route("/stats")
def stats():
    return "s"


app.register_blueprint(api)
app.register_blueprint(admin, url_prefix="/admin")
