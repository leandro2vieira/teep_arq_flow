# Serviço RabbitMQ FTP

Sistema de serviço Linux que integra RabbitMQ e FTP para transferência automatizada de arquivos e diretórios, com **Interface Web** para gerenciamento via Flask.

## 📋 Funcionalidades atuais

- ✅ service\ (systemd) para Linux (arquivo de unidade incluído)  
- ✅ Integração com RabbitMQ para mensagens de comando/resposta  
- ✅ Suporte a FTP, FTPS (TLS) e SCP 
- ✅ Interface web com Flask para gerenciamento e monitoramento  
- ✅ CRUD para servidores periféricos
- ✅ Histórico detalhado de operações com logging e persistência (SQLite)  
- ✅ Upload/download recursivo de  arquivos e diretórios  
- ✅ Exclusão remota recursiva (arquivos e diretórios)  
- ✅ Listagem remota robusta usando MLSD quando disponível, fallback para LIST  
- ✅ Operações FTP mais seguras: contexto para mudança de diretório remoto, tentativas e fallbacks para stor/retr  
- ✅ Construção e envio de respostas unificados no handler `GenericFileTransfer`  
- ✅ Tratamento de erros e logging estruturado entre os módulos

## 🗂️ Uso via RabbitMQ

Envie mensagens JSON para a fila de entrada configurada (padrão `recv_queue_index_$`). As respostas são publicadas na fila de saída configurada (padrão `send_queue_index_$`). Substitua o `\$` pelo índice do servidor periférico configurado.

Ações de alto nível suportadas:
- upload_file, download_file  
- upload_directory, download_directory  
- delete_remote_file, delete_remote_directory  
- create_file_from_template (cria arquivo a partir de template JSON e envia ao servidor remoto)
- get_remote_file_tree, get_server_file_tree  
- Operações de stream enviam notificações START/FINISH para transferências grandes

### Lista de actions suportadas
| Nome da action              | Código | Descrição                                                                                      |
|-----------------------------|-------:|------------------------------------------------------------------------------------------------|
| `START_STREAM_FILE`         |     33 | Indica início do envio de arquivo (stream) do servidor local para o FTP remoto                 |
| `FINISH_STREAM_FILE`        |     34 | Indica conclusão bem-sucedida do envio de arquivo                                              |
| `STREAM_FILE`               |     35 | Comando para enviar um único arquivo                                                           |
| `START_DOWNLOAD_FILE`       |     55 | Indica início do download de arquivo do FTP remoto para o servidor local                       |
| `FINISH_DOWNLOAD_FILE`      |     56 | Indica conclusão bem-sucedida do download de arquivo                                           |
| `DOWNLOAD_FILE`             |     63 | Comando para baixar um único arquivo                                                           |
| `ERROR_DOWNLOAD_FILE`       |     57 | Indica erro no download; detalhes estarão em `value`                                           |
| `GET_SERVER_FILE_TREE`      |     58 | Solicita a árvore de arquivos do servidor local                                                |
| `GET_REMOTE_FILE_TREE`      |     59 | Solicita a árvore de arquivos do FTP remoto                                                    |
| `SERVER_FILE_TREE`          |     60 | Resposta com a árvore de arquivos do servidor local                                            |
| `CLIENT_FILE_TREE`          |     61 | Resposta com a árvore de arquivos do FTP remoto                                                |
| `ERROR`                     |     62 | Erro em alguma operação; detalhes em `value`                                                   |
| `DELETE_REMOTE_FILE`        |     63 | Comando para deletar arquivo no FTP remoto (retorna mesmo código em caso de sucesso)           |
| `DELETE_REMOTE_DIRECTORY`   |     64 | Comando para deletar diretório remoto recursivamente (retorna mesmo código em caso de sucesso) |
| `STREAM_DIRECTORY`          |     65 | Comando para enviar diretório local ao FTP remoto                                              |
| `DOWNLOAD_DIRECTORY`        |     66 | Comando para baixar diretório do FTP remoto para o servidor local                              |
| `PROGRESS_SEND_FILE`        |     67 | Indica o progresso de envio de arquivos `value`                                                |
| `LIST_PERIPHERALS`          |     68 | Lista periféricos                                                                              |
| `REMOTE_REBOOT`             |     69 | Envia commando reboot por ssh                                                                  |
| `CREATE_FILE_FROM_TEMPLATE` |     70 | Cria novo arquivo a partir de template, troca nome e envia para (S)FTP ou SCP                  |

## Modos de uso:

