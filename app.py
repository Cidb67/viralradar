import streamlit as st
import os
import sqlite3
import hashlib
import random
from datetime import datetime

# Nome do Banco de Dados SQLite
DB_NAME = "viralradar.db"
LIMITE_PLANO_INICIAL = 10
LIMITE_TROCAS_MENSAL = 5

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SQLite)
# ==========================================
def inicializar_banco():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabela de Usuários (com senha em hash)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            email TEXT PRIMARY KEY,
            senha TEXT NOT NULL
        )
    ''')
    
    # Tabela de Perfis Monitorados por Usuário
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS perfis (
            email TEXT,
            perfil TEXT,
            PRIMARY KEY (email, perfil)
        )
    ''')
    
    # Tabela de Métricas / Substituições por Usuário
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metricas (
            email TEXT PRIMARY KEY,
            trocas INTEGER DEFAULT 0
        )
    ''')
    
    # Tabela de Histórico de Alertas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mensagem TEXT,
            data TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    
    # Inserir histórico de exemplo se estiver vazio
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM historico")
    if cursor.fetchone()[0] == 0:
        exemplos = [
            ("Novo vídeo detectado em @virginiafonseca (739 posts)", "2026-10-30 12:10"),
            ("Novo vídeo detectado em @vinnijr (1462 posts)", "2026-10-30 11:25"),
            ("Novo vídeo detectado em @neymarjr (497 posts)", "2026-10-30 09:40")
        ]
        cursor.executemany("INSERT INTO historico (mensagem, data) VALUES (?, ?)", exemplos)
        conn.commit()
    conn.close()

# Inicializa o banco ao iniciar o app
inicializar_banco()

# Função para gerar Hash SHA-256 da senha (segurança profissional)
def criar_hash_senha(senha):
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

# ==========================================
# FUNÇÕES DE MANIPULAÇÃO DE DADOS
# ==========================================
def cadastrar_usuario_db(email, senha):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        senha_hash = criar_hash_senha(senha)
        cursor.execute("INSERT INTO usuarios (email, senha) VALUES (?, ?)", (email, senha_hash))
        cursor.execute("INSERT INTO metricas (email, trocas) VALUES (?, 0)", (email,))
        conn.commit()
        conn.close()
        return True, "Cadastro realizado com sucesso!"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "E-mail já cadastrado!"

def verificar_login_db(email, senha):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    senha_hash = criar_hash_senha(senha)
    cursor.execute("SELECT * FROM usuarios WHERE email = ? AND senha = ?", (email, senha_hash))
    usuario = cursor.fetchone()
    conn.close()
    return usuario is not None

def ler_perfis_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT perfil FROM perfis WHERE email = ?", (email,))
    resultados = [row[0] for row in cursor.fetchall()]
    conn.close()
    return resultados

def salvar_perfil_db(email, novo_perfil):
    perfis = ler_perfis_db(email)
    if len(perfis) >= LIMITE_PLANO_INICIAL:
        return False, "Limite atingido"
    
    if novo_perfil not in perfis:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO perfis (email, perfil) VALUES (?, ?)", (email, novo_perfil))
        conn.commit()
        conn.close()
        return True, "Sucesso"
    return False, "Duplicado"

def excluir_perfil_db(email, perfil_alvo):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM perfis WHERE email = ? AND perfil = ?", (email, perfil_alvo))
    conn.commit()
    conn.close()
    return True

def ler_trocas_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT trocas FROM metricas WHERE email = ?", (email,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0

def salvar_troca_db(email, qtd):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO metricas (email, trocas) VALUES (?, ?)", (email, qtd))
    conn.commit()
    conn.close()

def registrar_substituicao_db(email):
    trocas_feitas = ler_trocas_db(email)
    if trocas_feitas < LIMITE_TROCAS_MENSAL:
        salvar_troca_db(email, trocas_feitas + 1)
        return True
    return False

def ler_historico_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT data, mensagem FROM historico ORDER BY id DESC")
    resultados = [f"{row[0]} — {row[1]}" for row in cursor.fetchall()]
    conn.close()
    return resultados

# ==========================================
# CONFIGURAÇÃO DA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="ViralRadar - Monitor de Tendências",
    page_icon="🚀",
    layout="centered"
)

# Inicializa estados da sessão
if "logado" not in st.session_state:
    st.session_state.logado = False
if "usuario_atual" not in st.session_state:
    st.session_state.usuario_atual = ""
if "plano_checkout" not in st.session_state:
    st.session_state.plano_checkout = None

# ==========================================
# TELA DE AUTENTICAÇÃO (LOGIN / CADASTRO)
# ==========================================
if not st.session_state.logado:
    st.title("🚀 ViralRadar — Bem-vindo!")
    st.markdown("Faça login na sua conta ou crie um novo cadastro seguro para acessar o monitor de tendências.")
    
    aba_login, aba_cadastro = st.tabs(["🔑 Entrar (Login)", "📝 Criar Conta"])
    
    with aba_login:
        st.subheader("Acessar sua Conta")
        with st.form("form_login_db"):
            email_login = st.text_input("E-mail:")
            senha_login = st.text_input("Senha:", type="password")
            btn_entrar = st.form_submit_button("Entrar no Painel")
            
            if btn_entrar:
                if verificar_login_db(email_login, senha_login):
                    st.session_state.logado = True
                    st.session_state.usuario_atual = email_login
                    st.success("Login efetuado com sucesso! Entrando...")
                    st.rerun()
                else:
                    st.error("⚠️ E-mail ou senha incorretos.")

    with aba_cadastro:
        st.subheader("Criar Nova Conta")
        with st.form("form_cadastro_db"):
            email_cad = st.text_input("Seu E-mail:")
            senha_cad = st.text_input("Crie uma Senha:", type="password")
            btn_cadastrar = st.form_submit_button("Cadastrar e Acessar")
            
            if btn_cadastrar:
                if email_cad and senha_cad:
                    sucesso, msg = cadastrar_usuario_db(email_cad, senha_cad)
                    if sucesso:
                        st.session_state.logado = True
                        st.session_state.usuario_atual = email_cad
                        st.success(f"🎉 {msg} Seja bem-vindo!")
                        st.rerun()
                    else:
                        st.warning(msg)
                else:
                    st.warning("Preencha todos os campos para cadastrar.")
    
    st.stop()

# ==========================================
# PAINEL PRINCIPAL (APÓS O LOGIN)
# ==========================================
st.title("🚀 ViralRadar - Painel de Controle Inteligente")
st.markdown(f"Monitoramento de perfis, métricas e tendências | 👤 **{st.session_state.usuario_atual}**")

# Menu lateral estabilizado
with st.sidebar:
    st.header("📌 Menu")
    opcao = st.radio(
        "Seção:", 
        [
            "Painel Principal", 
            "🔥 Tendências e Assuntos", 
            "Histórico de Alertas", 
            "Assinatura / Planos", 
            "Finalizar Compra / Pagamento", 
            "Suporte"
        ], 
        key="menu_principal_viralradar_v6"
    )
    st.markdown("---")
    st.markdown("**Status do Sistema:** 🟢 Online (SQLite)")
    
    if st.button("🚪 Sair da Conta (Logout)", key="btn_logout_v6"):
        st.session_state.logado = False
        st.session_state.usuario_atual = ""
        st.rerun()
        
    if st.button("🔄 Atualizar Tela", key="btn_atualizar_geral_v6"):
        st.rerun()

email_usuario = st.session_state.usuario_atual
perfis_cadastrados = ler_perfis_db(email_usuario)
qtd_atual = len(perfis_cadastrados)
trocas_utilizadas = ler_trocas_db(email_usuario)
trocas_restantes = LIMITE_TROCAS_MENSAL - trocas_utilizadas

if opcao == "Painel Principal":
    st.subheader("Adicionar Perfil para Monitoramento")
    
    st.markdown(f"**Uso do Plano:** {qtd_atual} de {LIMITE_PLANO_INICIAL} perfis cadastrados.")
    st.progress(qtd_atual / LIMITE_PLANO_INICIAL if LIMITE_PLANO_INICIAL > 0 else 0)
    
    st.info(f"🔄 **Substituições no mês:** Você usou {trocas_utilizadas} de {LIMITE_TROCAS_MENSAL} trocas permitidas ({trocas_restantes} restantes).")
    
    with st.form("form_perfil_v6", clear_on_submit=True):
        perfil_input = st.text_input("Digite o nome ou @ do perfil (ex: natgeo):")
        enviado = st.form_submit_button("Cadastrar Perfil")
        
        if enviado:
            if perfil_input:
                perfil_limpo = perfil_input.replace("@", "").strip().lower()
                
                if len(perfis_cadastrados) >= LIMITE_PLANO_INICIAL:
                    st.error(f"⚠️ Limite de {LIMITE_PLANO_INICIAL} perfis simultâneos atingido. Exclua um perfil para liberar vaga.")
                else:
                    sucesso, status = salvar_perfil_db(email_usuario, perfil_limpo)
                    if sucesso:
                        if trocas_utilizadas < LIMITE_TROCAS_MENSAL:
                            registrar_substituicao_db(email_usuario)
                        st.success(f"Perfil '@{perfil_limpo}' cadastrado com sucesso!")
                        st.rerun()
                    elif status == "Duplicado":
                        st.warning("Este perfil já está cadastrado na sua lista.")
            else:
                st.warning("Por favor, digite um nome de perfil válido.")
            
    st.markdown("### 📋 Status e Ações dos Perfis Monitorados")
    
    if perfis_cadastrados:
        for p in perfis_cadastrados:
            total_posts = random.randint(120, 1500)
            tempo_ultimo = random.choice(["Há 12 minutos", "Há 45 minutos", "Há 2 horas", "Há 5 horas", "Ontem"])
            
            st.markdown(f"👤 **`@{p}`** | 📊 **Posts:** {total_posts} | ⏱️ **Último:** {tempo_ultimo}")
            
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                if st.button(f"▶️ Assistir", key=f"assistir_{p}"):
                    st.info(f"Abrindo player para @{p}...")
            with bcol2:
                if st.button(f"📥 Salvar Ref.", key=f"baixar_{p}"):
                    st.success(f"Referência de @{p} salva nos favoritos!")
            with bcol3:
                if st.button(f"🗑️ Excluir", key=f"excluir_{p}"):
                    if excluir_perfil_db(email_usuario, p):
                        st.success(f"Perfil @{p} removido!")
                        st.rerun()
            st.markdown("---")
        
        if st.button("🔄 Atualizar Métricas", key="btn_metricas_v6"):
            st.rerun()
    else:
        st.info("Nenhum perfil cadastrado ainda. Adicione acima para começar.")

elif opcao == "🔥 Tendências e Assuntos":
    st.subheader("🔥 Tracker de Assuntos e Hashtags em Alta")
    st.markdown("O robô vasculha os conteúdos mais engajados do dia para poupar o seu tempo de pesquisa.")
    
    st.markdown("""
    - 📈 **Nicho de Humor / Memes:** Formato de POV com transição rápida (+240% de engajamento hoje)
    - 🎵 **Áudio em Alta no Reels:** *“Batuque Digital Remix”* (Mais de 45 mil vídeos nas últimas 12h)
    - 🏷️ **Hashtags Mais Buscadas:** `#dicadodia`, `#viralvideo`, `#tendencia2026`
    - 💡 **Sugestão de Conteúdo Mastigada:** Criar vídeos curtos respondendo a dúvidas comuns do seu nicho nos primeiros 3 segundos.
    """)
    st.info("💡 *Dica:* Utilize esses temas nos seus roteiros para pegar a onda do algoritmo logo cedo!")

elif opcao == "Histórico de Alertas":
    st.subheader("🔔 Histórico de Disparos Recentes")
    st.markdown("Estes são os últimos alertas de vídeos virais identificados pelo robô.")
    
    historico = ler_historico_db()
    
    if historico:
        for item in historico:
            st.text(f"📢 {item}")
    else:
        st.info("Nenhum alerta registrado até o momento.")
        
    if st.button("🔄 Atualizar Histórico", key="btn_hist_v6"):
        st.rerun()

elif opcao == "Assinatura / Planos":
    st.subheader("💎 Planos e Assinaturas (ViralRadar)")
    st.markdown("Escolha a modalidade ideal e clique em um dos planos para prosseguir para o checkout.")
    
    st.markdown("### 🟢 1. Plano Iniciante (Até 10 perfis ativos)")
    st.markdown("* 5 trocas mensais | Alertas via Telegram")
    if st.button("💳 Assinar Iniciante - Mensal (R$ 19,99/mês)", key="plan_i_m_v6"):
        st.session_state.plano_checkout = "Plano Iniciante (Mensal) — R$ 19,99/mês"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Iniciante - Trimestral (R$ 54,99 | 3x de R$ 18,33)", key="plan_i_t_v6"):
        st.session_state.plano_checkout = "Plano Iniciante (Trimestral) — R$ 54,99 (3x de R$ 18,33/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Iniciante - Semestral (R$ 99,99 | 6x de R$ 16,66)", key="plan_i_s_v6"):
        st.session_state.plano_checkout = "Plano Iniciante (Semestral) — R$ 99,99 (6x de R$ 16,66/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Iniciante - Anual (R$ 179,99 | 12x de R$ 15,00)", key="plan_i_a_v6"):
        st.session_state.plano_checkout = "Plano Iniciante (Anual) — R$ 179,99 (12x de R$ 15,00/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")

    st.markdown("---")
    st.markdown("### 🟡 2. Plano Criador / Pró (Até 25 perfis ativos)")
    st.markdown("* Até 15 trocas mensais | Prioridade máxima nos alertas")
    if st.button("💳 Assinar Criador - Mensal (R$ 49,99/mês)", key="plan_c_m_v6"):
        st.session_state.plano_checkout = "Plano Criador/Pró (Mensal) — R$ 49,99/mês"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Criador - Trimestral (R$ 139,99 | 3x de R$ 46,66)", key="plan_c_t_v6"):
        st.session_state.plano_checkout = "Plano Criador/Pró (Trimestral) — R$ 139,99 (3x de R$ 46,66/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Criador - Semestral (R$ 259,99 | 6x de R$ 43,33)", key="plan_c_s_v6"):
        st.session_state.plano_checkout = "Plano Criador/Pró (Semestral) — R$ 259,99 (6x de R$ 43,33/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Criador - Anual (R$ 479,99 | 12x de R$ 39,99)", key="plan_c_a_v6"):
        st.session_state.plano_checkout = "Plano Criador/Pró (Anual) — R$ 479,99 (12x de R$ 39,99/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")

    st.markdown("---")
    st.markdown("### 🟣 3. Plano Agência / Power (Até 60 perfis ativos)")
    st.markdown("* Trocas ilimitadas | Múltiplos canais Telegram & Suporte VIP")
    if st.button("💳 Assinar Agência - Mensal (R$ 119,99/mês)", key="plan_a_m_v6"):
        st.session_state.plano_checkout = "Plano Agência/Power (Mensal) — R$ 119,99/mês"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Agência - Trimestral (R$ 329,99 | 3x de R$ 109,99)", key="plan_a_t_v6"):
        st.session_state.plano_checkout = "Plano Agência/Power (Trimestral) — R$ 329,99 (3x de R$ 109,99/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Agência - Semestral (R$ 599,99 | 6x de R$ 99,99)", key="plan_a_s_v6"):
        st.session_state.plano_checkout = "Plano Agência/Power (Semestral) — R$ 599,99 (6x de R$ 99,99/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")
    if st.button("💳 Assinar Agência - Anual (R$ 1.099,99 | 12x de R$ 91,66)", key="plan_a_a_v6"):
        st.session_state.plano_checkout = "Plano Agência/Power (Anual) — R$ 1.099,99 (12x de R$ 91,66/mês)"
        st.success("Plano selecionado! Vá até a aba 'Finalizar Compra / Pagamento' no menu lateral.")

elif opcao == "Finalizar Compra / Pagamento":
    st.subheader("🛒 Finalizar Assinatura — Checkout Seguro")
    
    if st.session_state.plano_checkout:
        st.markdown(f"📦 **Cliente Logado:** `{st.session_state.usuario_atual}`")
        st.markdown("### Resumo do seu Pedido:")
        st.info(f"**Plano Escolhido:** {st.session_state.plano_checkout}")
        st.markdown("---")
        st.markdown("**Formas de Pagamento Disponíveis:**")
        st.markdown("- 🟢 **Pix:** Aprovação instantânea.")
        st.markdown("- 💳 **Cartão de Crédito:** Parcelamento seguro via gateway.")
        
        if st.button("🚀 Confirmar e Pagar Agora", key="btn_concluir_pagamento_v6"):
            st.success("🎉 Pagamento simulado com sucesso! Bem-vindo ao ViralRadar Pro. O seu acesso foi liberado!")
    else:
        st.warning("⚠️ Nenhum plano selecionado ainda! Volte na aba **Assinatura / Planos** e clique em um dos botões de assinatura para prosseguir.")

elif opcao == "Suporte":
    st.subheader("💬 Central de Ajuda")
    st.markdown("Precisa de suporte técnico ou de ajuda com as assinaturas? Fale conosco pelo canal oficial.")