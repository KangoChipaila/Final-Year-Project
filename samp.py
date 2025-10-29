# ...existing code...
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from datetime import datetime
import pdfkit
from sqlalchemy.exc import OperationalError
# ...existing code...

# ------------------- Login Manager Setup -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ------------------- Fallback / Development user store -------------------
# Keep a small in-memory fallback for dev, but primary auth will try the DB.
USERS = {
    "admin": {"password": "admin123"},
    "kango": {"password": "erp2025"}
}

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    """
    Load a user by id. First try the database 'users' table (columns: id, username).
    If the DB/table isn't available, fall back to the in-memory USERS dict.
    """
    try:
        row = db.session.execute(
            "SELECT id, username FROM users WHERE id = :id LIMIT 1",
            {"id": user_id}
        ).fetchone()
        if row:
            return User(id=str(row[0]), username=row[1])
    except OperationalError:
        # DB not ready or table missing — fall back to in-memory users
        pass

    # fallback: check in-memory USERS
    if user_id in USERS:
        return User(id=user_id, username=user_id)
    return None

# ------------------- Login Route -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    """
    Login will first attempt to authenticate against a 'users' table in the DB
    (expected columns: id, username, password). If the table is not available,
    it will fall back to the in-memory USERS dict.
    Note: storing plaintext passwords in DB is insecure; use hashed passwords in production.
    """
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Try DB authentication
        try:
            row = db.session.execute(
                "SELECT id, username, password FROM users WHERE username = :username LIMIT 1",
                {"username": username}
            ).fetchone()
            if row:
                db_id, db_username, db_password = row[0], row[1], row[2]
                # NOTE: if passwords are hashed in your DB use a proper hash check here.
                if db_password == password:
                    login_user(User(id=str(db_id), username=db_username))
                    return redirect(url_for("index"))
                else:
                    return render_template("login.html", error="Invalid username or password")
        except OperationalError:
            # DB not available or table missing: fall back to in-memory users
            user = USERS.get(username)
            if user and user["password"] == password:
                login_user(User(id=username, username=username))
                return redirect(url_for("index"))
            else:
                return render_template("login.html", error="Invalid username or password")

        # If query ran but no matching row
        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")
# ...existing code...