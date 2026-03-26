import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Tuple
from ftp_manager import FTPManager
from scp_manager import SCPManager
from sftp_manager import SFTPManager
from helpers.enums import ActionTable
from queue import Queue
from threading import Thread
from models.message import Message

logger = logging.getLogger(__name__)

class IO:

    def __init__(self, config: Dict[str, Any]):
        self.index = config.get('index', None)
        self.notification_type = config.get('notification_type', 4)
        self.server_side_path = config.get('server_side_path', './')
        self.remote_side_path = config.get('remote_side_path', './')
        self.reboot_on_upload = config.get('reboot_on_upload', False)
        self.reboot_on_delete = config.get('reboot_on_delete', False)
        # server_os may be provided in io config or in parent config; default to 'linux'
        self.server_os = config.get('server_os') or config.get('serverOS') or 'linux'

        print(f"{config} - IO initialized with index: {self.index}, notification_type: {self.notification_type}, server_side_path: {self.server_side_path}, remote_side_path: {self.remote_side_path}", flush=True)

def _handle_list_local_directory(local_path: str, path: str) -> List[Dict[str, Any]]:
    local_path = _join_path(local_path, path)
    try:
        entries = []
        with os.scandir(local_path) as it:
            for entry in it:
                info = entry.stat()
                entries.append({
                    'name': entry.name,
                    'is_dir': entry.is_dir(),
                    'size': info.st_size,
                    'modified': int(info.st_mtime)
                })
        return entries
    except Exception as e:
        logger.error(f"Erro ao listar diretório local: {e}")
        return []

def _join_path(base: str, part: str) -> str:
    base = (base or '').rstrip('/')
    part = (part or '').lstrip('/')
    if base == '':
        return f"/{part}" if part else '/'
    return f"{base}/{part}" if part else base

def consume_queue(q: Queue, business_callback):
    while True:
        command = q.get()
        if command is None:
            break

        business_callback(command)

