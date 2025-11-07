from logging_config import configure_logging
from setup_config import ConfigManager
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.serving import make_server
import logging
from pathlib import Path

configure_logging(app_name="rabbitmq_ftp_service", level="INFO")
logger = logging.getLogger(__name__)


class FlaskWebApp:
    """Aplicação web Flask para gerenciamento"""

    def __init__(self, config_manager: ConfigManager, port: int = 5000):
        self.config_manager = config_manager
        self.port = port
        templates_dir = str(Path(__file__).resolve().parent / "templates")
        self.app = Flask(__name__, template_folder=templates_dir)
        self.app.secret_key = 'rabbitmq-ftp-service-secret-key'
        self.server = None
        self._setup_routes()

    def _setup_routes(self):
        """Configura as rotas da aplicação"""

        @self.app.route('/')
        def index():
            return render_template('index.html')

        # Rotas de Histórico
        @self.app.route('/api/operations', methods=['GET'])
        def get_operations():
            limit = request.args.get('limit', 50, type=int)
            operations = self.config_manager.get_operations(limit)
            return jsonify(operations)

        @self.app.route('/api/operations/<int:op_id>', methods=['DELETE'])
        def delete_operation(op_id):
            try:
                self.config_manager.delete_operation(op_id)
                return jsonify({'message': 'Operação deletada'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/operations', methods=['DELETE'])
        def delete_all_operations():
            try:
                # Prefer a dedicated method if ConfigManager provides it
                if hasattr(self.config_manager, "clear_operations"):
                    self.config_manager.clear_operations()
                else:
                    # Fallback: fetch all operations and delete them one by one
                    ops = self.config_manager.get_operations(10_000_000)
                    for op in ops:
                        # expect op to contain an 'id' key
                        op_id = op.get("id") if isinstance(op, dict) else None
                        if op_id is None:
                            continue
                        try:
                            self.config_manager.delete_operation(op_id)
                        except Exception:
                            # ignore individual deletion errors
                            continue
                return jsonify({"message": "Todos as operações deletadas"})
            except Exception as e:
                return jsonify({"error": str(e)}), 400

        # Rota de configurações
        @self.app.route('/api/config', methods=['GET'])
        def get_config():
            rabbitmq = self.config_manager.get('rabbitmq', {})
            ftp = self.config_manager.get('ftp', {})
            return jsonify({'rabbitmq': rabbitmq, 'ftp': ftp})

        @self.app.route('/api/config', methods=['POST'])
        def update_config():
            data = request.json
            try:
                if 'rabbitmq' in data:
                    self.config_manager.set('rabbitmq', data['rabbitmq'])
                if 'ftp' in data:
                    self.config_manager.set('ftp', data['ftp'])
                return jsonify({'message': 'Configuração atualizada'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        # Peripheral routes
        @self.app.route('/api/peripherals', methods=['GET'])
        def get_peripherals():
            peripherals = self.config_manager.get_peripherals()
            return jsonify(peripherals)

        @self.app.route('/api/peripherals/<int:peripheral_id>', methods=['GET'])
        def get_peripheral(peripheral_id):
            p = self.config_manager.get_peripheral(peripheral_id)
            if p:
                return jsonify(p)
            return jsonify({'error': 'Peripheral not found'}), 404

        @self.app.route('/api/peripherals', methods=['POST'])
        def create_peripheral():
            data = request.get_json() or {}
            name = data.get('name')
            if not name:
                return jsonify({'error': 'Field \"name\" is required'}), 400
            try:
                pid = self.config_manager.create_peripheral(
                    name=name,
                    interface=data.get('interface'),
                    json_connection_params=data.get('json_connection_params'),
                    device=data.get('device'),
                    model=data.get('model'),
                    json_channel_to_virtual_index=data.get('json_channel_to_virtual_index')
                )
                return jsonify({'id': pid, 'message': 'Peripheral created'}), 201
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/peripherals/<int:peripheral_id>', methods=['PUT'])
        def update_peripheral(peripheral_id):
            data = request.get_json() or {}
            # allow partial updates; pass only provided fields
            try:
                self.config_manager.update_peripheral(
                    peripheral_id=peripheral_id,
                    name=data.get('name'),
                    interface=data.get('interface'),
                    json_connection_params=data.get('json_connection_params'),
                    device=data.get('device'),
                    model=data.get('model'),
                    json_channel_to_virtual_index=data.get('json_channel_to_virtual_index')
                )
                return jsonify({'message': 'Peripheral updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/peripherals/<int:peripheral_id>', methods=['DELETE'])
        def delete_peripheral(peripheral_id):
            try:
                self.config_manager.delete_peripheral(peripheral_id)
                return jsonify({'message': 'Peripheral deleted'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

    def start(self):
        """Inicia o servidor web"""
        self.server = make_server('0.0.0.0', self.port, self.app)
        logger.info(f"Servidor web iniciado na porta {self.port}")
        self.server.serve_forever()

    def stop(self):
        """Para o servidor web"""
        if self.server:
            self.server.shutdown()
            logger.info("Servidor web parado")