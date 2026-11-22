"""
MACRO CLP (Gold) - dados da tela "Visão geral da planta" do cliente.

COMO ESTE ARQUIVO FUNCIONA (leia nesta ordem):
  1. CONFIGURAÇÕES ........ constantes que você pode ajustar (faixas, janela de online...)
  2. FUNÇÕES DE APOIO ..... classificar_status, media
  3. LEITURA DE DADOS ..... ler os CSVs do Silver (S3) e os CLPs cadastrados (MySQL)
  4. CÁLCULOS POR CLP ..... health do CLP e alertas
  5. MONTAGEM DA TELA ..... uma linha por CLP -> setores -> resultado final
  6. GRAVAÇÃO ............. salvar o JSON no S3 (pasta gold/.../clp-macro/)
  7. PONTO DE ENTRADA ..... client_macro_analysis(), a função que o main.py chama

RESUMO DO FLUXO (o que acontece quando o main.py chama client_macro_analysis):
  para cada empresa cliente:
      busca no MySQL os CLPs ativos da empresa (nome, setor, modelo)
      lê os CSVs do Silver de cada CLP e calcula health e alertas
      marca cada CLP como online/offline
      agrupa por setor, monta KPIs, situação da frota e ranking
      grava tudo em um JSON no S3

Este arquivo só LÊ (Silver e MySQL). Só GRAVA em gold/id-empresa-X/clp-macro/.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd

import config   # mesmo config do main.py (conexão com MySQL)


# ====================================================================
# 1. CONFIGURAÇÕES
# ====================================================================

# Faixas de status do Health Score (as mesmas usadas no resto do Gold):
#   health < LIMITE_ATENCAO   -> Crítico
#   LIMITE_ATENCAO..LIMITE_ESTAVEL-1 -> Atenção
#   health >= LIMITE_ESTAVEL  -> Estável
LIMITE_CRITICO = config.LIMITE_ATENCAO
LIMITE_ESTAVEL = config.LIMITE_ESTAVEL

# Setor só pode ter "CLP fora da curva" se tiver pelo menos esta quantidade de CLPs com health.
# (com poucos CLPs, um ruim já é metade do setor e não dá para chamar de "fora da curva")
MIN_CLPS_FORA_DA_CURVA = 4  # pendente de decisão final


#  Essa linha define quantos minutos um CLP pode ficar
#  sem enviar dados antes de ser considerado offline. Lendo de dentro para fora:

# ONLINE: o CLP é considerado online se o Silver recebeu arquivo dele há menos de N minutos.
# Atenção: o horário do arquivo (LastModified) é de quando a ETL GRAVOU, não de quando o CLP enviou.
# Por isso use pelo menos o DOBRO do intervalo do Bronze->Silver (veja no cron da EC2),
# senão um CLP vivo "pisca" entre online e offline.
# Dá para mudar sem mexer no código: ONLINE_WINDOW_MIN=60 no .env
ONLINE_WINDOW_MIN = int(os.getenv("ONLINE_WINDOW_MIN", "30"))

# Colunas do CSV do Silver que este arquivo usa. As outras são ignoradas na leitura (economiza memória).
COLUNAS = {
    "timestamp",
    "id_clp",
    "health_score",
    "porcentagem_uso_cpu",
    "porcentagem_uso_ram",
    "ram_usada_gb",
    "porcentagem_uso_swap",
    "scan_time_ms",
    "latencia_ms",
    "modelo_clp",
    "nome_modelo_cpl",
}

# Como cada componente aparece na tela (a chave é o "apelido" da tabela tipo_medicao).
NOME_COMPONENTE = {
    "CPU": "CPU",
    "RAM": "RAM",
    "SCAN": "Scan Time",
    "SWAP": "Swap",
    "PING": "Rede",
}




# ====================================================================
# 2. FUNÇÕES DE APOIO
# ====================================================================

def classificar_status(health):
    """Recebe um health e devolve o status padrão do projeto."""
    return config.classificar_health(health)


def media(valores):
    """Média de uma lista, com 2 casas decimais. Lista vazia devolve None (e não 0)."""
    if not valores:
        return None
    return round(sum(valores) / len(valores), 2)


# ====================================================================
# 3. LEITURA DE DADOS
# ====================================================================

def extrair_ids_do_arquivo(chave):
    """
    Descobre (id_empresa, id_clp) pelo nome do arquivo no S3.
    Exemplo: "silver/dados_6-12.csv" -> (6, 12)
    Se o nome não seguir o padrão, devolve (None, None) e o arquivo é ignorado.
    """
    nome = chave.split("/")[-1]                      # só o nome, sem a pasta

    # 1. precisa terminar com ".csv"
    if not nome.endswith(".csv"):
        return None, None

    # 2. tira o ".csv" e pega o que vem depois do ÚLTIMO "_"
    sem_extensao = nome.removesuffix(".csv")
    if "_" not in sem_extensao:
        return None, None
    trecho = sem_extensao.rsplit("_", maxsplit=1)[1]      # ex.: "6-12"

    # 3. precisa ter exatamente um "-" separando dois lados
    if trecho.count("-") != 1:
        return None, None
    empresa_str, clp_str = trecho.split("-")

    # 4. os dois lados precisam ser só números
    if not (empresa_str.isdecimal() and clp_str.isdecimal()):
        return None, None

    return int(empresa_str), int(clp_str)


def ler_csvs(s3, chaves):
    """
    Lê os arquivos CSV de UM CLP e junta tudo em uma tabela só (DataFrame).
    Recebe:
      s3     -> a conexão com o S3
      chaves -> lista com o caminho de cada arquivo, ex.: ["silver/dados_6-12.csv", ...]
    """
    todas_as_partes = []                                   # aqui guardamos a tabela de cada arquivo

    for chave in chaves:                                   # um arquivo por vez
        # 1. baixa o arquivo do S3
        resposta = s3.get_object(Bucket=os.getenv("AWS_BUCKET_NAME"), Key=chave)
        conteudo = resposta["Body"].read()                 # o texto do arquivo, em bytes

        # 2. transforma o conteúdo em uma tabela (DataFrame)
        tabela = pd.read_csv(BytesIO(conteudo))

        # 3. fica só com as colunas que a análise usa (e que existem neste arquivo)
        colunas_que_ficam = []
        for coluna in tabela.columns:
            if coluna in COLUNAS:
                colunas_que_ficam.append(coluna)
        tabela = tabela[colunas_que_ficam]

        todas_as_partes.append(tabela)

    # 4. sem arquivos, devolve uma tabela vazia (o concat dá erro com lista vazia)
    if not todas_as_partes:
        return pd.DataFrame()

    # 5. junta as tabelas, uma embaixo da outra
    df = pd.concat(todas_as_partes, ignore_index=True)

    # 6. transforma o texto da coluna de data em data de verdade
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed", errors="coerce")

    return df

def buscar_clps_da_empresa(id_empresa):
    # abre a conexão com o banco
    conn = config.conn_mysql()
    cursor = conn.cursor(dictionary=True)

    # Consulta SQL dos CLPs da empresa. Fica numa constante para ser fácil de achar e ajustar.
    # LEFT JOIN = traz o CLP mesmo que não tenha modelo/localização cadastrados
    # c.status = 1 = só CLPs ATIVOS (inativos não entram no total)

    # pergunta ao banco: "quais CLPs ativos essa empresa tem?"
    cursor.execute("""
        SELECT c.idClp AS idClp, c.nome AS apelido, l.nome AS setor, m.nome AS modelo
        FROM clp c
        LEFT JOIN modelo m ON m.idmodelo = c.modeloId
        LEFT JOIN localizacao l ON l.idLocalizacao = c.localizacaoId
        WHERE c.empresaId = %s AND c.status = 1
    """, (id_empresa,))

    # recebe as linhas da resposta
    clps = cursor.fetchall()

    # fecha a conexão
    cursor.close()
    conn.close()
    return clps


def listar_ultimos_arquivos(s3):
  """
  Olha os arquivos .csv na pasta silver/ do S3 e guarda,
  para cada empresa e CLP, qual foi o arquivo mais recente.
  """
  nome_do_bucket = os.getenv("AWS_BUCKET_NAME")
  paginador = s3.get_paginator("list_objects_v2")

  # Guarda o resultado final no formato:
  # {empresa: {clp: {"key": caminho, "last_modified": data}}}
  ultimos_arquivos = {}

  # O S3 devolve os arquivos aos poucos (em páginas), então percorremos página por página
  for pagina in paginator.paginate(Bucket=nome_do_bucket, Prefix="silver/"):
    arquivos_da_pagina = pagina.get("Contents", [])

    for arquivo in arquivos_da_pagina:
      caminho = arquivo.get("Key")

      # Ignora o que não for arquivo ou não terminar com .csv
      if not caminho or not caminho.endswith(".csv"):
        continue

      # Tenta pegar o id da empresa e do clp a partir do nome do arquivo
      empresa, clp = extrair_ids_do_arquivo(caminho)
      if empresa is None:
        continue

      data_arquivo = arquivo.get("LastModified")

      # Se ainda não vimos essa empresa, cria um espaço vazio para ela
      if empresa not in ultimos_arquivos:
        ultimos_arquivos[empresa] = {}

      clps_da_empresa = ultimos_arquivos[empresa]

      # Se ainda não vimos esse CLP nessa empresa, guarda este arquivo
      if clp not in clps_da_empresa:
        clps_da_empresa[clp] = {
            "key": caminho,
            "last_modified": data_arquivo,
        }
      else:
        # Se já existe um arquivo guardado, só substitui se este for mais novo
        data_anterior = clps_da_empresa[clp]["last_modified"]
        if data_arquivo > data_anterior:
          clps_da_empresa[clp] = {
              "key": caminho,
              "last_modified": data_arquivo,
          }

  return ultimos_arquivos


# ====================================================================
# 4. CÁLCULOS POR CLP
# ====================================================================

def calcular_health(df, location):
  """
  Health do CLP no período. Mesma regra do Micro CLP:
  - janela atual (location == ""): usa a ÚLTIMA leitura
  - 7-dias / 30-dias: usa a MÉDIA do período
  Se não houver nenhum health válido, devolve None (o CLP fica fora das médias; nunca vira 0).
  """
  # Se a tabela nem tem a coluna health_score, não há o que calcular
  if "health_score" not in df.columns:
    return None

  # Faz uma cópia da tabela para não alterar a tabela original fora da função
  tabela = df.copy()

  # Transforma os valores em número; o que for texto inválido ou vazio vira "vazio" (NaN)
  tabela["health_score"] = pd.to_numeric(
      tabela["health_score"], errors="coerce"
  )

  # Remove as linhas onde health_score ficou vazio
  tabela_valida = tabela.dropna(subset=["health_score"])

  # Se sobrou zero linhas válidas, devolve None (e não 0)
  if tabela_valida.empty:
    return None

  # Janela atual: pega a última leitura (mais recente, se houver coluna de data/hora)
  if location == "":
    if "timestamp" in tabela_valida.columns:
      tabela_valida = tabela_valida.sort_values("timestamp")

    ultima_linha = tabela_valida["health_score"].iloc[-1]
    return round(float(ultima_linha), 2)

  # Outros períodos (ex.: 7-dias ou 30-dias): calcula a média do período
  media_periodo = tabela_valida["health_score"].mean()
  return round(float(media_periodo), 2)


def contar_alertas(df, medicoes, limites):
  """
  Conta os ALERTAS do CLP e descobre o componente mais problemático.
  Alerta = uma leitura com valor >= limite cadastrado (mesma comparação >= do resto do Gold).

  Recebe:
    df        -> leituras do CLP
    medicoes  -> {"CPU": "%", "RAM": "GB", ...}  quais medições estão ativas (vem do MySQL)
    limites   -> {"CPU": 80, "RAM": 80, ...}     limite de cada medição (vem do MySQL)
  Devolve: (total_de_alertas, nome_do_componente_com_mais_alertas)
  """
  # Se não há nenhuma leitura na tabela, não há alertas
  if df.empty:
    return 0, None

  # Escolhe qual coluna da tabela representa a RAM de acordo com a unidade ativa
  if medicoes.get("RAM") == "GB":
    coluna_ram = "ram_usada_gb"
  else:
    coluna_ram = "porcentagem_uso_ram"

  # Mapa de qual coluna do CSV corresponde a cada componente
  colunas = {
      "CPU": "porcentagem_uso_cpu",
      "SCAN": "scan_time_ms",
      "SWAP": "porcentagem_uso_swap",
      "PING": "latencia_ms",
      "RAM": coluna_ram,
  }

  # Guarda quantos alertas cada componente teve
  por_componente = {}

  for componente, coluna in colunas.items():
    # Só avalia se o componente está ativo, tem limite configurado e existe na tabela
    if componente not in medicoes:
      continue
    if componente not in limites:
      continue
    if coluna not in df.columns:
      continue

    limite = limites[componente]

    # Converte a coluna para número; o que não for número vira vazio (NaN)
    valores = pd.to_numeric(df[coluna], errors="coerce")

    # Marca quais linhas estouraram ou atingiram o limite (>=) e soma quantas são verdadeiras
    alertas_do_componente = int((valores >= limite).sum())
    por_componente[componente] = alertas_do_componente

  # Soma todos os alertas encontrados
  total = 0
  for quantidade in por_componente.values():
    total = total + quantidade

  if total == 0:
    return 0, None

  # Descobre o componente com mais alertas (em empate de quantidade, fica o maior nome em ordem alfabética)
  pior_componente = None
  maior_quantidade = -1

  for componente, quantidade in por_componente.items():
    if quantidade > maior_quantidade or (
        quantidade == maior_quantidade and componente > pior_componente
    ):
      maior_quantidade = quantidade
      pior_componente = componente

  nome_exibicao = NOME_COMPONENTE.get(pior_componente, pior_componente)
  return total, nome_exibicao


# ====================================================================
# 5. MONTAGEM DA TELA
# ====================================================================

def montar_linhas(itens_empresa, cadastrados, ultimos_arquivos, s3, location):
    """
    Monta UMA LINHA por CLP ativo da empresa, juntando tudo o que a tela precisa:
    cadastro (MySQL) + online/offline (último arquivo) + health e alertas (CSVs).

    Recebe:
      itens_empresa    -> [(id_clp, dados)] dos CLPs que têm arquivos na janela atual do Gold
      cadastrados      -> CLPs ativos do MySQL
      ultimos_arquivos -> arquivo mais recente de cada CLP (para online/offline)
    """
    agora_utc = datetime.now(timezone.utc)

    # --- Passo 1: uma linha para cada CLP cadastrado (começa "vazia", como offline) ---
    linhas = {}
    for clp in cadastrados:
        id_clp = int(clp["idClp"])
        ultimo = ultimos_arquivos.get(id_clp)
        online = False
        ultima_leitura = None

        if ultimo and ultimo.get("last_modified"):
            ultima_leitura = ultimo["last_modified"]
            # online = o arquivo mais recente tem menos de ONLINE_WINDOW_MIN minutos
            online = (agora_utc - ultima_leitura) <= timedelta(minutes=ONLINE_WINDOW_MIN)

        linhas[id_clp] = {
            "idClp": id_clp,
            # "ou": se o apelido estiver vazio, usa "CLP-<id>"
            "apelido": clp.get("apelido") or f"CLP-{id_clp}",
            "setor": clp.get("setor") or "Sem setor",
            "modelo": clp.get("modelo") or "Sem modelo",
            "online": online,
            "ultimaLeituraEm": ultima_leitura.isoformat() if ultima_leitura else None,
            "health": None,        # preenchidos no passo 2
            "status": None,
            "alertas": 0,
            "componente": None,
        }

    # --- Passo 2: para quem tem arquivos na janela, calcula health e alertas ---
    for id_clp, value in itens_empresa:
        id_clp_int = int(id_clp)
        linha = linhas.get(id_clp_int)
        if linha is None:
            continue           # CLP inativo ou não cadastrado: ignorado

        df = ler_csvs(s3, value.get("csv", []))
        linha["health"] = calcular_health(df, location)
        if linha["health"] is not None:
            linha["status"] = classificar_status(linha["health"])

        alertas, componente = contar_alertas(df, value.get("medicoes", {}), value.get("limit", {}))
        linha["alertas"] = alertas
        linha["componente"] = componente

       # se o banco não trouxe o modelo, tenta pegar o nome que vem no CSV
        if linha["modelo"] == "Sem modelo":
          coluna_modelo = None
          if "modelo_clp" in df.columns:
            coluna_modelo = "modelo_clp"
          elif "nome_modelo_cpl" in df.columns:
            coluna_modelo = "nome_modelo_cpl"

          if coluna_modelo is not None:
            nomes = df[coluna_modelo].dropna().astype(str)
            if not nomes.empty:
              linha["modelo"] = nomes.iloc[-1]
    return list(linhas.values())


def analisar_setores(linhas):
  """
  Agrupa os CLPs por setor e calcula, para cada setor:
  - health médio (SEM os CLPs "fora da curva")
  - quantos CLPs estão online e quantos existem no total
  - os CLPs fora da curva, se houver
  Devolve a lista de setores, do PIOR para o melhor.
  """
  # 1. Agrupa as linhas por nome do setor: {"Britagem": [clp1, clp2, ...], ...}
  setores = {}
  for linha in linhas:
    nome_setor = linha["setor"]
    if nome_setor not in setores:
      setores[nome_setor] = []
    setores[nome_setor].append(linha)

  resultado = []

  # 2. Analisa cada setor separadamente
  for nome_setor, clps in setores.items():
    # Conta quantos CLPs do setor estão online
    quantidade_online = 0
    for clp in clps:
      if clp["online"]:
        quantidade_online = quantidade_online + 1

    # Separa apenas os CLPs que têm valor de health (não é None)
    com_health = []
    for clp in clps:
      if clp["health"] is not None:
        com_health.append(clp)

    # 3. Identifica CLPs "fora da curva" (status "Crítico" enquanto os demais têm média >= LIMITE_CRITICO)
    fora_da_curva = []
    if len(com_health) >= MIN_CLPS_FORA_DA_CURVA:
      for clp in com_health:
        if clp["status"] != "Crítico":
          continue

        # Junta o health de todos os outros CLPs do setor (exceto este)
        health_outros = []
        for outro in com_health:
          if outro["idClp"] != clp["idClp"]:
            health_outros.append(outro["health"])

        if len(health_outros) > 0 and media(health_outros) >= LIMITE_CRITICO:
          fora_da_curva.append(clp)

    # Guarda os IDs de quem ficou fora da curva para excluir da média "típica"
    ids_fora = []
    for clp in fora_da_curva:
      ids_fora.append(clp["idClp"])

    # Monta a lista de health apenas dos CLPs que NÃO estão fora da curva
    health_tipico = []
    for clp in com_health:
      if clp["idClp"] not in ids_fora:
        health_tipico.append(clp["health"])

    # Formata a lista de CLPs fora da curva no formato esperado na saída
    lista_fora_formatada = []
    for clp in fora_da_curva:
      lista_fora_formatada.append({
          "idClp": clp["idClp"],
          "apelido": clp["apelido"],
          "health": clp["health"],
      })

    # Calcula a média com todos os CLPs que têm health (só quando existe alguém fora da curva)
    if len(fora_da_curva) > 0:
      todos_health = []
      for clp in com_health:
        todos_health.append(clp["health"])
      media_com_fora = media(todos_health)
    else:
      media_com_fora = None

    resultado.append({
        "nome": nome_setor,
        "health": media(health_tipico),
        "online": quantidade_online,
        "total": len(clps),
        "clpsNaMedia": len(health_tipico),
        "mediaComForaDaCurva": media_com_fora,
        "foraDaCurva": lista_fora_formatada,
    })

  # 4. Ordena do pior para o melhor health; quem tem health=None vai para o final
  # Usa um laço simples (selection sort) em vez de lambda para ficar bem visível
  for i in range(len(resultado)):
    indice_menor = i
    for j in range(i + 1, len(resultado)):
      if _setor_vem_antes(resultado[j], resultado[indice_menor]):
        indice_menor = j

    if indice_menor != i:
      temp = resultado[i]
      resultado[i] = resultado[indice_menor]
      resultado[indice_menor] = temp

  return resultado


def _setor_vem_antes(setor_a, setor_b):
  """Devolve True se setor_a deve aparecer antes de setor_b (pior health primeiro; None por último)."""
  health_a = setor_a["health"]
  health_b = setor_b["health"]

  # Se A não tem health e B tem, B vem antes
  if health_a is None and health_b is not None:
    return False
  # Se A tem health e B não tem, A vem antes
  if health_a is not None and health_b is None:
    return True
  # Se ambos não têm health, mantém ordem
  if health_a is None and health_b is None:
    return False

  # Ambos têm health: menor valor = pior = vem antes
  return health_a < health_b


def montar_resultado(id_empresa, linhas, location):
  """
  Junta tudo no dicionário final (que vira o JSON): KPIs, frota, setores e ranking.
  """
  # 1. Separa os CLPs que têm health válido e os que estão online agora
  com_health = []
  for linha in linhas:
    if linha["health"] is not None:
      com_health.append(linha)

  quantidade_online = 0
  for linha in linhas:
    if linha["online"]:
      quantidade_online = quantidade_online + 1

  # 2. Conta quantos CLPs estão em cada situação da frota (cada um cai em apenas um caso)
  frota = {
      "estavel": 0,
      "atencao": 0,
      "critico": 0,
      "offline": 0,
      "semHealth": 0,
  }
  for linha in linhas:
    if not linha["online"]:
      frota["offline"] = frota["offline"] + 1
    elif linha["health"] is None:
      frota["semHealth"] = frota["semHealth"] + 1
    elif linha["status"] == "Crítico":
      frota["critico"] = frota["critico"] + 1
    elif linha["status"] == "Atenção":
      frota["atencao"] = frota["atencao"] + 1
    else:
      frota["estavel"] = frota["estavel"] + 1

  # 3. Analisa os setores (já vêm ordenados do pior para o melhor)
  setores = analisar_setores(linhas)

  # Pega o primeiro setor que tenha health válido (ou None se nenhum tiver)
  setor_mais_preocupante = None
  for setor in setores:
    if setor["health"] is not None:
      setor_mais_preocupante = setor
      break

  if setor_mais_preocupante is not None:
    info_setor_preocupante = {
        "nome": setor_mais_preocupante["nome"],
        "health": setor_mais_preocupante["health"],
        "status": classificar_status(setor_mais_preocupante["health"]),
    }
  else:
    info_setor_preocupante = None

  # 4. Calcula health geral e disponibilidade (%)
  valores_health = []
  for linha in com_health:
    valores_health.append(linha["health"])
  health_geral = media(valores_health)

  if health_geral is not None:
    health_geral_status = classificar_status(health_geral)
  else:
    health_geral_status = None

  total = len(linhas)
  if total > 0:
    disponibilidade_pct = round((quantidade_online / total) * 100, 1)
  else:
    disponibilidade_pct = None

  # 5. Ordena do menor para o maior health para montar o ranking
  ranking_ordenado = list(com_health)
  for i in range(len(ranking_ordenado)):
    indice_menor = i
    for j in range(i + 1, len(ranking_ordenado)):
      if ranking_ordenado[j]["health"] < ranking_ordenado[indice_menor]["health"]:
        indice_menor = j

    if indice_menor != i:
      temp = ranking_ordenado[i]
      ranking_ordenado[i] = ranking_ordenado[indice_menor]
      ranking_ordenado[indice_menor] = temp

  ranking_formatado = []
  for clp in ranking_ordenado:
    ranking_formatado.append({
        "idClp": clp["idClp"],
        "apelido": clp["apelido"],
        "setor": clp["setor"],
        "modelo": clp["modelo"],
        "health": clp["health"],
        "status": clp["status"],
        "alertas": clp["alertas"],
        "componenteMaisProblematico": clp["componente"],
        "online": clp["online"],
        "ultimaLeituraEm": clp["ultimaLeituraEm"],
    })

  # Define o rótulo do período ("agora" quando location vem vazio)
  if location:
    periodo = location
  else:
    periodo = "agora"

  agora_iso = datetime.now().astimezone().isoformat(timespec="seconds")

  return {
      "empresaId": int(id_empresa),
      "periodo": periodo,
      "geradoEm": agora_iso,
      "faixas": {
          "criticoAbaixoDe": LIMITE_CRITICO,
          "estavelAPartirDe": LIMITE_ESTAVEL,
      },
      "kpis": {
          "healthGeral": health_geral,
          "healthGeralStatus": health_geral_status,
          "clpsOnline": quantidade_online,
          "clpsTotal": total,
          "clpsNaMedia": len(com_health),
          "disponibilidadePct": disponibilidade_pct,
          "clpsCriticos": frota["critico"],
          "setorMaisPreocupante": info_setor_preocupante,
      },
      "frota": frota,
      "setores": setores,
      "ranking": ranking_formatado,
  }

# ====================================================================
# 6. GRAVAÇÃO
# ====================================================================

def salvar_json(s3, dados, chave):
    """
    Grava o dicionário como JSON no S3, em gold/<chave>.json.
    ensure_ascii=False mantém os acentos legíveis (Crítico, Atenção...).
    """
    s3.put_object(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Key=f"gold/{chave}.json",
        Body=json.dumps(dados, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"[{datetime.now()}] Upload realizado com sucesso para: gold/{chave}.json")


# ====================================================================
# 7. PONTO DE ENTRADA (é esta a função que o main.py chama)
# ====================================================================

def client_macro_analysis(json_clp, s3):
  """
  Roda a análise Macro CLP para cada empresa cliente.

  json_clp é o mesmo dicionário que o main.py já montou: {(id_empresa, id_clp): {...}}
  com os arquivos do Silver que estão na janela atual (agora / 7 dias / 30 dias).
  """
  if not json_clp:
    return

  agora_inicio = datetime.now()
  
  print(
      f"\n[{agora_inicio}] [Macro CLP] Iniciando processamento por empresa"
      " cliente..."
  )

  # 1. Reagrupa json_clp por empresa: {id_empresa: [(id_clp, dados), ...]}
  por_empresa = {}
  for chave, value in json_clp.items():
    id_empresa = int(chave[0])
    id_clp = chave[1]

    if id_empresa not in por_empresa:
      por_empresa[id_empresa] = []

    por_empresa[id_empresa].append((id_clp, value))

  # 2. Busca uma única vez os últimos arquivos do Silver (usado para saber online/offline)
  # Se falhar, interrompe aqui para não marcar todo mundo como offline por engano
  try:
    todos_ultimos = listar_ultimos_arquivos(s3)
  except Exception as erro:
    print(
        f"[{datetime.now()}] [Macro CLP] Erro ao listar o Silver: {erro}"
    )
    return

  nome_data_hora = datetime.now().strftime("%Y-%m-%d_%H-%M")

  # 3. Processa cada empresa separadamente
  for id_empresa, itens_empresa in por_empresa.items():
    try:
      # Pega o período ("", "7-dias" ou "30-dias") do primeiro item da empresa
      primeiro_item = itens_empresa[0]
      dados_primeiro_clp = primeiro_item[1]
      location = dados_primeiro_clp.get("location", "")

      cadastrados = buscar_clps_da_empresa(id_empresa)
      ultimos = todos_ultimos.get(int(id_empresa), {})

      linhas = montar_linhas(itens_empresa, cadastrados, ultimos, s3, location)
      resultado = montar_resultado(id_empresa, linhas, location)

      # Monta o caminho da pasta: "id-empresa-X/clp-macro" ou "id-empresa-X/clp-macro/7-dias"
      if location:
        pasta = f"id-empresa-{id_empresa}/clp-macro/{location}"
      else:
        pasta = f"id-empresa-{id_empresa}/clp-macro"

      caminho_historico = f"{pasta}/{nome_data_hora}"
      caminho_atual = f"{pasta}/ultimo-resultado"

      salvar_json(s3, resultado, caminho_historico)
      salvar_json(s3, resultado, caminho_atual)
    except Exception as erro:
      # Se uma empresa falhar, registra o erro e segue para as próximas
      print(
          f"[{datetime.now()}] [Macro CLP] Erro na empresa {id_empresa}: {erro}"
      )

  print(f"[{datetime.now()}] [Macro CLP] Concluído.")
