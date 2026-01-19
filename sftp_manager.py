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

    def test_socket_connect(self) -> bool:
        try:
            import socket
            addr = (self.host, int(self.port))
            sock = socket.create_connection(addr, timeout=self.timeout)
            sock.close()
            return True
        except Exception as e:
            logger.debug("sftp socket test failed for %s:%s -> %s", self.host, self.port, e)
            return False

    def connect(self) -> bool:
        # quick reachability check
        try:
            if not self.test_socket_connect():
                logger.error("SFTP connect error: cannot reach %s:%s (TCP connect failed)", self.host, self.port)
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
            logger.error("SFTP connect error: %r", e)
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

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        try:
            if not self.sftp:
                raise RuntimeError("Not connected")
            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                self._mkdir_remote_recursive(remote_dir)
            self.sftp.put(local_path, remote_path)
            logger.info("SFTP: uploaded %s -> %s", local_path, remote_path)
            return True
        except Exception as e:
            logger.error("SFTP upload_file error: %s", e)
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        try:
            if not self.sftp:
                raise RuntimeError("Not connected")
            os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
            self.sftp.get(remote_path, local_path)
            logger.info("SFTP: downloaded %s -> %s", remote_path, local_path)
            return True
        except Exception as e:
            logger.error("SFTP download_file error: %s", e)
            return False

    def upload_directory(self, local_dir: str, remote_dir: str) -> bool:
        try:
            if not self.sftp:
                raise RuntimeError("Not connected")
            local_path = Path(local_dir)
            for item in local_path.rglob('*'):
                if item.is_file():
                    rel = item.relative_to(local_path)
                    remote_file = f"{remote_dir}/{rel.as_posix()}"
                    remote_parent = os.path.dirname(remote_file)
                    if remote_parent:
                        self._mkdir_remote_recursive(remote_parent)
                    self.sftp.put(str(item), remote_file)
            logger.info("SFTP: directory uploaded %s -> %s", local_dir, remote_dir)
            return True
        except Exception as e:
            logger.error("SFTP upload_directory error: %s", e)
            return False

    def download_directory(self, remote_dir: str, local_dir: str) -> bool:
        try:
            if not self.sftp:
                raise RuntimeError("Not connected")
            os.makedirs(local_dir, exist_ok=True)
            self._download_recursive(remote_dir, local_dir)
            logger.info("SFTP: directory downloaded %s -> %s", remote_dir, local_dir)
            return True
        except Exception as e:
            logger.error("SFTP download_directory error: %s", e)
            return False

    def _mkdir_remote_recursive(self, remote_path: str):
        parts = remote_path.strip('/').split('/')
        cur = ''
        for p in parts:
            cur = f"{cur}/{p}" if cur else p
            try:
                self.sftp.mkdir(cur)
            except IOError:
                try:
                    self.sftp.stat(cur)
                except Exception:
                    pass
            except Exception:
                pass

    # compatibility helpers used by GenericFileTransfer
    def ensure_dir(self, remote_path: str):
        """Ensure remote_path exists (compat wrapper)."""
        try:
            if not remote_path:
                return True
            self._mkdir_remote_recursive(remote_path)
            return True
        except Exception:
            return False

    def mkdir(self, remote_path: str):
        """Create single directory (compat)."""
        try:
            self.sftp.mkdir(remote_path)
            return True
        except Exception:
            return False

    def stat(self, remote_path: str) -> Dict[str, Any]:
        """Return a dict with size/modified if possible, else empty dict."""
        try:
            st = self.sftp.stat(remote_path)
            return {'size': getattr(st, 'st_size', None), 'modified': getattr(st, 'st_mtime', None)}
        except Exception:
            return {}

    def delete_file(self, remote_path: str) -> bool:
        try:
            self.sftp.remove(remote_path)
            return True
        except Exception as e:
            logger.error("SFTP delete_file error: %s", e)
            return False

    def delete_directory(self, remote_path: str) -> bool:
        """Attempt to remove remote directory recursively if needed."""
        try:
            # try rmdir (will fail if not empty)
            try:
                self.sftp.rmdir(remote_path)
                return True
            except Exception:
                # attempt recursive delete
                entries = self.list_remote(remote_path, recursive=False) or []
                for e in entries:
                    name = e.get('name')
                    if not name:
                        continue
                    child_path = f"{remote_path.rstrip('/')}/{name}"
                    if e.get('is_dir'):
                        self.delete_directory(child_path)
                    else:
                        try:
                            self.sftp.remove(child_path)
                        except Exception:
                            pass
                # now try rmdir again
                try:
                    self.sftp.rmdir(remote_path)
                    return True
                except Exception as e:
                    logger.error("SFTP delete_directory final rmdir failed: %s", e)
                    return False
        except Exception as e:
            logger.error("SFTP delete_directory error: %s", e)
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
                logger.error('list_remote: not connected and connect() failed')
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

