# RabbitMQ FTP Service

Sistema de serviço Linux que integra RabbitMQ e FTP para transferência automatizada de arquivos e diretórios, com **Interface Web completa** para gerenciamento via Flask.

## 📋 Características

- ✅ Serviço systemd nativo do Linux
- ✅ Integração completa com RabbitMQ para mensageria
- ✅ Suporte a operações FTP (upload/download de arquivos e diretórios)
- ✅ Suporte a FTP com TLS/SSL
- ✅ **Interface Web moderna com Flask** 🆕
- ✅ **CRUD completo de servidores FTP** 🆕
- ✅ **Gerenciamento de tarefas agendadas** 🆕
- ✅ **Visualização de histórico de operações** 🆕
- ✅ Configurações armazenadas em SQLite
- ✅ Logs detalhados
- ✅ API RESTful

## 🔧 Requisitos

### Sistema
- Linux (testado em Ubuntu/Debian)
- Python 3.7+
- systemd
- RabbitMQ Server

### Dependências Python
- pika >= 1.3.0
- flask >= 2.3.0
- werkzeug >= 2.3.0
- sqlite3 (incluído no Python)

## 📦 Instalação

### 1. Instalar RabbitMQ

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install rabbitmq-server
sudo systemctl enable rabbitmq-server
sudo systemctl start rabbitmq-server
```

### 2. Clonar e instalar o serviço

```bash
# Baixar os arquivos
git clone <seu-repositorio>
cd rabbitmq_ftp_service

# Executar instalação (como root)
sudo chmod +x install.sh
sudo ./install.sh
```

### 3. Configurar o serviço

```bash
# Executar configuração inicial
sudo python3 /opt/rabbitmq_ftp_service/setup_config.py
```

### 4. Iniciar o serviço

```bash
# Iniciar
sudo systemctl start rabbitmq-ftp

# Verificar status
sudo systemctl status rabbitmq-ftp

# Habilitar início automático
sudo systemctl enable rabbitmq-ftp
```

### 5. Acessar a Interface Web

Abra o navegador em:
```
http://localhost:5000
```

ou

```
http://SEU_IP:5000
```

## 🌐 Interface Web

A interface web oferece gerenciamento completo através de 4 abas principais:

### 📁 Servidores FTP
- **Criar** novos servidores FTP
- **Editar** servidores existentes
- **Deletar** servidores
- **Visualizar** todos os servidores cadastrados
- Configurar host, porta, usuário, senha e TLS
- Ativar/desativar servidores

### ⏰ Tarefas Agendadas
- **Criar** tarefas de upload/download
- **Editar** tarefas existentes
- **Deletar** tarefas
- Configurar agendamento (cron)
- Associar a servidores FTP
- Ativar/desativar tarefas

### 📊 Histórico
- Visualizar todas as operações executadas
- Ver status (sucesso/erro)
- Ver detalhes completos de cada operação
- Limpar histórico

### ⚙️ Configurações
- Configurar RabbitMQ (host, porta, filas, credenciais)
- Configurar FTP padrão
- Salvar configurações

## 📖 API RESTful

### Servidores FTP

**GET** `/api/ftp-servers` - Lista todos os servidores
```json
[
  {
    "id": 1,
    "name": "Servidor Principal",
    "host": "ftp.example.com",
    "port": 21,
    "user": "admin",
    "use_tls": false,
    "is_active": true
  }
]
```

**POST** `/api/ftp-servers` - Cria novo servidor
```json
{
  "name": "Novo Servidor",
  "host": "ftp.example.com",
  "port": 21,
  "user": "admin",
  "password": "senha123",
  "use_tls": false
}
```

**PUT** `/api/ftp-servers/{id}` - Atualiza servidor

**DELETE** `/api/ftp-servers/{id}` - Deleta servidor

### Tarefas Agendadas

**GET** `/api/tasks` - Lista todas as tarefas

**POST** `/api/tasks` - Cria nova tarefa
```json
{
  "name": "Backup Diário",
  "task_type": "upload_directory",
  "ftp_server_id": 1,
  "local_path": "/backup",
  "remote_path": "/remote/backup",
  "schedule": "0 2 * * *"
}
```

**PUT** `/api/tasks/{id}` - Atualiza tarefa

**DELETE** `/api/tasks/{id}` - Deleta tarefa

### Histórico

**GET** `/api/operations?limit=50` - Lista operações

**DELETE** `/api/operations/{id}` - Deleta operação

### Configurações

**GET** `/api/config` - Obtém configurações

**POST** `/api/config` - Atualiza configurações

## 📝 Uso via RabbitMQ

### Estrutura das Mensagens

Envie mensagens JSON para a fila `ftp_commands`:

#### Upload de Arquivo

```json
{
  "command": "upload_file",
  "local_path": "/caminho/local/arquivo.txt",
  "remote_path": "/caminho/remoto/arquivo.txt"
}
```

#### Download de Arquivo

```json
{
  "command": "download_file",
  "remote_path": "/caminho/remoto/arquivo.txt",
  "local_path": "/caminho/local/arquivo.txt"
}
```

#### Upload de Diretório

```json
{
  "command": "upload_directory",
  "local_dir": "/caminho/local/pasta",
  "remote_dir": "/caminho/remoto/pasta"
}
```

#### Download de Diretório

```json
{
  "command": "download_directory",
  "remote_dir": "/caminho/remoto/pasta",
  "local_dir": "/caminho/local/pasta"
}
```

### Respostas

As respostas são enviadas para a fila `ftp_responses`:

```json
{
  "command": "upload_file",
  "status": "success",
  "timestamp": "2025-10-27T10:30:00",
  "result": {
    "success": true
  }
}
```

## 🗂️ Estrutura de Arquivos

```
/opt/rabbitmq_ftp_service/
├── service.py              # Código principal do serviço
├── requirements.txt        # Dependências Python
├── setup_config.py         # Script de configuração
└── templates/
    └── index.html          # Interface web

