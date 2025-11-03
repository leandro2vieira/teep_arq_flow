import sys
import json
import sqlite3
import logging
import signal
from datetime import datetime
from threading import Thread
from logging_config import configure_logging
from setup_config import ConfigManager
from flask_web_app import FlaskWebApp
from rabbitmq_service import RabbitMQService

configure_logging(app_name="rabbitmq_ftp_service", level="INFO")
logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Manipula sinais do sistema"""
    logger.info(f"Sinal recebido: {signum}")
    sys.exit(0)


def main():
    """Função principal"""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    config_manager = ConfigManager()

    # Obter porta da web do config ou usar padrão
    web_port = config_manager.get('web_port', 5000)

    # Iniciar servidor web em thread separada
    web_app = FlaskWebApp(config_manager, web_port)
    web_thread = Thread(target=web_app.start, daemon=True)
    web_thread.start()

    # Iniciar serviço RabbitMQ na thread principal
    rabbitmq_service = RabbitMQService(config_manager)
    rabbitmq_service.start()


if __name__ == '__main__':
    main()