# python
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING
import pika
import time
import helpers.helper as helper
from ftp_manager import FTPManager

if TYPE_CHECKING:
    from setup_config import ConfigManager  # only for type checking to avoid circular imports

logger = logging.getLogger(__name__)

class RabbitMQService:
    """Serviço de integração com RabbitMQ"""

    def __init__(self, config_manager: 'ConfigManager'):
        self.config_manager = config_manager
        self.connection = None
        self.channel = None
        self.running = False
        self.consumed_queues = set()
        self.retry_delay = 5

        self.rabbitmq_config = config_manager.get('rabbitmq', {
            'host': 'localhost',
            'port': 5672,
            'user': 'guest',
            'password': 'guest'
        })

        self.ftp_config = config_manager.get('ftp', {
            'host': 'localhost',
            'port': 21,
            'user': 'anonymous',
            'password': '',
            'use_tls': False
        })

    def connect(self) -> bool:
        """Conecta ao RabbitMQ"""
        try:
            credentials = pika.PlainCredentials(
                self.rabbitmq_config['user'],
                self.rabbitmq_config['password']
            )

            parameters = pika.ConnectionParameters(
                host=self.rabbitmq_config['host'],
                port=self.rabbitmq_config['port'],
                credentials=credentials,
                heartbeat=600
            )

            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()

            logger.info("Conectado ao RabbitMQ")
            return True
        except Exception as e:
            logger.error(f"Erro ao conectar RabbitMQ: {e}")
            return False

    def declare_queues(self):
        """Declara as filas necessárias"""
        try:
            peripherals = self.config_manager.get_peripherals()

            # allow this consumer to receive only one unacked message at a time
            # set QoS once for the channel
            self.channel.basic_qos(prefetch_count=1)

            for peripheral in peripherals:
                json_channel_to_virtual_index = peripheral.get('json_channel_to_virtual_index', {})
                _index = helper.has_key_with_substring(json_channel_to_virtual_index, 'index')
                print(f"_index: {_index}")

                recv_queue_name = f"recv_queue_index_{_index}"
                send_queue_name = f"send_queue_index_{_index}"
                self.channel.queue_declare(queue=recv_queue_name, durable=False)
                self.channel.queue_declare(queue=send_queue_name, durable=False)

                if recv_queue_name not in self.consumed_queues:
                    self.channel.basic_consume(
                        queue=recv_queue_name,
                        on_message_callback=self.process_message
                    )
                    self.consumed_queues.add(recv_queue_name)
                    logger.info(f"consuming queue: {recv_queue_name}")

                logger.info(f"Filas declaradas: {recv_queue_name} - {send_queue_name}")
        except Exception as e:
            logger.error(f"Erro ao declarar filas: {e}")

    def send_message(self, message: Dict[str, Any]):
        """Envia uma mensagem para a fila de saída"""
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=self.rabbitmq_config['queue_out'],
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Mensagem enviada: {message.get('type', 'unknown')}")
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")

    def process_message(self, ch, method, properties, body):
        """Processa mensagens recebidas"""
        try:
            message = json.loads(body)
            command = message.get('command')

            logger.info(f"Mensagem recebida: {command}")

            response = {
                'command': command,
                'status': 'success',
                'timestamp': datetime.now().isoformat()
            }

            if command == 'upload_file':
                result = self._handle_upload_file(message)
                response['result'] = result
            elif command == 'download_file':
                result = self._handle_download_file(message)
                response['result'] = result
            elif command == 'upload_directory':
                result = self._handle_upload_directory(message)
                response['result'] = result
            elif command == 'download_directory':
                result = self._handle_download_directory(message)
                response['result'] = result
            elif command == 'update_config':
                result = self._handle_update_config(message)
                response['result'] = result
            else:
                response['status'] = 'error'
                response['error'] = f"Comando desconhecido: {command}"

            self.send_message(response)
            ch.basic_ack(delivery_tag=method.delivery_tag)

            self.config_manager.log_operation(
                command,
                response['status'],
                json.dumps(response)
            )

        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def _handle_upload_file(self, message: Dict) -> Dict:
        """Processa upload de arquivo"""
        ftp = FTPManager(self.ftp_config)
        if not ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            local_path = message.get('local_path')
            remote_path = message.get('remote_path')
            success = ftp.upload_file(local_path, remote_path)
            return {'success': success}
        finally:
            ftp.disconnect()

    def _handle_download_file(self, message: Dict) -> Dict:
        """Processa download de arquivo"""
        ftp = FTPManager(self.ftp_config)
        if not ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            remote_path = message.get('remote_path')
            local_path = message.get('local_path')
            success = ftp.download_file(remote_path, local_path)
            return {'success': success}
        finally:
            ftp.disconnect()

    def _handle_upload_directory(self, message: Dict) -> Dict:
        """Processa upload de diretório"""
        ftp = FTPManager(self.ftp_config)
        if not ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            local_dir = message.get('local_dir')
            remote_dir = message.get('remote_dir')
            success = ftp.upload_directory(local_dir, remote_dir)
            return {'success': success}
        finally:
            ftp.disconnect()

    def _handle_download_directory(self, message: Dict) -> Dict:
        """Processa download de diretório"""
        ftp = FTPManager(self.ftp_config)
        if not ftp.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}

        try:
            remote_dir = message.get('remote_dir')
            local_dir = message.get('local_dir')
            success = ftp.download_directory(remote_dir, local_dir)
            return {'success': success}
        finally:
            ftp.disconnect()

    def _handle_update_config(self, message: Dict) -> Dict:
        """Atualiza configurações"""
        try:
            config_type = message.get('config_type')
            config_data = message.get('config_data')

            self.config_manager.set(config_type, config_data)

            if config_type == 'ftp':
                self.ftp_config = config_data
            elif config_type == 'rabbitmq':
                self.rabbitmq_config = config_data

            return {'success': True, 'message': 'Configuração atualizada'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def start(self):
        """Inicia o serviço"""
        self.running = True

        while self.running:
            if not self.connect():
                logger.error(f"Falha ao iniciar serviço RabbitMQ, tentar novamente em {self.retry_delay} segundos...")
                time.sleep(self.retry_delay)
                continue

            try:
                self.declare_queues()

                logger.info("Serviço RabbitMQ iniciado. Aguardando mensagens...")

                self.channel.start_consuming()
            except KeyboardInterrupt:
                self.stop()
            except Exception as e:
                logger.error(f"Erro no serviço RabbitMQ: {e}")
                self.reconnect()
            finally:
                if not self.running:
                    break

    def reconnect(self) -> bool:
        """Fecha conecoes existentes e tenta reconectar"""
        logger.info(f"Tentando reconectar em {self.retry_delay} segundos...")

        try:
            if self.channel:
                try:
                    self.channel.stop_consuming()
                except Exception:
                    pass
                self.channel = None

            if self.connection:
                try:
                    self.connection.close()
                except Exception:
                    pass
                self.connection = None
        except Exception:
            pass

        while self.running:
            if self.connect():
                logger.info("Conexao com o RabbitMQ restabelecida")
                return True
            logger.error(f"Falha ao reconectar. Tentando novamente em {self.retry_delay} segundos...")
            time.sleep(self.retry_delay)

        logger.info("reconexao abortada porque o servico foi parado")
        return False

    def stop(self):
        """Para o serviço"""
        self.running = False
        if self.channel:
            self.channel.stop_consuming()
        if self.connection:
            self.connection.close()
        logger.info("Serviço RabbitMQ parado")