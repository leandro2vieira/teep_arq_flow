from action_registry import registry
from models import Peripheral  # Assumindo que os modelos estão em models.py


# --- IMPLEMENTAÇÃO DA REGRA 1: MULTIPLEXADOR ---
@registry.register("multiplex_peripherals")
def action_multiplex_peripherals(action_config, message_payload, db_session):
    """
    Envia o payload da mensagem para múltiplos periféricos definidos na configuração.
    Espera action_config no formato: {'peripheral_ids': [1, 2, 5]}
    """
    target_ids = action_config.get('peripheral_ids', [])

    if not target_ids:
        print("Aviso: Nenhum ID de periférico configurado para multiplexação.")
        return

    # Busca os periféricos no banco
    peripherals = db_session.query(Peripheral).filter(Peripheral.id.in_(target_ids)).all()

    for peripheral in peripherals:
        print(
            f"--- > [AÇÃO MULTIPLEX] Enviando mensagem '{message_payload}' para Periférico {peripheral.name} (ID: {peripheral.id}) usando config {peripheral.connection_config}")
        # AQUI VOCÊ CHAMA SUA FUNÇÃO EXISTENTE QUE FALA COM O PERIFÉRICO REAL
        # send_to_device(peripheral.connection_config, message_payload)


# --- OUTRO EXEMPLO: REGRA PARA REENVIAR AO RABBITMQ ---
@registry.register("forward_to_rabbitmq")
def action_forward_rabbitmq(action_config, message_payload, db_session):
    """
    Encaminha a mensagem para outra fila RabbitMQ.
    Espera action_config no formato: {'target_queue': 'nome_da_fila_destino'}
    """
    target_queue = action_config.get('target_queue')
    print(f"--- > [AÇÃO FORWARD] Reenviando mensagem para fila RabbitMQ: {target_queue}")
    # rabbitmq_channel.basic_publish(exchange='', routing_key=target_queue, body=message_payload)