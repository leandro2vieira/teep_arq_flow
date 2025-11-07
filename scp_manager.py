# python
from pathlib import Path
import os
import logging
from typing import Dict, Any

import paramiko

try:
    from scp import SCPClient  # optional, faster single-file SCP if installed
    _HAS_SCP = True
except Exception:
    _HAS_SCP = False

logger = logging.getLogger(__name__)

class SCPManager:
    """Manager for transferring files over SSH/SCP (SFTP fallback)."""

    def __init__(self, host: str, port: int = 22, user: str = '',
                 password: str = '', timeout: int = 30):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.key_filename = None  # optional path to private key
        self.timeout = timeout
        self.ssh: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self.scp: SCPClient | None = None

    def connect(self) -> bool:
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password if self.password else None,
                key_filename=self.key_filename,
                timeout=self.timeout,
                look_for_keys=bool(self.key_filename is None)
            )
            # open SFTP for directory operations and reliable transfers
            self.sftp = self.ssh.open_sftp()
            # optional SCP client for faster single-file operations
            if _HAS_SCP:
                try:
                    self.scp = SCPClient(self.ssh.get_transport())
                except Exception:
                    self.scp = None
            logger.info("Connected to SSH/SFTP: %s:%s", self.host, self.port)
            return True
        except Exception as e:
            logger.error("SSH connect error: %s", e)
            self.disconnect()
            return False

    def disconnect(self) -> None:
        try:
            if self.scp:
                try:
                    self.scp.close()
                except Exception:
                    pass
                self.scp = None
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
            logger.info("Disconnected from SSH/SFTP")
        except Exception:
            pass

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        try:
            if not self.sftp or not self.ssh:
                raise RuntimeError("Not connected")
            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                self._mkdir_remote_recursive(remote_dir)
            if self.scp:
                # SCPClient.put handles directories incorrectly for remote creation, prefer sftp for dirs
                self.scp.put(local_path, remote_path)
            else:
                self.sftp.put(local_path, remote_path)
            logger.info("File uploaded: %s -> %s", local_path, remote_path)
            return True
        except Exception as e:
            logger.error("upload_file error: %s", e)
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        try:
            if not self.sftp or not self.ssh:
                raise RuntimeError("Not connected")
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            self.sftp.get(remote_path, local_path)
            logger.info("File downloaded: %s -> %s", remote_path, local_path)
            return True
        except Exception as e:
            logger.error("download_file error: %s", e)
            return False

    def upload_directory(self, local_dir: str, remote_dir: str) -> bool:
        try:
            if not self.sftp or not self.ssh:
                raise RuntimeError("Not connected")
            local_path = Path(local_dir)
            for item in local_path.rglob('*'):
                if item.is_file():
                    rel = item.relative_to(local_path)
                    remote_file = f"{remote_dir}/{rel.as_posix()}"
                    remote_parent = os.path.dirname(remote_file)
                    if remote_parent:
                        self._mkdir_remote_recursive(remote_parent)
                    # use sftp for directory uploads to ensure remote dirs exist
                    self.sftp.put(str(item), remote_file)
            logger.info("Directory uploaded: %s -> %s", local_dir, remote_dir)
            return True
        except Exception as e:
            logger.error("upload_directory error: %s", e)
            return False

    def download_directory(self, remote_dir: str, local_dir: str) -> bool:
        try:
            if not self.sftp or not self.ssh:
                raise RuntimeError("Not connected")
            os.makedirs(local_dir, exist_ok=True)
            self._download_recursive(remote_dir, local_dir)
            logger.info("Directory downloaded: %s -> %s", remote_dir, local_dir)
            return True
        except Exception as e:
            logger.error("download_directory error: %s", e)
            return False

    def _mkdir_remote_recursive(self, remote_path: str):
        # create remote directories recursively using SFTP
        parts = remote_path.strip('/').split('/')
        cur = ''
        for p in parts:
            cur = f"{cur}/{p}" if cur else p
            try:
                self.sftp.mkdir(cur)
            except IOError:
                # directory probably exists
                try:
                    self.sftp.stat(cur)
                except Exception:
                    # if stat also fails, ignore and continue
                    pass
            except Exception:
                pass

    def _download_recursive(self, remote_dir: str, local_dir: str):
        # list remote dir and recurse; uses SFTP's listdir_attr
        try:
            for attr in self.sftp.listdir_attr(remote_dir):
                name = attr.filename
                if name in ('.', '..'):
                    continue
                remote_path = f"{remote_dir}/{name}"
                local_path = os.path.join(local_dir, name)
                if paramiko.SFTPAttributes.from_stat(attr.st_mode).st_mode & 0o40000:  # not reliable; simpler check below
                    # attempt to determine directory by presence of attributes: fallback to try cwd
                    is_dir = stat_is_dir(attr)
                else:
                    is_dir = stat_is_dir(attr)
                # safer approach: try to open as directory by calling listdir on it
                try:
                    if stat_is_dir(attr):
                        os.makedirs(local_path, exist_ok=True)
                        self._download_recursive(remote_path, local_path)
                    else:
                        self.sftp.get(remote_path, local_path)
                except IOError:
                    # treat as file if cannot listdir
                    self.sftp.get(remote_path, local_path)
        except Exception:
            # fallback naive approach: try to list and download files
            try:
                entries = self.sftp.listdir(remote_dir)
                for name in entries:
                    if name in ('.', '..'):
                        continue
                    remote_path = f"{remote_dir}/{name}"
                    local_path = os.path.join(local_dir, name)
                    try:
                        # try entering directory
                        self.sftp.listdir(remote_path)
                        os.makedirs(local_path, exist_ok=True)
                        self._download_recursive(remote_path, local_path)
                    except Exception:
                        self.sftp.get(remote_path, local_path)
            except Exception as e:
                logger.error("_download_recursive error: %s", e)

    def list_remote(self, remote_path: str = ".", recursive: bool = False, max_depth: int = 3, max_entries: int = 1000) -> list[Dict[str, Any]]:
        """
        List remote directory contents without downloading.

        Parameters:
        - remote_path: remote directory to list (default: ".")
        - recursive: if True, recurse into subdirectories up to max_depth
        - max_depth: maximum recursion depth (root is depth 0)
        - max_entries: global limit of returned entries to avoid huge listings

        Returns:
        - A list of dicts: { 'name', 'path', 'is_dir', 'size', 'children' (optional) }
        """
        results: list[Dict[str, Any]] = []
        if not remote_path:
            remote_path = "."

        # ensure connection
        if not self.sftp or not self.ssh:
            if not self.connect():
                logger.error("list_remote: not connected and connect() failed")
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
                logger.error("list_remote: remote path not found: %s", path)
                return entries
            except Exception as e:
                logger.error("list_remote: error listing %s: %s", path, e)
                return entries

            for attr in attrs:
                if seen >= max_entries:
                    break
                name = getattr(attr, "filename", None)
                if not name or name in ('.', '..'):
                    continue
                is_dir = stat_is_dir(attr)
                item_path = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                item: Dict[str, Any] = {
                    "name": name,
                    "path": item_path,
                    "is_dir": bool(is_dir),
                    "size": getattr(attr, "st_size", None),
                }
                if is_dir and recursive and depth < max_depth:
                    item["children"] = _walk(item_path, depth + 1)
                entries.append(item)
                seen += 1
            return entries

        try:
            results = _walk(remote_path, 0)
        except Exception as e:
            logger.error("list_remote: unexpected error: %s", e)
        return results

# Helper to detect directory from SFTPAttributes safely
def stat_is_dir(attr: paramiko.SFTPAttributes) -> bool:
    # SFTPAttributes has longname and st_mode; use S_ISDIR on st_mode if present
    try:
        import stat as _stat
        return _stat.S_ISDIR(attr.st_mode)
    except Exception:
        # fallback: longname starts with 'd' in many servers
        try:
            return getattr(attr, 'longname', '').startswith('d')
        except Exception:
            return False