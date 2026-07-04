# Mercado Livre Data Lake & e-Commerce Intelligence

Este projeto implementa um **Data Lake estruturado** para inteligência de e-commerce (Mercado Livre), com foco em **escalabilidade**, **histórico**, **performance** e **baixo consumo de armazenamento**.

O sistema é headless (sem interface gráfica), funcionando inteiramente via API REST e processamento assíncrono em segundo plano (Workers).

---

## 🛠️ Tecnologias Utilizadas

- **Backend**: Python 3.12 (com tipagem estática completa)
- **REST Framework**: FastAPI
- **Banco de Dados**: PostgreSQL 17 (com particionamento de tabelas)
- **ORM / Migrations**: SQLAlchemy 2.0 (Async) & Alembic
- **Mensageria / Fila**: Redis Streams (usando grupos de consumidores para concorrência)
- **Coleta**: API Oficial do Mercado Livre (resiliente, com retentativas exponenciais) e Playwright (como crawler de fallback para renderizações complexas)
- **Logs**: Loguru (configurado com rotação automática por tamanho e compactação)
- **Ambientes**: Docker Compose

---

## 📐 Arquitetura

O sistema é construído utilizando os princípios de **Clean Architecture** (Arquitetura Limpa), separando completamente as responsabilidades:

- `api/`: Exposição de rotas REST, validações de esquemas de entrada/saída.
- `workers/`: Consumidores da fila Redis Streams. Processam tarefas de coleta de forma assíncrona.
- `collector/`: Motores de requisições. Contém o cliente HTTP da API e o crawler Playwright.
- `scheduler/`: Disparador periódico de varreduras utilizando APScheduler.
- `database/`: Conexão, gerenciamento de sessões assíncronas e migrações.
- `models/`: Definições das tabelas do banco (SQLAlchemy).
- `repositories/`: Padrão Repository para isolar as consultas SQL da lógica de negócios.
- `services/`: Lógica central (Regras de Negócio), como processamento de payloads e a deduplicação de histórico.
- `utils/`: Utilitários compartilhados, como logs configurados com Loguru e tratamento de retry.
- `config/`: Configurações via variáveis de ambiente carregadas pelo Pydantic Settings.

---

## 📦 Como Executar

### Pré-requisitos
- Docker e Docker Compose instalados.

### Passos para Inicialização

1. Copie o arquivo de exemplo de ambiente:
   ```bash
   cp .env.example .env
   ```

2. Inicialize todos os serviços com o Docker Compose:
   ```bash
   docker-compose up --build -d
   ```

Este comando inicializa os seguintes contêineres:
- `datalake-db`: Banco de dados PostgreSQL 17.
- `datalake-redis`: Redis configurado para persistência de dados.
- `datalake-api`: API FastAPI que aplica automaticamente as migrações do banco ao iniciar.
- `datalake-worker`: Processador de fila que consome tarefas de coleta.
- `datalake-scheduler`: Gerenciador cron que enfileira tarefas periodicamente.
- `datalake-backup`: Contêiner que gera backups compactados do banco a cada 24 horas e mantém apenas os últimos 7 dias.

---

## 🔌 API Endpoints (REST)

### Health Check
- **`GET /health`**: Retorna o estado de saúde do banco de dados e do Redis.

### Produtos
- **`POST /products/{product_id}`**: Adiciona um ID de produto do Mercado Livre (ex: `MLB3396781234`) à fila de monitoramento imediatamente.
- **`GET /products/{product_id}`**: Retorna as informações estáticas do produto e a métrica de preço/estoque mais recente.
- **`GET /products/{product_id}/history`**: Retorna a série histórica de alterações de preço, estoque e status do produto (excluindo duplicados).

### Vendedores
- **`POST /sellers/{seller_id}`**: Agenda uma tarefa para enriquecer os dados cadastrais do vendedor.
- **`GET /sellers/{seller_id}`**: Retorna os detalhes do vendedor e a lista de produtos deste vendedor que estão sendo monitorados no Data Lake.

---

## 🛡️ Otimizações de Armazenamento e Performance

1. **Deduplicação de Métricas (Delta Metrics)**: A tabela `product_metrics_history` apenas armazena novas linhas se houver mudança em relação à última medição (ex: alteração de preço ou nível de estoque). Se as informações forem idênticas, a linha não é criada, economizando até 95% do armazenamento em monitoramento contínuo.
2. **Particionamento de Tabelas**: As tabelas de histórico (`product_metrics_history`) e logs brutos (`raw_payloads`) são particionadas por data (partições mensais). Isso garante alta performance nas queries ao limitar o tamanho dos índices e das varreduras físicas do disco.
3. **Backup Compactado Automático**: O serviço `db-backup` utiliza `pg_dump` compactado com `gzip` e mantém uma política de rotação que descarta cópias com mais de 7 dias para evitar estouro de disco.

---

## 🧪 Rodando os Testes

Para rodar os testes unitários localmente:

1. Instale as dependências de desenvolvimento:
   ```bash
   pip install -e ".[dev]"
   ```
2. Execute o pytest:
   ```bash
   pytest
   ```
