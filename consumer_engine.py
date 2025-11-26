# consumer_engine.py
import json
import time
# Imports fictícios das suas libs de rabbitmq
# import pika

from models import SessionLocal, Automation, Trigger, Action, Peripheral
from action_registry import registry
import actions_impl  # Importante importar para registrar as funções!


def process_message(queue_name, message_body):
    print(f"\n[ENGINE] Recebida mensagem na fila '{queue_name}': {message_body}")

    db = SessionLocal()
    try:
        # 1. Encontrar triggers que escutam esta fila
        # Agora Trigger has a 'queue_name' column
        triggers = db.query(Trigger).filter(
            Trigger.queue_name == queue_name
        ).all()

        if not triggers:
            print("[ENGINE] Nenhuma automação encontrada para esta mensagem.")
            return

        matching_automations = []
        for trigger in triggers:
            if trigger.automation is not None:
                matching_automations.append(trigger.automation)

        if not matching_automations:
            print("[ENGINE] Nenhuma automação associada aos triggers encontrados.")
            return

        # 2. Para cada automação encontrada, executar suas ações
        for automation in matching_automations:
            print(f"[ENGINE] Executando Automação: {automation.name}")
            for action in automation.actions:
                try:
                    # action.description stores the action type/identifier
                    registry.execute_action(
                        action_type=action.description,
                        action_config=action.action_config,
                        message_payload=message_body,  # Passa os dados que chegaram
                        db_session=db  # Passa a sessão caso a ação precise consultar o banco
                    )
                except Exception as e:
                    print(f"Erro ao executar ação {getattr(action, 'description', 'unknown')}: {e}")

    finally:
        db.close()


# =========================================
# SIMULAÇÃO (Para testar sem o RabbitMQ real)
# =========================================

def setup_dummy_data():
    db = SessionLocal()
    # Criar periféricos
    p1 = Peripheral(name="Lâmpada Sala", connection_config={"ip": "192.168.1.10"})
    p2 = Peripheral(name="Ar Condicionado", connection_config={"ip": "192.168.1.20"})
    p3 = Peripheral(name="Log System", connection_config={"type": "dummy"})
    db.add_all([p1, p2, p3])
    db.commit()

    # Criar Automação: "Modo Cheguei em Casa"
    # Se chegar mensagem na fila 'entrada_casa', liga Lâmpada e Ar.
    auto1 = Automation(name="Ativar Modo Casa")
    db.add(auto1)
    db.commit()

    # Trigger: Fila 'entrada_casa'
    trig1 = Trigger(automation=auto1, queue_name='entrada_casa')
    db.add(trig1)

    # Ação: Multiplexar para periféricos 1 e 2
    act1 = Action(
        automation=auto1,
        description='multiplex_peripherals',
        action_config={'peripheral_ids': [p1.id, p2.id]}
    )
    db.add(act1)
    db.commit()


    print("Dados de simulação criados.")
    db.close()


if __name__ == "__main__":
    # OBS: Em produção, use migrations (alembic) em vez de create_all
    # Base.metadata.drop_all(engine) # Limpa banco para teste
    # Base.metadata.create_all(engine)

    try:
        setup_dummy_data()
    except Exception:
        pass  # Dados já existem

    print("Simulando chegada de mensagem do RabbitMQ...")
    time.sleep(1)

    # Simulando o callback do RabbitMQ recebendo uma mensagem
    msg_body_simulado = '{"comando": "ativar", "usuario": "admin"}'
    process_message(queue_name='entrada_casa', message_body=msg_body_simulado)

    print("\nSimulação finalizada.")