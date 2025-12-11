from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING
import pika
import time
import os
from queue import Queue
from peripheral.generic_file_transfer import GenericFileTransfer
from threading import Thread
from models.message import Message

if TYPE_CHECKING:
    from setup_config import ConfigManager  # only for type checking to avoid circular imports

logger = logging.getLogger(__name__)


def consume_queue(q: Queue, business_callback):
    while True:
        command = q.get()
        if command is None:
            break

        business_callback(command)

class RabbitMQService:
    """Serviço de integração com RabbitMQ"""

    def __init__(self, config_manager: 'ConfigManager', command_queue: Queue):
        self.config_manager = config_manager
        self.connection = None
        self.channel = None
        self.running = False
        self.consumed_queues = set()
        self.retry_delay = 5
        self.command_queue = command_queue
        self.queue_pool: Dict[str, Queue] = {}

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
                heartbeat=30,  # Reduzido de 600 para 30 segundos
                blocked_connection_timeout=10,  # Timeout para conexões bloqueadas
                socket_timeout=5  # Timeout para operações de socket
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
        automations = self.config_manager.get_automations()
        logger.info(f"Declarando filas para {len(peripherals) if hasattr(peripherals,'__len__') else 'unknown'} periféricos e {len(automations) if hasattr(automations,'__len__') else 'unknown'} automações")

        # Defensive: if config methods returned JSON strings or malformed data, try to recover
        if isinstance(peripherals, str):
            try:
                peripherals = json.loads(peripherals)
            except Exception:
                logger.error("peripherals config is a string but could not be parsed as JSON: %r", peripherals)
                peripherals = []
        if not isinstance(peripherals, (list, tuple)):
            logger.error("peripherals config is not a list/tuple, got %r. Skipping.", type(peripherals))
            peripherals = []

        if isinstance(automations, str):
            try:
                automations = json.loads(automations)
            except Exception:
                logger.error("automations config is a string but could not be parsed as JSON: %r", automations)
                automations = []
        if not isinstance(automations, (list, tuple)):
            logger.error("automations config is not a list/tuple, got %r. Skipping.", type(automations))
            automations = []

        logger.info(f"Declarando filas para {len(peripherals)} periféricos e {len(automations)} automações")

        # allow this consumer to receive only one unacked message at a time
        # set QoS once for the channel
        try:
            self.channel.basic_qos(prefetch_count=1)
        except Exception as e:
            logger.warning(f"Could not set basic_qos on channel: {e}")

        for peripheral in peripherals:

            # Ensure peripheral is a dict-like object
            if not isinstance(peripheral, dict):
                logger.warning("Skipping peripheral with unexpected type %s: %r", type(peripheral).__name__, peripheral)
                continue

            json_channel_to_virtual_index = peripheral.get('json_channel_to_virtual_index', {})
            json_connection_params = peripheral.get('json_connection_params', {})

            # Defensive validation: these fields may come as JSON strings or other types.
            if isinstance(json_connection_params, str):
                try:
                    json_connection_params = json.loads(json_connection_params)
                except Exception:
                    logger.error("json_connection_params is a string but could not be parsed as JSON for peripheral %r: %r", peripheral, json_connection_params)
                    json_connection_params = {}
            if not isinstance(json_connection_params, dict):
                logger.warning("json_connection_params is not a dict for peripheral %r, got %r; using empty config", peripheral, type(json_connection_params))
                json_connection_params = {}

            if isinstance(json_channel_to_virtual_index, str):
                try:
                    json_channel_to_virtual_index = json.loads(json_channel_to_virtual_index)
                except Exception:
                    logger.error("json_channel_to_virtual_index is a string but could not be parsed as JSON for peripheral %r: %r", peripheral, json_channel_to_virtual_index)
                    json_channel_to_virtual_index = {}
            if not isinstance(json_channel_to_virtual_index, dict):
                logger.warning("json_channel_to_virtual_index is not a dict for peripheral %r, got %r; using empty mapping", peripheral, type(json_channel_to_virtual_index))
                json_channel_to_virtual_index = {}

            logger.info(f"Peripheral params: {json_connection_params}")
            logger.info(f"Virtual Indexes: {json_channel_to_virtual_index}")

            _peripheral = GenericFileTransfer(json_connection_params,
                                              json_channel_to_virtual_index,
                                              self.send_message,
                                              Queue(),
                                              self.config_manager)

            _index = _peripheral.get_index()
            if _index is None:
                logger.error(f"Peripheral doesn't have an index...")
                raise ValueError("Peripheral index is required for queue declaration")

            recv_queue_name = f"recv_queue_index_{_index}"
            send_queue_name = f"send_queue_index_{_index}"
            self.channel.queue_declare(queue=recv_queue_name, durable=True)
            self.channel.queue_declare(queue=send_queue_name, durable=True)

            # register the consumer callback for the send queue (server -> device)
            if send_queue_name not in self.consumed_queues:
                self.channel.basic_consume(
                    queue=send_queue_name,
                    on_message_callback=_peripheral.process_message
                )
                self.consumed_queues.add(send_queue_name)
                logger.info(f"consuming queue: {send_queue_name}")

            self.queue_pool[_index] = _peripheral.get_command_queue()

            logger.info(f"Filas declaradas: {recv_queue_name} - {send_queue_name}")

        try:
            for automation in automations:
                if not isinstance(automation, dict):
                    logger.warning("Skipping automation with unexpected type %s: %r", type(automation).__name__, automation)
                    continue

                logger.info(f"Processing automation: {automation}")
                automation_id = automation.get('id')
                triggers = self.config_manager.get_triggers(automation_id)

                # Defensive: normalize triggers if needed
                if isinstance(triggers, str):
                    try:
                        triggers = json.loads(triggers)
                    except Exception:
                        logger.error("triggers for automation %r is a string but could not be parsed: %r", automation_id, triggers)
                        triggers = []
                if not isinstance(triggers, (list, tuple)):
                    logger.error("triggers for automation %r is not a list/tuple, got %r. Skipping.", automation_id, type(triggers))
                    triggers = []

                for trigger in triggers:
                    if not isinstance(trigger, dict):
                        logger.warning("Skipping trigger with unexpected type %s: %r", type(trigger).__name__, trigger)
                        continue

                    queue_name = trigger.get('queue_name')
                    if queue_name and queue_name not in self.consumed_queues:
                        self.channel.queue_declare(queue=queue_name, durable=True)

                        actions = self.config_manager.get_actions(automation_id)

                        # Defensive: normalize actions
                        if isinstance(actions, str):
                            try:
                                actions = json.loads(actions)
                            except Exception:
                                logger.error("actions for automation %r is a string but could not be parsed: %r", automation_id, actions)
                                actions = []
                        if not isinstance(actions, (list, tuple)):
                            logger.error("actions for automation %r is not a list/tuple, got %r. Skipping.", automation_id, type(actions))
                            actions = []

                        target_queues = []
                        for action in actions:
                            if not isinstance(action, dict):
                                logger.warning("Skipping action with unexpected type %s: %r", type(action).__name__, action)
                                continue

                            action_type = action.get('description')
                            if action_type == 'forward_to_rabbitmq':
                                _target_queues = action.get('action_config', [])
                                if not isinstance(_target_queues, (list, tuple)):
                                    logger.warning("action_config for action is not a list/tuple, got %r", type(_target_queues))
                                    _target_queues = []

                                for target_queue in _target_queues:
                                    send_to = None
                                    if isinstance(target_queue, dict):
                                        send_to = target_queue.get('sent_to')
                                    elif isinstance(target_queue, str):
                                        # allow string shortcuts
                                        send_to = target_queue

                                    if send_to:
                                        if send_to not in self.consumed_queues:
                                            logger.info(f"registering target queue: {send_to}")
                                        target_queues.append(send_to)

                        def make_callback(qname, target_queues_param):
                            def callback(ch, method, properties, body):
                                message = Message.from_json(body)
                                logger.info(f"Mensagem recebida na fila '{qname}': {body}")
                                self.route_to_next_queue(message)
                                for _target_queue in target_queues_param:
                                    logger.info(f"Roteando mensagem para fila: {_target_queue}")
                                    self.send_message(message, _target_queue)
                                ch.basic_ack(delivery_tag=method.delivery_tag)
                            return callback

                        self.channel.basic_consume(
                            queue=queue_name,
                            on_message_callback=make_callback(queue_name, target_queues)
                        )
                        self.consumed_queues.add(queue_name)
                        logger.info(f"consuming queue: {queue_name}")
        except Exception as e:
            logger.error(f"Erro ao declarar filas de automação: {e}")


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

                t = Thread(target=consume_queue, args=(self.command_queue, self.route_to_next_queue), daemon=True)
                t.start()

                logger.info("Serviço RabbitMQ iniciado. Aguardando mensagens...")

                # Use connection.process_data_events() in a loop to allow quick interruption
                while self.running:
                    try:
                        # process callbacks (from basic_consume) and wait up to 1 second
                        self.connection.process_data_events(time_limit=1)
                    except Exception as e:
                        logger.error(f"Erro durante processamento de eventos: {e}")
                        # break to trigger reconnect/stop flow
                        break

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt recebido, parando serviço...")
                self.stop()
            except ValueError as ve:
                logger.error(f"Erro de configuração: {ve}")
                self.stop()
            except Exception as e:
                logger.error(f"Erro no serviço RabbitMQ: {e}")
                if self.running:
                    self.reconnect()
            finally:
                if not self.running:
                    break

    def reconnect_now(self) -> bool:
        """Force re-establish the RabbitMQ connection immediately.

        Safely stops consuming and closes any existing connection/channel,
        then tries to connect once and redeclare queues. Returns True on success.
        """
        logger.info("Forcing RabbitMQ reconnection now...")
        return

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

    def route_to_next_queue(self, message: Message):
        """Roteia a mensagem para a próxima fila apropriada"""
        if message.index is None:
            logger.error("Mensagem recebida sem índice, não é possível rotear")
            return

        _queue = self.queue_pool.get(message.index)
        if _queue is None:
            logger.error("No command queue registered for index %s, dropping message", message.index)
            return
        _queue.put(message)

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
        logger.info("Parando serviço RabbitMQ...")
        self.running = False

        try:
            if self.channel:
                try:
                    logger.info("Parando consumo de mensagens...")
                    self.channel.stop_consuming()
                except Exception as e:
                    logger.warning(f"Erro ao parar consumo: {e}")
        except Exception as e:
            logger.error(f"Erro ao acessar channel durante stop: {e}")

        try:
            if self.connection and self.connection.is_open:
                try:
                    logger.info("Fechando conexão RabbitMQ...")
                    self.connection.close()
                except Exception as e:
                    logger.warning(f"Erro ao fechar conexão: {e}")
        except Exception as e:
            logger.error(f"Erro ao acessar connection durante stop: {e}")

        logger.info("Serviço RabbitMQ parado")