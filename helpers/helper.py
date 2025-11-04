from typing import Any, Optional

def has_key_with_substring(obj: Any, substring: str, *, case_sensitive: bool = False) -> Optional[Any]:
    """
    Verifica se algum dos valores das chaves do dicionário contém a substring especificada.

    Args:
        obj (Any): O objeto a ser verificado (deve ser um dicionário).
        substring (str): A substring a ser procurada nos valores das chaves.
        case_sensitive (bool, optional): Se a busca deve ser sensível a maiúsculas e minúsculas. Padrão é False.

    Returns:
        bool: True se algum valor de chave contém a substring, False caso contrário.
    """
    if not isinstance(obj, dict) or substring == "":
        return False

    if not case_sensitive:
        substring = substring.lower()

    def match_key(key: str) -> bool:
        k = key if case_sensitive else key.lower()
        return substring in k

    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and match_key(k):
                    return v
                res = _walk(v)
                if res is not None:
                    return res
            return None
        if isinstance(node, (list, tuple, set)):
            for item in node:
                res = _walk(item)
                if res is not None:
                    return res
            return None
        return None

    return _walk(obj)

