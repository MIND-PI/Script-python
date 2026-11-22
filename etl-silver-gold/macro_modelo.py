"""
Comparativo de modelos (visão do fornecedor / qualidade).

Lê a base de capturas que o micro_modelo.py já grava
(gold/base-capturas/...), uma linha por captura de cada CLP, e gera um JSON
por período e por filtro para a tela "Comparativo de modelos":

gold/id-empresa-{fornecedor}/comparativo-modelos/{24h|7d|30d}/{filtro}/ultimo-resultado.json
gold/id-empresa-{fornecedor}/comparativo-modelos/filtros.json   (opções dos filtros)

filtro = todos | setor-{slug} | empresa-{id}

Regras (conforme o dashboard):
- unidade = CLP; CLPs com clp.status = 0 no MySQL ficam fora de tudo
- status: Estável >= 75 | Atenção 50-74 | Crítico < 50 (config.classificar_health)
- unidade crítica = health médio do CLP no período < 50
- alerta de componente = o CLP teve ao menos uma captura com alerta nele
  (REDE = alerta de latência OU de perda de pacotes)
- degradação = CLP ainda não crítico, com regressão linear do health negativa
- tendência = último bucket completo vs. bucket anterior
"""
import re
import unicodedata
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from micro_modelo import (
    COMPONENTES,
    FUSO,
    PERIODOS,
    _gravar_json,
    _recortar,
    carregar_fatos,
    listar_modelos,
)

# Degradação: precisa de histórico mínimo e de uma queda relevante na reta ajustada
DEGRADACAO_MIN_BUCKETS = 4
DEGRADACAO_MIN_QUEDA = 5.0  # pontos de health perdidos ao longo do período

# Tendência: variação dentro da faixa conta como "sem variação"
FAIXA_SEM_VARIACAO = 1.0
PIORA_FORTE = 5.0

ROTULOS = {"CPU": "CPU", "RAM": "RAM", "SCAN": "Scan time", "REDE": "Rede", "SWAP": "Swap"}


# ----------------------------------------------------------------------------
# Cadastro (MySQL)
# ----------------------------------------------------------------------------
def carregar_cadastro():
    """CLPs (status e empresa) e empresas (nome e setor) em uma ida ao banco."""
    conexao = config.conn_mysql()
    try:
        cursor = conexao.cursor()
        cursor.execute("SELECT idClp, status FROM clp")
        clps = {int(i): s for i, s in cursor.fetchall()}
        cursor.execute("SELECT idEmpresa, nome_fantasia, setor FROM empresa")
        empresas = {int(i): {"nome": n, "setor": s} for i, n, s in cursor.fetchall()}
        cursor.close()
    finally:
        conexao.close()
    inativos = {i for i, s in clps.items() if s is not None and int(s) == 0}
    return inativos, empresas


def _slug(texto):
    t = unicodedata.normalize("NFKD", str(texto or "sem-setor"))
    t = t.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") or "sem-setor"


# ----------------------------------------------------------------------------
# Base e filtros
# ----------------------------------------------------------------------------
def carregar_base(s3, id_fornecedor, agora, inativos):
    """Capturas de todos os modelos do fornecedor, sem os CLPs inativos."""
    maior_janela = max(cfg["duracao"] for cfg in PERIODOS.values()) * 2
    desde = agora - maior_janela

    quadros = [carregar_fatos(s3, id_fornecedor, m, desde) for m in listar_modelos(s3, id_fornecedor)]
    quadros = [q for q in quadros if not q.empty]
    if not quadros:
        return pd.DataFrame()

    base = pd.concat(quadros, ignore_index=True)
    return base[~base["id_clp"].isin(inativos)]


def definir_filtros(base, empresas):
    filtros = [{"chave": "todos", "tipo": "todos", "rotulo": "Todas", "ids_empresa": None}]

    por_setor = {}
    ids = sorted(int(i) for i in base["id_empresa"].unique())
    for id_empresa in ids:
        setor = empresas.get(id_empresa, {}).get("setor") or "Sem setor"
        por_setor.setdefault(setor, []).append(id_empresa)

    for setor, ids_setor in sorted(por_setor.items()):
        filtros.append({
            "chave": f"setor-{_slug(setor)}", "tipo": "setor",
            "rotulo": setor, "ids_empresa": ids_setor,
        })
    for id_empresa in ids:
        info = empresas.get(id_empresa, {})
        filtros.append({
            "chave": f"empresa-{id_empresa}", "tipo": "empresa",
            "rotulo": info.get("nome") or f"Empresa {id_empresa}",
            "setor": info.get("setor") or "Sem setor",
            "ids_empresa": [id_empresa],
        })
    return filtros


# ----------------------------------------------------------------------------
# Cálculos
# ----------------------------------------------------------------------------
def _mediana(valores):
    s = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
    return None if s.empty else float(s.median())


def _media(valores):
    s = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
    return None if s.empty else float(s.mean())


def _diferenca(a, b):
    return None if a is None or b is None else round(a - b, 2)


