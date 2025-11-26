import logging

logger = logging.getLogger(__name__)


class ActionRegistry:
    def __init__(self):
        self._actions = {}

    def register(self, action_name):
        """Decorator para registrar novas funções de ação."""

        def decorator(func):
            self._actions[action_name] = func
            return func

        return decorator

    def execute_action(self, action_type, action_config, message_payload, db_session):
        """Executa a ação baseada no tipo e passa as configurações."""
        action_func = self._actions.get(action_type)
        if not action_func:
            logger.error(f"Tipo de ação desconhecido: {action_type}")
            raise ValueError(f"Action type '{action_type}' not implemented.")

        logger.info(f"Executando ação: {action_type} com config: {action_config}")
        # Passamos o payload da mensagem original, a config da ação e a sessão do BD
        return action_func(action_config, message_payload, db_session)


# Instância global do registro
registry = ActionRegistry()