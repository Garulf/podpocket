from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "podpocket:migrations")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")
