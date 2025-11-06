from pathlib import Path
from ftplib import FTP, FTP_TLS
import sys
import os
import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def normalize_remote_path(path: str) -> str:
    """Normalize remote FTP path: convert backslashes, collapse duplicate slashes, strip trailing slash except root."""
    if path is None:
        return path
    p = path.replace('\\', '/')
    p = re.sub('/+', '/', p)
    if len(p) > 1 and p.endswith('/'):
        p = p.rstrip('/')
    return p

def get_file_tree(path: str, include_hidden: bool = False, max_depth: int = 10) -> dict:
    """
    Return a nested dict representing the file tree at `path`.
    Each node: {name, path, type: 'file'|'directory', size, mtime, children?}
    max_depth prevents unbounded recursion; set to 0 to avoid reading children.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"path": path, "exists": False}

    def scan(p: str, depth: int) -> dict:
        node = {
            "name": os.path.basename(p) or p,
            "path": p,
            "type": "directory" if os.path.isdir(p) else "file",
        }
        try:
            st = os.stat(p)
            node["size"] = st.st_size if node["type"] == "file" else None
            node["mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat()
        except Exception:
            node.setdefault("meta_error", "failed to read stat")

        if node["type"] == "directory" and depth > 0:
            children = []
            try:
                with os.scandir(p) as it:
                    for entry in it:
                        if not include_hidden and entry.name.startswith("."):
                            continue
                        try:
                            children.append(scan(entry.path, depth - 1))
                        except Exception as e:
                            children.append({"name": entry.name, "path": entry.path, "error": str(e)})
                # directories first, then files, both alphabetically
                children.sort(key=lambda x: (x.get("type") != "directory", x.get("name", "").lower()))
                node["children"] = children
            except Exception as e:
                node["error"] = str(e)
        return node

    return scan(path, max_depth)


class FTPManager:
    """Gerenciador de operações FTP"""

    def __init__(self, host: str, port: int, user: str, password: str, use_tls: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.ftp = None

    def connect(self):
        """Conecta ao servidor FTP"""
        try:
            if self.use_tls:
                self.ftp = FTP_TLS()
            else:
                self.ftp = FTP()

            logger.info(f"Conectando ao FTP: {self.host}:{self.port} com usuário {self.user}")
            self.ftp.connect(host=self.host, port=self.port)
            self.ftp.login(self.user, self.password)

            if self.use_tls:
                self.ftp.prot_p()

            logger.info(f"Conectado ao FTP: {self.host}")
            return True
        except Exception as e:
            logger.error(f"Erro ao conectar FTP: {e}")
            return False

    def disconnect(self):
        """Desconecta do servidor FTP"""
        if self.ftp:
            try:
                self.ftp.quit()
            except:
                self.ftp.close()
            logger.info("Desconectado do FTP")

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Envia um arquivo via FTP"""
        try:
            with open(local_path, 'rb') as f:
                self.ftp.storbinary(f'STOR {remote_path}', f)
            logger.info(f"Arquivo enviado: {local_path} -> {remote_path}")
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar arquivo: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Baixa um arquivo via FTP"""
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'wb') as f:
                self.ftp.retrbinary(f'RETR {remote_path}', f.write)
            logger.info(f"Arquivo baixado: {remote_path} -> {local_path}")
            return True
        except Exception as e:
            logger.error(f"Erro ao baixar arquivo: {e}")
            return False

    def upload_directory(self, local_dir: str, remote_dir: str) -> bool:
        """Envia uma pasta inteira via FTP"""
        remote_dir = normalize_remote_path(remote_dir or '')
        try:
            local_path = Path(local_dir)

            try:
                self.ftp.mkd(remote_dir)
            except:
                pass

            for item in local_path.rglob('*'):
                if item.is_file():
                    relative_path = item.relative_to(local_path)
                    remote_file_path = normalize_remote_path(f"{remote_dir}/{relative_path}")
                    remote_file_dir = os.path.dirname(remote_file_path)

                    self._create_remote_dirs(remote_file_dir)
                    self.upload_file(str(item), remote_file_path)

            logger.info(f"Diretório enviado: {local_dir} -> {remote_dir}")
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar diretório: {e}")
            return False

    def download_directory(self, remote_dir: str, local_dir: str) -> bool:
        """Baixa uma pasta inteira via FTP"""
        try:
            os.makedirs(local_dir, exist_ok=True)
            self.ftp.cwd(remote_dir)
            self._download_recursive(remote_dir, local_dir)
            logger.info(f"Diretório baixado: {remote_dir} -> {local_dir}")
            return True
        except Exception as e:
            logger.error(f"Erro ao baixar diretório: {e}")
            return False

    def _create_remote_dirs(self, remote_path: str):
        """Cria diretórios remotos recursivamente"""
        remote_path = normalize_remote_path(remote_path or '')
        if remote_path == '':
            return
        dirs = remote_path.split('/')
        current = ''
        for d in dirs:
            if d:
                current += f'/{d}'
                try:
                    self.ftp.mkd(current)
                except:
                    pass

    def _download_recursive(self, remote_dir: str, local_dir: str):
        """Baixa arquivos recursivamente"""
        remote_dir = normalize_remote_path(remote_dir or '.')
        items = []
        self.ftp.retrlines('LIST', items.append)

        for item in items:
            parts = item.split()
            if len(parts) < 9:
                continue

            name = ' '.join(parts[8:])
            if name in ['.', '..']:
                continue

            if item.startswith('d'):
                new_remote = normalize_remote_path(f"{remote_dir}/{name}")
                new_local = os.path.join(local_dir, name)
                os.makedirs(new_local, exist_ok=True)

                current_dir = self.ftp.pwd()
                self.ftp.cwd(name)
                self._download_recursive(new_remote, new_local)
                self.ftp.cwd(current_dir)
            else:
                new_remote = normalize_remote_path(f"{remote_dir}/{name}")
                local_file = os.path.join(local_dir, name)
                self.download_file(new_remote, local_file)

    def list_remote(self, remote_dir: str = '.', include_hidden: bool = False) -> list[Dict[str, Any]]:
        """
        Return a flat list of entries in `remote_dir` on the FTP server.
        Each entry: {name, path, type: 'file'|'directory', size, mtime}
        Tries MLSD (preferable) and falls back to parsing LIST output.
        """
        remote_dir = normalize_remote_path(remote_dir or '.')
        try:
            entries_lines = []
            # ensure we are in the target dir for MLSD when possible
            try:
                self.ftp.cwd(remote_dir)
            except Exception:
                # some servers accept MLSD with a path argument, but we'll attempt cwd first
                pass

            using_mlsd = True
            try:
                self.ftp.retrlines('MLSD', entries_lines.append)
            except Exception:
                entries_lines = []
                using_mlsd = False
                # fallback to LIST for the directory
                try:
                    # try LIST on the directory (some servers expect cwd)
                    self.ftp.retrlines('LIST ' + remote_dir, entries_lines.append)
                except Exception:
                    # final attempt: LIST the current working dir
                    self.ftp.retrlines('LIST', entries_lines.append)

            results = []
            from datetime import datetime

            def make_path(dir_path: str, name: str) -> str:
                if dir_path in ('', '.', '/'):
                    base = '/' if dir_path == '/' else ''
                    return normalize_remote_path(f"{base}{name}")
                return normalize_remote_path(f"{dir_path.rstrip('/')}/{name}")

            if using_mlsd:
                # MLSD lines have semicolon-separated facts then the name
                for line in entries_lines:
                    try:
                        parts = line.split(';')
                        facts = {}
                        for fact in parts[:-1]:
                            if '=' in fact:
                                k, v = fact.split('=', 1)
                                facts[k.lower()] = v
                        name = parts[-1].strip()
                        if not name:
                            continue
                        if not include_hidden and name.startswith('.'):
                            continue
                        typ = 'directory' if facts.get('type') == 'dir' else 'file'
                        size = int(facts['size']) if 'size' in facts and facts['size'].isdigit() else None
                        mtime = None
                        if 'modify' in facts:
                            try:
                                mtime = datetime.strptime(facts['modify'], '%Y%m%d%H%M%S').isoformat()
                            except Exception:
                                mtime = facts.get('modify')
                        results.append({
                            'name': name,
                            'path': make_path(remote_dir, name),
                            'type': typ,
                            'size': size,
                            'mtime': mtime
                        })
                    except Exception:
                        # skip malformed MLSD line
                        continue
            else:
                # Parse traditional LIST output (Unix-style)
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
                        # mtime typically in parts[5:8], keep as string
                        mtime = ' '.join(parts[5:8])
                        results.append({
                            'name': name,
                            'path': make_path(remote_dir, name),
                            'type': typ,
                            'size': size,
                            'mtime': mtime
                        })
                    except Exception:
                        continue

            return results
        except Exception as e:
            logger.error("Erro ao listar diretório remoto: %s", e)
            return []