/etc/rabbitmq_ftp_service/
└── config.db              # Banco de dados SQLite

/etc/systemd/system/
└── rabbitmq-ftp.service   # Unit file do systemd

/var/log/
└── rabbitmq_ftp_service.log  # Logs do serviço
```

## 🗄️ Banco de Dados SQLite

### Tabelas

#### `config`
Configurações gerais do sistema

#### `ftp_servers`
Cadastro de servidores FTP
- id, name, host, port, user, password, use_tls, is_active

#### `scheduled_tasks`
Tarefas agendadas
- id, name, task_type, ftp_server_id, local_path, remote_path, schedule, is_active

#### `operation_history`
Histórico de operações
- id, operation_type, status, details, created_at

## 🔒 Segurança

### Firewall

Para acessar a interface web de outras máquinas:

```bash
# Permitir porta 5000
sudo ufw allow 5000/tcp

# Ou apenas de uma rede específica
sudo ufw allow from 192.168.1.0/24 to any port 5000
```

### Recomendações

1. **Mude a porta padrão** (5000) em produção
2. **Use HTTPS** com proxy reverso (nginx/apache)
3. **Implemente autenticação** na interface web
4. **Não use credenciais padrão** do RabbitMQ
5. **Ative TLS/SSL** no FTP quando possível
6. **Configure firewall** adequadamente
7. **Faça backup** do banco de dados regularmente

## 🐛 Troubleshooting

### Interface web não carrega

```bash
# Verificar se o serviço está rodando
sudo systemctl status rabbitmq-ftp

# Verificar logs
sudo journalctl -u rabbitmq-ftp -n 50

# Verificar se a porta está em uso
sudo netstat -tlnp | grep 5000
```

### Erro ao conectar FTP

```bash
# Testar conexão FTP manualmente
ftp <host-ftp>

# Verificar logs
sudo tail -f /var/log/rabbitmq_ftp_service.log
```

### RabbitMQ não conecta

```bash
# Verificar status do RabbitMQ
sudo systemctl status rabbitmq-server

# Testar conexão
telnet localhost 5672
```

## 📊 Monitoramento

### Ver logs em tempo real

```bash
# Logs do serviço
sudo journalctl -u rabbitmq-ftp -f

# Logs da aplicação
sudo tail -f /var/log/rabbitmq_ftp_service.log
```

### Estatísticas do banco

```bash
sqlite3 /etc/rabbitmq_ftp_service/config.db

# Dentro do sqlite:
SELECT COUNT(*) FROM ftp_servers;
SELECT COUNT(*) FROM scheduled_tasks;
SELECT COUNT(*) FROM operation_history;
```

## 🔄 Atualizações

```bash
# Parar serviço
sudo systemctl stop rabbitmq-ftp

