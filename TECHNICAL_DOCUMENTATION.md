# Documentação Técnica: Mercado Livre Data Lake

Este documento detalha as decisões arquiteturais e técnicas tomadas para o desenvolvimento do Data Lake de e-commerce (Mercado Livre), justificando as escolhas sob a perspectiva de **escalabilidade, histórico, performance, manutenabilidade e economia de armazenamento**.

---

## 1. Clean Architecture (Arquitetura Limpa)

A estrutura de diretórios separa rigidamente as responsabilidades em camadas concêntricas. Isso permite que regras de negócio permaneçam independentes de frameworks de terceiros, bancos de dados ou protocolos de comunicação (REST, filas, CLI).

### Divisão das Camadas:
1. **Regras de Negócio e Serviços (`services/`)**: Contém a lógica central da inteligência de mercado, como o fluxo de ingestão e a lógica de detecção de drift (mudança) de preços.
2. **Abstração de Acesso a Dados (`repositories/`)**: Implementa o padrão Repository sobre o SQLAlchemy. Nenhum serviço conhece detalhes das transações ou a sintaxe das queries do banco de dados, facilitando migrações futuras ou o uso de mocks nos testes.
3. **Coletores (`collector/`)**: Camada responsável pela interação externa (Mercado Livre API e Playwright). Isola cabeçalhos, proxies, limites de taxa e lógicas de repetição.
4. **Infraestrutura e Entrada de Dados (`api/`, `workers/`, `scheduler/`)**: Camadas externas que disparam a lógica de negócio. A API expõe rotas HTTP, o worker consome do Redis Streams, e o scheduler gera triggers temporais.

---

## 2. Estratégia do Data Lake (Bronze & Silver Layers)

Para simular o pipeline clássico de um Data Lake (Bronze/Silver/Gold) usando o PostgreSQL 17, implementamos duas estratégias complementares:

### Bronze Layer (`raw_payloads`)
Toda resposta bruta retornada pela API ou Scraper é persistida em sua integridade no formato JSONB dentro da tabela `raw_payloads`. Isso garante:
- **Histórico Completo**: Se os requisitos de negócio mudarem amanhã (ex: se quisermos analisar fotos, tags de promoção, reputação detalhada que ignoramos hoje), não precisamos re-coletar os dados. Podemos ler a tabela `raw_payloads` e reprocessá-la de forma retroativa.
- **Rastreabilidade**: É possível auditar falhas comparando os dados brutos com a modelagem relacional.
- **Eficiência**: O formato JSONB do PostgreSQL é binário e semi-compactado nativamente. A tabela é particionada por mês e pode ser facilmente rotacionada ou movida para armazenamento externo frio (ex: AWS S3) para diminuir custos de armazenamento local.

### Silver Layer (`products`, `sellers`, `product_metrics_history`)
A tabela `products` e `sellers` atuam como dimensões estáticas (atributos que mudam raramente). A tabela `product_metrics_history` atua como a tabela de fatos/tempo (métricas dinâmicas como preço, estoque e status).

---

## 3. Otimização Crítica de Armazenamento: Delta Metrics

Em sistemas tradicionais de monitoramento de preços, uma nova linha é criada para cada verificação periódica. Se verificarmos um produto 4 vezes ao dia, teremos 1.460 linhas por produto ao ano. Em uma escala de 100.000 produtos, isso resulta em **146 milhões de registros ao ano**, a maioria com dados redundantes (preço e estoque idênticos).

### Mecanismo de Deduplicação implementado:
O `IngestionService` implementa uma lógica de **Drift Check**:
1. Busca a última métrica registrada para o produto na tabela `product_metrics_history`.
2. Compara os valores atuais (`price`, `original_price`, `available_quantity`, `status`) com os da última linha gravada.
3. Se todos os valores forem iguais, o sistema **descarta** a gravação da nova linha de métricas. Atualiza-se apenas a coluna `updated_at` na tabela estática `products` para registrar que o rastreamento ocorreu com sucesso.
4. Se houver qualquer variação (queda de estoque, mudança de preço, pausa de anúncio), insere-se uma nova linha registrando o timestamp exato da mudança.

> [!TIP]
> Essa decisão prioriza o **baixo consumo de armazenamento** e a **performance de consultas**. Como os preços mudam poucas vezes por semana, o volume de escrita na tabela de histórico é reduzido em até **95%**, mantendo a integridade absoluta da série histórica.

---

## 4. Particionamento Físico de Tabelas no PostgreSQL 17

À medida que o banco de dados cresce, os índices B-Tree das tabelas principais deixam de caber na memória RAM, degradando a performance de escrita e leitura de forma exponencial.

Para mitigar isso, as tabelas `product_metrics_history` e `raw_payloads` são **particionadas por intervalo (`RANGE`)** baseando-se na coluna `captured_at`.

### Benefícios:
- **Indexação Isolada**: Cada partição (um mês) possui seu próprio índice. Consultas que buscam o histórico recente de preços lerão apenas o índice do mês corrente, melhorando o tempo de resposta.
- **Query Pruning**: O planejador de consultas do PostgreSQL ignora partições fora do intervalo de busca, poupando I/O de disco.
- **Manutenção Simplificada**: É possível apagar dados antigos (ex: logs brutos de 1 ano atrás) instantaneamente rodando um `DROP TABLE` na partição correspondente, sem gerar sobrecarga de escrita/locks no banco principal.
- **Criação Dinâmica**: Criamos uma rotina assíncrona (`create_monthly_partitions`) executada durante o fluxo de ingestão que detecta se a partição do mês atual e do próximo mês existem, criando-as preventivamente no PostgreSQL.

---

## 5. Fila de Alta Performance com Redis Streams

Escolhemos **Redis Streams** em vez de RabbitMQ ou Celery por dois motivos cruciais:
1. **Consumo de Recursos**: O Redis já é utilizado para cache e controle de estados, evitando a instalação e manutenção de outro serviço pesado de mensageria (como RabbitMQ).
2. **Arquitetura de Consumer Groups**: O Redis Streams suporta grupos de consumidores natively. Isso permite inicializar múltiplos contêineres de workers (`datalake-worker`) lendo da mesma fila, escalando a capacidade de crawling sob demanda, com garantia de entrega e controle de pendências (PEL).

---

## 6. Resiliência de Coleta (Rate Limiting & Retries)

O coletor implementa uma estratégia robusta contra bloqueios comuns em scraping:
- **Retry Exponencial com Jitter**: Implementado via biblioteca `tenacity`. No caso de erros temporários de rede ou servidores do Mercado Livre indisponíveis (HTTP 5xx), o cliente aguarda um intervalo incremental antes de tentar novamente.
- **Tratamento de Rate Limit (HTTP 429)**: Se a API do Mercado Livre retornar HTTP 429, o coletor extrai o cabeçalho `Retry-After` para saber quantos segundos deve aguardar de forma exata, pausando a fila para aquele worker e evitando banimento de IP.
- **Playwright Fallback**: Configurado para rodar em modo Headless Chromium apenas quando dados de estoque dinâmico ou badges de reputação que não constam na API pública forem necessários.

---

## 7. Backups e Logs Rotativos

- **Compressão de Backups**: A automação de backup do PostgreSQL compacta a saída do `pg_dump` via `gzip` na hora da exportação, reduzindo o tamanho do arquivo em até **10 vezes**.
- **Loguru File Rotation**: Configurado para rotacionar logs de aplicação ao atingirem 10 MB, compactando os logs antigos em arquivos `.zip` e deletando arquivos com mais de 14 dias.
