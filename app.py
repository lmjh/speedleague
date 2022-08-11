import os
import math
import urllib.parse
import re

from functools import wraps
from flask import (
    Flask,
    flash,
    render_template,
    redirect,
    request,
    session,
    url_for,
)
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime

if os.path.exists("env.py"):
    import env


app = Flask(__name__)

app.config["MONGO_DBNAME"] = os.environ.get("MONGO_DBNAME")
app.config["MONGO_URI"] = os.environ.get("MONGO_URI")
app.secret_key = os.environ.get("SECRET_KEY")

mongo = PyMongo(app)


def find_user(username):
    """
    Helper function that searches the users collection for a record with a
    username matching the given username and returns the record if found.
    """
    # query database for username and return it
    user = mongo.db.users.find_one({"username": username})
    return user


def login_required(f):
    """
    Decorator to check if a user is currently logged in and redirect to the
    login page if not. Based on this function from the Flask documetation:
    https://flask.palletsprojects.com/en/2.0.x/patterns/viewdecorators/#login-required-decorator
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user") is None:
            flash("You must login to access that page.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def admin_only(f):
    """
    Decorator to check if current user is the admin and redirect to home page
    if not.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user") != "admin":
            flash("You do not have permission to access that page.")
            return redirect(url_for("home"))
        return f(*args, **kwargs)

    return decorated_function


@app.context_processor
def inject_now():
    """
    Context processor to inject current datetime into template
    """
    return {'now': datetime.utcnow()}


@app.template_global()
def centi_to_string(centi):
    """
    Converts an integer number of centiseconds into a string in the format
    "hours:minutes:seconds:centiseconds".
    """
    centiseconds = centi % 100
    seconds = math.floor((centi / 100) % 60)
    minutes = math.floor(((centi / (100 * 60)) % 60))
    hours = math.floor(((centi / (100 * 60 * 60)) % 24))
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


@app.template_global()
def string_to_centi(string):
    """
    Converts a string in the format "hours:minutes:seconds:centiseconds" into
    an integer number of centiseconds.
    """
    split = re.split(":|\.", string)
    centi = (
        int(split[3])
        + int(split[2]) * 100
        + int(split[1]) * 100 * 60
        + int(split[0]) * 100 * 60 * 60
    )
    return centi


@app.template_global()
def url_to_display(text):
    """
    Replaces underscores with spaces.
    """
    return text.replace("_", " ")


@app.template_global()
def display_to_url(text):
    """
    Replaces spaces with underscores.
    """
    return text.replace(" ", "_")


def nav_links():
    """
    Build a dict of games and categories to use in building nav links
    """
    # Retrieve list of games from database
    games = list(mongo.db.games.find({}))
    # Iteration done in nav.html:
    # https://realpython.com/iterate-through-dictionary-python/
    # Dict comprehension:
    # https://careerkarma.com/blog/python-convert-list-to-dictionary/
    # Build a dictopmary with names of games as keys and array of categories as
    # values
    game_dictionary = {
        game["name"]: list(
            mongo.db.categories.find({"game_id": ObjectId(game["_id"])})
        )
        for game in games
    }
    return game_dictionary


@app.route("/")
def home():
    """
    Finds the game name and category name of the default game and redirect to
    its leaderboard page.
    """
    default_game = mongo.db.games.find_one_or_404(
        {"_id": ObjectId("62ed293931cff58ed6a6148b")}
    )
    default_category = mongo.db.categories.find_one_or_404(
        {"_id": ObjectId("62ed29e68465a6e232e28242")}
    )
    return redirect(
        url_for(
            "show_scores",
            game_name=default_game["name"],
            category_name=default_category["name"],
        )
    )


