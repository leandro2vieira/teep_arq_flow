import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from ftp_manager import FTPManager
from scp_manager import SCPManager
from helpers.enums import ActionTable

logger = logging.getLogger(__name__)

class IO:

    def __init__(self, config: Dict[str, Any]):
        self.index = config.get('index', None)
        self.notification_type = config.get('notification_type', 4)
        self.local_path = config.get('local_path', './')

        print(f"{config} - IO initialized with index: {self.index}, notification_type: {self.notification_type}, local_path: {self.local_path}", flush=True)


# python
def _handle_list_local_directory(local_path: str) -> List[Dict[str, Any]]:
    try:
        entries = []
        with os.scandir(local_path) as it:
            for entry in it:
                info = entry.stat()
                entries.append({
                    'name': entry.name,
                    'is_dir': entry.is_dir(),
                    'size': info.st_size,
                    'modified': int(info.st_mtime)
                })
        return entries
    except Exception as e:
        logger.error(f"Erro ao listar diretório local: {e}")
        return []


class GenericFileTransfer:
    def __init__(self, config: Dict[str, Any], io_config: Dict[str, Any], send_message_callback, config_manager):
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 21)
        self.user = config.get('user', 'anonymous')
        self.password = config.get('password', '')
        self.passive = config.get('passive', True)
        self.timeout = config.get('timeout', 30)
        self.protocol = config.get('protocol', 'ftp')
        self.local_path = config.get('local_path', './')
        self.remote = None

        if self.protocol.lower() == 'scp':
            self.remote = SCPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout
            )
        else:
            self.remote = FTPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout
            )

        self.io: IO = IO(io_config.get('GENERIC_FILE_TRANSFER', {}))

        self.send_message = send_message_callback
        self.config_manager = config_manager

    # --- helpers ----------------------------------------------------------------

    def get_index(self) -> int:
        return self.io.index

    def _build_response(self, action, value=None):
        return {
            'action': action,
            'data': {
                'index': self.get_index(),
                'value': value if value is not None else '',
                'timestamp': int(datetime.now().timestamp())
            }
        }

    def _send(self, action, value=None):
        resp = self._build_response(action, value)
        self.send_message(resp, f"recv_queue_index_{str(self.get_index())}")
        return resp

    def _join_remote(self, base: str, part: str) -> str:
        base = (base or '').rstrip('/')
        part = (part or '').lstrip('/')
        if base == '':
            return f"/{part}" if part else '/'
        return f"{base}/{part}" if part else base

    # Wrapper that ensures FTP connection and disconnect
    def _with_ftp(self, func, *args, **kwargs):
        if not self.remote.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception("FTP operation failed: %s", e)
            return {'success': False, 'error': str(e)}
        finally:
            try:
                self.remote.disconnect()
            except Exception:
                pass

    # --- message processing -----------------------------------------------------

    def process_message(self, ch, method, properties, body):
        try:
            message = json.loads(body)
            action = message.get('action')

            logger.info(f"Mensagem recebida: {action}")

            response = self._build_response(action)

            if action == ActionTable.GET_SERVER_FILE_TREE.value:
                result = _handle_list_local_directory(self.local_path)
                response['action'] = ActionTable.SERVER_FILE_TREE.value
            elif action == ActionTable.GET_REMOTE_FILE_TREE.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_list_directory(remote_path)
                response['action'] = ActionTable.CLIENT_FILE_TREE.value
            elif action == ActionTable.STREAM_DIRECTORY.value:
                data = message.get('data', {})
                local_path = data.get('value', '')
                result = self._handle_upload_directory(local_path)
            elif action == ActionTable.STREAM_FILE.value:
                data = message.get('data', {})
                local_path = data.get('value', '')
                result = self._handle_upload_file(local_path)
            elif action == ActionTable.DOWNLOAD_FILE.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_download_file(remote_path)
            elif action == ActionTable.DOWNLOAD_DIRECTORY.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_download_directory(remote_path)
            elif action == ActionTable.DELETE_REMOTE_FILE.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_delete_remote_file(remote_path)
            elif action == ActionTable.DELETE_REMOTE_DIRECTORY.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_delete_remote_directory(remote_path)
            else:
                response['action'] = ActionTable.ERROR.value
                result = f"Comando desconhecido: {action}"

            response['data']['value'] = result

            value_for_log = response['data']['value']
            if not isinstance(value_for_log, (str, bytes, int, float, type(None))):
                try:
                    value_for_log = json.dumps(value_for_log, default=str)
                except Exception:
                    value_for_log = str(value_for_log)

            self.send_message(response, f"recv_queue_index_{str(self.get_index())}")
            ch.basic_ack(delivery_tag=method.delivery_tag)

            self.config_manager.log_operation(
                response['action'],
                value_for_log,
                json.dumps(response)
            )

        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    # --- handlers ---------------------------------------------------------------

    def _handle_upload_directory(self, local_path: str) -> Dict:
        def op():
            self._send(ActionTable.START_STREAM_FILE.value)
            local_dir = local_path
            remote_dir = self.io.local_path
            success = self.remote.upload_directory(local_dir, remote_dir)
            self._send(ActionTable.FINISH_STREAM_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_upload_file(self, local_path: str) -> Dict:
        def op():
            self._send(ActionTable.START_STREAM_FILE.value)
            local_file = local_path
            remote_file = self._join_remote(self.io.local_path, os.path.basename(local_path))
            success = self.remote.upload_file(local_file, remote_file)
            self._send(ActionTable.FINISH_STREAM_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_download_directory(self, remote_path) -> Dict:
        def op():
            self._send(ActionTable.START_DOWNLOAD_FILE.value)
            remote_dir = remote_path
            local_dir = self.local_path
            logger.info(f"Downloading directory from {remote_dir} to {local_dir}")
            success = self.remote.download_directory(remote_dir, local_dir)
            self._send(ActionTable.FINISH_DOWNLOAD_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_download_file(self, remote_path: str) -> Dict:
        def op():
            self._send(ActionTable.START_DOWNLOAD_FILE.value)
            remote_file = remote_path
            local_file = os.path.join(self.local_path, os.path.basename(remote_path))
            logger.info(f"Downloading file from {remote_file} to {local_file}")
            success = self.remote.download_file(remote_file, local_file)
            self._send(ActionTable.FINISH_DOWNLOAD_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_delete_remote_file(self, remote_path: str) -> Dict:
        def op():
            # use FTPManager.delete_file (refactored name)
            success = self.remote.delete_file(remote_path)
            if isinstance(success, bool):
                return {'success': success}
            return success

        return self._with_ftp(op)

    def _handle_delete_remote_directory(self, remote_path: str) -> Dict:
        def op():
            success = self.remote.delete_remote_path(remote_path)
            if isinstance(success, bool):
                return {'success': success}
            return success

        return self._with_ftp(op)

    def _handle_list_directory(self, remote_path: str) -> List:
        def op():
            # join configured base path and requested remote path cleanly
            full_remote = self._join_remote(self.io.local_path, remote_path)
            file_list = self.remote.list_remote(full_remote)
            return file_list

        return self._with_ftp(op)