"""Restore a local backup into an isolated QA database, check it, then remove that database."""
import argparse
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(
        ['docker', 'compose', 'exec', '-T', 'db', *args],
        cwd=ROOT, check=True, **kwargs,
    )


def verify(backup):
    # Only this newly created, random database can be removed by this script.
    database = 'fairflow_restore_qa_' + uuid.uuid4().hex
    created = False
    with backup.open('rb') as source:
        try:
            docker('createdb', '-U', 'fairflow', database)
            created = True
            docker('pg_restore', '-U', 'fairflow', '--exit-on-error',
                   '--no-owner', '-d', database, stdin=source)
            query = """
                SELECT json_build_object(
                    'users', (SELECT count(*) FROM users),
                    'projects', (SELECT count(*) FROM platform_projects),
                    'agreements', (SELECT count(*) FROM agreement_versions),
                    'ledger_entries', (SELECT count(*) FROM ledger_entries),
                    'unbalanced_projects', (SELECT count(*) FROM (
                        SELECT project_id, currency FROM ledger_entries
                        GROUP BY project_id, currency HAVING sum(amount_cents) <> 0
                    ) AS balances),
                    'migration', (SELECT version_num FROM alembic_version)
                )
            """
            result = docker('psql', '-U', 'fairflow', '-d', database,
                            '-v', 'ON_ERROR_STOP=1', '-Atc', query,
                            capture_output=True, text=True)
            counts = json.loads(result.stdout)
            if counts['unbalanced_projects']:
                raise RuntimeError('Restored ledger has unbalanced project/currency groups.')
            print(json.dumps({'result': 'passed', 'backup': backup.name, **counts}))
        finally:
            if created:
                docker('dropdb', '-U', 'fairflow', database)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', nargs='?', type=Path)
    args = parser.parse_args()
    candidates = sorted((ROOT / 'backups').glob('postgres-*.dump'))
    backup = args.backup or (candidates[-1] if candidates else None)
    if backup is None:
        parser.error('Create a backup with python -m scripts.backup_database first.')
    verify(backup)
