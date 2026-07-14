"""Shared repository connection ownership contract."""

import sqlite3


class SQLiteRepository:
    """A non-owning view over ``DatabaseManager``'s live connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
