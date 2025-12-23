# python
import os
import re
import logging
from ftplib import FTP, FTP_TLS
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
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
        self.timeout = timeout
        self.passive = passive
        self.use_tls = use_tls
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

    def _create_remote_dirs(self, remote_path: str) -> None:
        remote_path = normalize_path(remote_path or '')
        if not remote_path or remote_path in ('.', '/'):
            return
        parts = [p for p in remote_path.split('/') if p]
        cur = ''
        for p in parts:
            cur = f"{cur}/{p}"
            # 1) Try absolute MKD
            try:
                self.ftp.mkd(cur)
                continue
            except Exception:
                pass
            # 2) If MKD failed, check if the directory already exists by trying to CWD into it
            prev = None
            try:
                try:
                    prev = self.ftp.pwd()
                except Exception:
                    prev = None
                try:
                    self.ftp.cwd(cur)
                    # exists, restore cwd if possible
                    if prev is not None:
                        try:
                            self.ftp.cwd(prev)
                        except Exception:
                            pass
                    continue
                except Exception:
                    # 3) Try relative MKD
                    try:
                        self.ftp.mkd(p)
                        continue
                    except Exception:
                        # 4) As a last check, try relative CWD to see if dir already exists
                        try:
                            if prev is not None:
                                self.ftp.cwd(p)
                                try:
                                    self.ftp.cwd(prev)
                                except Exception:
                                    pass
                                continue
                        except Exception:
                            pass
            finally:
                # no state changes kept here; continue to next part
                pass

    def upload_file(self, local_path: str, remote_path: str):
        if not self._ensure_connected():
            return {'success': False, 'error': 'Not connected'}

        remote = normalize_path(remote_path or '')
        if not remote:
            logger.error("upload_file: empty remote path")
            return {'success': False, 'error': 'empty remote path'}

        if not os.path.isfile(local_path):
            logger.error("upload_file: local file not found: %s", local_path)
            return {'success': False, 'error': 'local file not found'}

        try:
            with open(local_path, 'rb') as f:
                try:
                    f.seek(0)
                    self.ftp.storbinary(f"STOR {remote}", f)
                    logger.info(f"Uploaded {local_path} to {remote}")
                    return {'success': True}
                except Exception as first_exc:
                    logger.warning("Direct upload failed for %s to %s, error: %s", local_path, remote, first_exc)
                    parent = os.path.dirname(remote)
                    name = os.path.basename(remote)
                    if not name:
                        logger.error("upload_file: invalid remote name for %s", remote)
                        return {'success': False, 'error': 'invalid remote name'}
                    try:
                        try:
                            self._create_remote_dirs(parent)
                        except Exception:
                            logger.debug("Failed to create remote dirs for %s (ignored)", parent)
                        f.seek(0)
                        try:
                            # Preferred: change to parent and upload by basename
                            with self._cwd(parent):
                                self.ftp.storbinary(f"STOR {name}", f)
                            logger.info("Uploaded file %s via cwd %s", name, parent)
                            return {'success': True}
                        except Exception as cwd_exc:
                            logger.debug("cwd/upload to %s failed: %s; trying alternative STOR with full path", parent, cwd_exc)
                            # Try storing using the full remote path as a final fallback
                            try:
                                f.seek(0)
                                self.ftp.storbinary(f"STOR {remote}", f)
                                logger.info("Uploaded file to %s (fallback full-path STOR)", remote)
                                return {'success': True}
                            except Exception as alt_exc:
                                logger.error("upload_file fallback failed for %s via cwd %s: %s", name, parent, alt_exc)
                                return {'success': False, 'error': str(alt_exc)}
                    except Exception as second_exc:
                        logger.error("upload_file fallback failed for %s via cwd %s: %s", name, parent, second_exc)
                        return {'success': False, 'error': str(second_exc)}
        except Exception as e:
            logger.exception("upload_file error: %s", e)
            return {'success': False, 'error': str(e)}

    def download_file(self, remote_path: str, local_path: str) -> Tuple[bool, str]:
        if not self._ensure_connected():
            return False, "FTP not connected"
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
            return True, ""
        except Exception as e:
            logger.error("download_file error: %s", e)
            return False, f"download_file error: {e}"

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
        results: List[Dict[str, Any]] = []

        def make_path(dir_path: str, name: str) -> str:
            if name.startswith('/') or '/' in name:
                return normalize_path(name)
            if dir_path in ('', '.', '/'):
                base = '/' if dir_path == '/' else ''
                return normalize_path(f"{base}{name}")
            return normalize_path(f"{dir_path.rstrip('/')}/{name}")

        def normalize_entry(raw_name: str, dir_path: str):
            raw = (raw_name or '').rstrip('/')
            display = os.path.basename(raw) or raw
            full = make_path(dir_path, raw) if raw and not raw.startswith('/') and '/' not in raw else normalize_path(raw)
            return display, full

        # regex para linhas do LIST no estilo Windows: "11-10-25  07:18AM       <DIR>          Name"
        windows_list_re = re.compile(r'^\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?:AM|PM)\s+(?:<DIR>|\d+)\s+(.+)$', re.IGNORECASE)

        try:
            # 1) Try FTP.mlsd (preferred)
            try:
                if hasattr(self.ftp, 'mlsd'):
                    for raw_name, facts in self.ftp.mlsd(remote_dir):
                        if not raw_name or (not include_hidden and raw_name.startswith('.')):
                            continue
                        name, full_path = normalize_entry(raw_name, remote_dir)
                        ftype = (facts.get('type') or '').lower()
                        typ = 'directory' if ftype in ('dir', 'cdir', 'pdir') or ftype.startswith('d') else 'file'
                        size = None
                        if 'size' in facts:
                            try:
                                size = int(facts['size'])
                            except Exception:
                                size = None
                        mtime = None
                        if 'modify' in facts:
                            try:
                                mtime = datetime.strptime(facts['modify'], '%Y%m%d%H%M%S').isoformat()
                            except Exception:
                                mtime = facts.get('modify')
                        results.append({'name': name, 'path': full_path, 'type': typ, 'size': size, 'mtime': mtime})
                if results:
                    return results
            except Exception:
                pass

            # 2) Try LIST and parse leniently
            entries: List[str] = []
            try:
                self.ftp.retrlines(f"LIST {remote_dir}", entries.append)
            except Exception:
                try:
                    self.ftp.retrlines("LIST", entries.append)
                except Exception:
                    entries = []

            if entries:
                for line in entries:
                    try:
                        parts = line.split()
                        if len(parts) >= 9:
                            raw_name = ' '.join(parts[8:])
                            if not raw_name or (not include_hidden and raw_name.startswith('.')):
                                continue
                            name, full_path = normalize_entry(raw_name, remote_dir)
                            typ = 'directory' if line.startswith('d') else 'file'
                            size = None
                            try:
                                size = int(parts[4])
                            except Exception:
                                size = None
                            mtime = ' '.join(parts[5:8])
                            results.append({'name': name, 'path': full_path, 'type': typ, 'size': size, 'mtime': mtime})
                        else:
                            # Detecta formato Windows LIST e extrai apenas o nome
                            m = windows_list_re.match(line.strip())
                            if m:
                                raw_name = m.group(1).strip()
                            else:
                                raw_name = line.strip()
                            if not raw_name or (not include_hidden and raw_name.startswith('.')):
                                continue
                            name, full_path = normalize_entry(raw_name, remote_dir)
                            # probe to detect directory
                            typ = 'file'
                            try:
                                self.ftp.cwd(full_path)
                                typ = 'directory'
                                try:
                                    self.ftp.cwd(remote_dir)
                                except Exception:
                                    pass
                            except Exception:
                                typ = 'file'
                            results.append({'name': name, 'path': full_path, 'type': typ, 'size': None, 'mtime': None})
                    except Exception:
                        continue
                if results:
                    return results

            # 3) Final fallback: NLST and probes
            try:
                names = self.ftp.nlst(remote_dir)
            except Exception:
                try:
                    names = self.ftp.nlst()
                except Exception:
                    names = []

            for n in names:
                if not n:
                    continue
                name, full = normalize_entry(n, remote_dir)
                if not name or (not include_hidden and name.startswith('.')):
                    continue
                typ = 'file'
                try:
                    self.ftp.cwd(full)
                    typ = 'directory'
                    try:
                        self.ftp.cwd(remote_dir)
                    except Exception:
                        pass
                except Exception:
                    typ = 'file'
                size = None
                try:
                    size = int(self.ftp.size(full))
                except Exception:
                    size = None
                results.append({'name': name, 'path': full, 'type': typ, 'size': size, 'mtime': None})

            return results
        except Exception as e:
            logger.exception("list_remote error: %s", e)
            return []

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
    
    def delete_file(self, remote_path: str) -> Tuple[bool, str]:
        if not self._ensure_connected():
            return False, "FTP not connected"
        remote = normalize_path(remote_path or '')
        if not remote:
            logger.error("delete_file: empty path")
            return False, "empty path"
        try:
            try:
                self.ftp.delete(remote)
                logger.info("Deleted remote file: %s", remote)
                return True, "file deleted"
            except Exception as first_exc:
                # fallback: try deleting by changing to parent and removing basename
                parent = os.path.dirname(remote)
                name = os.path.basename(remote)
                if not name:
                    logger.error("delete_file: invalid name for %s", remote)
                    return False, "invalid name"
                try:
                    with self._cwd(parent):
                        self.ftp.delete(name)
                    logger.info("Deleted remote file %s via cwd %s", name, parent)
                    return True, "file deleted"
                except Exception as second_exc:
                    msg = str(second_exc)
                    # detect FTP permission/file-not-found style codes (e.g. 550)
                    if '550' in msg:
                        logger.warning("delete_file fallback failed for %s: %s", remote, msg)
                    else:
                        logger.warning("delete_file fallback failed for %s: %s", remote, msg)
                    return False, msg
        except Exception as e:
            logger.exception("delete_file error: %s", e)
            return False, str(e)

    def delete_remote_path(self, remote_path: str) -> Tuple[bool, str]:
        if not self._ensure_connected():
            return False, "FTP not connected"
        remote = normalize_path(remote_path or '')
        if not remote or remote == '/':
            logger.error("delete_remote_path: invalid path %s", remote)
            return False, "invalid path"
        try:
            # try as file first
            ok, msg = self.delete_file(remote)
            if ok:
                return True, msg
            # list children and remove recursively
            children = self.list_remote(remote, include_hidden=True)
            for c in children:
                path = c.get('path') or ''
                if not path:
                    continue
                if c.get('type') == 'file':
                    ok, msg = self.delete_file(path)
                    if not ok:
                        logger.warning("Failed to delete file %s: %s", path, msg)
                else:
                    if not self.delete_remote_path(path)[0]:
                        logger.warning("Failed to delete directory %s", path)
            # remove the directory itself
            try:
                self.ftp.rmd(remote)
                logger.info("Removed remote directory: %s", remote)
                return True, "directory removed"
            except Exception:
                parent = os.path.dirname(remote)
                base = os.path.basename(remote)
                if not base:
                    return False, "invalid directory name"
                try:
                    with self._cwd(parent):
                        self.ftp.rmd(base)
                    logger.info("Removed remote directory %s via cwd %s", base, parent)
                    return True, "directory removed"
                except Exception as e:
                    logger.error("Failed to remove directory %s via cwd %s: %s", base, parent, e)
                    return False, "failed to remove directory"
        except Exception as e:
            logger.error("delete_remote_path error: %s", e)
            return False, f"error occurred: {e}"