# Atualizar código
sudo cp service.py /opt/rabbitmq_ftp_service/
sudo cp templates/index.html /opt/rabbitmq_ftp_service/templates/

# Reiniciar
sudo systemctl start rabbitmq-ftp
```

## 📄 Licença

MIT License

## 🤝 Contribuindo

Contribuições são bem-vindas! Por favor:
1. Fork o projeto
2. Crie uma branch para sua feature
3. Commit suas mudanças
4. Push para a branch
5. Abra um Pull RequestDebian)
- Python 3.7+
- systemd
- RabbitMQ Server

### Dependências Python
- pika >= 1.3.0
- sqlite3 (incluído no Python)

## 📦 Instalação

### 1. Instalar RabbitMQ

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install rabbitmq-server
sudo systemctl enable rabbitmq-server
sudo systemctl start rabbitmq-server
```

### 2. Clonar e instalar o serviço

```bash
# Baixar os arquivos
git clone <seu-repositorio>
cd rabbitmq_ftp_service

# Executar instalação (como root)
sudo chmod +x install.sh
sudo ./install.sh
```

### 3. Configurar o serviço

```bash
# Executar configuração inicial
sudo python3 /opt/rabbitmq_ftp_service/setup_config.py
```

Você será solicitado a informar:

**Configuração RabbitMQ:**
- Host (padrão: localhost)
- Porta (padrão: 5672)
- Usuário (padrão: guest)
- Senha (padrão: guest)
- Fila de entrada (padrão: ftp_commands)
- Fila de saída (padrão: ftp_responses)

**Configuração FTP:**
- Host do servidor FTP
- Porta (padrão: 21)
- Usuário
- Senha
- Usar TLS (s/n)

### 4. Iniciar o serviço

```bash
# Iniciar
sudo systemctl start rabbitmq-ftp

# Verificar status
sudo systemctl status rabbitmq-ftp

# Habilitar início automático
sudo systemctl enable rabbitmq-ftp
```

## 📖 Uso

### Comandos do Sistema

```bash
# Iniciar serviço
sudo systemctl start rabbitmq-ftp

# Parar serviço
sudo systemctl stop rabbitmq-ftp

# Reiniciar serviço
sudo systemctl restart rabbitmq-ftp

# Ver status
sudo systemctl status rabbitmq-ftp

# Ver logs em tempo real
sudo journalctl -u rabbitmq-ftp -f

# Ver últimos logs
sudo journalctl -u rabbitmq-ftp -n 100
```

### Cliente Interativo

```bash
# Executar cliente exemplo
python3 client_example.py
```

O cliente oferece um menu interativo para:
1. Upload de arquivo
2. Download de arquivo
3. Upload de diretório
4. Download de diretório
5. Atualizar configuração FTP
6. Atualizar configuração RabbitMQ

### Enviar Comandos via RabbitMQ

#### Estrutura das Mensagens

Todas as mensagens devem ser enviadas em formato JSON para a fila `ftp_commands`.

#### Upload de Arquivo

```json
{
  "command": "upload_file",
  "local_path": "/caminho/local/arquivo.txt",
  "remote_path": "/caminho/remoto/arquivo.txt"
}
```

#### Download de Arquivo

```json
{
  "command": "download_file",
  "remote_path": "/caminho/remoto/arquivo.txt",
  "local_path": "/caminho/local/arquivo.txt"
}
```

#### Upload de Diretório

```json
{
  "command": "upload_directory",
  "local_dir": "/caminho/local/pasta",
  "remote_dir": "/caminho/remoto/pasta"
}
```

#### Download de Diretório

```json
{
  "command": "download_directory",
  "remote_dir": "/caminho/remoto/pasta",
  "local_dir": "/caminho/local/pasta"
}
```

#### Atualizar Configuração

```json
{
  "command": "update_config",
  "config_type": "ftp",
  "config_data": {
    "host": "ftp.exemplo.com",
    "port": 21,
    "user": "usuario",
    "password": "senha",
    "use_tls": false
  }
}
```

### Respostas

As respostas são enviadas para a fila `ftp_responses` no formato:

