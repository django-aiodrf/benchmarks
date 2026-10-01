"""Route the MongoDB models to MongoDB and the others to PostgreSQL."""


class MongoRouter:
    def db_for_read(self, model, **hints):
        return "mongodb" if model._meta.app_label == "mongoapp" else "default"

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, **hints):
        return (db == "mongodb") == (app_label == "mongoapp")
