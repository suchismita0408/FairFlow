from dotenv import load_dotenv
load_dotenv()
from alembic import context
from app.db import engine, metadata
with engine().connect() as connection:
    context.configure(connection=connection,target_metadata=metadata,compare_type=True)
    sqlite=connection.dialect.name=='sqlite'
    # Batch ALTER recreates SQLite tables. Keep dependent legacy rows intact,
    # then check every foreign key before returning the connection to the pool.
    if sqlite:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF');connection.commit()
    try:
        with context.begin_transaction():context.run_migrations()
        if sqlite and connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Migration introduced a foreign-key violation.')
    finally:
        if sqlite:
            connection.commit();connection.exec_driver_sql('PRAGMA foreign_keys=ON');connection.commit()