```json
{
  "command": "upload_file",
  "status": "success",
  "timestamp": "2025-10-27T10:30:00",
  "result": {
    "success": true
  }
}
```

Em caso de erro:

```json
{
  "command": "upload_file",
  "status": "error",
  "timestamp": "2025-10-27T10:30:00",
  "error": "Descrição do erro"
}
```

## 🗂️ Estrutura de Arquivos

```
/opt/rabbitmq_ftp_service/
├── service.py              # Código principal do serviço
├── requirements.txt        # Dependências Python
└── setup_config.py         # Script de configuração

/etc/rabbitmq_ftp_service/
└── config.db              # Banco de dados SQLite

/etc/systemd/system/
└── rabbitmq-ftp.service   # Unit file do systemd

/var/log/
└── rabbitmq_ftp_service.log  # Logs do serviço
```

## 🗄️ Banco de Dados

O serviço usa SQLite para armazenar:

### Tabela `config`
- `key`: Chave da configuração
- `value`: Valor (JSON)
- `updated_at`: Data de atualização

### Tabela `operation_history`
- `id`: ID da operação
- `operation_type`: Tipo de operação
- `status`: Status (success/error)
- `details`: Detalhes (JSON)
- `created_at`: Data de criação

### Consultar Histórico

```python
import sqlite3

conn = sqlite3.connect('/etc/rabbitmq_ftp_service/config.db')
cursor = conn.cursor()

# Últimas 10 operações
cursor.execute('''
    SELECT * FROM operation_history 
    ORDER BY created_at DESC 
    LIMIT 10
''')

for row in cursor.fetchall():
    print(row)

conn.close()
```

## 🔒 Segurança

### Permissões de Arquivos

O serviço roda com o usuário `rabbitmq-ftp` com privilégios limitados:
- Acesso apenas aos diretórios necessários
- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`

### Recomendações

1. **Não use credenciais padrão** em produção
2. **Ative TLS/SSL** no FTP quando possível
3. **Configure firewall** para limitar acesso ao RabbitMQ
4. **Monitore os logs** regularmente
5. **Faça backup** do banco de dados de configuração

## 🐛 Troubleshooting

### Serviço não inicia

```bash
# Verificar logs
sudo journalctl -u rabbitmq-ftp -n 50

# Verificar se RabbitMQ está rodando
sudo systemctl status rabbitmq-server

# Testar conexão RabbitMQ
telnet localhost 5672
```

### Problemas com FTP

```bash
# Testar conexão FTP manualmente
ftp <host-ftp>

# Verificar logs do serviço
sudo tail -f /var/log/rabbitmq_ftp_service.log
```

### Permissões negadas

```bash
# Verificar permissões
ls -la /etc/rabbitmq_ftp_service/
ls -la /opt/rabbitmq_ftp_service/

# Corrigir se necessário
sudo chown -R rabbitmq-ftp:rabbitmq-ftp /etc/rabbitmq_ftp_service/
sudo chown -R rabbitmq-ftp:rabbitmq-ftp /opt/rabbitmq_ftp_service/
```

## 📝 Exemplo de Uso Programático

```python
import pika
import json

# Conectar ao RabbitMQ
credentials = pika.PlainCredentials('guest', 'guest')
connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost', 5672, credentials=credentials)
)
channel = connection.channel()

# Enviar comando de upload
message = {
    'command': 'upload_file',
    'local_path': '/tmp/teste.txt',
    'remote_path': '/upload/teste.txt'
}

channel.basic_publish(
    exchange='',
    routing_key='ftp_commands',
    body=json.dumps(message)
)

print("Comando enviado!")
connection.close()
```

## 🔄 Atualizações

Para atualizar o serviço:

```bash
# Parar serviço
sudo systemctl stop rabbitmq-ftp

# Atualizar código
sudo cp service.py /opt/rabbitmq_ftp_service/

# Reiniciar
sudo systemctl start rabbitmq-ftp
```

## 📄 Licença

MIT License

## 🤝 Contribuindo

Contribuições são bem-vindas! Por favor:
1. Fork o projeto
2. Crie uma branch para sua feature
3. Commit suas mudanças
4. Push para a branch
5. Abra um Pull Request

## 📞 Suporte

Para problemas ou dúvidas:
- Abra uma issue no GitHub
- Consulte os logs do serviço
- Verifique a documentação do RabbitMQ e FTP