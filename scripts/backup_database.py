"""Create a PostgreSQL custom-format backup without printing records or credentials."""
import subprocess
from pathlib import Path
from datetime import datetime,timezone

root=Path(__file__).resolve().parents[1]
destination=root/'backups'/('postgres-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.dump')
destination.parent.mkdir(exist_ok=True)
with destination.open('wb') as output:
    subprocess.run(['docker','compose','exec','-T','db','pg_dump','-U','fairflow','-d','fairflow','-Fc'],cwd=root,stdout=output,check=True)
print('Backup created:',destination.name,'bytes:',destination.stat().st_size)
