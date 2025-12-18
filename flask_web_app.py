from logging_config import configure_logging
from setup_config import ConfigManager
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.serving import make_server
import logging
from pathlib import Path
from threading import Thread
from queue import Queue
from models.message import Message

configure_logging(app_name="rabbitmq_ftp_service", level="INFO")
logger = logging.getLogger(__name__)


class FlaskWebApp:
    """Aplicação web Flask para gerenciamento"""

    def __init__(self, config_manager: ConfigManager, command_queue: Queue, port: int = 5000):
        self.config_manager = config_manager
        self.port = port
        templates_dir = str(Path(__file__).resolve().parent / "templates")
        self.app = Flask(__name__, template_folder=templates_dir)
        self.app.secret_key = 'rabbitmq-ftp-service-secret-key'
        self.server = None
        self.rabbitmq_service = None  # will be set from outside if available
        self.command_queue = command_queue
        self._setup_routes()

    def set_rabbitmq_service(self, rabbitmq_service):
        """Provide the RabbitMQService instance so endpoints can trigger reconnects."""
        self.rabbitmq_service = rabbitmq_service

    def _trigger_rabbit_reconnect(self):
        """Call reconnect_now in a background thread if rabbitmq_service is available."""
        if getattr(self, "rabbitmq_service", None):
            try:
                Thread(target=self.rabbitmq_service.reconnect_now, daemon=True).start()
            except Exception:
                logger.exception("Failed to start rabbitmq reconnect thread")


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
                return jsonify({'error': 'Field "name" is required'}), 400
            try:
                pid = self.config_manager.create_peripheral(
                    name=name,
                    interface=data.get('interface'),
                    json_connection_params=data.get('json_connection_params'),
                    device=data.get('device'),
                    model=data.get('model'),
                    json_channel_to_virtual_index=data.get('json_channel_to_virtual_index')
                )
                self._trigger_rabbit_reconnect()
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
                self._trigger_rabbit_reconnect()
                return jsonify({'message': 'Peripheral updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/peripherals/<int:peripheral_id>', methods=['DELETE'])
        def delete_peripheral(peripheral_id):
            try:
                self.config_manager.delete_peripheral(peripheral_id)
                self._trigger_rabbit_reconnect()
                return jsonify({'message': 'Peripheral deleted'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/activate_debug/<string:index>', methods=['POST'])
        def activate_debug(index):
            try:
                self.command_queue.put(Message(
                    index=str(index),
                    cmd='START_DEBUG',
                    args=(str(index),),
                    kwargs={},
                    reply_q=Queue()
                ))
                return jsonify({'message': f'Debug activated for peripheral {index}'})
            except Exception as e:
                logger.exception("activate_debug failed for index %s: %s", index, e)
                return jsonify({'error': str(e)}), 400

        # ========== Automation routes ==========
        @self.app.route('/api/automations', methods=['GET'])
        def get_automations():
            automations = self.config_manager.get_automations()
            return jsonify(automations)

        @self.app.route('/api/automations/<int:automation_id>', methods=['GET'])
        def get_automation(automation_id):
            automation = self.config_manager.get_automation(automation_id)
            if automation:
                return jsonify(automation)
            return jsonify({'error': 'Automation not found'}), 404

        @self.app.route('/api/automations', methods=['POST'])
        def create_automation():
            data = request.get_json() or {}
            name = data.get('name')
            if not name:
                return jsonify({'error': 'Field "name" is required'}), 400
            try:
                aid = self.config_manager.create_automation(name=name)
                return jsonify({'id': aid, 'message': 'Automation created'}), 201
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/automations/<int:automation_id>', methods=['PUT'])
        def update_automation(automation_id):
            data = request.get_json() or {}
            name = data.get('name')
            if not name:
                return jsonify({'error': 'Field "name" is required'}), 400
            try:
                self.config_manager.update_automation(automation_id=automation_id, name=name)
                return jsonify({'message': 'Automation updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/automations/<int:automation_id>', methods=['DELETE'])
        def delete_automation(automation_id):
            try:
                self.config_manager.delete_automation(automation_id)
                return jsonify({'message': 'Automation deleted'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        # ========== Trigger routes ==========
        @self.app.route('/api/triggers', methods=['GET'])
        def get_triggers():
            automation_id = request.args.get('automation_id', type=int)
            triggers = self.config_manager.get_triggers(automation_id=automation_id)
            return jsonify(triggers)

        @self.app.route('/api/triggers/<int:trigger_id>', methods=['GET'])
        def get_trigger(trigger_id):
            trigger = self.config_manager.get_trigger(trigger_id)
            if trigger:
                return jsonify(trigger)
            return jsonify({'error': 'Trigger not found'}), 404

        @self.app.route('/api/triggers', methods=['POST'])
        def create_trigger():
            data = request.get_json() or {}
            automation_id = data.get('automation_id')
            # support both new (description, queue_name) and old (trigger_type, trigger_config)
            description = data.get('description') or data.get('trigger_type')
            queue_name = data.get('queue_name')
            if not queue_name:
                # fallback: if trigger_config is a plain string, treat it as queue_name
                tcfg = data.get('trigger_config')
                if isinstance(tcfg, str) and tcfg.strip():
                    queue_name = tcfg.strip()
            if not automation_id or not queue_name:
                return jsonify({'error': 'Fields "automation_id" and "queue_name" are required'}), 400
            try:
                tid = self.config_manager.create_trigger(
                    automation_id=automation_id,
                    description=description or '',
                    queue_name=queue_name
                )
                return jsonify({'id': tid, 'message': 'Trigger created'}), 201
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/triggers/<int:trigger_id>', methods=['PUT'])
        def update_trigger(trigger_id):
            data = request.get_json() or {}
            # accept description/queue_name or old names
            description = data.get('description') if 'description' in data else data.get('trigger_type')
            queue_name = data.get('queue_name') if 'queue_name' in data else data.get('trigger_config')
            try:
                self.config_manager.update_trigger(
                    trigger_id=trigger_id,
                    description=description,
                    queue_name=queue_name
                )
                return jsonify({'message': 'Trigger updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        # ========== Result routes ==========
        @self.app.route('/api/results', methods=['GET'])
        def get_results():
            automation_id = request.args.get('automation_id', type=int)
            results = self.config_manager.get_results(automation_id=automation_id)
            return jsonify(results)

        @self.app.route('/api/results/<int:result_id>', methods=['GET'])
        def get_result(result_id):
            result = self.config_manager.get_result(result_id)
            if result:
                return jsonify(result)
            return jsonify({'error': 'Result not found'}), 404

        @self.app.route('/api/results', methods=['POST'])
        def create_result():
            data = request.get_json() or {}
            automation_id = data.get('automation_id')
            # support both new (description, queue_name) and old (result_type, result_config)
            description = data.get('description') or data.get('result_type')
            queue_name = data.get('queue_name')
            result_config = data.get('result_config') if 'result_config' in data else data.get('result_extra_config') if 'result_extra_config' in data else None
            if not queue_name:
                # fallback: if result_config is a plain string, treat it as queue_name
                rcfg = data.get('result_config')
                if isinstance(rcfg, str) and rcfg.strip():
                    queue_name = rcfg.strip()
            if not automation_id or not queue_name:
                return jsonify({'error': 'Fields "automation_id" and "queue_name" are required'}), 400
            try:
                rid = self.config_manager.create_result(
                    automation_id=automation_id,
                    description=description or '',
                    queue_name=queue_name,
                    result_config=result_config
                )
                return jsonify({'id': rid, 'message': 'Result created'}), 201
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/results/<int:result_id>', methods=['PUT'])
        def update_result(result_id):
            data = request.get_json() or {}
            description = data.get('description') if 'description' in data else data.get('result_type')
            queue_name = data.get('queue_name') if 'queue_name' in data else data.get('result_config')
            result_config = data.get('result_config') if 'result_config' in data else None
            try:
                self.config_manager.update_result(
                    result_id=result_id,
                    description=description,
                    queue_name=queue_name,
                    result_config=result_config
                )
                return jsonify({'message': 'Result updated'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/results/<int:result_id>', methods=['DELETE'])
        def delete_result(result_id):
            try:
                self.config_manager.delete_result(result_id)
                return jsonify({'message': 'Result deleted'})
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        # ========== Action routes ==========
        @self.app.route('/api/actions', methods=['GET'])
        def get_actions():
            automation_id = request.args.get('automation_id', type=int)
            actions = self.config_manager.get_actions(automation_id=automation_id)
            return jsonify(actions)

        @self.app.route('/api/actions/<int:action_id>', methods=['GET'])
        def get_action(action_id):
            action = self.config_manager.get_action(action_id)
            if action:
                return jsonify(action)
            return jsonify({'error': 'Action not found'}), 404

        @self.app.route('/api/actions', methods=['POST'])
        def create_action():
            data = request.get_json() or {}
            automation_id = data.get('automation_id')
            # support both new (description, action_config) and old (action_type, action_config)
            description = data.get('description') or data.get('action_type')
            action_config = data.get('action_config')
            if action_config is None:
                return jsonify({'error': 'Field "action_config" is required'}), 400
            try:
                aid = self.config_manager.create_action(
                    automation_id=automation_id,
                    description=description or '',
                    action_config=action_config
                )
                return jsonify({'id': aid, 'message': 'Action created'}), 201
            except Exception as e:
                return jsonify({'error': str(e)}), 400

        @self.app.route('/api/actions/<int:action_id>', methods=['PUT'])
        def update_action(action_id):
            data = request.get_json() or {}
            description = data.get('description') if 'description' in data else data.get('action_type')
            action_config = data.get('action_config') if 'action_config' in data else None
            try:
                self.config_manager.update_action(
                    action_id=action_id,
                    description=description,
                    action_config=action_config
                )
                return jsonify({'message': 'Action updated'})
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

