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

configure_logging(app_name="rabbitmq_ftp_service", level="INFO")
logger = logging.getLogger(__name__)

stop_event = Event()
rabbitmq_service = None  # will hold RabbitMQService instance
web_service = None  # will hold FlaskWebApp instance


def signal_handler(signum, frame):
    """Manipula sinais do sistema"""
    logger.info(f"Sinal recebido: {signum}")
    if rabbitmq_service:
        try:
            rabbitmq_service.stop()
        except Exception as e:
            logger.error(f"Erro ao parar o serviço rabbitmq: {e}")
    if web_service:
        try:
            web_service.stop()
        except Exception as e:
            logger.error(f"Erro ao parar o serviço web: {e}")
    stop_event.set()


def main():
    """Função principal"""
    global rabbitmq_service
    global web_service

    config_manager = ConfigManager()
    # Obter porta da web do config ou usar padrão
    web_port = config_manager.get('web_port', 5000)

    # Iniciar servidor web em thread separada
    web_service = FlaskWebApp(config_manager, web_port)
    web_thread = Thread(target=web_service.start, daemon=True)
    web_thread.start()

    # Iniciar serviço RabbitMQ na thread principal
    rabbitmq_service = RabbitMQService(config_manager)
    rabbitmq_thread = Thread(target=rabbitmq_service.start, daemon=True)
    rabbitmq_thread.start()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Wait until signal_handler sets the event
    stop_event.wait()

    # Give the thread a moment to stop, then ensure logging is flushed
    web_thread.join(timeout=5)
    rabbitmq_thread.join(timeout=5)
    logging.shutdown()



if __name__ == '__main__':
    main()