def _pct(parte, total):
    return round(parte / total * 100, 1) if total else None


def resumo_clps(fatos):
    """Uma linha por CLP: health médio, status e se teve alerta em cada componente."""
    colunas_alerta = [f"alerta_{c}" for c in COMPONENTES]
    colunas = ["id_clp", "modelo_clp", "id_empresa", "health", "status"] + colunas_alerta
    if fatos.empty:
        return pd.DataFrame(columns=colunas)

    grupo = fatos.groupby("id_clp")
    resumo = grupo.agg(
        modelo_clp=("modelo_clp", "first"),
        id_empresa=("id_empresa", "first"),
        health=("health", "mean"),
    ).reset_index()

    for componente, cols in COMPONENTES.items():
        total = grupo[cols].sum().sum(axis=1)
        resumo[f"alerta_{componente}"] = total.reindex(resumo["id_clp"]).to_numpy() > 0

    resumo["status"] = resumo["health"].apply(config.classificar_health)
    return resumo[colunas]


def _health_por_bucket(fatos, freq):
    validos = fatos.dropna(subset=["health"]).copy()
    if validos.empty:
        return pd.DataFrame(columns=["modelo_clp", "id_clp", "bucket", "health"])
    validos["bucket"] = validos["captura_em"].dt.tz_convert(FUSO).dt.floor(freq)
    return (
        validos.groupby(["modelo_clp", "id_clp", "bucket"])["health"]
        .mean()
        .reset_index()
    )


def clps_em_degradacao(fatos, freq, resumo):
    """CLPs ainda não críticos cujo health cai de forma contínua (regressão linear)."""
    por_bucket = _health_por_bucket(fatos, freq)
    criticos = set(resumo.loc[resumo["status"] == "Crítico", "id_clp"])

    ids = []
    for id_clp, g in por_bucket.groupby("id_clp"):
        if id_clp in criticos or len(g) < DEGRADACAO_MIN_BUCKETS:
            continue
        horas = (g["bucket"] - g["bucket"].iloc[0]).dt.total_seconds().to_numpy() / 3600
        if horas[-1] <= 0:
            continue
        inclinacao = np.polyfit(horas, g["health"].to_numpy(), 1)[0]
        queda_total = -inclinacao * horas[-1]
        if inclinacao < 0 and queda_total >= DEGRADACAO_MIN_QUEDA:
            ids.append(int(id_clp))
    return ids


def classificar_variacao(delta):
    if delta is None:
        return "sem_dados"
    if delta <= -PIORA_FORTE:
        return "piorou_forte"
    if delta < -FAIXA_SEM_VARIACAO:
        return "piorou"
    if delta > FAIXA_SEM_VARIACAO:
        return "melhorou"
    return "sem_variacao"


def tendencia_modelos(fatos, freq, agora):
    """Última leitura vs. anterior de cada modelo, em buckets (evita o ruído de uma captura só)."""
    por_bucket = _health_por_bucket(fatos, freq)
    if por_bucket.empty:
        return {}

    por_modelo = por_bucket.groupby(["modelo_clp", "bucket"])["health"].mean()
    bucket_atual = pd.Timestamp(agora).tz_convert(FUSO).floor(freq)

    resultado = {}
    for modelo, serie in por_modelo.groupby(level=0):
        serie = serie.droplevel(0).sort_index()
        # o bucket em andamento ainda está incompleto: só entra se não houver outro
        if len(serie) >= 3 and serie.index[-1] == bucket_atual:
            serie = serie.iloc[:-1]
        delta = round(float(serie.iloc[-1] - serie.iloc[-2]), 2) if len(serie) >= 2 else None
        resultado[int(modelo)] = {"delta": delta, "categoria": classificar_variacao(delta)}
    return resultado


def _nivel_alerta(pct):
    if pct < 20:
        return "baixo"
    if pct < 40:
        return "medio"
    if pct < 60:
        return "alto"
    return "critico"


