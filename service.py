import sys
import json
import sqlite3
import logging
import signal
from datetime import datetime
from threading import Thread, Event
from logging_config import configure_logging
from setup_config import ConfigManager
from flask_web_app import FlaskWebApp
from rabbitmq_service import RabbitMQService
from queue import Queue

configure_logging(app_name="rabbitmq_ftp_service", level="INFO")
logger = logging.getLogger(__name__)

stop_event = Event()
rabbitmq_service = None  # will hold RabbitMQService instance
web_service = None  # will hold FlaskWebApp instance
command_queue = Queue()


def signal_handler(signum, frame):
    """Manipula sinais do sistema"""
    logger.info(f"Sinal recebido: {signum}. Iniciando shutdown...")

    if rabbitmq_service:
        try:
            logger.info("Parando serviço RabbitMQ...")
            rabbitmq_service.stop()
        except Exception as e:
            logger.error(f"Erro ao parar o serviço rabbitmq: {e}")

    if web_service:
        try:
            logger.info("Parando serviço web...")
            web_service.stop()
        except Exception as e:
            logger.error(f"Erro ao parar o serviço web: {e}")

    stop_event.set()
    logger.info("Evento de parada ativado")


def main():
    """Função principal"""
    global rabbitmq_service
    global web_service
    global command_queue

    config_manager = ConfigManager()
    # Obter porta da web do config ou usar padrão
    web_port = config_manager.get('web_port', 5000)

    # Iniciar serviço RabbitMQ na thread principal
    rabbitmq_service = RabbitMQService(config_manager, command_queue)
    rabbitmq_thread = Thread(target=rabbitmq_service.start, daemon=True)
    rabbitmq_thread.start()

    # Iniciar servidor web em thread separada
    web_service = FlaskWebApp(config_manager, command_queue, web_port)
    web_service.set_rabbitmq_service(rabbitmq_service)
    web_thread = Thread(target=web_service.start, daemon=True)
    web_thread.start()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Wait until signal_handler sets the event
    stop_event.wait()

    # Give the threads time to stop gracefully
    logger.info("Aguardando threads finalizarem...")

    logger.info("Aguardando thread web...")
    web_thread.join(timeout=10)
    if web_thread.is_alive():
        logger.warning("Thread web não terminou dentro do timeout")
    else:
        logger.info("Thread web finalizada")

    logger.info("Aguardando thread RabbitMQ...")
    rabbitmq_thread.join(timeout=10)
    if rabbitmq_thread.is_alive():
        logger.warning("Thread RabbitMQ não terminou dentro do timeout")
    else:
        logger.info("Thread RabbitMQ finalizada")

    logger.info("Finalizando logging...")
    logging.shutdown()



if __name__ == '__main__':
    main()