from queue import Queue
import json
from typing import Any, Optional


class Message:

    def __init__(self, index: Optional[str], cmd: str, args: Any = None, kwargs: dict | None = None, reply_q: Queue | None = None):
        self.index = index
        self.cmd = cmd
        self.args = args if args is not None else ()
        self.kwargs = kwargs if kwargs is not None else {}
        self.reply_q = reply_q

    @classmethod
    def from_json(cls, data):
        # Accept bytes, str (JSON) or dict
        if isinstance(data, (bytes, bytearray)):
            try:
                data = data.decode('utf-8')
            except Exception:
                data = data.decode(errors='ignore')
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                # not JSON, treat as raw command
                return cls(index=None, cmd=str(data))
        if isinstance(data, dict):
            return cls(
                index=str(data.get('index')) if data.get('index') is not None else None,
                cmd=data.get('cmd') or data.get('action') or '',
                args=data.get('args', ()),
                kwargs=data.get('kwargs', {}),
                reply_q=None
            )
        # fallback
        return cls(index=None, cmd=str(data))

    def to_dict(self):
        return {
            'index': self.index,
            'cmd': self.cmd,
            'args': self.args,
            'kwargs': self.kwargs
        }
