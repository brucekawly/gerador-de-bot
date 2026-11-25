import os
import sqlite3
from contextlib import closing

# Colunas na ordem usada pelo resto do app:
# 0 id, 1 vendor, 2 model, 3 firmware, 4 hardware, 5 actions_script, 6 credentials, 7 defaults
_COLUMNS = "id, vendor, model, firmware, hardware, actions_script, COALESCE(credentials, ''), COALESCE(defaults, '')"


class DatabaseHandler:
    def __init__(self, db_name=None):
        """Banco na raiz do projeto, ou em $GERADOR_BOT_DB, ou no caminho informado."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(base_dir, db_name or os.environ.get("GERADOR_BOT_DB") or "bot_templates.db")
        self._init_db()

    def _connect(self):
        return closing(sqlite3.connect(self.db_path))

    def _init_db(self):
        with self._connect() as conn, conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vendor TEXT NOT NULL,
                    model TEXT NOT NULL,
                    firmware TEXT,
                    hardware TEXT,
                    actions_script TEXT NOT NULL,
                    credentials TEXT,
                    defaults TEXT
                )
            ''')
            cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)")}
            if "credentials" not in cols:  # migração de bancos da v1.2.0
                conn.execute("ALTER TABLE templates ADD COLUMN credentials TEXT")
            if "defaults" not in cols:   # valores padrão de variáveis (v1.3)
                conn.execute("ALTER TABLE templates ADD COLUMN defaults TEXT")
            # servidores dos clientes (acesso aos equipamentos pela rede deles). Senha fica no cofre do sistema.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    user TEXT NOT NULL,
                    auth TEXT NOT NULL DEFAULT 'key',
                    key_path TEXT
                )
            ''')

    # ------------------------------------------------------------------ servidores
    def get_servers(self):
        """[(id, name, host, port, user, auth, key_path)]"""
        with self._connect() as conn:
            return conn.execute("SELECT id, name, host, port, user, auth, COALESCE(key_path, '') "
                                "FROM servers ORDER BY name").fetchall()

    def get_server(self, server_id):
        with self._connect() as conn:
            return conn.execute("SELECT id, name, host, port, user, auth, COALESCE(key_path, '') "
                                "FROM servers WHERE id=?", (server_id,)).fetchone()

    def save_server(self, name, host, port, user, auth="key", key_path="", server_id=None):
        with self._connect() as conn, conn:
            if server_id:
                conn.execute("UPDATE servers SET name=?, host=?, port=?, user=?, auth=?, key_path=? WHERE id=?",
                             (name, host, int(port), user, auth, key_path, server_id))
                return server_id
            return conn.execute("INSERT INTO servers (name, host, port, user, auth, key_path) VALUES (?,?,?,?,?,?)",
                                (name, host, int(port), user, auth, key_path)).lastrowid

    def delete_server(self, server_id):
        with self._connect() as conn, conn:
            conn.execute("DELETE FROM servers WHERE id=?", (server_id,))

    def save_template(self, vendor, model, firmware, hardware, script, credentials="", defaults=""):
        with self._connect() as conn, conn:
            cur = conn.execute(
                'INSERT INTO templates (vendor, model, firmware, hardware, actions_script, credentials, defaults) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (vendor, model, firmware, hardware, script, credentials or "", defaults or ""))
            return cur.lastrowid

    def update_template(self, template_id, vendor, model, firmware, hardware, script, credentials=None,
                        defaults=None):
        with self._connect() as conn, conn:
            if defaults is not None:
                conn.execute('UPDATE templates SET defaults=? WHERE id=?', (defaults, template_id))
            if credentials is None:
                conn.execute('UPDATE templates SET vendor=?, model=?, firmware=?, hardware=?, actions_script=? '
                             'WHERE id=?', (vendor, model, firmware, hardware, script, template_id))
            else:
                conn.execute('UPDATE templates SET vendor=?, model=?, firmware=?, hardware=?, actions_script=?, '
                             'credentials=? WHERE id=?',
                             (vendor, model, firmware, hardware, script, credentials, template_id))

    def update_credentials(self, template_id, credentials):
        with self._connect() as conn, conn:
            conn.execute('UPDATE templates SET credentials=? WHERE id=?', (credentials or "", template_id))

    def delete_template(self, template_id):
        with self._connect() as conn, conn:
            conn.execute('DELETE FROM templates WHERE id = ?', (template_id,))

    def get_all_templates(self):
        with self._connect() as conn:
            return conn.execute(f'SELECT {_COLUMNS} FROM templates ORDER BY vendor, model').fetchall()

    def get_template(self, template_id):
        with self._connect() as conn:
            return conn.execute(f'SELECT {_COLUMNS} FROM templates WHERE id = ?', (template_id,)).fetchone()