class GenericFileTransfer:
    def __init__(self, config: Dict[str, Any], io_config: Dict[str, Any], send_message_callback, command_queue: Queue, config_manager, name: str):
        self.host = config.get('host', 'localhost')
        self.name = name
        # coerce port to int when possible; default to 21 (ftp) or later adjusted for sftp/scp
        try:
            self.port = int(config.get('port')) if config.get('port') is not None and config.get('port') != '' else 21
        except Exception:
            self.port = 21
        self.user = config.get('user', 'anonymous')
        self.password = config.get('password', '')
        self.passive = config.get('passive', True)
        self.timeout = config.get('timeout', 30)
        self.protocol = config.get('protocol', 'ftp')
        self.remote = None
        self.command_queue = command_queue

        proto = (self.protocol or 'ftp').lower()
        # if protocol is SSH-based and no explicit port provided (default 21), use 22
        if proto in ('scp', 'sftp') and (self.port == 21 or not self.port):
            self.port = 22
        # use SCPManager for 'scp' (SCP) and SFTPManager for 'sftp' (pure SFTP via paramiko)
        if proto == 'scp':
            self.remote = SCPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout,
                name=self.name
            )
            # allow optional key file
            key_path = config.get('private_key_path') or config.get('key_filename') or config.get('private_key')
            if key_path:
                try:
                    self.remote.key_filename = key_path
                except Exception:
                    pass
        elif proto == 'sftp':
            # pure SFTP manager (no SCP fallback)
            self.remote = SFTPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout,
                name=self.name
            )
            key_path = config.get('private_key_path') or config.get('key_filename') or config.get('private_key')
            if key_path:
                try:
                    self.remote.key_filename = key_path
                except Exception:
                    pass
        else:
            # support FTPS if explicitly requested by protocol 'ftps'
            use_tls = proto == 'ftps'
            self.remote = FTPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                use_tls=use_tls,
                timeout=self.timeout,
                name=self.name
            )

        # merge possible server_os from top-level config into io config for convenience
        merged_io = io_config.get('GENERIC_FILE_TRANSFER', {}) or {}
        if 'server_os' not in merged_io and 'server_os' in config:
            merged_io['server_os'] = config.get('server_os')
        self.io: IO = IO(merged_io)

        self.send_message = send_message_callback
        self.config_manager = config_manager

        t = Thread(target=consume_queue, args=(self.command_queue, self.process_command), daemon=True)
        t.start()

    def process_command(self, message: Message):
        if message.cmd == 'START_DEBUG':
            logger.debug(f"Debug message received: args={message.args}, kwargs={message.kwargs}")

    def get_command_queue(self) -> Queue:
        return self.command_queue

    # --- helpers ----------------------------------------------------------------

    def get_index(self) -> int:
        return self.io.index

    def _build_response(self, action, value=None):
        return {
            'action': action,
            'data': {
                'index': self.get_index(),
                'value': value if value is not None else '',
                'timestamp': int(datetime.now().timestamp())
            }
        }

    def _send(self, action, value=None) -> Dict:
        resp = self._build_response(action, value)
        self.send_message(resp, f"recv_queue_index_{str(self.get_index())}")
        return resp

    # Wrapper that ensures FTP connection and disconnect
    def _with_ftp(self, func, *args, **kwargs):
        if not self.remote.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP', 'message': 'Falha ao conectar FTP', 'status': 'error'}
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception("FTP operation failed: %s", e)
            return {'success': False, 'error': str(e), 'message': str(e), 'status': 'error'}
        finally:
            try:
                self.remote.disconnect()
            except Exception:
                pass

    # --- message processing -----------------------------------------------------

    def process_message(self, ch, method, properties, body):
        """Callback invoked by pika when a message arrives.

        We ack the message immediately and dispatch the actual work to a
        background thread so that pika's I/O loop is free to flush any
        progress messages published by ``_send()`` in real time.
        """
        try:
            message = json.loads(body)
        except Exception as e:
            logger.error(f"Erro ao decodificar mensagem: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # Acknowledge right away so pika can continue processing I/O (and
        # flushing outbound publishes scheduled via add_callback_threadsafe).
        try:
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logger.error(f"Erro ao fazer ack da mensagem: {e}")

        # Run the heavy lifting in a daemon thread
        worker = Thread(target=self._process_message_worker, args=(message,), daemon=True)
        worker.start()

    def _process_message_worker(self, message: dict):
        """Executes the actual message handling in a background thread."""
        try:
            action = message.get('action')

            logger.info(f"Mensagem recebida: {action}")

            response = self._build_response(action)

            if action == ActionTable.GET_SERVER_FILE_TREE.value:
                data = message.get('data', {})
                value = data.get('value', {})
                path = value.get('local_path', '')
                result = _handle_list_local_directory(self.io.server_side_path, path)
                response['action'] = ActionTable.SERVER_FILE_TREE.value
            elif action == ActionTable.GET_REMOTE_FILE_TREE.value:
                data = message.get('data', {})
                value = data.get('value', {})
                path = value.get('remote_path', '')
                result = self._handle_list_directory(self.io.remote_side_path, path)
                response['action'] = ActionTable.CLIENT_FILE_TREE.value
            elif action == ActionTable.STREAM_DIRECTORY.value:
                # reboot server
                if self.io.reboot_on_upload:
                    self._handle_remote_reboot()
                else:
                    data = message.get('data', {})
                    value = data.get('value', {})
                    local_path = value.get('local_path', '')
                    remote_path = value.get('remote_path', '')
                    result = self._handle_upload_directory(local_path, remote_path)
            elif action == ActionTable.STREAM_FILE.value:
                # reboot server
                if self.io.reboot_on_upload:
                    self._handle_remote_reboot()
                else:
                    data = message.get('data', {})
                    value = data.get('value', {})
                    local_path = value.get('local_path', '')
                    remote_path = value.get('remote_path', '')
                    result = self._handle_upload_file(local_path, remote_path)
            elif action == ActionTable.DOWNLOAD_FILE.value:
                data = message.get('data', {})
                value = data.get('value', {})
                local_path = value.get('local_path', '')
                remote_path = value.get('remote_path', '')
                result = self._handle_download_file(local_path, remote_path)

            elif action == ActionTable.DOWNLOAD_DIRECTORY.value:
                data = message.get('data', {})
                value = data.get('value', {})
                local_path = value.get('local_path', '')
                remote_path = value.get('remote_path', '')
                # result = self._handle_download_directory(local_path, remote_path)
                result = self._handle_download_file(local_path, remote_path)

            elif action == ActionTable.DELETE_REMOTE_FILE.value:
                # reboot server
                if self.io.reboot_on_delete:
                    self._handle_remote_reboot()
                else:
                    data = message.get('data', {})
                    value = data.get('value', {})
                    remote_path = value.get('remote_path', '')
                    result = self._handle_delete_remote_file(remote_path)
            elif action == ActionTable.DELETE_REMOTE_DIRECTORY.value:
                 # reboot server
                if self.io.reboot_on_delete:
                    self._handle_remote_reboot()
                else:
                    data = message.get('data', {})
                    value = data.get('value', {})
                    remote_path = value.get('remote_path', '')
                    result = self._handle_delete_remote_directory(remote_path)
            elif action == ActionTable.REMOTE_REBOOT.value:
                result = self._handle_remote_reboot()
            elif action == ActionTable.CREATE_FILE_FROM_TEMPLATE.value:
                data = message.get('data', {})
                value = data.get('value', {})
                name = value.get('name', '')
                local_path = value.get('local_path', '')
                remote_path = value.get('remote_path', '')
                result = self._handle_create_file_from_template(name, local_path, remote_path)
            else:
                response['action'] = ActionTable.ERROR.value
                result = f" Comando desconhecido: {action}"

            # normalize handler return: accept both (bool, payload_or_message) and legacy returns
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
                success, payload = result

                # build standardized value
                if success:
                    value_obj = {'success': True, 'message': '', 'result': payload}
                else:
                    value_obj = {'success': False, 'message': str(payload), 'result': None}
            else:
                value_obj = result

            response['data']['value'] = value_obj

            value_for_log = response['data']['value']
            if not isinstance(value_for_log, (str, bytes, int, float, type(None))):
                try:
                    value_for_log = json.dumps(value_for_log, default=str)
                except Exception:
                    value_for_log = str(value_for_log)

            self.send_message(response, f"recv_queue_index_{str(self.get_index())}")

            self.config_manager.log_operation(
                response['action'],
                value_for_log,
                json.dumps(response)
            )

        except Exception as e:
            logger.exception(f"Erro ao processar mensagem no worker: {e}")

    def _ensure_remote_dirs(self, remote_dir: str) -> bool:
        """
        Ensure remote_dir exists on the remote server.
        Tries several possible APIs on self.remote, then falls back to ftp.mkd.
        Best-effort: ignores errors for existing directories.
        """
        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath

        if not remote_dir:
            return True
        # normalize and build path parts
        remote_dir = remote_dir.rstrip('/')
        parts = [p for p in remote_dir.split('/') if p]
        if not parts:
            return True

        cur = ''
        for part in parts:
            cur = path_mod.join(cur, part)
            try:
                # prefer high-level helpers if present
                if hasattr(self.remote, 'make_dirs'):
                    try:
                        self.remote.make_dirs(cur)
                        continue
                    except Exception:
                        pass
                if hasattr(self.remote, 'ensure_dir'):
                    try:
                        self.remote.ensure_dir(cur)
                        continue
                    except Exception:
                        pass
                if hasattr(self.remote, 'mkdir'):
                    try:
                        self.remote.mkdir(cur)
                        continue
                    except Exception:
                        pass

                # fallback to direct ftp.mkd if ftp attribute exposed
                ftp_obj = getattr(self.remote, 'ftp', None)
                if ftp_obj:
                    try:
                        ftp_obj.mkd(cur)
                    except Exception:
                        # ignore (likely already exists or server path style mismatch)
                        pass
                else:
                    # no known API available; continue best-effort
                    logger.debug("No mkdir API exposed on remote manager for %s", cur)
            except Exception as e:
                logger.debug("Failed to ensure remote dir %s: %s", cur, e)
        return True

    def _handle_upload_directory(self, local_path: str, remote_path: str) -> Dict:

        self._send(ActionTable.STREAM_FILE.value, {'status': 'start', 'message': f'Iniciando upload de diretório para {self.remote.name}: {self.remote.host}'})

        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath
        new_remote_path = local_path
        local_path = _join_path(self.io.server_side_path, local_path)
        remote_path = _join_path(self.io.remote_side_path, remote_path)
        # when composing remote paths, use appropriate path module
        remote_path = _join_path(remote_path, new_remote_path)
        def op():
            self._send(ActionTable.START_STREAM_FILE.value, {'status': 'start'})
            local_dir = local_path
            remote_dir = remote_path

            verification = {
                'success': False,
                'missing_on_remote': [],
                'extra_on_remote': [],
                'size_mismatches': []
            }

            try:
                # build list of files to upload with relative paths and sizes
                files_to_upload = []
                total_bytes = 0
                for root, _, files in os.walk(local_dir):
                    for f in files:
                        abs_path = os.path.join(root, f)
                        rel_path = os.path.relpath(abs_path, start=local_dir).replace(os.sep, '/')
                        try:
                            size = os.path.getsize(abs_path)
                        except Exception:
                            size = None
                        files_to_upload.append((abs_path, rel_path, size))
                        if size:
                            total_bytes += int(size)

                total_files = len(files_to_upload)
                bytes_sent = 0
                files_done = 0

                # upload files one by one so progress can be reported
                upload_errors = []
                for abs_path, rel_path, size in sorted(files_to_upload, key=lambda x: x[1]):
                    # build remote target using server OS conventions (posix for linux, nt for windows)
                    # normalize rel_path to posix style for FTP listing compatibility
                    rel_norm = rel_path.replace('\\', '/').lstrip('/')
                    if getattr(self.io, 'server_os', 'linux') == 'linux':
                        remote_target = posixpath.join(remote_dir.rstrip('/'), rel_norm).lstrip('/')
                        remote_target = f"/{remote_target}" if not remote_target.startswith('/') else remote_target
                    else:
                        # windows style remote paths: avoid forcing leading '/'
                        remote_target = ntpath.join(remote_dir.rstrip('/').replace('/', '\\'), rel_norm.replace('/', '\\'))

                    # ensure parent folders exist on remote before uploading
                    parent_remote = posixpath.dirname(remote_target)
                    try:
                        self._ensure_remote_dirs(parent_remote)
                    except Exception:
                        logger.debug("Failed to create remote parent dirs for %s", parent_remote)

                    try:
                        # attempt per-file upload
                        success = self.remote.upload_file(abs_path, remote_target)
                        if isinstance(success, dict):
                            ok = success.get('success', False)
                        else:
                            ok = bool(success)
                        if not ok:
                            upload_errors.append({'file': rel_path, 'error': success})
                            upload_result = {'success': len(upload_errors) == 0,
                                             'status': 'error' if len(upload_errors) == 0 else 'error',
                                             'message': upload_errors}
                            self._send(ActionTable.FINISH_STREAM_FILE.value, upload_result)
                            break
                    except Exception as e:
                        upload_errors.append({'file': rel_path, 'error': str(e)})

                    # update counters
                    files_done += 1
                    if size:
                        bytes_sent += int(size)

                    # compute progress (bytes if available, else files)
                    if total_bytes > 0:
                        percent = int(bytes_sent * 100 / total_bytes)
                    else:
                        percent = int(files_done * 100 / total_files) if total_files > 0 else 100

                    # send progress update
                    progress_payload = {
                        'file': rel_path,
                        'file_index': files_done,
                        'total_files': total_files,
                        'bytes_sent': bytes_sent,
                        'total_bytes': total_bytes,
                        'percent': percent
                    }
                    try:
                        self._send(ActionTable.PROGRESS_SEND_FILE.value, progress_payload)
                    except Exception:
                        logger.debug("Failed to send progress update for %s", rel_path)

                # if there were upload errors, still attempt verification but mark upload_result accordingly
                upload_result = {'success': len(upload_errors) == 0, 'status': 'done' if len(upload_errors) == 0 else 'error', 'message': upload_errors}
                self._send(ActionTable.FINISH_STREAM_FILE.value, upload_result)

                # perform verification as before
                # build local file map: relative_path -> size
                local_map = {}
                for root, _, files in os.walk(local_dir):
                    for f in files:
                        abs_path = os.path.join(root, f)
                        rel_path = os.path.relpath(abs_path, start=local_dir).replace(os.sep, '/')
                        try:
                            size = os.path.getsize(abs_path)
                        except Exception:
                            size = None
                        local_map[rel_path] = size

                # build remote file map by recursively listing remote directories
                remote_map = {}
                base_remote = (remote_dir or '/').rstrip('/')
                if base_remote == '':
                    base_remote = '/'

                # traverse remote tree and collect files using same relative-key scheme used when building files_to_download
                queue_remote = [(base_remote, '')]
                while queue_remote:
                    cur_remote, rel_prefix = queue_remote.pop()
                    entries = self.remote.list_remote(cur_remote) or []
                    for e in entries:
                        name = e.get('name') if isinstance(e, dict) else None
                        if not name:
                            name = e.get('filename') or e.get('path') or ''
                        is_dir = False
                        if isinstance(e, dict):
                            try:
                                is_dir = bool(e.get('is_dir'))
                            except Exception:
                                is_dir = False

                        child_rel = f"{rel_prefix}/{name}".lstrip('/') if rel_prefix else name

                        if is_dir:
                            child_remote = posixpath.join(cur_remote.rstrip('/'), name) if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath.join(cur_remote.rstrip('/').replace('/', '\\'), name.replace('/', '\\'))
                            queue_remote.append((child_remote, child_rel))
                        else:
                            # regular file: add to remote map keyed by relative path
                            try:
                                remote_map[child_rel] = e.get('size')
                            except Exception:
                                remote_map[child_rel] = None

                # compare local and remote file maps for verification
                for rel_path, size in local_map.items():
                    remote_size = remote_map.get(rel_path, None)
                    if remote_size is None:
                        # file is missing on remote
                        verification['missing_on_remote'].append(rel_path)
                    elif remote_size != size:
                        # size mismatch
                        verification['size_mismatches'].append((rel_path, size, remote_size))

                # send verification result
                verification['success'] = True
                verification['message'] = f'Iniciando upload de arquivo para {self.remote.name}: {self.remote.host}'
                verification['status'] = 'done'
                self._send(ActionTable.FINISH_STREAM_FILE.value, verification)

            except Exception as e:
                logger.exception("Error processing directory upload")
                self._send(ActionTable.FINISH_STREAM_FILE.value, {'success': False, 'error': str(e)})

        result = self._with_ftp(op)
        print(f"Upload directory result: {result}", flush=True)

        return self._send(ActionTable.STREAM_DIRECTORY.value, result)

    # --- file and directory handling ------------------------------------------------

    def _handle_list_directory(self, remote_path: str, path: str) -> List[Dict[str, Any]]:
        """
        List files and directories in the given remote path.
        """
        try:
            _remote_path = _join_path(remote_path, path)
            entries = self.remote.list_remote(_remote_path) or []
            result = []
            for e in entries:
                name = e.get('name') if isinstance(e, dict) else None
                if not name:
                    name = e.get('filename') or e.get('path') or ''
                # normalize directory flag safely
                is_dir = False
                if isinstance(e, dict):
                    try:
                        is_dir = bool(e.get('is_dir'))
                    except Exception:
                        is_dir = False

                result.append({
                    'name': name,
                    'is_dir': is_dir,
                    'size': e.get('size') or (0 if is_dir else None),
                    'modified': e.get('modified') or (0 if is_dir else None)
                })
            return result
        except Exception as e:
            logger.error(f"Erro ao listar diretório remoto {path}: {e}")
            return []

    def _handle_download_file(self, local_path: str, remote_path: str) -> Dict:
        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath

        # preserve original inputs (passed by caller) to derive safe folder names
        original_local_input = (local_path or '').rstrip('/\\')
        original_remote_input = (remote_path or '').rstrip('/\\')

        # compute safe base names (remove any path separators so we get a single name)
        local_base = os.path.basename(original_local_input) if original_local_input else ''
        remote_base = os.path.basename(original_remote_input.replace('\\', '/')) if original_remote_input else ''

        # build timestamp string DDMMYYYY_HHMMSS
        timestamp = datetime.now().strftime('%d%m%Y_%H%M%S')

        folder_name = f"download_{timestamp}_{remote_base}" if remote_base else f"download_{timestamp}"

        # ensure folder_name contains no slashes
        folder_name = folder_name.replace('/', '_').replace('\\', '_')

        # final local_path is server_side_path joined with the composed folder_name
        local_path = os.path.join(self.io.server_side_path.rstrip('/\\'), folder_name)
        print(f"Local path to save: {local_path}", flush=True)

        remote_path = _join_path(self.io.remote_side_path, remote_path)
        print(f"Remote path to download: {remote_path}", flush=True)

        def op():
            # ensure remote file exists
            # perform the download
            result = self.remote.download_file(remote_path, local_path)
            logger.info(f"SFTP Remote path to download file: {remote_path} with result: {result}")
            if isinstance(result, dict):
                return result
            return {'success': bool(result)}

        result = self._with_ftp(op)
        if result and result.get('success'):
            return self._send(ActionTable.DOWNLOAD_FILE.value, {'status': 'done'})

        return result

    def _handle_download_directory(self, local_path: str, remote_path: str) -> Dict:
        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath

        # preserve original inputs (passed by caller) to derive safe folder names
        original_local_input = (local_path or '').rstrip('/\\')
        original_remote_input = (remote_path or '').rstrip('/\\')

        # compute safe base names (remove any path separators so we get a single name)
        local_base = os.path.basename(original_local_input) if original_local_input else ''
        remote_base = os.path.basename(original_remote_input.replace('\\', '/')) if original_remote_input else ''

        # build timestamp string DDMMYYYY_HHMMSS
        timestamp = datetime.now().strftime('%d%m%Y_%H%M%S')

        folder_name = f"download_{timestamp}_{remote_base}" if remote_base else f"download_{timestamp}"

        # ensure folder_name contains no slashes
        folder_name = folder_name.replace('/', '_').replace('\\', '_')

        # final local_path is server_side_path joined with the composed folder_name
        local_path = os.path.join(self.io.server_side_path.rstrip('/\\'), folder_name)
        print(f"Local path to save: {local_path}", flush=True)

        remote_path = _join_path(self.io.remote_side_path, remote_path)
        print(f"Remote path to download: {remote_path}", flush=True)

        def op():
            self._send(ActionTable.START_STREAM_FILE.value, {'status': 'start'})
            local_dir = local_path
            remote_dir = remote_path

            verification = {
                'success': False,
                'missing_on_remote': [],
                'extra_on_remote': [],
                'size_mismatches': []
            }

            try:
                # build list of files to download by traversing the remote_dir
                files_to_download = []
                total_bytes = 0
                # queue entries are tuples (remote_path, rel_prefix)
                queue_remote = [(remote_dir.rstrip('/') if remote_dir else '/', '')]
                while queue_remote:
                    cur_remote, rel_prefix = queue_remote.pop()
                    entries = self.remote.list_remote(cur_remote) or []
                    for e in entries:
                        name = e.get('name') if isinstance(e, dict) else None
                        if not name:
                            name = e.get('filename') or e.get('path') or ''
                        # normalize directory flag safely
                        is_dir = False
                        if isinstance(e, dict):
                            try:
                                is_dir = bool(e.get('is_dir'))
                            except Exception:
                                is_dir = False

                        # build relative path under the base remote_dir
                        child_rel = f"{rel_prefix}/{name}".lstrip('/') if rel_prefix else name

                        if is_dir:
                            # compute child remote path and enqueue
                            child_remote = posixpath.join(cur_remote.rstrip('/'), name) if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath.join(cur_remote.rstrip('/').replace('/', '\\'), name.replace('/', '\\'))
                            queue_remote.append((child_remote, child_rel))
                        else:
                            # file: prepare the local absolute path and collect size from remote listing if available
                            size = None
                            if isinstance(e, dict):
                                try:
                                    size = e.get('size')
                                except Exception:
                                    size = None
                            local_abs = os.path.join(local_dir, child_rel.replace('/', os.sep))
                            files_to_download.append((local_abs, child_rel, size))
                            if size:
                                try:
                                    total_bytes += int(size)
                                except Exception:
                                    pass

                total_files = len(files_to_download)
                bytes_received = 0
                files_done = 0

                # download files one by one so progress can be reported
                download_errors = []
                for abs_path, rel_path, size in sorted(files_to_download, key=lambda x: x[1]):
                    # build remote source using server OS conventions (posix for linux, nt for windows)
                    # normalize rel_path to posix style for FTP listing compatibility
                    rel_norm = rel_path.replace('\\', '/').lstrip('/')
                    if getattr(self.io, 'server_os', 'linux') == 'linux':
                        remote_source = posixpath.join(remote_dir.rstrip('/'), rel_norm).lstrip('/')
                        remote_source = f"/{remote_source}" if not remote_source.startswith('/') else remote_source
                    else:
                        # windows style remote paths: avoid forcing leading '/'
                        remote_source = ntpath.join(remote_dir.rstrip('/').replace('/', '\\'), rel_norm.replace('/', '\\'))

                    try:
                        # ensure parent folders exist locally before downloading
                        parent_local = os.path.dirname(abs_path)
                        os.makedirs(parent_local, exist_ok=True)
                    except Exception:
                        logger.debug("Failed to create local parent dirs for %s", abs_path)

                    try:
                        # attempt per-file download
                        success = self.remote.download_file(remote_source, abs_path)
                        if isinstance(success, dict):
                            ok = success.get('success', False)
                        else:
                            ok = bool(success)
                        if not ok:
                            download_errors.append({'file': rel_path, 'error': success})
                    except Exception as e:
                        download_errors.append({'file': rel_path, 'error': str(e)})

                    # update counters
                    files_done += 1
                    if size:
                        bytes_received += int(size)

                    # compute progress (bytes if available, else files)
                    if total_bytes > 0:
                        percent = int(bytes_received * 100 / total_bytes)
                    else:
                        percent = int(files_done * 100 / total_files) if total_files > 0 else 100

                    # send progress update
                    progress_payload = {
                        'file': rel_path,
                        'file_index': files_done,
                        'total_files': total_files,
                        'bytes_received': bytes_received,
                        'total_bytes': total_bytes,
                        'percent': percent
                    }
                    try:
                        self._send(ActionTable.PROGRESS_SEND_FILE.value, progress_payload)
                    except Exception:
                        logger.debug("Failed to send progress update for %s", rel_path)

                # if there were download errors, still attempt verification but mark download_result accordingly
                download_result = {'success': len(download_errors) == 0, 'errors': download_errors}
                self._send(ActionTable.FINISH_STREAM_FILE.value, download_result)

                # perform verification as before
                # build local file map: relative_path -> size
                local_map = {}
                for root, _, files in os.walk(local_dir):
                    for f in files:
                        abs_path = os.path.join(root, f)
                        rel_path = os.path.relpath(abs_path, start=local_dir).replace(os.sep, '/')
                        try:
                            size = os.path.getsize(abs_path)
                        except Exception:
                            size = None
                        local_map[rel_path] = size

                # build remote file map by recursively listing remote directories
                remote_map = {}
                base_remote = (remote_dir or '/').rstrip('/')
                if base_remote == '':
                    base_remote = '/'

                # traverse remote tree and collect files using same relative-key scheme used when building files_to_download
                queue_remote = [(base_remote, '')]
                while queue_remote:
                    cur_remote, rel_prefix = queue_remote.pop()
                    entries = self.remote.list_remote(cur_remote) or []
                    for e in entries:
                        name = e.get('name') if isinstance(e, dict) else None
                        if not name:
                            name = e.get('filename') or e.get('path') or ''
                        is_dir = False
                        if isinstance(e, dict):
                            try:
                                is_dir = bool(e.get('is_dir'))
                            except Exception:
                                is_dir = False

                        child_rel = f"{rel_prefix}/{name}".lstrip('/') if rel_prefix else name

                        if is_dir:
                            child_remote = posixpath.join(cur_remote.rstrip('/'), name) if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath.join(cur_remote.rstrip('/').replace('/', '\\'), name.replace('/', '\\'))
                            queue_remote.append((child_remote, child_rel))
                        else:
                            # regular file: add to remote map keyed by relative path
                            try:
                                remote_map[child_rel] = e.get('size')
                            except Exception:
                                remote_map[child_rel] = None

                # compare local and remote file maps for verification
                for rel_path, size in local_map.items():
                    remote_size = remote_map.get(rel_path, None)
                    if remote_size is None:
                        # file is missing on remote
                        verification['missing_on_remote'].append(rel_path)
                    elif remote_size != size:
                        # size mismatch
                        verification['size_mismatches'].append((rel_path, size, remote_size))

                # send verification result
                verification['success'] = True

                return verification

            except Exception as e:
                logger.exception("Error processing directory download")
                self._send(ActionTable.DOWNLOAD_FILE.value, {'success': False, 'error': str(e)})
                return verification

        result = self._with_ftp(op)
        if result and result.get('success'):
            return self._send(ActionTable.DOWNLOAD_FILE.value, {'status': 'done'})

        return result

    def _handle_delete_remote_file(self, remote_path: str) -> Dict:
        remote_path = _join_path(self.io.remote_side_path, remote_path)
        def op():
            # attempt to delete the remote file
            result = self.remote.delete_file(remote_path)
            if isinstance(result, dict):
                return result
            return {'success': bool(result)}

        result = self._with_ftp(op)
        if result and result.get('success'):
            return self._send(ActionTable.DELETE_REMOTE_FILE.value, {'status': 'done'})

        return result

    def _handle_delete_remote_directory(self, remote_path: str) -> Dict:
        remote_path = _join_path(self.io.remote_side_path, remote_path)
        def op():
            # attempt to delete the remote directory and report progress
            def progress_cb(info: Dict[str, Any]):
                # info contains 'path', 'type', 'status', optional 'error'
                try:
                    payload = {
                        'path': info.get('path', ''),
                        'type': info.get('type', ''),
                        'status': info.get('status', ''),
                        'error': info.get('error', None)
                    }
                    # keep compatibility with existing progress payload shape used elsewhere
                    progress_payload = {
                        'file': payload['path'],
                        'file_type': payload['type'],
                        'status': payload['status'],
                        'error': payload['error']
                    }
                    self._send(ActionTable.DELETE_REMOTE_DIRECTORY.value, progress_payload)
                except Exception:
                    logger.debug("Failed to send delete progress for %s", info)

            logger.info(f"Starting deletion of remote directory: {remote_path}")
            result = self.remote.delete_directory(remote_path, progress_cb=progress_cb)
            logger.info(f"Finished deletion of remote directory: {remote_path}")
            if isinstance(result, dict):
                return result
            return {'success': bool(result)}

        result = self._with_ftp(op)
        if result and result.get('success'):
            return self._send(ActionTable.DELETE_REMOTE_DIRECTORY.value, {'status': 'done'})

        return result

    def _handle_create_file_from_template(self, name: str, local_path: str, remote_path: str) -> tuple:
        """Create a new file from a JSON template, set programa.name, and upload to FTP.

        Args:
            name: value to write into template["programa"]["name"]
            local_path: path (relative to server_side_path) to the JSON template file
            remote_path: destination path (relative to remote_side_path) on the FTP server
        Returns:
            (bool, str) tuple with success flag and message
        """
        import tempfile
        import posixpath

        # resolve template file on the local server
        template_abs = os.path.join(
            self.io.server_side_path.rstrip('/\\'),
            (local_path or '').lstrip('/\\')
        )

        if not os.path.isfile(template_abs):
            msg = f"Template file not found: {template_abs}"
            logger.error(msg)
            return False, msg

        # read and parse the JSON template
        try:
            with open(template_abs, 'r', encoding='utf-8') as f:
                template_data = json.load(f)
        except Exception as e:
            msg = f"Failed to read/parse template JSON: {e}"
            logger.error(msg)
            return False, msg

        # set programa.name in the template
        if not isinstance(template_data, dict):
            msg = "Template JSON root is not a dict"
            logger.error(msg)
            return False, msg

        # Navigate to RemoteIO (list of objects), each containing a 'program' list.
        # Find the program object with port == 2 and replace its name.
        remote_io_list = template_data.get('RemoteIO')
        if not isinstance(remote_io_list, list):
            msg = "Template JSON 'RemoteIO' is not a list"
            logger.error(msg)
            return False, msg

        target_found = False
        for rio in remote_io_list:
            if not isinstance(rio, dict):
                continue
            program_list = rio.get('program')
            if not isinstance(program_list, list):
                continue
            for prog in program_list:
                if isinstance(prog, dict) and prog.get('port') == 2:
                    prog['name'] = name
                    target_found = True
                    break
            if target_found:
                break

        if not target_found:
            msg = "No program object with port == 2 found in RemoteIO[].program[]"
            logger.error(msg)
            return False, msg

        # write the modified JSON to a temporary file
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='template_')
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmp_f:
                json.dump(template_data, tmp_f, ensure_ascii=False, indent=2)
        except Exception as e:
            msg = f"Failed to write temporary file: {e}"
            logger.error(msg)
            return False, msg

        # build the remote destination path
        # use template filename as the uploaded filename
        template_filename = os.path.basename(template_abs)
        remote_dest = _join_path(self.io.remote_side_path, remote_path)
        remote_dest = posixpath.join(remote_dest.rstrip('/'), template_filename)

        def op():
            try:
                # ensure remote directory exists
                parent_remote = posixpath.dirname(remote_dest)
                try:
                    self._ensure_remote_dirs(parent_remote)
                except Exception:
                    logger.debug("Failed to ensure remote dirs for %s", parent_remote)

                # upload the file
                result = self.remote.upload_file(tmp_path, remote_dest)
                if isinstance(result, dict):
                    ok = result.get('success', False)
                else:
                    ok = bool(result)

                if ok:
                    logger.info("Template file uploaded to %s with programa.name='%s'", remote_dest, name)
                    return {"success": True, "message": f"Arquivo enviado para: {remote_dest}, para {self.name}: {self.host}"}
                else:
                    msg = f"Envio falhou: {result}, para {self.name}: {self.host}"
                    logger.error(msg)
                    return {'success': False, 'message': msg}
            except Exception as e:
                logger.exception("Error uploading template file")
                return {'success': False, 'message': f"Erro ao enviar arquivo: {e}, para {self.name}: {self.host}"}
            finally:
                # clean up temp file
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            return {'success': False, 'message': f'{self.name}: {self.host}'}

        result = self._with_ftp(op)

        if result['success']:
            result['status'] = 'done'
            return self._send(ActionTable.STREAM_FILE.value, result)
        result['status'] = 'error'
        return self._send(ActionTable.STREAM_FILE.value, result)

    def _handle_remote_reboot(self) -> tuple:
        """Send 'sudo reboot' to the remote server via SSH.

        Only supported on SSH-based protocols (scp / sftp).
        Assumes the remote user has passwordless sudo for the reboot command.
        Returns (bool, str) tuple.
        """
        proto = (self.protocol or '').lower()
        if proto not in ('scp', 'sftp'):
            msg = "Reboot only supported on SSH-based protocols (scp/sftp)"
            logger.warning(msg)
            return False, msg

        def op():
            logger.info("Sending 'sudo reboot' to %s:%s", self.host, self.port)
            success, output = self.remote.exec_command("sudo reboot", sudo_password=self.password)
            if success:
                logger.info("Reboot command accepted by %s:%s", self.host, self.port)
            else:
                logger.error("Reboot command failed on %s:%s -> %s", self.host, self.port, output)
            return success, output

        result = self._with_ftp(op)
        # _with_ftp may wrap exceptions into {'success': False, 'error': ...}
        if isinstance(result, dict):
            return result.get('success', False), result.get('error', 'unknown error')
        return result

    # --- file and directory streaming ------------------------------------------------

    def _stream_file(self, local_path: str, remote_path: str, is_upload: bool = True) -> Dict:
        """
        Stream a file to or from the remote server.
        """
        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath
        new_remote_path = local_path
        local_path = _join_path(self.io.server_side_path, local_path)
        remote_path = _join_path(self.io.remote_side_path, remote_path)
        # when composing remote paths, use appropriate path module
        remote_path = _join_path(remote_path, new_remote_path)
        def op():
            self._send(ActionTable.START_STREAM_FILE.value, {'status': 'start'})
            local_file = local_path
            remote_file = remote_path

            verification = {
                'success': False,
                'missing_on_remote': [],
                'extra_on_remote': [],
                'size_mismatches': []
            }

            try:
                if is_upload:
                    # Upload: ensure remote file does not already exist
                    exists = False
                    try:
                        remote_info = self.remote.stat(remote_file)
                        exists = remote_info.get('size') is not None
                    except Exception:
                        pass

                    if exists:
                        return {'success': False, 'error': 'Arquivo remoto já existe'}

                    # perform the upload
                    result = self.remote.upload_file(local_file, remote_file)
                    if isinstance(result, dict):
                        return result
                    return {'success': bool(result)}
                else:
                    # Download: ensure local file does not already exist
                    exists = os.path.exists(local_file)

                    if exists:
                        return {'success': False, 'error': 'Arquivo local já existe'}

                    # perform the download
                    result = self.remote.download_file(remote_file, local_file)
                    if isinstance(result, dict):
                        return result
                    return {'success': bool(result)}
            except Exception as e:
                logger.exception("Error streaming file")
                return {'success': False, 'error': str(e)}

        result = self._with_ftp(op)
        if result and result.get('success'):
            return self._send(ActionTable.FINISH_STREAM_FILE.value, {'status': 'done', 'message': f'Finalizado upload de arquivo para {self.remote.name}: {self.remote.host}'})
        print(f"Upload directory result: {result}", flush=True)
        return result

    def _handle_upload_file(self, local_path: str, remote_path: str) -> Dict:
        self._send(ActionTable.STREAM_FILE.value, {'status': 'start',
                                                   'message': f'Iniciando upload de arquivo para {self.remote.name}: {self.remote.host}'})
        return self._stream_file(local_path, remote_path, is_upload=True)

    def _handle_download_file_stream(self, local_path: str, remote_path: str) -> Dict:
        import posixpath
        import ntpath
        path_mod = posixpath if getattr(self.io, 'server_os', 'linux') == 'linux' else ntpath

        # preserve original inputs (passed by caller) to derive safe folder names
        original_local_input = (local_path or '').rstrip('/\\')
        original_remote_input = (remote_path or '').rstrip('/\\')

        # compute safe base names (remove any path separators so we get a single name)
        local_base = os.path.basename(original_local_input) if original_local_input else ''
        remote_base = os.path.basename(original_remote_input.replace('\\', '/')) if original_remote_input else ''

        # build timestamp string DDMMYYYY_HHMMSS
        timestamp = datetime.now().strftime('%d%m%Y_%H%M%S')

        # compose folder name: prefer local_base; if empty, use 'download'
        if local_base:
            folder_name = f"{local_base}_{timestamp}_{remote_base}" if remote_base else f"{local_base}_{timestamp}"
        else:
            folder_name = f"download_{timestamp}_{remote_base}" if remote_base else f"download_{timestamp}"

        # ensure folder_name contains no slashes
        folder_name = folder_name.replace('/', '_').replace('\\', '_')

        # final local_path is server_side_path joined with the composed folder_name
        local_path = os.path.join(self.io.server_side_path.rstrip('/\\'), folder_name)
        print(f"Local path to save: {local_path}", flush=True)

        remote_path = _join_path(self.io.remote_side_path, remote_path)
        print(f"Remote path to download: {remote_path}", flush=True)

        result = self.remote.download_file(remote_path, local_path)

        if result and result.get('success'):
            return self._send(ActionTable.DOWNLOAD_FILE.value, {'status': 'done'})

        return result

    # --- server file tree handling ------------------------------------------------

    def _handle_server_file_tree(self, path: str) -> List[Dict[str, Any]]:
        """
        Build the file tree structure for the server side.
        """
        result = []
        try:
            # start with the base server side path
            base_path = self.io.server_side_path.rstrip('/')
            if base_path == '':
                base_path = '/'

            # normalize and ensure base path exists
            try:
                self.remote.ensure_dir(base_path)
            except Exception:
                logger.debug("Base path may not exist on server: %s", base_path)

            # recursive helper to build the file tree
            def _build_tree(cur_path: str):
                entries = self.remote.list_remote(cur_path) or []
                for e in entries:
                    name = e.get('name') if isinstance(e, dict) else None
                    if not name:
                        name = e.get('filename') or e.get('path') or ''
                    # normalize directory flag safely
                    is_dir = False
                    if isinstance(e, dict):
                        try:
                            is_dir = bool(e.get('is_dir'))
                        except Exception:
                            is_dir = False

                    rel_path = name
                    if not is_dir:
                        # regular file: add to tree
                        result.append({
                            'path': cur_path + '/' + rel_path,
                            'is_dir': is_dir,
                            'size': e.get('size') or 0,
                            'modified': e.get('modified') or 0
                        })
                    else:
                        # directory: recurse
                        _build_tree(cur_path + '/' + rel_path)

            # build the tree starting from the base path
            _build_tree(base_path)

            # remove base path prefix from results
            base_path_len = len(base_path)
            for r in result:
                r['path'] = r['path'][base_path_len:].lstrip('/')

        except Exception as e:
            logger.error(f"Erro ao construir árvore de arquivos do servidor: {e}")

        return result

    # --- remote file handling ------------------------------------------------

    def _handle_remote_file_tree(self, path: str) -> List[Dict[str, Any]]:
        """
        Build the file tree structure for the remote side.
        """
        result = []
        try:
            # start with the base remote side path
            base_path = self.io.remote_side_path.rstrip('/')
            if base_path == '':
                base_path = '/'

            # normalize and ensure base path exists
            try:
                self.remote.ensure_dir(base_path)
            except Exception:
                logger.debug("Base path may not exist on remote: %s", base_path)

            # recursive helper to build the file tree
            def _build_tree(cur_path: str):
                entries = self.remote.list_remote(cur_path) or []
                for e in entries:
                    name = e.get('name') if isinstance(e, dict) else None
                    if not name:
                        name = e.get('filename') or e.get('path') or ''
                    # normalize directory flag safely
                    is_dir = False
                    if isinstance(e, dict):
                        try:
                            is_dir = bool(e.get('is_dir'))
                        except Exception:
                            is_dir = False

                    rel_path = name
                    if not is_dir:
                        # regular file: add to tree
                        result.append({
                            'path': cur_path + '/' + rel_path,
                            'is_dir': is_dir,
                            'size': e.get('size') or 0,
                            'modified': e.get('modified') or 0
                        })
                    else:
                        # directory: recurse
                        _build_tree(cur_path + '/' + rel_path)

            # build the tree starting from the base path
            _build_tree(base_path)

            # remove base path prefix from results
            base_path_len = len(base_path)
            for r in result:
                r['path'] = r['path'][base_path_len:].lstrip('/')

        except Exception as e:
            logger.error(f"Erro ao construir árvore de arquivos remoto: {e}")

        return result