def montar_dashboard(periodo, cfg, id_fornecedor, filtro, agora,
                     atual, anterior, inicio, inicio_anterior):
    res = resumo_clps(atual)
    res_ant = resumo_clps(anterior)

    degradadas = clps_em_degradacao(atual, cfg["bucket"], res)
    tendencia = tendencia_modelos(atual, cfg["bucket"], agora)

    total = len(res)
    criticas = int((res["status"] == "Crítico").sum())
    total_ant = len(res_ant)
    criticas_ant = int((res_ant["status"] == "Crítico").sum())

    mediano = _mediana(res["health"])
    mediano_ant = _mediana(res_ant["health"])

    modelos = []
    for modelo, sub in res.groupby("modelo_clp"):
        n = len(sub)
        com_alerta = {c: int(sub[f"alerta_{c}"].sum()) for c in COMPONENTES}
        pior = None
        if any(com_alerta.values()):
            componente = max(COMPONENTES, key=lambda c: com_alerta[c])
            pior = {
                "componente": componente,
                "rotulo": ROTULOS[componente],
                "unidades_com_alerta": com_alerta[componente],
                "unidades": n,
            }
        saude = _media(sub["health"])
        crit = int((sub["status"] == "Crítico").sum())
        pct_alerta = {c: _pct(com_alerta[c], n) for c in COMPONENTES}

        modelos.append({
            "id_modelo": int(modelo),
            "nome": None,  # TODO: nome do modelo (a validar)
            "unidades": n,
            "health_medio": saude,
            "status": config.classificar_health(saude),
            "tendencia": tendencia.get(int(modelo), {"delta": None, "categoria": "sem_dados"}),
            "pior_componente": pior,
            "criticos": {"quantidade": crit, "pct": _pct(crit, n)},
            "_pct_alerta": pct_alerta,
        })

    modelos.sort(key=lambda m: float("inf") if m["health_medio"] is None else m["health_medio"])

    mapa = {
        "componentes": [{"id": c, "rotulo": ROTULOS[c]} for c in COMPONENTES],
        "linhas": [
            {
                "id_modelo": m["id_modelo"],
                "nome": m["nome"],
                "valores": {
                    c: {"pct": p, "nivel": None if p is None else _nivel_alerta(p)}
                    for c, p in m["_pct_alerta"].items()
                },
            }
            for m in modelos
        ],
    }
    variacao = [
        {"id_modelo": m["id_modelo"], "nome": m["nome"], **m["tendencia"]}
        for m in modelos
    ]
    for m in modelos:
        del m["_pct_alerta"]

    return {
        "gerado_em": agora,
        "periodo": periodo,
        "id_fornecedor": int(id_fornecedor),
        "filtro": {k: v for k, v in filtro.items() if k != "ids_empresa"},
        "janela": {"inicio": inicio, "fim": agora, "anterior_inicio": inicio_anterior},
        "limites_status": {"estavel": config.LIMITE_ESTAVEL, "atencao": config.LIMITE_ATENCAO},
        "kpis": {
            "criticas": {
                "quantidade": criticas,
                "total": total,
                "pct": _pct(criticas, total),
                "quantidade_anterior": criticas_ant,
                "pct_anterior": _pct(criticas_ant, total_ant),
                "delta_quantidade": criticas - criticas_ant,
            },
            "degradacao": {"quantidade": len(degradadas), "ids_clp": degradadas},
            "health_mediano": {
                "valor": mediano,
                "status": config.classificar_health(mediano),
                "anterior": mediano_ant,
                "delta": _diferenca(mediano, mediano_ant),
            },
        },
        "ranking": modelos,
        "mapa_problemas": mapa,
        "variacao": variacao,
    }


# ----------------------------------------------------------------------------
# Função que a main.py chama
# ----------------------------------------------------------------------------
def atualizar(s3, id_fornecedor, agora=None):
    """Precisa rodar DEPOIS do micro_modelo.atualizar (que alimenta a base de capturas)."""
    agora = pd.Timestamp(agora or datetime.now(timezone.utc))
    if agora.tzinfo is None:
        agora = agora.tz_localize("UTC")

    print(f"[{datetime.now()}] [Comparativo de modelos] Fornecedor {id_fornecedor}...")

    inativos, empresas = carregar_cadastro()
    base = carregar_base(s3, id_fornecedor, agora, inativos)
    if base.empty:
        print("Nenhuma captura de CLP ativo na base de capturas.")
        return

    filtros = definir_filtros(base, empresas)
    raiz = f"gold/id-empresa-{id_fornecedor}/comparativo-modelos"

    _gravar_json(s3, f"{raiz}/filtros.json", {
        "gerado_em": agora,
        "periodos": list(PERIODOS),
        "setores": [{"chave": f["chave"], "rotulo": f["rotulo"]} for f in filtros if f["tipo"] == "setor"],
        "empresas": [
            {"chave": f["chave"], "rotulo": f["rotulo"], "setor": f["setor"]}
            for f in filtros if f["tipo"] == "empresa"
        ],
    })

    for periodo, cfg in PERIODOS.items():
        inicio = agora - cfg["duracao"]
        inicio_anterior = inicio - cfg["duracao"]
        atual_geral = _recortar(base, inicio, agora + pd.Timedelta(seconds=1))
        anterior_geral = _recortar(base, inicio_anterior, inicio)

        for filtro in filtros:
            ids = filtro["ids_empresa"]
            if ids is None:
                atual, anterior = atual_geral, anterior_geral
            else:
                atual = atual_geral[atual_geral["id_empresa"].isin(ids)]
                anterior = anterior_geral[anterior_geral["id_empresa"].isin(ids)]

            dashboard = montar_dashboard(
                periodo, cfg, id_fornecedor, filtro, agora,
                atual, anterior, inicio, inicio_anterior,
            )
            _gravar_json(s3, f"{raiz}/{periodo}/{filtro['chave']}/ultimo-resultado.json", dashboard)

    print(f"[{datetime.now()}] [Comparativo de modelos] Concluído.")