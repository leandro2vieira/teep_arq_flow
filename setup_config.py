import json
import sqlite3
import logging
import signal
import sys
import os
from pathlib import Path
from ftplib import FTP, FTP_TLS
from typing import Dict, Any, Optional, List
import pika
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ConfigManager:
    """Gerenciador de configurações usando SQLite"""

    def __init__(self, db_path: str = '/etc/teep_arq_flow/config.db'):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Inicializa o banco de dados"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Tabela de configurações gerais
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS config
                       (
                           key
                           TEXT
                           PRIMARY
                           KEY,
                           value
                           TEXT,
                           updated_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        # Tabela de histórico de operações
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS operation_history
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           operation_type
                           TEXT,
                           status
                           TEXT,
                           details
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS peripheral
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           name
                           TEXT
                           NOT
                           NULL
                           UNIQUE,
                           interface
                           TEXT,
                           json_connection_params
                           TEXT,
                           device
                           TEXT,
                           model
                           TEXT,
                           json_channel_to_virtual_index
                           TEXT,
                           last_update
                           TIMESTAMP,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        # Tabela de automações (mantém-se como está)
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS automation
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           name
                           TEXT
                           NOT
                           NULL,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        # --- Nova definição da tabela de triggers ---
        # Agora: id, automation_id, description, queue_name, created_at
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS trigger
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           automation_id
                           INTEGER
                           NOT
                           NULL,
                           description
                           TEXT,
                           queue_name
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP,
                           FOREIGN KEY (automation_id) REFERENCES automation(id) ON DELETE CASCADE
                       )
                       ''')

        # --- Nova definição da tabela de actions ---
        # Agora: id, automation_id, description, action_config (JSON/text), created_at
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS action
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           automation_id
                           INTEGER
                           NOT
                           NULL,
                           description
                           TEXT,
                           action_config
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP,
                           FOREIGN KEY (automation_id) REFERENCES automation(id) ON DELETE CASCADE
                       )
                       ''')

        # --- Migration checks ---, handle existing legacy schemas for trigger and action
        def _get_columns(table_name: str):
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            return [r[1] for r in cursor.fetchall()]

        # Migrate trigger table if it has legacy columns
        try:
            trig_cols = _get_columns('trigger')
            expected_trig = {'id', 'automation_id', 'description', 'queue_name', 'created_at'}
            if set(trig_cols) != expected_trig:
                # perform migration: rename old, create new, copy data best-effort
                cursor.execute('ALTER TABLE trigger RENAME TO trigger_old')
                cursor.execute('''
                    CREATE TABLE trigger
                    (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        automation_id INTEGER NOT NULL,
                        description TEXT,
                        queue_name TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (automation_id) REFERENCES automation(id) ON DELETE CASCADE
                    )
                ''')
                # Best-effort copy: map existing trigger_type -> description, trigger_config -> queue_name (if text)
                cursor.execute("SELECT id, automation_id, trigger_type, trigger_config, created_at FROM trigger_old")
                rows = cursor.fetchall()
                for r in rows:
                    _id, _aid, _t_type, _t_cfg, _created = r
                    desc = _t_type if _t_type is not None else ''
                    qname = None
                    if isinstance(_t_cfg, str):
                        qname = _t_cfg
                    # insert preserving id and created_at when possible
                    cursor.execute('INSERT INTO trigger (id, automation_id, description, queue_name, created_at) VALUES (?,?,?,?,?)',
                                   (_id, _aid, desc, qname, _created))
                cursor.execute('DROP TABLE trigger_old')
        except Exception:
            # if trigger_old doesn't exist or other issues, ignore and continue
            pass

        # Migrate action table if it has legacy columns
        try:
            act_cols = _get_columns('action')
            expected_act = {'id', 'automation_id', 'description', 'action_config', 'created_at'}
            if set(act_cols) != expected_act:
                cursor.execute('ALTER TABLE action RENAME TO action_old')
                cursor.execute('''
                    CREATE TABLE action
                    (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        automation_id INTEGER NOT NULL,
                        description TEXT,
                        action_config TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (automation_id) REFERENCES automation(id) ON DELETE CASCADE
                    )
                ''')
                cursor.execute("SELECT id, automation_id, action_type, action_config, created_at FROM action_old")
                rows = cursor.fetchall()
                for r in rows:
                    _id, _aid, _a_type, _a_cfg, _created = r
                    desc = _a_type if _a_type is not None else ''
                    cfg = _a_cfg
                    cursor.execute('INSERT INTO action (id, automation_id, description, action_config, created_at) VALUES (?,?,?,?,?)',
                                   (_id, _aid, desc, cfg, _created))
                cursor.execute('DROP TABLE action_old')
        except Exception:
            pass

        conn.commit()
        conn.close()
        logger.info("Banco de dados inicializado")

    def get(self, key: str, default: Any = None) -> Any:
        """Obtém uma configuração"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
        result = cursor.fetchone()
        conn.close()

        if result:
            try:
                return json.loads(result[0])
            except json.JSONDecodeError:
                return result[0]
        return default

    def set(self, key: str, value: Any):
        """Define uma configuração"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        json_value = json.dumps(value) if not isinstance(value, str) else value

        cursor.execute('''
            INSERT OR REPLACE INTO config (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, json_value))

        conn.commit()
        conn.close()
        logger.info(f"Configuração '{key}' atualizada")

    def log_operation(self, op_type: str, status: str, details: str = ""):
        """Registra uma operação no histórico"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
                       INSERT INTO operation_history (operation_type, status, details)
                       VALUES (?, ?, ?)
                       ''', (op_type, status, details))
        conn.commit()
        conn.close()

    def get_operations(self, limit: int = 50) -> List[Dict]:
        """Obtém histórico de operações"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
                       SELECT *
                       FROM operation_history
                       ORDER BY created_at DESC LIMIT ?
                       ''', (limit,))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def delete_operation(self, op_id: int):
        """Deleta uma operação do histórico"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM operation_history WHERE id = ?', (op_id,))
        conn.commit()
        conn.close()

    def get_peripherals(self) -> List[Dict]:
        """Return all peripherals"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM peripheral ORDER BY name')
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        # parse JSON fields
        for r in results:
            for fld in ("json_connection_params", "json_channel_to_virtual_index"):
                if r.get(fld):
                    try:
                        r[fld] = json.loads(r[fld])
                    except Exception:
                        pass
        return results

    def get_peripheral(self, peripheral_id: int) -> Optional[Dict]:
        """Return a single peripheral by id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM peripheral WHERE id = ?', (peripheral_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        result = dict(row)
        for fld in ("json_connection_params", "json_channel_to_virtual_index"):
            if result.get(fld):
                try:
                    result[fld] = json.loads(result[fld])
                except Exception:
                    pass
        return result

    def create_peripheral(self,
                          name: str,
                          interface: Optional[str] = None,
                          json_connection_params: Any = None,
                          device: Optional[str] = None,
                          model: Optional[str] = None,
                          json_channel_to_virtual_index: Any = None) -> int:
        """Create a new peripheral and return its id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        j_conn = json.dumps(json_connection_params) if json_connection_params is not None and not isinstance(
            json_connection_params, str) else json_connection_params
        j_chan = json.dumps(
            json_channel_to_virtual_index) if json_channel_to_virtual_index is not None and not isinstance(
            json_channel_to_virtual_index, str) else json_channel_to_virtual_index
        cursor.execute('''
                       INSERT INTO peripheral
                       (name, interface, json_connection_params, device, model, json_channel_to_virtual_index,
                        last_update)
                       VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ''', (name, interface, j_conn, device, model, j_chan))
        pid = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Peripheral '{name}' created (id={pid})")
        return pid

    def update_peripheral(self,
                          peripheral_id: int,
                          name: Optional[str] = None,
                          interface: Optional[str] = None,
                          json_connection_params: Any = None,
                          device: Optional[str] = None,
                          model: Optional[str] = None,
                          json_channel_to_virtual_index: Any = None):
        """Update an existing peripheral; sets last_update to CURRENT_TIMESTAMP"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # build dynamic update
        fields = []
        params = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if interface is not None:
            fields.append("interface = ?")
            params.append(interface)
        if json_connection_params is not None:
            j_conn = json.dumps(json_connection_params) if not isinstance(json_connection_params,
                                                                          str) else json_connection_params
            fields.append("json_connection_params = ?")
            params.append(j_conn)
        if device is not None:
            fields.append("device = ?")
            params.append(device)
        if model is not None:
            fields.append("model = ?")
            params.append(model)
        if json_channel_to_virtual_index is not None:
            j_chan = json.dumps(json_channel_to_virtual_index) if not isinstance(json_channel_to_virtual_index,
                                                                                 str) else json_channel_to_virtual_index
            fields.append("json_channel_to_virtual_index = ?")
            params.append(j_chan)

        if fields:
            # always update last_update
            fields.append("last_update = CURRENT_TIMESTAMP")
            sql = f"UPDATE peripheral SET {', '.join(fields)} WHERE id = ?"
            params.append(peripheral_id)
            cursor.execute(sql, tuple(params))
            conn.commit()
        conn.close()
        logger.info(f"Peripheral id={peripheral_id} updated")

    def delete_peripheral(self, peripheral_id: int):
        """Delete a peripheral"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM peripheral WHERE id = ?', (peripheral_id,))
        conn.commit()
        conn.close()
        logger.info(f"Peripheral id={peripheral_id} deleted")

    # ========== CRUD para Automation ==========

    def get_automations(self) -> List[Dict]:
        """Return all automations"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM automation ORDER BY name')
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_automation(self, automation_id: int) -> Optional[Dict]:
        """Return a single automation by id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM automation WHERE id = ?', (automation_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def create_automation(self, name: str) -> int:
        """Create a new automation and return its id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
                       INSERT INTO automation (name)
                       VALUES (?)
                       ''', (name,))
        aid = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Automation '{name}' created (id={aid})")
        return aid

    def update_automation(self, automation_id: int, name: str):
        """Update an existing automation"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE automation SET name = ? WHERE id = ?', (name, automation_id))
        conn.commit()
        conn.close()
        logger.info(f"Automation id={automation_id} updated")

    def delete_automation(self, automation_id: int):
        """Delete an automation (cascades to triggers and actions)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM automation WHERE id = ?', (automation_id,))
        conn.commit()
        conn.close()
        logger.info(f"Automation id={automation_id} deleted")

    # ========== CRUD para Trigger ==========

    def get_triggers(self, automation_id: Optional[int] = None) -> List[Dict]:
        """Return all triggers, optionally filtered by automation_id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if automation_id is not None:
            cursor.execute('SELECT id, automation_id, description, queue_name, created_at FROM trigger WHERE automation_id = ? ORDER BY id', (automation_id,))
        else:
            cursor.execute('SELECT id, automation_id, description, queue_name, created_at FROM trigger ORDER BY automation_id, id')
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_trigger(self, trigger_id: int) -> Optional[Dict]:
        """Return a single trigger by id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT id, automation_id, description, queue_name, created_at FROM trigger WHERE id = ?', (trigger_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def create_trigger(self,
                      automation_id: int,
                      description: str = '',
                      queue_name: Optional[str] = None) -> int:
        """Create a new trigger and return its id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
                       INSERT INTO trigger (automation_id, description, queue_name)
                       VALUES (?, ?, ?)
                       ''', (automation_id, description, queue_name))
        tid = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Trigger created (id={tid}) for automation_id={automation_id}")
        return tid

    def update_trigger(self,
                      trigger_id: int,
                      description: Optional[str] = None,
                      queue_name: Optional[str] = None):
        """Update an existing trigger"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        fields = []
        params = []
        if description is not None:
            fields.append("description = ?")
            params.append(description)
        if queue_name is not None:
            fields.append("queue_name = ?")
            params.append(queue_name)

        if fields:
            sql = f"UPDATE trigger SET {', '.join(fields)} WHERE id = ?"
            params.append(trigger_id)
            cursor.execute(sql, tuple(params))
            conn.commit()
        conn.close()
        logger.info(f"Trigger id={trigger_id} updated")

    def delete_trigger(self, trigger_id: int):
        """Delete a trigger"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM trigger WHERE id = ?', (trigger_id,))
        conn.commit()
        conn.close()
        logger.info(f"Trigger id={trigger_id} deleted")

    # ========== CRUD para Action ==========

    def get_actions(self, automation_id: Optional[int] = None) -> List[Dict]:
        """Return all actions, optionally filtered by automation_id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if automation_id is not None:
            cursor.execute('SELECT id, automation_id, description, action_config, created_at FROM action WHERE automation_id = ? ORDER BY id', (automation_id,))
        else:
            cursor.execute('SELECT id, automation_id, description, action_config, created_at FROM action ORDER BY automation_id, id')
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        # parse JSON fields in action_config
        for r in results:
            if r.get('action_config'):
                try:
                    r['action_config'] = json.loads(r['action_config'])
                except Exception:
                    pass
        return results

    def get_action(self, action_id: int) -> Optional[Dict]:
        """Return a single action by id"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT id, automation_id, description, action_config, created_at FROM action WHERE id = ?', (action_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        result = dict(row)
        if result.get('action_config'):
            try:
                result['action_config'] = json.loads(result['action_config'])
            except Exception:
                pass
        return result

    def create_action(self,
                     automation_id: int,
                     description: str = '',
                     action_config: Any = None) -> int:
        """Create a new action and return its id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        a_config = json.dumps(action_config) if action_config is not None and not isinstance(action_config, str) else action_config
        cursor.execute('''
                       INSERT INTO action (automation_id, description, action_config)
                       VALUES (?, ?, ?)
                       ''', (automation_id, description, a_config))
        aid = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Action created (id={aid}) for automation_id={automation_id}")
        return aid

    def update_action(self,
                     action_id: int,
                     description: Optional[str] = None,
                     action_config: Any = None):
        """Update an existing action"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        fields = []
        params = []
        if description is not None:
            fields.append("description = ?")
            params.append(description)
        if action_config is not None:
            a_config = json.dumps(action_config) if not isinstance(action_config, str) else action_config
            fields.append("action_config = ?")
            params.append(a_config)

        if fields:
            sql = f"UPDATE action SET {', '.join(fields)} WHERE id = ?"
            params.append(action_id)
            cursor.execute(sql, tuple(params))
            conn.commit()
        conn.close()
        logger.info(f"Action id={action_id} updated")

    def delete_action(self, action_id: int):
        """Delete an action"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM action WHERE id = ?', (action_id,))
        conn.commit()
        conn.close()
        logger.info(f"Action id={action_id} deleted")
