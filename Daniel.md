# MIND: ETL (Bronze → Silver → Gold) cruzada com a tela Macro CLP

> Documento de contexto para o Codex. Junta (1) a infraestrutura e o ETL como estão hoje, segundo a documentação técnica
> produzida com outro assistente (ChatGPT), com (2) o que a tela **Macro CLP** do cliente precisa de dados.
>
> Legenda usada nas tabelas e listas:
> - **[CONFIRMADO]** informado na documentação técnica; ainda assim, confira no código real.
> - **[VERIFICAR]** não se sabe; o Codex deve ler o código/banco e responder antes de qualquer alteração.
> - **[PROPOSTA]** sugestão deste documento, ainda não decidida pela equipe.
> - **[PENDENTE]** decisão de negócio em aberto: **perguntar, não decidir sozinho**.

---

## 1. Como o Codex deve usar este documento

1. **Primeiro, só leitura.** Leia os scripts reais e o banco antes de editar. Este documento pode estar desatualizado.
2. Use o **modo plan**: mostre o plano antes de qualquer edição.
3. Diferencie sempre **testado** de **não testado** (seção 4). O fluxo completo Bronze → Silver → Gold **ainda não foi validado**.
4. Não invente regras de negócio, nomes de campos, tabelas ou parâmetros. Quando faltar informação, pergunte.
5. Nunca escreva senhas, chaves ou tokens em arquivos, commits ou respostas.
6. O objetivo desta fase: **adaptar somente a camada Gold** para entregar os dados da tela Macro CLP (seções 6 a 9), respeitando as restrições abaixo.

### 1.1 Restrições de escopo (valem acima de qualquer outra instrução)

| Camada | Regra |
|---|---|
| **Bronze** | Roda na máquina local do usuário. **Fora do escopo: não alterar.** |
| **Silver** | Padronizada para o projeto inteiro (outras telas e outras pessoas dependem dela). **Somente leitura: não alterar script, colunas, formato nem nomes de arquivo.** |
| **Gold** | Única camada em que se trabalha, e **somente de forma aditiva** (regras abaixo). |

Regras para o Gold:
- **Não apagar, renomear, mover nem sobrescrever** nada que já exista: código, funções, pastas, arquivos no S3 ou saídas atuais. Outras telas podem depender deles.
- **Não mudar o comportamento** das funções existentes. O código novo vai em arquivo ou função **nova**, chamada a partir do `main.py` com a menor alteração possível (só acrescentar a chamada). Mostrar o plano antes.
- Escrever as saídas novas **somente em caminho novo** do S3 (ex.: `gold/id-empresa-{idEmpresa}/clp-macro/`), nunca nos caminhos que o Gold já usa.
- Ler o Silver e consultar o MySQL é permitido (o Gold já consulta o MySQL). **Não escrever no MySQL, não criar tabelas nem colunas.**
- Se uma necessidade da tela **não puder ser atendida sem mexer em Bronze, Silver ou no schema do banco**, não resolver mexendo neles: registrar como **bloqueio**, explicar o motivo e perguntar.

---

## 2. Arquitetura atual (resumo do que foi montado)

**Fluxo** [CONFIRMADO]
1. Computador local coleta métricas de hardware do CLP e gera **CSV**.
2. CSV vai para o S3, prefixo `bronze/`.
3. Na **EC2**, `bronze2silver.py` lê o Bronze, consulta os limites no MySQL, valida/transforma, calcula o `health_score` e grava em `silver/`.
4. Na **EC2**, `etl-silver-gold/main.py` processa o Silver e grava em `gold/`.

**AWS** [CONFIRMADO]
- Bucket único: `s3-raw-lab1542541524152`, região `us-east-1`, com os prefixos `bronze/`, `silver/` e `gold/`.
  (O diagrama de arquitetura ainda mostra 3 buckets; está desatualizado.)
- EC2 Ubuntu. Projeto em `~/itops`, ambiente virtual `~/itops/.venv`.
- A EC2 acessa o S3 pela **IAM Role** (sem chaves no `.env` da EC2). O computador local tem configuração própria de acesso.
- A infraestrutura é de **teste e validação**, não de produção definitiva.

**MySQL** [CONFIRMADO]
- MySQL 8.4 na EC2, banco `mind`.
- Tabelas verificadas: `ativacao`, `clp`, `clp_medicao`, `empresa`, `modelo`, `tipo_medicao`, `usuario`.
- **Não há tabela de setor nem de alertas nessa lista.** Ver seção 8.

