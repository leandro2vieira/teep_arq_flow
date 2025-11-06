from helpers.enums import ActionTable
from ftp_manager import FTPManager, get_file_tree
from typing import Dict, Any
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)

class IO:

    def __init__(self, config: Dict[str, Any]):
        self.index = config.get('index', None)
        self.notification_type = config.get('notification_type', 4)
        self.local_path = config.get('local_path', './')

        print(f"{config} - IO initialized with index: {self.index}, notification_type: {self.notification_type}, local_path: {self.local_path}", flush=True)


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

        self.ftp = FTPManager(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password
        )

        self.io: IO = IO(io_config.get('GENERIC_FILE_TRANSFER', {}))

        self.send_message = send_message_callback
        self.config_manager = config_manager

    def get_index(self) -> int:
        """Retorna o índice do IO or None"""
        return self.io.index

    def process_message(self, ch, method, properties, body):
        """Processa mensagens recebidas"""
        try:
            message = json.loads(body)
            action = message.get('action')

            logger.info(f"Mensagem recebida: {action}")

            response = {
                'action': action,
                'data': {
                    'index': self.get_index(),
                    'value': '',
                    'timestamp': int(datetime.now().timestamp())
                }
            }

            if action == ActionTable.GET_SERVER_FILE_TREE.value:
                result = get_file_tree(self.local_path, include_hidden=False, max_depth=1)
                logger.info(f"File tree: {result}")
                response['action'] = ActionTable.SERVER_FILE_TREE.value
            elif action == ActionTable.GET_CLIENT_FILE_TREE.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_list_directory(remote_path)
                response['action'] = ActionTable.CLIENT_FILE_TREE.value
            elif action == ActionTable.STREAM_FILE.value:
                data = message.get('data', {})
                local_path = data.get('value', '')
                result = self._handle_upload_directory(local_path)
            elif action == ActionTable.DOWNLOAD_FILE.value:
                data = message.get('data', {})
                remote_path = data.get('value', '')
                result = self._handle_download_directory(remote_path)
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

            self.send_message(response, f"send_queue_index_{str(self.get_index())}")
            ch.basic_ack(delivery_tag=method.delivery_tag)

            self.config_manager.log_operation(
                response['action'],
                value_for_log,
                json.dumps(response)
            )

        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def _handle_upload_file(self, message: Dict) -> Dict:
        """Processa upload de arquivo"""
        if not self.ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            local_path = message.get('local_path')
            remote_path = message.get('remote_path')
            success = self.ftp.upload_file(local_path, remote_path)
            return success
        finally:
           self.ftp.disconnect()

    def _handle_download_file(self, message: Dict) -> Dict:
        """Processa download de arquivo"""
        if not self.ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            remote_path = message.get('remote_path')
            local_path = message.get('local_path')
            success = self.ftp.download_file(remote_path, local_path)
            return success
        finally:
            self.ftp.disconnect()

    def _handle_upload_directory(self, local_path: str) -> Dict:
        """Processa upload de diretório"""
        if not self.ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        response = {
            'action': ActionTable.START_STREAM_FILE.value,
            'data': {
                'index': self.get_index(),
                'value': '',
                'timestamp': int(datetime.now().timestamp())
            }
        }

        self.send_message(response, f"send_queue_index_{str(self.get_index())}")

        try:
            local_dir = local_path
            remote_dir = self.io.local_path
            success = self.ftp.upload_directory(local_dir, remote_dir)
            response['action'] = ActionTable.FINISH_STREAM_FILE.value
            self.send_message(response, f"send_queue_index_{str(self.get_index())}")
            return success
        except Exception as e:
            response['action'] = ActionTable.ERROR.value
            response['data']['value'] = str(e)
            self.send_message(response, f"send_queue_index_{str(self.get_index())}")
            return {'success': False, 'error': str(e)}
        finally:
            self.ftp.disconnect()

    def _handle_download_directory(self, remote_path) -> Dict:
        """Processa download de diretório"""
        if not self.ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        response = {
            'action': ActionTable.START_DOWNLOAD_FILE.value,
            'data': {
                'index': self.get_index(),
                'value': '',
                'timestamp': int(datetime.now().timestamp())
            }
        }
        self.send_message(response, f"send_queue_index_{str(self.get_index())}")

        try:
            remote_dir = remote_path
            local_dir = self.local_path
            logger.info(f"Downloading directory from {remote_dir} to {local_dir}")
            success = self.ftp.download_directory(remote_dir, local_dir)
            response['action'] = ActionTable.FINISH_DOWNLOAD_FILE.value
            self.send_message(response, f"send_queue_index_{str(self.get_index())}")
            return success
        except Exception as e:
            response['action'] = ActionTable.ERROR_DOWNLOAD_FILE.value
            response['data']['value'] = str(e)
            self.send_message(response, f"send_queue_index_{str(self.get_index())}")
            return {'success': False, 'error': str(e)}
        finally:
            self.ftp.disconnect()

    def _handle_list_directory(self, remote_path: str) -> Dict:
        """Lista arquivos em um diretório remoto"""
        if not self.ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            file_list = self.ftp.list_remote(f'{self.io.local_path}{remote_path}')
            return file_list
        finally:
            self.ftp.disconnect()