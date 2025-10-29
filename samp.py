# ...existing code...
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
# ...existing code...

# ------------------- Login Manager Setup -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# lightweight wrapper so Flask-Login works with models.User (DB model)
class AuthUser(UserMixin):
    def __init__(self, model):
        self._model = model
        # Flask-Login expects get_id() to return a string
        self.id = str(getattr(model, "id", ""))
        self.username = getattr(model, "username", None)
        self.is_active = bool(getattr(model, "is_active", True))

    @property
    def model(self):
        return self._model

# simple fallback in-memory user wrapper (for development)
class FallbackUser:
    def __init__(self, username):
        self.id = username
        self.username = username
        self.is_active = True

# remove the local shadowing User class (it previously hid models.User)
# ...existing code...

@login_manager.user_loader
def load_user(user_id):
    """
    Load a user by id. Try numeric-id lookup in DB first, otherwise username lookup.
    If DB is unavailable or lookup fails, fall back to in-memory USERS.
    Returns an AuthUser (wrapping the DB model) or None.
    """
    try:
        # try treating user_id as integer primary key first
        try:
            uid = int(user_id)
            user_row = db.session.get(User, uid)
            if user_row:
                return AuthUser(user_row)
        except (ValueError, TypeError):
            # not an int — do username lookup
            user_row = None

        if user_row is None:
            try:
                user_row = db.session.query(User).filter_by(username=user_id).first()
                if user_row:
                    return AuthUser(user_row)
            except (OperationalError, DataError):
                # DB trouble during username lookup — fall through to fallback
                pass

    except (OperationalError, DataError):
        # DB not ready — fall back to in-memory users
        pass

    # fallback to in-memory USERS
    if user_id in USERS:
        return AuthUser(FallbackUser(user_id))
    return None

# ------------------- Signup Route -------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    """
    Simple signup page that creates a new User row (if DB available).
    Expects form fields: username, password, (optional) email.
    """
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", None)

        if not username or not password:
            flash("Please provide a username and password.", "error")
            return redirect(url_for("signup"))

        # Try to create user in DB
        try:
            # Ensure username not already taken
            existing = db.session.query(User).filter_by(username=username).first()
            if existing:
                flash("Username already taken.", "error")
                return redirect(url_for("signup"))

            new_user = User(username=username)
            # set optional fields if model defines them
            if hasattr(new_user, "email") and email:
                setattr(new_user, "email", email)

            # Prefer model helper methods for password handling
            if hasattr(new_user, "set_password"):
                new_user.set_password(password)
            elif hasattr(new_user, "password_hash"):
                # fallback: set password_hash directly (insecure — replace with proper hashing)
                setattr(new_user, "password_hash", password)

            db.session.add(new_user)
            db.session.commit()

            login_user(AuthUser(new_user))
            flash("Account created and logged in.", "success")
            return redirect(url_for("index"))

        except (OperationalError, DataError) as e:
            flash("Database unavailable. Cannot create account right now.", "error")
            return redirect(url_for("signup"))
        except Exception as e:
            # generic error (validation / schema mismatch)
            flash("Failed to create account.", "error")
            return redirect(url_for("signup"))

    return render_template("signup.html")

# ------------------- Login Route (use DB model when available) -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    """
    Authenticate against models.User when available. Fall back to in-memory USERS.
    """
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Try DB authentication
        try:
            user = db.session.query(User).filter_by(username=username).first()
            if user:
                # prefer model's password checker if provided
                if hasattr(user, "check_password"):
                    if user.check_password(password):
                        login_user(AuthUser(user))
                        return redirect(url_for("index"))
                    else:
                        return render_template("login.html", error="Invalid username or password")
                # fallback if password_hash stores plaintext (not recommended)
                if getattr(user, "password_hash", None) == password:
                    login_user(AuthUser(user))
                    return redirect(url_for("index"))
                return render_template("login.html", error="Invalid username or password")
        except (OperationalError, DataError):
            # DB not available — try in-memory
            pass

        # fallback: in-memory USERS
        user = USERS.get(username)
        if user and user["password"] == password:
            login_user(AuthUser(FallbackUser(username)))
            return redirect(url_for("index"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

# ...existing code...

if __name__ == '__main__':
    # Ensure DB tables exist (create missing tables from models.py)
    try:
        with app.app_context():
            db.create_all()

            # seed a default admin user if no users exist
            try:
                user_count = 0
                try:
                    user_count = db.session.query(User).count()
                except Exception:
                    user_count = 0

                if user_count == 0:
                    admin = User(username='admin')
                    if hasattr(admin, 'email'):
                        setattr(admin, 'email', 'admin@example.com')
                    if hasattr(admin, 'full_name'):
                        setattr(admin, 'full_name', 'Administrator')
                    if hasattr(admin, 'group_id'):
                        setattr(admin, 'group_id', 0)
                    if hasattr(admin, 'role'):
                        setattr(admin, 'role', 'admin')
                    if hasattr(admin, 'is_active'):
                        setattr(admin, 'is_active', True)

                    if hasattr(admin, 'set_password'):
                        admin.set_password('admin123')
                    elif hasattr(admin, 'password_hash'):
                        setattr(admin, 'password_hash', 'admin123')

                    db.session.add(admin)
                    db.session.commit()
                    print("Created default admin user (username=admin, password=admin123). Change immediately.")
            except Exception as seed_err:
                print("Warning: could not seed admin user:", seed_err)

    except OperationalError as e:
        print("Database not available, skipping automatic table creation:", e)

    app.run(debug=True)
# ...existing code...