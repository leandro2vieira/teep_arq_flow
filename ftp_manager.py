from pathlib import Path
from ftplib import FTP, FTP_TLS
import sys
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class FTPManager:
    """Gerenciador de operações FTP"""

    def __init__(self, config: Dict[str, Any]):
        self.host = config.get('host')
        self.port = config.get('port', 21)
        self.user = config.get('user')
        self.password = config.get('password')
        self.use_tls = config.get('use_tls', False)
        self.ftp = None

    def connect(self):
        """Conecta ao servidor FTP"""
        try:
            if self.use_tls:
                self.ftp = FTP_TLS()
            else:
                self.ftp = FTP()

            self.ftp.connect(self.host, self.port)
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
        try:
            local_path = Path(local_dir)

            try:
                self.ftp.mkd(remote_dir)
            except:
                pass

            for item in local_path.rglob('*'):
                if item.is_file():
                    relative_path = item.relative_to(local_path)
                    remote_file_path = f"{remote_dir}/{relative_path}".replace('\\', '/')
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
                new_remote = f"{remote_dir}/{name}"
                new_local = os.path.join(local_dir, name)
                os.makedirs(new_local, exist_ok=True)

                current_dir = self.ftp.pwd()
                self.ftp.cwd(name)
                self._download_recursive(new_remote, new_local)
                self.ftp.cwd(current_dir)
            else:
                remote_file = f"{remote_dir}/{name}"
                local_file = os.path.join(local_dir, name)
                self.download_file(remote_file, local_file)