**Arquivos** [CONFIRMADO]
- Local (máquina do usuário): `script_escrita_mind.py` (coleta, gera CSV), `rodar.py` (executa a coleta de forma recorrente).
- EC2, em `~/itops`: `etl-bronze-to-silver/bronze2silver.py`, `etl-silver-gold/main.py`, `etl-silver-gold/config.py`, `.env`.
- Arquivos antigos (`bronze2silver.py`, `silver2gold.py`, `script_bronze.py`, pastas `bronze/`, `silver/`, `gold/`) **não** são a implementação atual. Não apagar nem usar sem verificar.
- Variáveis de ambiente usadas: `AWS_BUCKET_NAME`, `AWS_BUCKET_REGION`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`; na coleta: `ID_CLP`, `ID_EMPRESA`, `MODELO_PLC_MONITORADO`, `NOME_MODELO_CLP`, `ID_FORNECEDOR`, `LOCALIZACAO_CLP`, `AWS_BUCKET_NAME`.
- O `.env` fica em `~/itops/.env`; o `config.py` do Gold já foi corrigido para apontar para ele.

---

## 3. Instruções herdadas da outra documentação (regras de preservação)

- Não reestruturar o projeto, não trocar bibliotecas, não adicionar frameworks ou dependências sem justificativa.
- Preservar **nomes de colunas, identificadores e contratos de dados** entre Bronze, Silver e Gold.
- Não alterar o schema do banco, nem criar tabelas, colunas ou parâmetros, sem justificativa e aprovação.
- **Não alterar a fórmula do Health Score, os pesos nem os indicadores** sem decisão expressa de negócio.
- Se uma regra de negócio divergir da implementação, **documentar a divergência** em vez de corrigir sozinho.
- Antes de mexer em um campo ou formato, ver como os scripts seguintes o usam.
- Não remover arquivos antigos automaticamente.
- **Bronze e Silver ficam intocados; o Gold só recebe acréscimos** (seção 1.1).

---

## 4. O que foi feito e validado, e o que não foi

**Validado** [CONFIRMADO]
- MySQL instalado, banco `mind` importado, tabelas e registros iniciais conferidos.
- Conexão Python com MySQL (`mysql.connector`) funcionando.
- Acesso ao S3 pela IAM Role da EC2 com Boto3; listagem de `bronze/` funcionou (o prefixo estava **vazio**).
- Consulta de limites para o CLP 1 funcionou.
- Sintaxe dos três arquivos principais (`py_compile`) sem erro.
- Caminho do `.env` no `config.py` do Gold corrigido.

**NÃO validado**
- Coleta local gerando um CSV novo e enviando para `bronze/`.
- Execução de `bronze2silver.py` com um arquivo real e gravação em `silver/`.
- Execução de `etl-silver-gold/main.py` e gravação em `gold/`.
- Se o que o Gold gera respeita as regras de negócio.
- **O que o Gold atual realmente produz** [VERIFICAR]: a documentação técnica só descreve `get_measurements()`, `get_limit()` e `main()` e diz que usa `id_fornecedor` e `id_empresa`. Não há descrição das agregações nem do formato de saída. Não presumir.

---

## 5. Contratos de dados conhecidos

**Silver** (CSV, mesmo nome do arquivo Bronze: `silver/nome_do_arquivo.csv`) [CONFIRMADO]
Campos citados: identificação do CLP, da empresa e do fornecedor; uptime; utilização de CPU, RAM e SWAP; tempo de SCAN; latência; informações de memória; velocidades de upload e download; `health_score`.
- Nomes exatos das colunas, tipos e **coluna de data/hora**: [VERIFICAR]. A documentação técnica não cita timestamp, mas ele é indispensável para filtrar por período.
- O Bronze→Silver só processa arquivos `.csv` com registros, modificados nos **últimos 15 minutos**, e exige a coluna `id_fornecedor`.

**Limites por CLP** (MySQL: `clp_medicao` + `tipo_medicao`, apenas `status = 1`) [CONFIRMADO]
Exemplo (CLP 1): CPU 80, RAM 80, UPTIME 95, SWAP 80, SCAN 100, PING 100, LOSS 1, UPLOAD 500, DOWNLOAD 500, PROCESSOS 3, **HEALTH 70**.
Mapeados no Silver: CPU→cpu, RAM→ram, SWAP→swap, SCAN→scan_time, PING→latencia.

**Health Score** (`calcular_health_score`) [CONFIRMADO, não alterar]
- Pesos: CPU 20, RAM 20, SWAP 15, SCAN 20, Latência 15 (soma 90).
- Nota de cada indicador: `max(0, 100 - valor / limite * 100)`.
- Resultado: média ponderada das cinco notas, 2 casas decimais. Valor, limite ausente ou inválido → retorna string vazia (tratar como **sem health**, nunca como 0).
- [VERIFICAR] se a média divide por 90 (resultado de 0 a 100) ou por 100 (máximo 90).

**Estrutura de pastas do Gold já definida no projeto** [CONFIRMADO no documento do projeto, item 18]
- Clientes: `gold/id-empresa-{idEmpresa}/id-clp-{idClp}/` com `clp-ultimos-processos`, `clp-micro`, `clp-ranking-processos`, `clp-ping`.
- Fornecedores: `gold/id-empresa-{idEmpresa}/` com `modelo-health-score-historico`, `modelo-health-score-status`, `modelo-resumo`.
- [VERIFICAR] se o Gold atual já grava dessa forma.

---

## 6. A tela Macro CLP: o que ela precisa

Tela do **cliente final** (gestor da operação), visão macro da planta. Filtros: **período** (24h, 7 dias, 30 dias), **setor**, atualização.
Faixas de health usadas na tela: **Crítico < 40, Atenção 40 a 59, Estável ≥ 60** (provisórias, ver seção 8).

### Cruzamento tela × dados

| Item da tela | Dado necessário | Fonte provável | Situação |
|---|---|---|---|
| Health geral da planta | média do health dos CLPs **online** | `health_score` do Silver | ✅ existe; falta agregação |
| Disponibilidade (ex.: 20 de 30) | CLPs online ÷ total de CLPs da empresa | online: Silver/hora da última leitura; total: tabela `clp` | ⚠ falta definir "online" e o total |
| CLPs críticos (ex.: 7) | CLPs com health abaixo do limite de crítico | `health_score` + faixa | ⚠ depende da faixa oficial |
| Setor mais preocupante | setor com menor health médio | setor de cada CLP | ❌ **setor não está nas tabelas listadas** |
| 5 piores setores: health médio | média por setor, só online, sem fora da curva | setor + health | ❌ depende de setor |
| "5/8 online" por setor | CLPs online e total por setor | `clp` + setor | ❌ depende de setor |
| "Os outros 37 setores ≥ 62" | total de setores; menor health fora do top 5 | idem | ❌ depende de setor |
| CLP fora da curva | health do CLP, média do setor com e sem ele | regra da seção 7 | ❌ não implementado em lugar nenhum |
| Situação da frota | contagem de estável / atenção / crítico / offline | health + online | ⚠ falta definir online |
| Ranking: apelido | nome/apelido do CLP | tabela `clp` | [VERIFICAR] coluna existe? |
| Ranking: setor | setor do CLP | [VERIFICAR] (`LOCALIZACAO_CLP` na coleta?) | ❌ |
| Ranking: modelo | nome do modelo | `modelo` / `NOME_MODELO_CLP` | ⚠ verificar |
| Ranking: health | health médio do CLP no período | `health_score` por data | ⚠ falta data/hora no contrato |
| Ranking: status | faixa do health | regra de faixa | ⚠ |
| Ranking: alertas | quantidade de alertas no período | **não existe** | ❌ ver seção 7 |
| Ranking: componente mais problemático | componente com mais alertas | leituras vs limites | ❌ ver seção 7 |
| Última atualização | data/hora de geração do Gold | gerada no Gold | ✅ simples |

---

## 7. Definições de negócio que o cálculo exige

Cada item abaixo precisa de decisão ou de confirmação **antes de implementar**.

1. **CLP online** [PENDENTE]: sugestão de regra: o CLP enviou pelo menos uma leitura nos últimos N minutos. A janela de 15 min do Bronze→Silver é uma referência, não uma decisão. Offline = **sem health** e fora de todas as médias.
2. **Total de CLPs** [PROPOSTA]: CLPs cadastrados da empresa na tabela `clp` (não só os que enviaram dados, senão nunca se enxerga um offline).
3. **Health do CLP no período** [PENDENTE]: média do `health_score` no período escolhido, ou última leitura? A tela usa "Health médio".
4. **Faixas de status** [PENDENTE]: ver seção 8. Concentrar os limites em **constantes no topo do código**.
5. **Setor do CLP** [PENDENTE]: de onde vem. Como Bronze e Silver não podem mudar, o setor só serve à tela se **já existir** no Silver (verificar se `LOCALIZACAO_CLP` vira coluna do CSV) ou no MySQL (tabela `clp` ou outra), que o Gold pode ler. Se não existir em nenhum dos dois, é um **bloqueio**: não criar coluna nem alterar a coleta; registrar e levar à equipe.
6. **Alerta** [PENDENTE]: não há tabela de alertas. Sugestão: leitura de um componente **acima do limite** cadastrado (CPU, RAM, SWAP, SCAN, PING) conta como 1 alerta. O motor de alertas do projeto (Java, Slack) pode ter outra definição: verificar para não divergir. O cálculo deve ficar **no Gold**, a partir dos valores do Silver e dos limites do MySQL; nada a montante muda.
7. **Componente mais problemático** [PENDENTE]: componente com mais alertas no período no CLP. Em caso de empate, definir o critério.
8. **CLP fora da curva** [DECIDIDO NO NEGÓCIO, não implementado]:
   - só para **baixo**;
   - o CLP está em **Crítico** e o restante do setor, sem ele, está em **Atenção ou Estável**;
   - se o setor inteiro está crítico, ninguém é fora da curva;
   - a barra do setor usa a média **sem** o fora da curva, mas ele continua contado no ranking, nos CLPs críticos e na situação da frota;
   - mínimo de CLPs online por setor para aplicar a regra: [PENDENTE] (sugestão: 4).
9. **Período** [PENDENTE]: o Gold precisa filtrar por data. Como os arquivos Silver têm o nome do Bronze, é preciso saber se o nome ou uma coluna carrega a data/hora. Se o Silver não tiver data/hora utilizável, é um **bloqueio** (não alterar o Silver).
10. **Disponibilidade e meta** [PENDENTE]: o banco tem o parâmetro `UPTIME = 95` para o CLP 1. Pode servir de meta de disponibilidade, mas ainda não há decisão. A tela atual não mostra meta.

---

## 8. Divergências e pontos de atenção

1. **Faixas de health: três versões em circulação.** Especificação do Micro CLP: Crítico < 40, Atenção 40–59, Estável ≥ 60. Tela Macro Modelo (outro integrante): < 50, 50–74, ≥ 75. O banco tem ainda `HEALTH = 70` por CLP. Usar 40/60 em constantes até decisão.
2. **Efeito da fórmula nas faixas** (aritmética, para a equipe avaliar): com `nota = 100 - valor/limite*100`, um CLP que usa 50% do limite em todos os indicadores tem health 50 (Atenção). Para chegar a "Estável" (60) é preciso usar cerca de 40% ou menos do limite. Isso não é um erro, mas afeta o que a tela mostrará como "bom".
3. **Setor ausente** nas tabelas listadas (seção 7, item 5): é a lacuna que mais bloqueia a tela. Não se resolve alterando Bronze ou Silver.
4. **Timestamp** do Silver não documentado: sem ele não há filtro por período.
5. **Alertas** não existem como dado: precisam ser derivados ou lidos de onde o motor de alertas os grava.
6. **Diagrama desatualizado:** mostra 3 buckets; o real é 1 bucket com 3 prefixos.
7. **Usuário `root` do MySQL** no `.env` da EC2: recomendável criar um usuário com permissões mínimas. Não é prioridade da tela.
8. **Gold atual desconhecido** (seção 4): o que ele gera, e em que formato, precisa ser lido no código.
9. **Janela de 15 minutos no Bronze→Silver:** se o processamento rodar de forma pontual, arquivos antigos do Bronze são ignorados. Isso afeta o histórico de 7 e 30 dias. Verificar como a ETL é agendada.
10. **Dados de exemplo do front:** a tela usa `mockDashboardData.js`. A saída do Gold deve ser mapeada para os nomes de campo desse arquivo, **sem renomear no front**.

---

## 9. Saída do Gold para a Macro CLP [PROPOSTA]

Onde: o Gold documentado é por CLP (clientes) ou por modelo (fornecedor). A Macro CLP precisa de dados **da empresa inteira**. Sugestão de pasta nova, dentro do padrão existente:

`gold/id-empresa-{idEmpresa}/clp-macro/macro-24h.json`, `macro-7d.json` e `macro-30d.json` (uma por período).

Formato: JSON, por ter listas e objeto aninhado (o restante do pipeline usa CSV: verificar o que o Node/API consegue ler). [PENDENTE]

```json
{
  "empresaId": 1,
  "periodo": "24h",
  "geradoEm": "2026-11-19T12:10:00-03:00",
  "faixas": { "criticoAbaixoDe": 40, "estavelAPartirDe": 60 },
  "kpis": {
    "healthGeral": 47,
    "clpsOnline": 20,
    "clpsTotal": 30,
    "disponibilidadePct": 66.7,
    "clpsCriticos": 7,
    "setorMaisPreocupante": { "nome": "USINAGEM", "health": 32 }
  },
  "frota": { "estavel": 7, "atencao": 6, "critico": 7, "offline": 10 },
  "setores": [
    {
      "nome": "USINAGEM", "health": 32, "online": 5, "total": 8,
      "foraDaCurva": { "clp": "CLP-12", "health": 14, "mediaComEle": 28.4 }
    },
    { "nome": "PINTURA", "health": 44, "online": 5, "total": 7, "foraDaCurva": null }
  ],
  "ranking": [
    {
      "idClp": 7, "apelido": "CLP-07", "setor": "USINAGEM", "modelo": "SIEMENS-S7-1200",
      "health": 18, "status": "critico", "alertas": 15, "componenteMaisProblematico": "CPU"
    }
  ]
}
```

Notas:
- Todos os valores acima são **exemplos**, não dados reais.
- `setores`: lista completa, do pior para o melhor. O front pega os 5 primeiros e calcula "os outros N setores" e o menor health do restante. (Hoje o mock traz `total` e `menorHealthDoRestante` prontos: alinhar.)
- `health` do setor = média dos CLPs online **sem** o fora da curva. `foraDaCurva` é `null` quando não há.
- `ranking`: do menor para o maior health. Se houver centenas ou milhares de CLPs, limitar o tamanho ou paginar [PENDENTE]. CLPs offline no ranking: [PENDENTE] (a tela atual não mostra).
- O **filtro de setor** da tela: aplicado no front sobre estes dados ou gerado por setor no Gold? [PENDENTE]
- Status em texto minúsculo (`critico`, `atencao`, `estavel`); offline não tem `health`.

---

## 10. Ordem de trabalho sugerida para o Codex

Cada passo em **plan mode**, parando para confirmação. Passos 1 e 2 são só leitura.

1. **Ler o Gold atual** (`main.py`, `config.py`): o que lê, calcula e grava, e em que formato. Dizer onde diverge deste documento.
2. **Ler o contrato real**: colunas do Silver (inclui data/hora?), colunas das tabelas `clp`, `modelo` e `empresa`. Responder: de onde vem o **setor**, o **apelido** e o **modelo**?
3. **Responder as definições da seção 7** que dependem do código (itens 5, 6, 9) e listar as que dependem de decisão de negócio.
4. Propor o **formato final do JSON** e a pasta, já alinhados com `mockDashboardData.js`.
5. Implementar **só o Gold, apenas acrescentando** (seção 1.1), em passos pequenos: primeiro as agregações simples (frota, KPIs), depois setores, ranking e, por último, fora da curva.
6. Testar o Gold com **dados de exemplo locais** antes de rodar na AWS.
7. Só depois, validar o fluxo completo Bronze → Silver → Gold com um CSV real e ligar a API.

---

## 11. Cuidados

- **Bronze e Silver: não tocar. Gold: nunca apagar nem sobrescrever o que existe; só acrescentar** (seção 1.1).
- Código Python **simples e legível**, comentários curtos em português. Quem mantém é iniciante/intermediário.
- Nenhum comando destrutivo no S3 ou no MySQL sem aviso e confirmação.
- Explicar, antes de cada comando AWS ou SQL, o que ele faz e se tem custo.
- Constantes (faixas, janela de online, mínimo de CLPs por setor) em **um único lugar**.
- Ao final de cada etapa: listar o que mudou, em qual arquivo, e como conferir ou desfazer.
- Manter este arquivo atualizado: quando um [PENDENTE] for decidido, registrar a decisão no lugar dele.  