@app.route("/<game_name>/<category_name>")
def show_scores(game_name, category_name):
    """
    Renders the leaderboard page for the given game and category.
    """
    # find game
    game = mongo.db.games.find_one_or_404(
        {"name": urllib.parse.unquote(game_name)}
    )
    # find category
    category = mongo.db.categories.find_one_or_404(
        {
            "name": urllib.parse.unquote(category_name),
            "game_id": ObjectId(game["_id"]),
        }
    )
    # find scores of players who have scores in this category
    scores = list(
        mongo.db.scores.aggregate(
            [
                {
                    "$match": {
                        "game_id": ObjectId(game["_id"]),
                        "category_id": ObjectId(category["_id"]),
                    }
                },
                {
                    "$lookup": {
                        "from": "players",
                        "localField": "player_id",
                        "foreignField": "_id",
                        "as": "player",
                    }
                },
                {"$unwind": "$player"},
                {
                    "$group": {
                        "_id": "$player.name",
                        "score": {"$min": "$score"},
                        "links": {"$first": "$player.links"},
                    }
                },
                {"$sort": {"score": 1}},
            ]
        )
    )

    return render_template(
        "show_scores.html",
        page_title=url_to_display(game["name"])
        + " - "
        + url_to_display(category["name"]),
        scores=scores,
        game=game,
        category=category,
        nav_links=nav_links(),
    )


@app.route("/manage_users")
@admin_only
def manage_users():
    """
    Renders the Manage Users page if current user is admin.
    """
    users = list(mongo.db.users.find())
    return render_template(
        "manage_users.html",
        page_title="Manage Users",
        nav_links=nav_links(),
        users=users,
    )


@app.route("/add_user", methods=["GET", "POST"])
@admin_only
def add_user():
    """
    GET: Renders Add User page if current user is "admin"
    POST: Gathers submitted data and adds new user to database if current user
    is "admin
    """
    if request.method == "POST":
        # check if submitted username already exists in the database
        username = request.form.get("username").lower()
        duplicate_user = find_user(username)

        # if username already exists, redirect to add user page
        if duplicate_user:
            flash(f'Username "{username}" is unavailable.')
            return redirect(url_for("add_user"))

        # build dictionary with submitted details
        new_user = {
            "username": username,
            "password": generate_password_hash(request.form.get("password")),
        }

        # insert new user dict to users database
        mongo.db.users.insert_one(new_user)

        flash(f"User '{username}' has been added to the database.")
        return redirect(url_for("add_user"))

    return render_template(
        "add_user.html", page_title="Add User", nav_links=nav_links()
    )


@app.route("/edit_user/<user_id>", methods=["GET", "POST"])
@admin_only
def edit_user(user_id):
    """
    Renders the Edit Users page for the given user_id if current user is admin.
    """
    user = mongo.db.users.find_one_or_404({"_id": ObjectId(user_id)})
    if request.method == "POST":
        # update the user's password and redirect to manage users page
        new_password = generate_password_hash(request.form.get("password"))
        mongo.db.users.update_one(user, {"$set": {"password": new_password}})

        flash("Password updated.")
        return redirect(url_for("manage_users"))

    return render_template(
        "edit_user.html",
        page_title="Edit User",
        user=user,
        nav_links=nav_links(),
    )


