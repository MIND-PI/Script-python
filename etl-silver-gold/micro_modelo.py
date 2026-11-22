import json
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from botocore.exceptions import ClientError

import config

FUSO = "America/Sao_Paulo"

# Linhas da mesma captura chegam com segundos de diferença; mais que isso é outra captura
GAP_ENTRE_CAPTURAS = pd.Timedelta(minutes=2)

RANKING_MAX = 10

PERIODOS = {
    "24h": {"duracao": timedelta(hours=24), "bucket": "1h", "rotulo": "%H:%M"},
    "7d": {"duracao": timedelta(days=7), "bucket": "6h", "rotulo": "%d/%m %Hh"},
    "30d": {"duracao": timedelta(days=30), "bucket": "1D", "rotulo": "%d/%m"},
}

# Componentes que aparecem na tela e as colunas de alerta que cada um soma
COMPONENTES = {
    "CPU": ["alerta_cpu"],
    "RAM": ["alerta_ram"],
    "SCAN": ["alerta_scan"],
    "REDE": ["alerta_ping", "alerta_loss"],
    "SWAP": ["alerta_swap"],
}
COLUNAS_ALERTA = [
    "alerta_cpu", "alerta_ram", "alerta_swap",
    "alerta_scan", "alerta_ping", "alerta_loss",
]
COLUNAS_FATOS = [
    "id_fornecedor", "modelo_clp", "id_empresa", "id_clp",
    "captura_em", "health", "n_amostras",
] + COLUNAS_ALERTA


# ----------------------------------------------------------------------------
# S3 e JSON
# ----------------------------------------------------------------------------
def _bucket():
    return os.getenv("AWS_BUCKET_NAME")


def _py(valor):
    """Converte tipos do pandas/numpy para tipos JSON (NaN vira null)."""
    if isinstance(valor, dict):
        return {str(k): _py(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_py(v) for v in valor]
    if valor is None or valor is pd.NaT:
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, (np.bool_, bool)):
        return bool(valor)
    if isinstance(valor, (np.integer, int)):
        return int(valor)
    if isinstance(valor, (np.floating, float)):
        numero = float(valor)
        return round(numero, 2) if math.isfinite(numero) else None
    return valor


def _ler_json(s3, chave):
    try:
        resposta = s3.get_object(Bucket=_bucket(), Key=chave)
    except ClientError as erro:
        if erro.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise
    return json.loads(resposta["Body"].read().decode("utf-8"))


def _gravar_json(s3, chave, objeto):
    corpo = json.dumps(_py(objeto), ensure_ascii=False, allow_nan=False)
    s3.put_object(
        Bucket=_bucket(),
        Key=chave,
        Body=corpo.encode("utf-8"),
        ContentType="application/json",
    )
    print(f"[{datetime.now()}] Upload realizado com sucesso para: {chave}")


def _chave_fatos(id_fornecedor, modelo, dia):
    return f"gold/base-capturas/id-empresa-{id_fornecedor}/id-modelo-{modelo}/{dia}.json"


