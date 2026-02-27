import sqlite3
import os

class DatabaseHandler:
    def __init__(self, db_name='bot_templates.db'):
        import sys
        # Save DB in the same path as the executable if frozen, otherwise parent of this script
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
        self.db_path = os.path.join(base_dir, db_name)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL,
                model TEXT NOT NULL,
                firmware TEXT,
                hardware TEXT,
                actions_script TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

    def save_template(self, vendor, model, firmware, hardware, script):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO templates (vendor, model, firmware, hardware, actions_script)
            VALUES (?, ?, ?, ?, ?)
        ''', (vendor, model, firmware, hardware, script))
        conn.commit()
        conn.commit()
        conn.close()

    def update_template(self, template_id, vendor, model, firmware, hardware, script):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE templates 
            SET vendor = ?, model = ?, firmware = ?, hardware = ?, actions_script = ?
            WHERE id = ?
        ''', (vendor, model, firmware, hardware, script, template_id))
        conn.commit()
        conn.close()

    def delete_template(self, template_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM templates WHERE id = ?', (template_id,))
        conn.commit()
        conn.close()

    def get_all_templates(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, vendor, model, firmware, hardware, actions_script FROM templates')
        rows = cursor.fetchall()
        conn.close()
        return rows
        
    def get_template(self, template_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM templates WHERE id = ?', (template_id,))
        row = cursor.fetchone()
        conn.close()
        return row
