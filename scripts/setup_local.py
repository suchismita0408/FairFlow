"""Generate missing local configuration without overwriting existing secrets or projects."""
import secrets
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import dotenv_values

root=Path(__file__).resolve().parents[1]
database=root/'.env.database'
if not database.exists():
    database.write_text('POSTGRES_DB=fairflow\nPOSTGRES_USER=fairflow\nPOSTGRES_PASSWORD='+secrets.token_hex(32)+'\n')
config=dotenv_values(database)
env=root/'.env'
existing=dotenv_values(env) if env.exists() else {}
defaults={'FAIRFLOW_MODE':'demo','AI_MODE':'gemini' if existing.get('GEMINI_API_KEY') else 'demo','APP_BASE_URL':'http://127.0.0.1:8000','AUTH_ENABLED':'true','EMAIL_MODE':'local','APP_ENV':'development','TOKEN_ENCRYPTION_KEY':Fernet.generate_key().decode(),'DATABASE_URL':f"postgresql+psycopg://{config['POSTGRES_USER']}:{config['POSTGRES_PASSWORD']}@127.0.0.1:55432/{config['POSTGRES_DB']}"}
with env.open('a') as stream:
    for key,value in defaults.items():
        if key not in existing:stream.write('\n'+key+'='+value)
print('Missing local configuration added. Existing values preserved; no credentials printed.')