# ----------------------------------------------------------------------------
# Passo 1: linhas da silver -> uma linha por captura
# ----------------------------------------------------------------------------
def construir_fatos(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUNAS_FATOS)

    d = df.copy()

    # silver antiga (sem as colunas alerta_*): conta como "sem alerta"
    for coluna in COLUNAS_ALERTA:
        if coluna not in d.columns:
            d[coluna] = 0
        d[coluna] = pd.to_numeric(d[coluna], errors="coerce").fillna(0)

    d["health_score"] = pd.to_numeric(d["health_score"], errors="coerce")

    d["ts"] = pd.to_datetime(d["timestamp"], format="mixed", errors="coerce")
    d = d.dropna(subset=["ts"])
    d["ts"] = d["ts"].dt.tz_localize(FUSO, ambiguous="NaT", nonexistent="NaT")
    d = d.dropna(subset=["ts"])
    if d.empty:
        return pd.DataFrame(columns=COLUNAS_FATOS)
    d["ts"] = d["ts"].dt.tz_convert("UTC")

    # separa as capturas de cada CLP pelo intervalo entre as linhas
    d = d.sort_values(["id_clp", "ts"])
    intervalo = d.groupby("id_clp")["ts"].diff()
    nova_captura = intervalo.isna() | (intervalo > GAP_ENTRE_CAPTURAS)
    d["captura"] = nova_captura.astype(int).groupby(d["id_clp"]).cumsum()

    agregacao = {
        "id_fornecedor": ("id_fornecedor", "first"),
        "modelo_clp": ("modelo_clp", "first"),
        "id_empresa": ("id_empresa", "first"),
        "captura_em": ("ts", "min"),
        "health": ("health_score", "mean"),
        "n_amostras": ("ts", "count"),
    }
    for coluna in COLUNAS_ALERTA:
        agregacao[coluna] = (coluna, "max")

    fatos = (
        d.groupby(["id_clp", "captura"])
        .agg(**agregacao)
        .reset_index()
        .drop(columns="captura")
    )

    for coluna in ["id_clp", "id_fornecedor", "modelo_clp", "id_empresa"]:
        fatos[coluna] = pd.to_numeric(fatos[coluna]).astype(int)
    for coluna in COLUNAS_ALERTA:
        fatos[coluna] = fatos[coluna].astype(int)
    fatos["captura_em"] = fatos["captura_em"].dt.floor("s")

    return fatos[COLUNAS_FATOS]