@app.route("/delete_user/<user_id>")
@admin_only
def delete_user(user_id):
    """
    Deletes the given user from the users database.
    """
    user = mongo.db.users.find_one_or_404({"_id": ObjectId(user_id)})
    mongo.db.users.delete_one(user)
    flash(f"User '{user['username']}' deleted.")
    return redirect(url_for("manage_users"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """
    GET: If user is logged in, redirects them to home page. Otherwise,
    renders the login page.
    POST: Collects submitted user credentials. If username and passsword are
    correct, user is logged in and redirected to the home page. If username and
    password are incorrect, user is redirected to the login page.
    """
    # check if user is currently logged in
    if session.get("user") is None:
        if request.method == "POST":
            # assign submitted username to a variable and query the database to
            # find a record with that name
            username = request.form.get("username").lower()
            valid_username = find_user(username)

            # check the submitted username exists in the database
            if valid_username:

                # check the submitted password matches the database
                if check_password_hash(
                    valid_username["password"], request.form.get("password")
                ):

                    # add user to session cookie and redirect to home
                    session["user"] = username
                    flash(f"Welcome, {username}")
                    return redirect(url_for("admin"))

                # if submitted password is incorrect, return to login page
                flash("Username or password incorrect. Please try again.")
                return redirect(url_for("login"))

            # if submitted username is incorrect, return to login page
            flash("Username or password incorrect. Please try again.")
            return redirect(url_for("login"))

        return render_template(
            "login.html", page_title="Login", nav_links=nav_links()
        )

    # redirect already logged in users to home page
    return redirect(url_for("admin"))


@app.route("/logout")
@login_required
def logout():
    """
    Removes the user from the session cookie and redirects to the home page.
    """
    session.pop("user")
    flash("You have been logged out.")
    return redirect(url_for("home"))


@app.route("/update_password", methods=["GET", "POST"])
@login_required
def update_password():
    """
    GET: Renders the Update Password page
    POST: Checks if user entered the correct current password, then updates the
    user's password
    """
    if request.method == "POST":
        # find the currently logged in user in the database
        user = find_user(session.get("user"))

        # check that the submitted current password matches the database
        if check_password_hash(user["password"], request.form.get("password")):
            # update user's password and redirect to admin page
            new_password = generate_password_hash(
                request.form.get("new_password")
            )
            mongo.db.users.update_one(
                user, {"$set": {"password": new_password}}
            )

            flash("Password updated.")
            return redirect(url_for("admin"))

        flash(
            "The current password you entered was incorrect.  Please try "
            "again."
        )
        return redirect(url_for("update_password"))

    return render_template(
        "update_password.html",
        page_title="Update Password",
        nav_links=nav_links(),
    )


@app.route("/admin")
@login_required
def admin():
    """
    Renders the admin panel page.
    """
    # query the database for all stored games and players
    games = list(mongo.db.games.find())
    players = list(mongo.db.players.find())

    # sort players list by name
    players.sort(key=lambda x: x["name"].lower())

    return render_template(
        "admin.html",
        page_title="Admin Panel",
        nav_links=nav_links(),
        games=games,
        players=players,
    )


@app.route("/add_score/<category_id>", methods=["GET", "POST"])
@login_required
def add_score(category_id):
    """
    GET: Renders the Add Score page for the given category.
    POST: Gathers submitted score data and adds to the database.
    """
    if request.method == "POST":
        # retrieve player_id from form
        player_id = request.form.get("player_name")

        # retireve all time data from form and convert into a string in the
        # format hours:minutes:seconds:centiseconds
        time_str = (
            request.form.get("hours")
            + ":"
            + request.form.get("minutes")
            + ":"
            + request.form.get("seconds")
            + ":"
            + request.form.get("centiseconds")
        )

        # convert time string into integer number of centiseconds
        score = string_to_centi(time_str)

        # find category and game from the database
        category = mongo.db.categories.find_one_or_404(
            {"_id": ObjectId(category_id)}
        )
        game = mongo.db.games.find_one_or_404(
            {"_id": ObjectId(category["game_id"])}
        )

        # build dict object with user submitted data
        new_score = {
            "game_id": ObjectId(game["_id"]),
            "category_id": ObjectId(category["_id"]),
            "player_id": ObjectId(player_id),
            "score": score,
        }

        # add score object to database and redirect to admin panel
        mongo.db.scores.insert_one(new_score)
        flash("Score added.")
        return redirect(url_for("admin"))

    # find category and game from the database
    category = mongo.db.categories.find_one_or_404(
        {"_id": ObjectId(category_id)}
    )
    game = mongo.db.games.find_one_or_404(
        {"_id": ObjectId(category["game_id"])}
    )

    # retrieve players list from database and sort by name
    players = list(mongo.db.players.find())
    players.sort(key=lambda x: x["name"])

    return render_template(
        "add_score.html",
        page_title="Add Score",
        nav_links=nav_links(),
        category=category,
        game=game,
        players=players,
    )


@app.route("/delete_scores/<category_id>")
@login_required
def delete_scores(category_id):
    """
    Renders the delete scores page for the given category.
    """
    # query the database for category and game
    category = mongo.db.categories.find_one_or_404(
        {"_id": ObjectId(category_id)}
    )
    game = mongo.db.games.find_one_or_404(
        {"_id": ObjectId(category["game_id"])}
    )

    # retrieve list of all scores for the given category and game
    scores = list(
        mongo.db.scores.aggregate(
            [
                {
                    "$match": {
                        "game_id": ObjectId(game["_id"]),
                        "category_id": ObjectId(category["_id"]),
                    }
                },
                {
                    "$lookup": {
                        "from": "players",
                        "localField": "player_id",
                        "foreignField": "_id",
                        "as": "player",
                    }
                },
                {"$unwind": "$player"},
                {"$sort": {"score": 1}},
            ]
        )
    )
    return render_template(
        "delete_scores.html",
        page_title="Delete Scores",
        nav_links=nav_links(),
        game=game,
        category=category,
        scores=scores,
    )


@app.route("/delete_score/<score_id>")
@login_required
def delete_score(score_id):
    """
    Adds a copy of the given score to the archive database and deletes the
    score from the scores database.
    """
    # find score
    score = mongo.db.scores.find_one_or_404({"_id": ObjectId(score_id)})

    # add score to the archive database and delete score from scores database
    mongo.db.archive.insert_one(score)
    mongo.db.scores.delete_one(score)
    flash("Score deleted.")
    return redirect(url_for("admin"))


@app.route("/add_player", methods=["GET", "POST"])
@login_required
def add_player():
    """
    GET: Renders the Add Player page.
    POST: Gathers submitted player data and adds to the players database.
    """
    if request.method == "POST":
        # retrieve submitted data from form
        name = request.form.get("name")
        twitch = request.form.get("twitch")
        youtube = request.form.get("youtube")
        link = request.form.get("link")

        # if a twitch username was entered, convert into link
        if twitch:
            twitch = "https://www.twitch.tv/" + twitch

        # if a youtube channel name was entered, convert into link
        if youtube:
            youtube = "https://www.youtube.com/c/" + youtube

        # check that the submitted name doesn't match any in the database
        duplicate = mongo.db.players.find_one({"name": name})

        # if name is already in the database, redirect back to add player page
        if duplicate:
            flash(
                "That name is already in use. Please try again with a "
                "different name."
            )
            return redirect(url_for("add_player"))

        # build dict with user submitted data and insert into players database
        new_player = {
            "name": name,
            "links": {
                "twitch": twitch,
                "youtube": youtube,
                "link": link,
            },
        }

        mongo.db.players.insert_one(new_player)
        flash("New player added.")
        return redirect(url_for("admin"))

    return render_template(
        "add_player.html",
        page_title="Add Player",
        nav_links=nav_links(),
    )


@app.route("/edit_player/<player_id>", methods=["GET", "POST"])
@login_required
def edit_player(player_id):
    """
    GET: Renders the Edit Player page for the given player.
    POST: Gathers submitted player data and updates the players database.
    """
    player = mongo.db.players.find_one_or_404({"_id": ObjectId(player_id)})
    if request.method == "POST":
        # retrieve submitted data from form
        name = request.form.get("name")
        twitch = request.form.get("twitch")
        youtube = request.form.get("youtube")
        link = request.form.get("link")

        # if the player name has changed, check it isn't a duplicate of another
        # name in the database
        if name != player["name"]:
            duplicate_name = mongo.db.players.find_one({"name": name})
            if duplicate_name:
                flash("Duplicate name. Please try again.")
                return redirect(
                    url_for("edit_player", player_id=player["_id"])
                )

        # if a twitch username was entered, convert into link
        if twitch:
            twitch = "https://www.twitch.tv/" + twitch

        # if a youtube channel name was entered, convert into link
        if youtube:
            youtube = "https://www.youtube.com/c/" + youtube

        # build dict with user submitted data
        edited_player = {
            "name": name,
            "links": {
                "twitch": twitch,
                "youtube": youtube,
                "link": link,
            },
        }

        # update the player's database entry with the data
        mongo.db.players.update_one(player, {"$set": edited_player})
        flash("Player updated.")
        return redirect(url_for("admin"))

    return render_template(
        "edit_player.html",
        page_title="Edit Player",
        nav_links=nav_links(),
        player=player,
    )


@app.route("/delete_player/<player_id>")
@login_required
def delete_player(player_id):
    """
    Adds a copy of the given player and their scores to the archive database
    and deletes them from the players and scores databases.
    """
    # query the database for the player and their scores
    player = mongo.db.players.find_one_or_404({"_id": ObjectId(player_id)})
    scores = mongo.db.scores.find({"player_id": ObjectId(player_id)})

    # copy the player object to a new dict and add the scores as a list
    player_archive = player
    player_archive["scores"] = list(scores)

    # add the archive dict to the archive database
    mongo.db.archive.insert_one(player_archive)

    # delete the player and their scores from the players and scores databases
    mongo.db.players.delete_one({"_id": ObjectId(player_id)})
    mongo.db.scores.delete_many({"player_id": ObjectId(player_id)})

    flash("Player and scores deleted.")
    return redirect(url_for("admin"))


@app.route("/add_game", methods=["GET", "POST"])
@login_required
def add_game():
    """
    GET: Renders the Add Game page.
    POST: Gathers submitted game data and adds to the games database.
    """
    if request.method == "POST":
        # retrieve game name from form and replace spaces with underscores
        name = display_to_url(request.form.get("name"))

        # check if the game name is already in use in the database
        duplicate = mongo.db.games.find_one({"name": name})
        if duplicate:
            flash(
                "The submitted game name is already in use. Please try again."
            )
            return redirect(url_for("add_game"))

        # Add the game name to a dict and then add to games database
        new_game = {
            "name": name,
        }
        mongo.db.games.insert_one(new_game)
        flash("Game added.")
        return redirect(url_for("admin"))

    return render_template(
        "add_game.html",
        page_title="Add Game",
        nav_links=nav_links(),
    )


@app.route("/edit_game/<game_id>", methods=["GET", "POST"])
@login_required
def edit_game(game_id):
    """
    GET: Renders the Edit Game page for the given game name.
    POST: Gathers submitted game data and adds to the games database.
    """
    # find the requested game in the database
    game = mongo.db.games.find_one_or_404({"_id": ObjectId(game_id)})
    if request.method == "POST":
        # retrieve game name from form and replace spaces with underscores
        name = display_to_url(request.form.get("name"))

        # check if the game name is already in use in the database
        duplicate_name = mongo.db.games.find_one({"name": name})
        if duplicate_name:
            flash("Duplicate name. Please try again.")
            return redirect(url_for("edit_game", game_id=game["_id"]))

        # Add the game name to a dict and then update the games database
        edited_game = {
            "name": name,
        }
        mongo.db.games.update_one(game, {"$set": edited_game})
        flash("Game updated.")
        return redirect(url_for("admin"))

    return render_template(
        "edit_game.html",
        page_title="Edit Game",
        nav_links=nav_links(),
        game=game,
    )


@app.route("/delete_game/<game_id>")
@login_required
def delete_game(game_id):
    """
    Adds a copy of the given game and all of its categories and scores to the
    archive database, then deletes them from the games, categories and scores
    databases.
    """
    # find game and all categories and scores for the game in the database
    game = mongo.db.games.find_one_or_404({"_id": ObjectId(game_id)})
    categories = mongo.db.categories.find({"game_id": ObjectId(game_id)})
    scores = mongo.db.scores.find({"game_id": ObjectId(game_id)})

    # copy the game object to a new dict and then add the categories and scores
    # as lists
    game_archive = game
    game_archive["categories"] = list(categories)
    game_archive["scores"] = list(scores)

    # add the archive dict to the archive database, then delete the game and
    # all its categories and scores from the games, categories and scores
    # databases
    mongo.db.archive.insert_one(game_archive)
    mongo.db.games.delete_one({"_id": ObjectId(game_id)})
    mongo.db.categories.delete_many({"game_id": ObjectId(game_id)})
    mongo.db.scores.delete_many({"game_id": ObjectId(game_id)})

    flash("Game, categories and scores deleted.")
    return redirect(url_for("admin"))


@app.route("/add_category", methods=["GET", "POST"])
@login_required
def add_category():
    """
    GET: Renders the Add Category page.
    POST: Gathers submitted category data and adds to the categories database.
    """
    if request.method == "POST":
        # retrieve the game_id and name from the form
        game_id = request.form.get("game_id")
        name = display_to_url(request.form.get("name"))

        # check if the given game already has a category with the given name
        duplicate = mongo.db.categories.find_one(
            {"name": name, "game_id": ObjectId(game_id)}
        )
        if duplicate:
            flash("Duplicate category name. Please try again.")
            return redirect(url_for("add_category", id=game_id))

        # retrieve the category description
        desc = request.form.get("desc")

        # add user data to a dict and then insert into categories database
        new_category = {
            "game_id": ObjectId(game_id),
            "name": name,
            "desc": desc,
        }
        mongo.db.categories.insert_one(new_category)
        flash("Category added.")
        return redirect(url_for("admin"))

    games = list(mongo.db.games.find())
    return render_template(
        "add_category.html",
        page_title="Add Category",
        nav_links=nav_links(),
        games=games,
    )


@app.route("/edit_category/<game_id>/<category_id>", methods=["GET", "POST"])
@login_required
def edit_category(game_id, category_id):
    """
    GET: Renders the Edit Category page for the given game and category.
    POST: Gathers submitted category data and adds to the categories database.
    """
    # find the requested game and category in the database
    mongo.db.games.find_one_or_404({"_id": ObjectId(game_id)})
    category = mongo.db.categories.find_one_or_404(
        {"_id": ObjectId(category_id)}
    )

    if request.method == "POST":
        # retrieve the game_id and category name from the form
        game_id = request.form.get("game_id")
        name = display_to_url(request.form.get("name"))

        # if the name has changed, check that it doesn't match other categories
        if name != category["name"]:
            duplicate = mongo.db.categories.find_one(
                {"name": name, "game_id": ObjectId(game_id)}
            )

            if duplicate:
                flash("Duplicate category name. Please try again.")
                return redirect(
                    url_for(
                        "edit_category",
                        game_id=game_id,
                        category_id=category_id,
                    )
                )

        # retrieve the category description
        desc = request.form.get("desc")

        # add user data to a dict and then update the categories database
        updated_category = {
            "game_id": ObjectId(game_id),
            "name": name,
            "desc": desc,
        }

        mongo.db.categories.update_one(category, {"$set": updated_category})
        flash("Category updated.")
        return redirect(url_for("admin"))

    games = list(mongo.db.games.find())
    return render_template(
        "edit_category.html",
        page_title="Edit Category",
        nav_links=nav_links(),
        games=games,
        game_id=game_id,
        category=category,
    )


@app.route("/delete_category/<category_id>")
@login_required
def delete_category(category_id):
    """
    Adds a copy of the given category and all of its scores to the archive
    database, then deletes them from the categories and scores databases.
    """
    # find category and all scores under that category
    category = mongo.db.categories.find_one_or_404(
        {"_id": ObjectId(category_id)}
    )
    scores = mongo.db.scores.find({"category_id": ObjectId(category_id)})

    # add category and all scores to archive dict
    category_archive = category
    category_archive["scores"] = list(scores)

    # add archive dict to archive collection
    mongo.db.archive.insert_one(category_archive)

    # delete category and scores
    mongo.db.categories.delete_one({"_id": ObjectId(category_id)})
    mongo.db.scores.delete_many({"category_id": ObjectId(category_id)})

    flash("Categories and scores deleted.")
    return redirect(url_for("admin"))


@app.errorhandler(404)
def page_not_found(error):
    """
    Renders an error page when a 404 exception is raised.
    """
    return (
        render_template(
            "errors/404.html",
            page_title="Page Not Found",
            nav_links=nav_links(),
        ),
        404,
    )


@app.errorhandler(500)
def internal_server_error(error):
    """
    Renders an error page when a 500 exception is raised.
    """
    return (
        render_template(
            "errors/500.html",
            page_title="Internal Server Error",
            nav_links=nav_links(),
        ),
        500,
    )


if __name__ == "__main__":
    app.run(
        host=os.environ.get("IP"),
        port=int(os.environ.get("PORT")),
        debug=(os.environ.get("ENV_DEBUG")),
    )
