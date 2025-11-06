from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING
import pika
import time
import os
from peripheral.generic_file_transfer import GenericFileTransfer

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
        peripherals = self.config_manager.get_peripherals()

        # allow this consumer to receive only one unacked message at a time
        # set QoS once for the channel
        self.channel.basic_qos(prefetch_count=1)

        for peripheral in peripherals:

            json_channel_to_virtual_index = peripheral.get('json_channel_to_virtual_index', {})
            json_connection_params = peripheral.get('json_connection_params', {})

            logger.info(f"Peripheral params: {json_connection_params}")
            logger.info(f"Virtual Indexes: {json_channel_to_virtual_index}")

            _peripheral = GenericFileTransfer(json_connection_params,
                                              json_channel_to_virtual_index,
                                              self.send_message,
                                              self.config_manager)

            _index = _peripheral.get_index()
            if _index is None:
                logger.error(f"Peripheral doesn't have an index...")
                raise ValueError("Peripheral index is required for queue declaration")

            recv_queue_name = f"recv_queue_index_{_index}"
            send_queue_name = f"send_queue_index_{_index}"
            self.channel.queue_declare(queue=recv_queue_name, durable=False)
            self.channel.queue_declare(queue=send_queue_name, durable=False)

            if recv_queue_name not in self.consumed_queues:
                self.channel.basic_consume(
                    queue=recv_queue_name,
                    on_message_callback=_peripheral.process_message
                )
                self.consumed_queues.add(recv_queue_name)
                logger.info(f"consuming queue: {recv_queue_name}")

            logger.info(f"Filas declaradas: {recv_queue_name} - {send_queue_name}")

    def send_message(self, message: Dict[str, Any], routing_key: str = None):
        """Envia uma mensagem para a fila de saída"""
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Mensagem enviada: {message.get('action', 'unknown')}")
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")

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
            except ValueError as ve:
                logger.error(f"Erro de configuração: {ve}")
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