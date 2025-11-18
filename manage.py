from flask.cli import FlaskGroup

from app import create_app
from app.extensions import db

app = create_app()
cli = FlaskGroup(app)


@cli.command("create_db")
def create_db():
    db.create_all()
    print("Database tables created")


if __name__ == "__main__":
    cli()
