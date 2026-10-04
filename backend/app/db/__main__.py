"""Initialize the configured local database without starting the API."""

from app.db import Database


def main() -> None:
    database = Database()
    applied = database.initialize()
    print(f"Database ready: {database.path} ({applied} migrations applied)")


if __name__ == "__main__":
    main()
