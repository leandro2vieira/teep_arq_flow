# python
import os
import re
import logging
from ftplib import FTP, FTP_TLS
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)


def normalize_path(path: Optional[str]) -> str:
    if path is None:
        return ''
    p = path.replace('\\', '/')
    p = re.sub('/+', '/', p)
    if len(p) > 1 and p.endswith('/'):
        p = p.rstrip('/')
    return p


class FTPManager:
    """Refactored FTP manager with safer connection and directory handling."""

    def __init__(self, host: str, port: int = 21, user: str = 'anonymous',
                 password: str = '', use_tls: bool = False, timeout: int = 30, passive: bool = True):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout
        self.passive = passive
        self.ftp: Optional[FTP] = None

    def connect(self) -> bool:
        try:
            self.ftp = FTP_TLS() if self.use_tls else FTP()
            # set timeout where supported
            try:
                self.ftp.timeout = self.timeout
            except Exception:
                pass
            logger.info("Connecting to FTP %s:%s as %s", self.host, self.port, self.user)
            self.ftp.connect(host=self.host, port=self.port, timeout=self.timeout)
            self.ftp.login(self.user, self.password)
            try:
                self.ftp.set_pasv(self.passive)
            except Exception:
                pass
            if self.use_tls and isinstance(self.ftp, FTP_TLS):
                try:
                    self.ftp.prot_p()
                except Exception:
                    pass
            logger.info("FTP connected: %s", self.host)
            return True
        except Exception as e:
            logger.error("Failed to connect FTP: %s", e)
            self.ftp = None
            return False

    def disconnect(self) -> None:
        if not self.ftp:
            return
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
        finally:
            self.ftp = None
            logger.info("FTP disconnected")

    def _ensure_connected(self) -> bool:
        if self.ftp:
            return True
        return self.connect()

    @contextmanager
    def _cwd(self, path: str):
        """
        Context manager to change remote working directory and restore previous cwd.
        If changing to `path` fails, original cwd is restored and an exception is raised.
        """
        if not self._ensure_connected():
            raise RuntimeError("FTP not connected")
        prev = None
        try:
            try:
                prev = self.ftp.pwd()
            except Exception:
                prev = None
            if path:
                self.ftp.cwd(path)
            yield
        finally:
            if prev is not None:
                try:
                    self.ftp.cwd(prev)
                except Exception:
                    pass

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        if not self._ensure_connected():
            return False
        local = normalize_path(local_path)
        remote = normalize_path(remote_path)
        try:
            with open(local, 'rb') as f:
                # some servers accept full remote path in STOR, others require cwd
                try:
                    self.ftp.storbinary(f"STOR {remote}", f)
                except Exception:
                    # attempt store by changing to parent dir
                    parent = os.path.dirname(remote)
                    name = os.path.basename(remote)
                    with self._cwd(parent):
                        self.ftp.storbinary(f"STOR {name}", f)
            logger.info("Uploaded %s -> %s", local, remote)
            return True
        except Exception as e:
            logger.error("upload_file error: %s", e)
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        if not self._ensure_connected():
            return False
        remote = normalize_path(remote_path)
        local = normalize_path(local_path)
        try:
            os.makedirs(os.path.dirname(local) or '.', exist_ok=True)
            try:
                self.ftp.retrbinary(f"RETR {remote}", open(local, 'wb').write)
            except Exception:
                # fallback: cwd into parent and retr by basename
                parent = os.path.dirname(remote)
                name = os.path.basename(remote)
                with self._cwd(parent):
                    with open(local, 'wb') as f:
                        self.ftp.retrbinary(f"RETR {name}", f.write)
            logger.info("Downloaded %s -> %s", remote, local)
            return True
        except Exception as e:
            logger.error("download_file error: %s", e)
            return False

    def _create_remote_dirs(self, remote_path: str) -> None:
        remote_path = normalize_path(remote_path or '')
        if not remote_path or remote_path in ('.', '/'):
            return
        parts = [p for p in remote_path.split('/') if p]
        cur = ''
        for p in parts:
            cur = f"{cur}/{p}"
            try:
                self.ftp.mkd(cur)
            except Exception:
                pass

    def upload_directory(self, local_dir: str, remote_dir: str) -> bool:
        if not self._ensure_connected():
            return False
        try:
            base = Path(local_dir)
            remote_base = normalize_path(remote_dir or '')
            # ensure base directory exists remotely
            try:
                self.ftp.mkd(remote_base)
            except Exception:
                pass
            for item in base.rglob('*'):
                if item.is_file():
                    rel = item.relative_to(base)
                    remote_file = normalize_path(f"{remote_base}/{rel.as_posix()}")
                    parent = os.path.dirname(remote_file)
                    self._create_remote_dirs(parent)
                    if not self.upload_file(str(item), remote_file):
                        logger.warning("Failed to upload %s", item)
            logger.info("Uploaded directory %s -> %s", local_dir, remote_dir)
            return True
        except Exception as e:
            logger.error("upload_directory error: %s", e)
            return False

    def list_remote(self, remote_dir: str = '.', include_hidden: bool = False) -> List[Dict[str, Any]]:
        if not self._ensure_connected():
            return []
        remote_dir = normalize_path(remote_dir or '.')
        entries_lines: List[str] = []
        using_mlsd = True
        try:
            # prefer MLSD
            try:
                self.ftp.retrlines(f"MLSD {remote_dir}", entries_lines.append)
            except Exception:
                entries_lines = []
                using_mlsd = False
                try:
                    self.ftp.retrlines(f"LIST {remote_dir}", entries_lines.append)
                except Exception:
                    self.ftp.retrlines("LIST", entries_lines.append)
        except Exception as e:
            logger.error("list_remote retrieval error: %s", e)
            return []

        results: List[Dict[str, Any]] = []

        def make_path(dir_path: str, name: str) -> str:
            if dir_path in ('', '.', '/'):
                base = '/' if dir_path == '/' else ''
                return normalize_path(f"{base}{name}")
            return normalize_path(f"{dir_path.rstrip('/')}/{name}")

        try:
            if using_mlsd:
                from datetime import datetime as _dt
                for line in entries_lines:
                    try:
                        parts = line.split(';')
                        facts = {}
                        for fact in parts[:-1]:
                            if '=' in fact:
                                k, v = fact.split('=', 1)
                                facts[k.lower()] = v
                        name = parts[-1].strip()
                        if not name or (not include_hidden and name.startswith('.')):
                            continue
                        typ = 'directory' if facts.get('type') == 'dir' else 'file'
                        size = int(facts['size']) if 'size' in facts and facts['size'].isdigit() else None
                        mtime = None
                        if 'modify' in facts:
                            try:
                                mtime = _dt.strptime(facts['modify'], '%Y%m%d%H%M%S').isoformat()
                            except Exception:
                                mtime = facts.get('modify')
                        results.append({'name': name, 'path': make_path(remote_dir, name), 'type': typ, 'size': size, 'mtime': mtime})
                    except Exception:
                        continue
            else:
                for line in entries_lines:
                    try:
                        parts = line.split()
                        if len(parts) < 9:
                            continue
                        name = ' '.join(parts[8:])
                        if not include_hidden and name.startswith('.'):
                            continue
                        typ = 'directory' if line.startswith('d') else 'file'
                        size = None
                        try:
                            size = int(parts[4])
                        except Exception:
                            pass
                        mtime = ' '.join(parts[5:8])
                        results.append({'name': name, 'path': make_path(remote_dir, name), 'type': typ, 'size': size, 'mtime': mtime})
                    except Exception:
                        continue
        except Exception as e:
            logger.exception("list_remote parse error: %s", e)

        return results

    def _download_recursive(self, remote_dir: str, local_dir: str) -> bool:
        remote_dir = normalize_path(remote_dir or '.')
        local_dir = normalize_path(local_dir or '.')
        try:
            os.makedirs(local_dir, exist_ok=True)
            entries = self.list_remote(remote_dir, include_hidden=True)
            for e in entries:
                p = e.get('path') or ''
                if not p:
                    continue
                if e.get('type') == 'directory':
                    name = e.get('name') or os.path.basename(p)
                    self._download_recursive(p, os.path.join(local_dir, name))
                else:
                    name = e.get('name') or os.path.basename(p)
                    local_file = os.path.join(local_dir, name)
                    if not self.download_file(p, local_file):
                        logger.warning("Failed to download %s", p)
            return True
        except Exception as exc:
            logger.error("Recursive download error for %s: %s", remote_dir, exc)
            return False

    def download_directory(self, remote_dir: str, local_dir: str) -> bool:
        if not self._ensure_connected():
            return False
        try:
            return self._download_recursive(remote_dir, local_dir)
        except Exception as e:
            logger.error("download_directory error: %s", e)
            return False

    def delete_file(self, remote_path: str) -> bool:
        """Primary public method expected by other modules (alias kept)."""
        if not self._ensure_connected():
            return False
        remote = normalize_path(remote_path or '')
        if not remote:
            logger.error("delete_file: empty path")
            return False
        try:
            try:
                self.ftp.delete(remote)
                logger.info("Deleted remote file: %s", remote)
                return True
            except Exception:
                parent = os.path.dirname(remote)
                name = os.path.basename(remote)
                if not name:
                    return False
                with self._cwd(parent):
                    self.ftp.delete(name)
                logger.info("Deleted remote file %s via cwd %s", name, parent)
                return True
        except Exception as e:
            logger.error("delete_file error: %s", e)
            return False

    # backward-compatible name from older code
    def delete_remote_file(self, remote_path: str) -> bool:
        return self.delete_file(remote_path)

    def delete_remote_path(self, remote_path: str) -> bool:
        if not self._ensure_connected():
            return False
        remote = normalize_path(remote_path or '')
        if not remote or remote == '/':
            logger.error("delete_remote_path: invalid path %s", remote)
            return False
        try:
            # try as file first
            if self.delete_file(remote):
                return True
            # list children and remove recursively
            children = self.list_remote(remote, include_hidden=True)
            for c in children:
                path = c.get('path') or ''
                if not path:
                    continue
                if c.get('type') == 'file':
                    if not self.delete_file(path):
                        logger.warning("Failed to delete file %s", path)
                else:
                    if not self.delete_remote_path(path):
                        logger.warning("Failed to delete directory %s", path)
            # remove the directory itself
            try:
                self.ftp.rmd(remote)
                logger.info("Removed remote directory: %s", remote)
                return True
            except Exception:
                parent = os.path.dirname(remote)
                base = os.path.basename(remote)
                if not base:
                    return False
                with self._cwd(parent):
                    self.ftp.rmd(base)
                logger.info("Removed remote directory %s via cwd %s", base, parent)
                return True
        except Exception as e:
            logger.error("delete_remote_path error: %s", e)
            return False