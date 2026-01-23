# Simple SFTP-only manager using paramiko
from pathlib import Path
import os
import logging
from typing import Dict, Any, Optional
import paramiko

logger = logging.getLogger(__name__)

class SFTPManager:
    """Manager for pure SFTP operations (uses Paramiko SFTPClient)."""

    def __init__(self, host: str, port: int = 22, user: str = '', password: str = '', timeout: int = 30):
        self.host = host
        self.port = int(port) if port is not None else 22
        self.user = user
        self.password = password
        self.timeout = timeout
        self.key_filename = None
        self.ssh: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        # store last error to help diagnostics
        self._last_error: Optional[str] = None

    def test_socket_connect(self) -> bool:
        try:
            import socket
            # try to resolve host first to give clearer diagnostics
            try:
                infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
                logger.debug("Resolved %s -> %s", self.host, infos)
            except Exception as e:
                logger.debug("getaddrinfo failed for %s:%s -> %r", self.host, self.port, e)

            addr = (self.host, int(self.port))
            sock = socket.create_connection(addr, timeout=self.timeout)
            sock.close()
            self._last_error = None
            return True
        except Exception as e:
            err = repr(e)
            logger.debug("sftp socket test failed for %s:%s -> %s", self.host, self.port, err)
            self._last_error = f"socket_connect_failed: {err}"
            return False

    def connect(self) -> bool:
        # quick reachability check
        try:
            if not self.test_socket_connect():
                logger.error("SFTP connect error: cannot reach %s:%s (TCP connect failed) - %s", self.host, self.port, self._last_error)
                return False
        except Exception:
            pass

        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(hostname=self.host, port=self.port, username=self.user or None,
                             password=self.password if self.password else None,
                             key_filename=self.key_filename,
                             timeout=self.timeout,
                             look_for_keys=bool(self.key_filename is None))
            self.sftp = self.ssh.open_sftp()
            logger.info("SFTP connected: %s:%s", self.host, self.port)
            return True
        except Exception as e:
            err = repr(e)
            logger.error("SFTP connect error: %s", err)
            self._last_error = f"ssh_connect_failed: {err}"
            try:
                self.disconnect()
            except Exception:
                pass
            return False

    def disconnect(self) -> None:
        try:
            if self.sftp:
                try:
                    self.sftp.close()
                except Exception:
                    pass
                self.sftp = None
            if self.ssh:
                try:
                    self.ssh.close()
                except Exception:
                    pass
                self.ssh = None
            logger.info("SFTP disconnected: %s", self.host)
        except Exception:
            pass

    def _mkdir_remote_recursive(self, remote_path: str):
        # Create directories recursively. Preserve absolute vs relative.
        if not remote_path:
            return
        is_abs = remote_path.startswith('/')
        # split parts ignoring leading/trailing slashes
        parts = [p for p in remote_path.strip('/').split('/') if p]
        cur = '/' if is_abs else ''
        for p in parts:
            cur = f"{cur.rstrip('/')}/{p}" if cur else p
            try:
                self.sftp.mkdir(cur)
            except IOError:
                try:
                    self.sftp.stat(cur)
                except Exception:
                    pass
            except Exception:
                # ignore other errors (may already exist or permissions)
                pass

    def _normalize_remote(self, path: Optional[str]) -> str:
        """Normalize remote path to posix style and remove duplicate slashes; keep leading slash if present."""
        if not path:
            return ''
        p = path.replace('\\', '/')
        # collapse multiple slashes
        while '//' in p:
            p = p.replace('//', '/')
        # strip trailing slash except root
        if len(p) > 1 and p.endswith('/'):
            p = p.rstrip('/')
        return p

    # Make upload_file return dict similar to FTPManager.upload_file
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        if not self.sftp:
            return {'success': False, 'error': 'Not connected'}
        remote = self._normalize_remote(remote_path)
        if not remote:
            logger.error("upload_file: empty remote path")
            return {'success': False, 'error': 'empty remote path'}
        if not os.path.isfile(local_path):
            logger.error("upload_file: local file not found: %s", local_path)
            return {'success': False, 'error': 'local file not found'}
        try:
            try:
                # try direct put
                self.sftp.put(local_path, remote)
                logger.info("SFTP: uploaded %s -> %s", local_path, remote)
                return {'success': True}
            except Exception as first_exc:
                logger.debug("Direct sftp.put failed for %s -> %s: %s", local_path, remote, first_exc)
                parent = os.path.dirname(remote)
                name = os.path.basename(remote)
                if parent:
                    try:
                        # create parent dirs and retry
                        self._mkdir_remote_recursive(parent)
                    except Exception:
                        logger.debug("Failed to create remote parent dirs for %s (ignored)", parent)
                try:
                    # retry put after ensuring dirs
                    self.sftp.put(local_path, remote)
                    logger.info("SFTP: uploaded %s -> %s (after mkdir)", local_path, remote)
                    return {'success': True}
                except Exception as second_exc:
                    logger.error("upload_file fallback failed for %s -> %s: %s", local_path, remote, second_exc)
                    return {'success': False, 'error': str(second_exc)}
        except Exception as e:
            logger.exception("upload_file error: %s", e)
            return {'success': False, 'error': str(e)}

    def download_file(self, remote_path: str, local_path: str) -> bool:
        # Return signature similar to FTPManager.download_file: (bool, message)
        if not self.sftp:
            return False, "Not connected"
        remote = self._normalize_remote(remote_path)
        local = local_path
        try:
            os.makedirs(os.path.dirname(local) or '.', exist_ok=True)
            try:
                self.sftp.get(remote, local)
            except Exception:
                # fallback: try retrieving by basename after checking parent
                parent = os.path.dirname(remote)
                name = os.path.basename(remote)
                try:
                    # attempt to list parent to see if exists
                    self.sftp.listdir(parent)
                    self.sftp.get(f"{parent}/{name}", local)
                except Exception as inner:
                    logger.error("SFTP download fallback failed for %s: %s", remote, inner)
                    return False, str(inner)
            logger.info("SFTP: downloaded %s -> %s", remote, local)
            return True, ""
        except Exception as e:
            logger.error("SFTP download_file error: %s", e)
            return False, f"download_file error: {e}"

    def upload_directory(self, local_dir: str, remote_dir: str) -> bool:
        if not self.sftp:
            return False
        try:
            logger.info("SFTP: uploading directory %s -> %s", local_dir, remote_dir)
            base = Path(local_dir)
            if not base.exists():
                logger.error("upload_directory: local dir not found: %s", local_dir)
                return False
            remote_base = self._normalize_remote(remote_dir or '')
            # ensure base dir exists remotely
            if remote_base:
                try:
                    self._mkdir_remote_recursive(remote_base)
                except Exception:
                    pass
            for item in base.rglob('*'):
                if item.is_file():
                    rel = item.relative_to(base)
                    remote_file = f"{remote_base}/{rel.as_posix()}" if remote_base else rel.as_posix()
                    res = self.upload_file(str(item), remote_file)
                    # upload_file returns dict
                    ok = False
                    if isinstance(res, dict):
                        ok = res.get('success', False)
                    else:
                        ok = bool(res)
                    if not ok:
                        logger.warning("Failed to upload %s -> %s (continuing)", item, remote_file)
            logger.info("SFTP: uploaded directory %s -> %s", local_dir, remote_dir)
            return True
        except Exception as e:
            logger.error("SFTP upload_directory error: %s", e)
            return False

    def download_directory(self, remote_dir: str, local_dir: str) -> bool:
        if not self.sftp:
            return False
        try:
            os.makedirs(local_dir, exist_ok=True)
            self._download_recursive(self._normalize_remote(remote_dir), local_dir)
            logger.info("SFTP: directory downloaded %s -> %s", remote_dir, local_dir)
            return True
        except Exception as e:
            logger.error("SFTP download_directory error: %s", e)
            return False

    def _download_recursive(self, remote_dir: str, local_dir: str):
        try:
            for attr in self.sftp.listdir_attr(remote_dir):
                name = attr.filename
                if name in ('.', '..'):
                    continue
                remote_path = f"{remote_dir}/{name}"
                local_path = os.path.join(local_dir, name)
                # detect dir
                is_dir = False
                try:
                    import stat as _stat
                    is_dir = _stat.S_ISDIR(attr.st_mode)
                except Exception:
                    try:
                        is_dir = getattr(attr, 'longname', '').startswith('d')
                    except Exception:
                        is_dir = False
                if is_dir:
                    os.makedirs(local_path, exist_ok=True)
                    self._download_recursive(remote_path, local_path)
                else:
                    self.sftp.get(remote_path, local_path)
        except Exception:
            try:
                entries = self.sftp.listdir(remote_dir)
                for name in entries:
                    if name in ('.', '..'):
                        continue
                    remote_path = f"{remote_dir}/{name}"
                    local_path = os.path.join(local_dir, name)
                    try:
                        # directory?
                        self.sftp.listdir(remote_path)
                        os.makedirs(local_path, exist_ok=True)
                        self._download_recursive(remote_path, local_path)
                    except Exception:
                        self.sftp.get(remote_path, local_path)
            except Exception as e:
                logger.error("SFTP _download_recursive error: %s", e)

    def list_remote(self, remote_path: str = '.', recursive: bool = False, max_depth: int = 3, max_entries: int = 1000) -> list[Dict[str, Any]]:
        results: list[Dict[str, Any]] = []
        if not remote_path:
            remote_path = '.'
        if not self.sftp:
            if not self.connect():
                # include last error if available to aid troubleshooting
                logger.error('list_remote: not connected and connect() failed: %s', self._last_error or 'no details')
                return results
        seen = 0
        def _walk(path: str, depth: int) -> list[Dict[str, Any]]:
            nonlocal seen
            entries: list[Dict[str, Any]] = []
            if seen >= max_entries:
                return entries
            try:
                attrs = self.sftp.listdir_attr(path)
            except FileNotFoundError:
                logger.error('list_remote: remote path not found: %s', path)
                return entries
            except Exception as e:
                logger.error('list_remote: error listing %s: %s', path, e)
                return entries
            for attr in attrs:
                if seen >= max_entries:
                    break
                name = getattr(attr, 'filename', None)
                if not name or name in ('.', '..'):
                    continue
                try:
                    import stat as _stat
                    is_dir = _stat.S_ISDIR(attr.st_mode)
                except Exception:
                    is_dir = getattr(attr, 'longname', '').startswith('d') if hasattr(attr, 'longname') else False
                item_path = f"{path.rstrip('/')}/{name}" if path != '/' else f"/{name}"
                item: Dict[str, Any] = {"name": name, "path": item_path, "is_dir": bool(is_dir), "size": getattr(attr, 'st_size', None)}
                if is_dir and recursive and depth < max_depth:
                    item['children'] = _walk(item_path, depth + 1)
                entries.append(item)
                seen += 1
            return entries
        try:
            results = _walk(remote_path, 0)
        except Exception as e:
            logger.error('list_remote: unexpected error: %s', e)
        return results

    def delete_directory(self, remote_path: str) -> Dict:
        """
        Remove recursivamente `remote_path`. Retorna dict {'success': bool, 'error': str?}.
        Comportamento:
        - Se o caminho for um ficheiro, tenta removê-lo.
        - Se for diretório, remove recursivamente conteúdos e depois o diretório.
        - Tenta conectar se necessário.
        """
        if not remote_path:
            return {'success': False, 'error': 'empty remote path'}

        if not self.sftp:
            if not self.connect():
                return {'success': False, 'error': f'Not connected: {self._last_error or "connect failed"}'}

        path = self._normalize_remote(remote_path)

        def _is_dir_from_attr(attr) -> bool:
            try:
                import stat as _stat
                return _stat.S_ISDIR(getattr(attr, 'st_mode', 0))
            except Exception:
                try:
                    return getattr(attr, 'longname', '').startswith('d')
                except Exception:
                    return False

        def _remove_recursive(p: str):
            try:
                # try listing attributes to decide file/dir
                try:
                    entries = self.sftp.listdir_attr(p)
                except IOError:
                    # not a directory (could be file) or doesn't exist
                    # attempt to remove as file
                    try:
                        self.sftp.remove(p)
                        return {'success': True}
                    except Exception as e_file:
                        return {'success': False, 'error': f'remove file failed: {e_file}'}
                except FileNotFoundError:
                    return {'success': False, 'error': 'not found'}

                # it's a directory: iterate children
                for attr in entries:
                    name = getattr(attr, 'filename', None)
                    if not name or name in ('.', '..'):
                        continue
                    child = f"{p.rstrip('/')}/{name}" if p != '/' else f"/{name}"
                    try:
                        if _is_dir_from_attr(attr):
                            res = _remove_recursive(child)
                            if not res.get('success', False):
                                return res
                            # attempt rmdir of emptied child
                            try:
                                self.sftp.rmdir(child)
                            except Exception:
                                # ignore rmdir failure here; maybe removed inside recursion
                                pass
                        else:
                            try:
                                self.sftp.remove(child)
                            except Exception as e_remove:
                                return {'success': False, 'error': f'remove child failed: {e_remove}'}
                    except Exception as e_iter:
                        return {'success': False, 'error': f'error processing child {child}: {e_iter}'}

                # after children removed, remove this directory
                try:
                    self.sftp.rmdir(p)
                    return {'success': True}
                except Exception as e_rmdir:
                    return {'success': False, 'error': f'rmdir failed: {e_rmdir}'}
            except Exception as e:
                return {'success': False, 'error': str(e)}

        try:
            return _remove_recursive(path)
        except Exception as e:
            logger.exception("delete_directory unexpected error for %s: %s", path, e)
            return {'success': False, 'error': str(e)}