Listar arquivos do servidor local
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
```json
{
  "action": 58,
  "data": { 
    "index": $,
    "value": {
      "local_path": "/program5/"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 60,
  "data": {
    "index": $,
    "value": {
      "success": true,
      "files": [
        {
          "name": "file.txt",
          "is_dir": false,
          "size": 52144265,
          "modified": 1762434280
        },
        {
          "name": "folder1",
          "is_dir": true,
          "size": 4096,
          "modified": 1762456808
        }
      ]
    }
  }
}
```
Listar arquivos do servidor FTP remoto
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
```json
{
  "action": 59,
  "data": { 
    "index": $,
    "value": {
      "remote_path": "/"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 61,
  "data": {
    "index": $,
    "value": [
      {
        "name": "file.txt", 
        "path": "/remote/dir/file.txt", 
        "type": "file", 
        "size": 21, 
        "mtime": "Nov 06 16:20"
      }, {
        "name": "file", 
        "path": "/remote/dir/file", 
        "type": "directory", 
        "size": 4096, 
        "mtime": "Nov 06 14:47"
      }
    ]
  }
}
```
Fazer upload de pasta local para o servidor FTP remoto
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento 
```json
{
  "action": 65, 
  "data": {
    "index": $, 
    "value": {
      "local_path": "/program5/", 
      "remote_path": "/"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 33, 
  "data": {
    "index": $, 
    "value": "", 
    "timestamp": 1762461866
  }
}
```
Fazer download de pasta do servidor FTP remoto para o local
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento
```json
{
  "action": 66, 
  "data": {
    "index": $, 
    "value": {
      "local_path": "/program5/", 
      "remote_path": "/"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 55, 
  "data": {
    "index": $, 
    "value": "", 
    "timestamp": 1762462077
  }
}
```
Deletar pasta remoto no servidor FTP
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento
```json
{
  "action": 64, 
  "data": {
    "index": $, 
    "value": {
      "remote_path": "/"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 64, 
  "data": {
    "index": $, 
    "value": {
      "success": true
    }, 
    "timestamp": 1762462249
  }
}
```
Enviar arquivo local para o servidor FTP remoto
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento
```json
{
  "action": 35, 
  "data": {
    "index": $, 
    "value": {
      "local_path": "/program5/job1.txt", 
      "remote_path": "/"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 33, 
  "data": {
    "index": $, 
    "value": "", 
    "timestamp": 1762463590
  }
}
```
Fazer download de arquivo do servidor FTP remoto para o local
- Enviar o seguinte JSON para a fila recv_queue_index_$ RabbitMQ:
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento
```json
{
  "action": 63, 
  "data": {
    "index": $, 
    "value": {
      "local_path": "/program5/", 
      "remote_path": "/job1.txt"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ.
```json
{
  "action": 55, 
  "data": {
    "index": $, 
    "value": "", 
    "timestamp": 1762463711
  }
}
```
Progresso no envio de arquivo ou diretório:
- A resposta para envio de diretório:
```json
{
    "action": 67,
    "data": {
        "index": "88",
        "value": {
            "file": "program.txt",
            "file_index": 1,
            "total_files": 1,
            "bytes_sent": 0,
            "total_bytes": 0,
            "percent": 100
        },
        "timestamp": 1762873063
    }
}
```
- A resposta para envio de arquivo:
```json
{
    "action": 67,
    "data": {
        "index": "88",
        "value": {
          "file": "program.txt", 
          "bytes_sent": 4096, 
          "total_bytes": 4096, 
          "percent": 100 
        },
        "timestamp": 1762873063
    }
}
```
- Listar periféricos no automation
```json
{
  "action": 68, 
  "data": {
    "index": $, 
    "value": "", 
    "timestamp": 1762463711
  }
}
```
- A resposta será:
```json
{
  "action": "68", 
  "data": {
    "index": $,
    "value": [
      {
        "index": 1, 
        "name": "Robo1", 
        "send_to": "send_queue_index_1"
      }, {
        "index": 2, 
        "name": "Robo2", 
        "send_to": "send_queue_index_2"
      }, {
        "index": 3, 
        "name": "Robo3", 
        "send_to": "send_queue_index_3"
      }
    ]
  }
}
```
Criar arquivo a partir de template JSON e enviar para o servidor remoto
- Enviar o seguinte JSON para a fila send_queue_index_$ RabbitMQ:
- O `local_path` aponta para o arquivo template JSON no servidor local (relativo ao `server_side_path` do periférico)
- O `remote_path` indica a pasta de destino no servidor remoto (relativo ao `remote_side_path` do periférico)
- O `name` é o valor que será escrito em `programa.name` dentro do template
- O "extra" é opcional, funciona apenas para automation, forca enviar para apenas um redirecionamento
```json
{
  "action": 70, 
  "data": {
    "index": $, 
    "value": {
      "name": "NomeDoProgramaAqui",
      "local_path": "/templates/template.json", 
      "remote_path": "/destino/"
    },
    "extra": {
      "index": 3, 
      "name": "Robo3", 
      "send_to": "send_queue_index_3"
    }
  }
}
```
- A resposta será enviada para a fila recv_queue_index_$ RabbitMQ:
```json
{
  "action": 70, 
  "data": {
    "index": $, 
    "value": {
      "success": true,
      "message": "",
      "result": "File uploaded to /destino/template.json"
    }, 
    "timestamp": 1762463590
  }
}
```
