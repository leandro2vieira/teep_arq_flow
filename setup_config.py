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