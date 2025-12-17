import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from ftp_manager import FTPManager, normalize_path
from scp_manager import SCPManager
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
    def __init__(self, config: Dict[str, Any], io_config: Dict[str, Any], send_message_callback, command_queue: Queue, config_manager):
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 21)
        self.user = config.get('user', 'anonymous')
        self.password = config.get('password', '')
        self.passive = config.get('passive', True)
        self.timeout = config.get('timeout', 30)
        self.protocol = config.get('protocol', 'ftp')
        self.remote = None
        self.command_queue = command_queue

        if self.protocol.lower() == 'scp':
            self.remote = SCPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout
            )
        else:
            self.remote = FTPManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                timeout=self.timeout
            )

        self.io: IO = IO(io_config.get('GENERIC_FILE_TRANSFER', {}))

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

    def _send(self, action, value=None):
        resp = self._build_response(action, value)
        self.send_message(resp, f"recv_queue_index_{str(self.get_index())}")
        return resp

    # Wrapper that ensures FTP connection and disconnect
    def _with_ftp(self, func, *args, **kwargs):
        if not self.remote.connect():
            return {'success': False, 'error': 'Falha ao conectar FTP'}
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception("FTP operation failed: %s", e)
            return {'success': False, 'error': str(e)}
        finally:
            try:
                self.remote.disconnect()
            except Exception:
                pass

    # --- message processing -----------------------------------------------------

    def process_message(self, ch, method, properties, body):
        try:
            message = json.loads(body)
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
                result = self._handle_list_directory(path)
                response['action'] = ActionTable.CLIENT_FILE_TREE.value
            elif action == ActionTable.STREAM_DIRECTORY.value:
                data = message.get('data', {})
                value = data.get('value', {})
                local_path = value.get('local_path', '')
                remote_path = value.get('remote_path', '')
                result = self._handle_upload_directory(local_path, remote_path)
            elif action == ActionTable.STREAM_FILE.value:
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
                result = self._handle_download_directory(local_path, remote_path)
            elif action == ActionTable.DELETE_REMOTE_FILE.value:
                data = message.get('data', {})
                value = data.get('value', {})
                remote_path = value.get('remote_path', '')
                result = self._handle_delete_remote_file(remote_path)
            elif action == ActionTable.DELETE_REMOTE_DIRECTORY.value:
                data = message.get('data', {})
                value = data.get('value', {})
                remote_path = value.get('remote_path', '')
                result = self._handle_delete_remote_directory(remote_path)
            else:
                response['action'] = ActionTable.ERROR.value
                result = f"Comando desconhecido: {action}"

            response['data']['value'] = result

            value_for_log = response['data']['value']
            if not isinstance(value_for_log, (str, bytes, int, float, type(None))):
                try:
                    value_for_log = json.dumps(value_for_log, default=str)
                except Exception:
                    value_for_log = str(value_for_log)

            self.send_message(response, f"recv_queue_index_{str(self.get_index())}")
            ch.basic_ack(delivery_tag=method.delivery_tag)

            self.config_manager.log_operation(
                response['action'],
                value_for_log,
                json.dumps(response)
            )

        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def _ensure_remote_dirs(self, remote_dir: str) -> bool:
        """
        Ensure remote_dir exists on the remote server.
        Tries several possible APIs on self.remote, then falls back to ftp.mkd.
        Best-effort: ignores errors for existing directories.
        """
        import posixpath

        if not remote_dir:
            return True
        # normalize and build path parts
        remote_dir = remote_dir.rstrip('/')
        parts = [p for p in remote_dir.split('/') if p]
        if not parts:
            return True

        cur = ''
        for part in parts:
            cur = posixpath.join(cur, part)
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
        import posixpath
        new_remote_path = local_path
        local_path = _join_path(self.io.server_side_path, local_path)
        remote_path = _join_path(self.io.remote_side_path, remote_path)
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
                    # build remote target path using posix style
                    remote_target = posixpath.join(remote_dir.rstrip('/'), rel_path).lstrip('/')
                    remote_target = f"/{remote_target}" if not remote_target.startswith('/') else remote_target

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
                upload_result = {'success': len(upload_errors) == 0, 'errors': upload_errors}
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

                queue = [base_remote]
                while queue:
                    cur_remote = queue.pop()
                    entries = self.remote.list_remote(cur_remote) or []
                    for e in entries:
                        name = e.get('name') if isinstance(e, dict) else None
                        if not name:
                            name = e.get('filename') or e.get('path') or ''
                        is_dir = bool(e.get('is_dir')) if isinstance(e, dict) else False
                        size = e.get('size') if isinstance(e, dict) else None

                        joined = posixpath.join(cur_remote.rstrip('/'), name)
                        rel_remote = posixpath.relpath(joined, base_remote).lstrip('./')
                        if rel_remote == '.':
                            rel_remote = ''
                        rel_remote = rel_remote.replace('\\', '/').lstrip('/')

                        if is_dir:
                            queue.append(joined)
                        else:
                            remote_map[rel_remote] = size

                # compare maps
                local_keys = set(local_map.keys())
                remote_keys = set(remote_map.keys())

                missing = sorted(list(local_keys - remote_keys))
                extra = sorted(list(remote_keys - local_keys))

                size_mismatches = []
                for key in sorted(local_keys & remote_keys):
                    lsize = local_map.get(key)
                    rsize = remote_map.get(key)
                    if lsize is None or rsize is None:
                        if lsize != rsize:
                            size_mismatches.append({'path': key, 'local_size': lsize, 'remote_size': rsize})
                    else:
                        if int(lsize) != int(rsize):
                            size_mismatches.append({'path': key, 'local_size': lsize, 'remote_size': rsize})

                verification['missing_on_remote'] = missing
                verification['extra_on_remote'] = extra
                verification['size_mismatches'] = size_mismatches
                verification['success'] = (len(missing) == 0 and len(size_mismatches) == 0 and len(upload_errors) == 0)

            except Exception as e:
                logger.exception("Upload directory failed: %s", e)
                verification['error'] = str(e)
                upload_result = {'success': False, 'error': str(e)}

            # return combined result (logged) but keep same return behavior as before
            result = {
                'upload_result': upload_result,
                'verification': verification
            }

            try:
                self.config_manager.log_operation(
                    "Upload Directory",
                    json.dumps(result['upload_result']),
                    json.dumps(result)
                )
            except Exception:
                logger.debug("Failed to log upload operation")

            logger.info(f"Upload directory result: {result}")
            return verification['success']

        return self._with_ftp(op)

    def _handle_upload_file(self, local_path: str, remote_path: str) -> Dict:
        import posixpath

        def op():
            self._send(ActionTable.START_STREAM_FILE.value)
            local_file = _join_path(self.io.server_side_path, local_path)
            filename = os.path.basename(local_file)

            remote_dir = _join_path(self.io.remote_side_path, remote_path)
            remote_file = _join_path(remote_dir, filename)

            # try to get total size for progress reporting
            try:
                total_bytes = os.path.getsize(local_file)
            except Exception:
                total_bytes = None

            # send initial progress (0%)
            try:
                init_payload = {
                    'file': filename,
                    'bytes_sent': 0,
                    'total_bytes': total_bytes,
                    'percent': 0
                }
                self._send(ActionTable.PROGRESS_SEND_FILE.value, init_payload)
            except Exception:
                logger.debug("Failed to send initial progress for %s", local_path)

            # ensure parent directory exists (NOT the file itself)
            try:
                parent_remote = posixpath.dirname(remote_file)
                self._ensure_remote_dirs(parent_remote)
            except Exception as e:
                logger.debug("Failed to create remote parent dirs for %s: %s", parent_remote, e)

            # perform upload
            try:
                success = self.remote.upload_file(local_file, remote_file)
            except Exception as e:
                logger.exception("Upload file failed: %s", e)
                success = {'success': False, 'error': str(e)}

            # send final progress (100%)
            try:
                final_bytes = total_bytes if isinstance(total_bytes, (int, float)) else None
                final_payload = {
                    'file': filename,
                    'bytes_sent': final_bytes,
                    'total_bytes': total_bytes,
                    'percent': 100
                }
                self._send(ActionTable.PROGRESS_SEND_FILE.value, final_payload)
            except Exception:
                logger.debug("Failed to send final progress for %s", local_path)

            self._send(ActionTable.FINISH_STREAM_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_download_directory(self, local_path: str, remote_path: str) -> Dict:
        import posixpath
        def op():
            self._send(ActionTable.START_DOWNLOAD_FILE.value)
            remote_dir = _join_path(self.io.remote_side_path, remote_path)
            timestamp = datetime.now().strftime("%H%M%S_%d%m%Y")
            local_dir = _join_path(self.io.server_side_path, local_path + f"_download_{timestamp}")
            logger.info(f"Downloading directory from {remote_dir} to {local_dir}")

            # perform download
            download_success = self.remote.download_directory(remote_dir, local_dir)
            self._send(ActionTable.FINISH_DOWNLOAD_FILE.value)

            verification = {
                'success': False,
                'missing_locally': [],
                'extra_locally': [],
                'size_mismatches': []
            }

            try:
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

                queue = [base_remote]
                while queue:
                    cur_remote = queue.pop()
                    entries = self.remote.list_remote(cur_remote) or []
                    for e in entries:
                        name = e.get('name') if isinstance(e, dict) else None
                        if not name:
                            name = e.get('filename') or e.get('path') or ''
                        is_dir = bool(e.get('is_dir')) if isinstance(e, dict) else False
                        size = e.get('size') if isinstance(e, dict) else None

                        joined = posixpath.join(cur_remote.rstrip('/'), name)
                        rel_remote = posixpath.relpath(joined, base_remote).lstrip('./')
                        if rel_remote == '.':
                            rel_remote = ''
                        rel_remote = rel_remote.replace('\\', '/').lstrip('/')

                        if is_dir:
                            queue.append(joined)
                        else:
                            remote_map[rel_remote] = size

                # compare maps (remote -> local)
                remote_keys = set(remote_map.keys())
                local_keys = set(local_map.keys())

                # files present on remote but missing locally
                missing_locally = sorted(list(remote_keys - local_keys))
                # files present locally but not on remote
                extra_locally = sorted(list(local_keys - remote_keys))

                size_mismatches = []
                for key in sorted(remote_keys & local_keys):
                    rsize = remote_map.get(key)
                    lsize = local_map.get(key)
                    if lsize is None or rsize is None:
                        if lsize != rsize:
                            size_mismatches.append({'path': key, 'local_size': lsize, 'remote_size': rsize})
                    else:
                        if int(lsize) != int(rsize):
                            size_mismatches.append({'path': key, 'local_size': lsize, 'remote_size': rsize})

                verification['missing_locally'] = missing_locally
                verification['extra_locally'] = extra_locally
                verification['size_mismatches'] = size_mismatches
                verification['success'] = (len(missing_locally) == 0 and len(size_mismatches) == 0)

            except Exception as e:
                logger.exception("Verification after download failed: %s", e)
                verification['error'] = str(e)

            result = {
                'download_result': download_success if isinstance(download_success, dict) else {'success': bool(download_success)},
                'verification': verification
            }

            self.config_manager.log_operation(
                "Download Directory",
                json.dumps(result['download_result']),
                json.dumps(result)
            )

            logger.info(f"Download directory result: {result}")
            return verification['success']

        return self._with_ftp(op)

    def _handle_download_file(self, local_path: str, remote_path: str) -> Dict:
        def op():
            self._send(ActionTable.START_DOWNLOAD_FILE.value)
            remote_file = _join_path(self.io.remote_side_path, remote_path)
            local_file = _join_path(self.io.server_side_path, local_path)
            logger.info(f"Downloading file from {remote_file} to {local_file}")
            success = self.remote.download_file(remote_file, local_file)
            self._send(ActionTable.FINISH_DOWNLOAD_FILE.value)
            return success

        return self._with_ftp(op)

    def _handle_delete_remote_file(self, remote_path: str) -> Dict:
        def op():
            # use FTPManager.delete_file (refactored name)
            success = self.remote.delete_file(remote_path)
            if isinstance(success, bool):
                return {'success': success}
            return success

        remote_path = _join_path(self.io.remote_side_path, remote_path)
        return self._with_ftp(op)

    def _handle_delete_remote_directory(self, remote_path: str) -> Dict:
        def op():
            success = self.remote.delete_remote_path(remote_path)
            if isinstance(success, bool):
                return {'success': success}
            return success

        remote_path = _join_path(self.io.remote_side_path, remote_path)
        return self._with_ftp(op)

    def _handle_list_directory(self, remote_path: str) -> List:
        def op():
            # join configured base path and requested remote path cleanly
            full_remote = _join_path(self.io.remote_side_path, remote_path)
            file_list = self.remote.list_remote(full_remote)
            return file_list

        return self._with_ftp(op)