# ----------------------------------------------------------------------------
# Passo 2: guardar a base de capturas (um arquivo por modelo e por dia)
# ----------------------------------------------------------------------------
def gravar_fatos(s3, id_fornecedor, fatos):
    if fatos.empty:
        return

    f = fatos.copy()
    f["dia"] = f["captura_em"].dt.tz_convert(FUSO).dt.strftime("%Y-%m-%d")
    f["captura_em"] = f["captura_em"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    for (modelo, dia), grupo in f.groupby(["modelo_clp", "dia"]):
        chave = _chave_fatos(id_fornecedor, modelo, dia)
        existentes = _ler_json(s3, chave)
        novos = grupo.drop(columns=["dia"]).to_dict("records")

        base = pd.DataFrame(existentes + [_py(r) for r in novos])
        base = base.drop_duplicates(subset=["id_clp", "captura_em"], keep="last")
        base = base.sort_values(["captura_em", "id_clp"])
        _gravar_json(s3, chave, base.to_dict("records"))


def listar_modelos(s3, id_fornecedor):
    prefixo = f"gold/base-capturas/id-empresa-{id_fornecedor}/"
    modelos = []
    paginator = s3.get_paginator("list_objects_v2")
    for pagina in paginator.paginate(Bucket=_bucket(), Prefix=prefixo, Delimiter="/"):
        for item in pagina.get("CommonPrefixes", []):
            nome = item["Prefix"][len(prefixo):].strip("/")
            if nome.startswith("id-modelo-"):
                modelos.append(nome.replace("id-modelo-", "", 1))
    return sorted(modelos)


def carregar_fatos(s3, id_fornecedor, modelo, desde):
    prefixo = f"gold/base-capturas/id-empresa-{id_fornecedor}/id-modelo-{modelo}/"
    dia_minimo = (pd.Timestamp(desde).tz_convert(FUSO) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    registros = []
    paginator = s3.get_paginator("list_objects_v2")
    for pagina in paginator.paginate(Bucket=_bucket(), Prefix=prefixo):
        for objeto in pagina.get("Contents", []):
            nome = objeto["Key"].rsplit("/", 1)[-1]
            if not nome.endswith(".json"):
                continue
            if nome[:-5] < dia_minimo:
                continue
            registros.extend(_ler_json(s3, objeto["Key"]))

    fatos = pd.DataFrame(registros, columns=COLUNAS_FATOS)
    fatos["captura_em"] = pd.to_datetime(fatos["captura_em"], utc=True)
    fatos["health"] = pd.to_numeric(fatos["health"], errors="coerce")
    for coluna in COLUNAS_ALERTA:
        fatos[coluna] = pd.to_numeric(fatos[coluna], errors="coerce").fillna(0)
    return fatos[fatos["captura_em"] >= pd.Timestamp(desde)]


def _recortar(fatos, inicio, fim):
    return fatos[(fatos["captura_em"] >= inicio) & (fatos["captura_em"] < fim)]


# ----------------------------------------------------------------------------
# Passo 3: números da tela
# ----------------------------------------------------------------------------
def resumo_por_clp(fatos):
    colunas_comp = [f"alertas_{c}" for c in COMPONENTES]
    colunas = ["id_clp", "id_empresa", "health", "ultima_captura"] + colunas_comp \
        + ["alertas", "pior_componente", "status"]
    if fatos.empty:
        return pd.DataFrame(columns=colunas)

    grupo = fatos.groupby("id_clp")
    resumo = grupo.agg(
        id_empresa=("id_empresa", "first"),
        health=("health", "mean"),
        ultima_captura=("captura_em", "max"),
    ).reset_index()

    for componente, cols in COMPONENTES.items():
        soma = grupo[cols].sum().sum(axis=1)
        resumo[f"alertas_{componente}"] = soma.reindex(resumo["id_clp"]).to_numpy().astype(int)

    resumo["alertas"] = resumo[colunas_comp].sum(axis=1).astype(int)
    resumo["pior_componente"] = (
        resumo[colunas_comp].idxmax(axis=1).str.replace("alertas_", "", regex=False)
    )
    resumo.loc[resumo["alertas"] == 0, "pior_componente"] = None
    resumo["status"] = resumo["health"].apply(config.classificar_health)
    return resumo[colunas]


def _mediana(valores):
    serie = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
    return None if serie.empty else float(serie.median())


def _pct_fora(resumo):
    if resumo.empty:
        return None
    fora = int(resumo["status"].isin(["Atenção", "Crítico"]).sum())
    return round(fora / len(resumo) * 100, 1)


def calcular_evolucao(fatos, inicio, fim, cfg):
    frequencia = cfg["bucket"]
    fim_local = pd.Timestamp(fim).tz_convert(FUSO)
    inicio_local = pd.Timestamp(inicio).tz_convert(FUSO).floor(frequencia)
    indice = pd.date_range(inicio_local, fim_local, freq=frequencia)

    validos = fatos.dropna(subset=["health"]).copy()
    if validos.empty:
        mediana = p25 = p75 = pd.Series(np.nan, index=indice)
    else:
        validos["bucket"] = validos["captura_em"].dt.tz_convert(FUSO).dt.floor(frequencia)
        por_clp = validos.groupby(["bucket", "id_clp"])["health"].mean()
        grupo = por_clp.groupby(level=0)
        mediana = grupo.median().reindex(indice)
        p25 = grupo.quantile(0.25).reindex(indice)
        p75 = grupo.quantile(0.75).reindex(indice)

    pontos = mediana.dropna()
    return {
        "labels": [t.strftime(cfg["rotulo"]) for t in indice],
        "mediana": mediana.tolist(),
        "p25": p25.tolist(),
        "p75": p75.tolist(),
        "minimo": None if pontos.empty else float(pontos.min()),
        "maximo": None if pontos.empty else float(pontos.max()),
        "atual": None if pontos.empty else float(pontos.iloc[-1]),
    }


def montar_dashboard(periodo, cfg, id_fornecedor, modelo, agora,
                     fatos_atual, fatos_anterior, resumos_outros, inicio, inicio_anterior):
    res = resumo_por_clp(fatos_atual)
    res_ant = resumo_por_clp(fatos_anterior)

    totais = {c: int(res[f"alertas_{c}"].sum()) if not res.empty else 0 for c in COMPONENTES}
    total_alertas = sum(totais.values())

    componente_critico = None
    if total_alertas > 0:
        componente_critico = max(totais, key=totais.get)

    alertas_outros = []
    for outro in resumos_outros:
        alertas_outros.extend(outro["alertas"].tolist())

    contagem_status = {
        nome: int((res["status"] == nome).sum()) if not res.empty else 0
        for nome in ["Estável", "Atenção", "Crítico", "Sem dados"]
    }

    anterior = res_ant.set_index("id_clp")["health"] if not res_ant.empty else pd.Series(dtype=float)

    ranking = res.copy()
    ranking["_ordem"] = ranking["health"].fillna(1e9)
    ranking = ranking.sort_values(["_ordem", "alertas"], ascending=[True, False]).head(RANKING_MAX)
    ranking_json = []
    for _, linha in ranking.iterrows():
        saude = linha["health"]
        saude_ant = anterior.get(linha["id_clp"], np.nan)
        ranking_json.append({
            "id_clp": int(linha["id_clp"]),
            "id_empresa": int(linha["id_empresa"]),
            "health": saude,
            "health_anterior": saude_ant,
            "status": linha["status"],
            "alertas": int(linha["alertas"]),
            "pior_componente": linha["pior_componente"],
            "ultima_captura": linha["ultima_captura"],
        })

    return {
        "gerado_em": agora,
        "periodo": periodo,
        "id_fornecedor": int(id_fornecedor),
        "modelo_clp": int(modelo),
        "janela": {"inicio": inicio, "fim": agora, "anterior_inicio": inicio_anterior},
        "meta_health_score": config.LIMITE_ESTAVEL,
        "limites_status": {"estavel": config.LIMITE_ESTAVEL, "atencao": config.LIMITE_ATENCAO},
        "kpis": {
            "clps_com_dados": int(len(res)),
            "clps_fora_padrao": int(res["status"].isin(["Atenção", "Crítico"]).sum()) if not res.empty else 0,
            "pct_fora_padrao": _pct_fora(res),
            "pct_fora_padrao_anterior": _pct_fora(res_ant),
            "health_mediano": _mediana(res["health"]),
            "health_mediano_anterior": _mediana(res_ant["health"]),
            "alertas_total": total_alertas,
            "alertas_mediana_por_clp": _mediana(res["alertas"]),
            "alertas_mediana_outros_modelos": _mediana(alertas_outros),
            "componente_critico": componente_critico,
            "componente_critico_alertas": totais[componente_critico] if componente_critico else 0,
        },
        "status": contagem_status,
        "alertas_por_componente": totais,
        "rede_detalhe": {
            "PING": int(fatos_atual["alerta_ping"].sum()),
            "LOSS": int(fatos_atual["alerta_loss"].sum()),
        },
        "ranking": ranking_json,
        "evolucao": calcular_evolucao(fatos_atual, inicio, agora, cfg),
    }


# ----------------------------------------------------------------------------
# Função que a main.py chama
# ----------------------------------------------------------------------------
def atualizar(s3, id_fornecedor, df_empresa, agora=None):
    """df_empresa = linhas da silver deste fornecedor (todos os CLPs e modelos)."""
    agora = pd.Timestamp(agora or datetime.now(timezone.utc))
    if agora.tzinfo is None:
        agora = agora.tz_localize("UTC")

    print(f"[{datetime.now()}] [Dashboard de modelo] Fornecedor {id_fornecedor}...")

    gravar_fatos(s3, id_fornecedor, construir_fatos(df_empresa))

    modelos = listar_modelos(s3, id_fornecedor)
    if not modelos:
        print("Nenhum modelo com dados na base de capturas.")
        return

    maior_janela = max(cfg["duracao"] for cfg in PERIODOS.values()) * 2
    base = {m: carregar_fatos(s3, id_fornecedor, m, agora - maior_janela) for m in modelos}

    for periodo, cfg in PERIODOS.items():
        inicio = agora - cfg["duracao"]
        inicio_anterior = inicio - cfg["duracao"]

        atual = {m: _recortar(base[m], inicio, agora + pd.Timedelta(seconds=1)) for m in modelos}
        anterior = {m: _recortar(base[m], inicio_anterior, inicio) for m in modelos}
        resumos = {m: resumo_por_clp(atual[m]) for m in modelos}

        for modelo in modelos:
            outros = [resumos[m] for m in modelos if m != modelo]
            dashboard = montar_dashboard(
                periodo, cfg, id_fornecedor, modelo, agora,
                atual[modelo], anterior[modelo], outros, inicio, inicio_anterior,
            )
            chave = f"gold/id-empresa-{id_fornecedor}/id-modelo-{modelo}/dashboard-modelo/{periodo}/ultimo-resultado.json"
            _gravar_json(s3, chave, dashboard)

    print(f"[{datetime.now()}] [Dashboard de modelo] Concluído.")