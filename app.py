import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import re
import io
import base64

# ====================================================================
# CONFIGURAÇÃO INICIAL
# ====================================================================
st.set_page_config(page_title="Controle de Fichinha", page_icon="📋", layout="wide")

# ====================================================================
# CONSTANTES
# ====================================================================
SENHA_GERENTE = "admin123"

# ====================================================================
# ESTADO DA SESSÃO
# ====================================================================
if 'form_data' not in st.session_state:
    st.session_state.form_data = {}
if 'modo_seguro' not in st.session_state:
    st.session_state.modo_seguro = False
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False
if 'tempo_autenticacao' not in st.session_state:
    st.session_state.tempo_autenticacao = None

# ====================================================================
# FUNÇÕES DE BANCO DE DADOS
# ====================================================================
def get_db():
    return sqlite3.connect('fichinha.db')

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Tabela clientes
    c.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            data_cadastro TEXT,
            modo_seguro INTEGER DEFAULT 0,
            cpf TEXT,
            rg TEXT,
            data_nascimento TEXT,
            email TEXT,
            celular TEXT,
            logradouro TEXT,
            numero TEXT,
            complemento TEXT,
            bairro TEXT,
            cidade TEXT,
            estado TEXT,
            cep TEXT,
            aceite_lgpd INTEGER DEFAULT 0,
            data_aceite_lgpd TEXT,
            observacoes TEXT
        )
    ''')
    
    # Tabela produtos
    c.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            nome TEXT NOT NULL,
            valor REAL NOT NULL,
            data_compra TEXT,
            pago INTEGER DEFAULT 0,
            tipo_pagamento TEXT,
            data_pagamento TEXT
        )
    ''')
    
    # Tabela pagamentos (apenas histórico)
    c.execute('''
        CREATE TABLE IF NOT EXISTS pagamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            valor REAL NOT NULL,
            tipo TEXT,
            data_pagamento TEXT,
            descricao TEXT
        )
    ''')
    
    # Tabela produtos padrão
    c.execute('''
        CREATE TABLE IF NOT EXISTS produtos_padrao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE,
            valor REAL NOT NULL,
            data_cadastro TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    migrar_banco()

def migrar_banco():
    """Adiciona colunas e tabelas faltantes se necessário"""
    conn = get_db()
    c = conn.cursor()
    
    # Verifica colunas da tabela clientes
    c.execute("PRAGMA table_info(clientes)")
    colunas = [col[1] for col in c.fetchall()]
    
    colunas_necessarias = ['cpf', 'rg', 'data_nascimento', 'email', 'celular', 
                          'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 
                          'estado', 'cep', 'modo_seguro', 'aceite_lgpd', 'data_aceite_lgpd', 'observacoes']
    
    for col in colunas_necessarias:
        if col not in colunas:
            tipo = "INTEGER DEFAULT 0" if col in ['modo_seguro', 'aceite_lgpd'] else "TEXT"
            try:
                c.execute(f"ALTER TABLE clientes ADD COLUMN {col} {tipo}")
            except:
                pass
    
    # Verifica se a tabela produtos_padrao existe
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='produtos_padrao'")
    if not c.fetchone():
        c.execute('''
            CREATE TABLE produtos_padrao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                valor REAL NOT NULL,
                data_cadastro TEXT
            )
        ''')
    
    conn.commit()
    conn.close()

# ====================================================================
# FUNÇÕES DE VALIDAÇÃO E FORMATAÇÃO
# ====================================================================
def valida_cpf(cpf):
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        digito = (soma * 10) % 11
        if digito == 10:
            digito = 0
        if int(cpf[i]) != digito:
            return False
    return True

def formata_cpf(cpf):
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf

def formata_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calcula_saldo(cliente_id):
    """Calcula o saldo CORRETO (apenas produtos não pagos)"""
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT SUM(valor) as total FROM produtos WHERE cliente_id = ? AND pago = 0",
        conn, params=(cliente_id,)
    )
    conn.close()
    return df['total'].iloc[0] or 0.0

def autentica(senha):
    if senha == SENHA_GERENTE:
        st.session_state.autenticado = True
        st.session_state.tempo_autenticacao = datetime.now()
        return True
    return False

def logout():
    st.session_state.autenticado = False
    st.session_state.tempo_autenticacao = None

def esta_autenticado():
    if st.session_state.autenticado and st.session_state.tempo_autenticacao:
        if (datetime.now() - st.session_state.tempo_autenticacao).seconds > 1800:
            logout()
            return False
        return True
    return False

# ====================================================================
# FUNÇÃO PARA GERAR PDF (versão HTML - sem reportlab)
# ====================================================================
def gerar_pdf_html(cliente_id, produtos):
    """Gera um PDF em formato HTML (compatível com qualquer navegador)"""
    conn = get_db()
    cliente = pd.read_sql_query("SELECT * FROM clientes WHERE id = ?", conn, params=(cliente_id,)).iloc[0]
    conn.close()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Comprovante de Dívida</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            h1 {{ color: #2E86C1; border-bottom: 3px solid #2E86C1; padding-bottom: 10px; }}
            .header {{ text-align: right; font-size: 12px; color: #666; margin-bottom: 20px; }}
            .cliente {{ margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #2E86C1; }}
            .cliente h2 {{ margin-top: 0; color: #333; font-size: 14px; }}
            .cliente p {{ margin: 5px 0; font-size: 12px; }}
            .produtos {{ margin: 20px 0; }}
            .produtos h2 {{ color: #333; font-size: 14px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
            th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #2E86C1; color: white; }}
            .total {{ font-size: 16px; font-weight: bold; margin-top: 20px; text-align: right; padding: 10px; background: #e8f4fd; border-radius: 5px; }}
            .footer {{ font-size: 10px; color: #666; margin-top: 40px; border-top: 1px solid #ddd; padding-top: 15px; text-align: center; }}
            .destaque {{ color: #c0392b; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h1>📋 COMPROVANTE DE DÍVIDA</h1>
        <div class="header">Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
        
        <div class="cliente">
            <h2>📌 DADOS DO DEVEDOR</h2>
            <p><strong>Nome:</strong> {cliente['nome']}</p>
            <p><strong>CPF:</strong> {formata_cpf(cliente['cpf']) if cliente['cpf'] and cliente['cpf'] != '00000000000' else 'Não informado'}</p>
            <p><strong>Telefone:</strong> {cliente['telefone'] or 'Não informado'}</p>
    """
    
    if cliente['celular']:
        html += f"<p><strong>Celular:</strong> {cliente['celular']}</p>"
    
    if cliente['logradouro']:
        html += f"""
            <p><strong>Endereço:</strong> {cliente['logradouro']}, {cliente['numero']}</p>
            <p><strong>Bairro:</strong> {cliente['bairro']}, {cliente['cidade']} - {cliente['estado']}</p>
        """
    
    html += """
        </div>
        
        <div class="produtos">
            <h2>🛒 PRODUTOS EM ABERTO</h2>
            <table>
                <tr>
                    <th>#</th>
                    <th>Produto</th>
                    <th>Valor</th>
                    <th>Data</th>
                </tr>
    """
    
    total = 0
    for i, (_, p) in enumerate(produtos.iterrows(), 1):
        html += f"""
            <tr>
                <td>{i}</td>
                <td>{p['nome']}</td>
                <td>{formata_moeda(p['valor'])}</td>
                <td>{p['data_compra']}</td>
            </tr>
        """
        total += p['valor']
    
    html += f"""
            </table>
            <div class="total">
                💰 TOTAL DA DÍVIDA: <span class="destaque">{formata_moeda(total)}</span>
            </div>
        </div>
        
        <div class="footer">
            <p>Este documento tem validade como comprovante de dívida para fins de cobrança judicial ou extrajudicial,<br>
            conforme previsto no Código Civil Brasileiro (Lei nº 10.406/2002).</p>
            <p style="margin-top: 10px; font-size: 9px; color: #999;">Documento gerado automaticamente pelo sistema de controle de fichinha.</p>
        </div>
    </body>
    </html>
    """
    
    return html

def gerar_pdf_download(cliente_id, produtos):
    """Gera o HTML e cria um link para download como arquivo HTML (funciona como PDF)"""
    html = gerar_pdf_html(cliente_id, produtos)
    b64 = base64.b64encode(html.encode()).decode()
    href = f'<a href="data:text/html;base64,{b64}" download="comprovante_divida_{datetime.now().strftime("%Y%m%d_%H%M")}.html">📥 Baixar Comprovante (HTML)</a>'
    return href

# ====================================================================
# INICIALIZAÇÃO
# ====================================================================
init_db()

# ====================================================================
# SIDEBAR
# ====================================================================
st.sidebar.title("📋 Fichinha")
st.sidebar.markdown("---")

menu = st.sidebar.radio(
    "Navegação",
    ["🏠 Dashboard", "👤 Clientes", "📝 Nova Fichinha", "💰 Pagamentos", "📊 Relatórios"]
)

st.sidebar.markdown("---")

if st.sidebar.button("🔒" if not st.session_state.modo_seguro else "🔓", help="Clique para ativar/desativar Modo Seguro"):
    st.session_state.modo_seguro = not st.session_state.modo_seguro

if st.session_state.modo_seguro:
    st.sidebar.warning("🔒 Modo Seguro ATIVADO")
else:
    st.sidebar.info("📱 Modo Normal")

st.sidebar.markdown("---")
st.sidebar.caption(f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}")

# ====================================================================
# PÁGINAS
# ====================================================================

# -------------------- DASHBOARD --------------------
if menu == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    
    conn = get_db()
    total_clientes = pd.read_sql_query("SELECT COUNT(*) as total FROM clientes", conn)['total'].iloc[0]
    total_pendentes = pd.read_sql_query("SELECT COUNT(*) as total FROM produtos WHERE pago = 0", conn)['total'].iloc[0]
    valor_aberto = pd.read_sql_query("SELECT SUM(valor) as total FROM produtos WHERE pago = 0", conn)['total'].iloc[0] or 0
    clientes_seguro = pd.read_sql_query("SELECT COUNT(*) as total FROM clientes WHERE modo_seguro = 1", conn)['total'].iloc[0]
    total_padrao = pd.read_sql_query("SELECT COUNT(*) as total FROM produtos_padrao", conn)['total'].iloc[0]
    conn.close()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👤 Clientes", total_clientes)
    c2.metric("📝 Pendentes", total_pendentes)
    c3.metric("💰 Em Aberto", formata_moeda(valor_aberto))
    c4.metric("🔒 Modo Seguro", clientes_seguro)
    
    col1, col2, col3 = st.columns(3)
    col2.metric("🏷️ Produtos Padrão", total_padrao)

# -------------------- CLIENTES --------------------
elif menu == "👤 Clientes":
    st.title("👤 Clientes")
    
    # Formulário de cadastro
    with st.expander("➕ Novo Cliente", expanded=False):
        with st.form("form_cliente"):
            nome = st.text_input("Nome*", value=st.session_state.form_data.get('nome', ''))
            telefone = st.text_input("Telefone*", value=st.session_state.form_data.get('telefone', ''))
            
            if st.session_state.modo_seguro:
                st.divider()
                st.warning("🔒 Modo Seguro - Dados completos")
                c1, c2 = st.columns(2)
                with c1:
                    cpf = st.text_input("CPF", max_chars=11, value=st.session_state.form_data.get('cpf', ''))
                    rg = st.text_input("RG", value=st.session_state.form_data.get('rg', ''))
                    data_nasc = st.date_input("Nascimento", value=st.session_state.form_data.get('data_nasc', None))
                with c2:
                    email = st.text_input("Email", value=st.session_state.form_data.get('email', ''))
                    celular = st.text_input("Celular", value=st.session_state.form_data.get('celular', ''))
                
                st.subheader("Endereço")
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    logradouro = st.text_input("Logradouro", value=st.session_state.form_data.get('logradouro', ''))
                with c2:
                    numero = st.text_input("Número", value=st.session_state.form_data.get('numero', ''))
                with c3:
                    complemento = st.text_input("Complemento", value=st.session_state.form_data.get('complemento', ''))
                
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    bairro = st.text_input("Bairro", value=st.session_state.form_data.get('bairro', ''))
                with c2:
                    cidade = st.text_input("Cidade", value=st.session_state.form_data.get('cidade', ''))
                with c3:
                    estado = st.text_input("UF", max_chars=2, value=st.session_state.form_data.get('estado', ''))
                
                cep = st.text_input("CEP", max_chars=8, value=st.session_state.form_data.get('cep', ''))
                aceite_lgpd = st.checkbox("Aceito LGPD", value=st.session_state.form_data.get('aceite_lgpd', False))
                observacoes = st.text_area("Observações", value=st.session_state.form_data.get('observacoes', ''))
            else:
                cpf = rg = email = celular = logradouro = numero = complemento = bairro = cidade = estado = cep = observacoes = None
                data_nasc = None
                aceite_lgpd = False
            
            if st.form_submit_button("Cadastrar"):
                erros = []
                if not nome:
                    erros.append("Nome obrigatório")
                if not telefone:
                    erros.append("Telefone obrigatório")
                if st.session_state.modo_seguro:
                    if not cpf or not valida_cpf(cpf):
                        erros.append("CPF inválido")
                    if not logradouro or not numero or not bairro or not cidade or not estado:
                        erros.append("Endereço completo obrigatório")
                    if not aceite_lgpd:
                        erros.append("Aceite LGPD obrigatório")
                
                if erros:
                    st.session_state.form_data = {
                        'nome': nome, 'telefone': telefone, 'cpf': cpf or '', 'rg': rg or '',
                        'data_nasc': data_nasc, 'email': email or '', 'celular': celular or '',
                        'logradouro': logradouro or '', 'numero': numero or '', 'complemento': complemento or '',
                        'bairro': bairro or '', 'cidade': cidade or '', 'estado': estado or '',
                        'cep': cep or '', 'aceite_lgpd': aceite_lgpd, 'observacoes': observacoes or ''
                    }
                    for erro in erros:
                        st.error(f"❌ {erro}")
                else:
                    conn = get_db()
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO clientes 
                           (nome, telefone, data_cadastro, modo_seguro, cpf, rg, data_nascimento, 
                            email, celular, logradouro, numero, complemento, bairro, cidade, estado, cep,
                            aceite_lgpd, data_aceite_lgpd, observacoes) 
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (nome, telefone, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         1 if st.session_state.modo_seguro else 0,
                         cpf, rg, str(data_nasc) if data_nasc else None,
                         email, celular, logradouro, numero, complemento, bairro, cidade, estado, cep,
                         1 if aceite_lgpd else 0,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S") if aceite_lgpd else None,
                         observacoes)
                    )
                    conn.commit()
                    conn.close()
                    st.session_state.form_data = {}
                    st.success(f"✅ Cliente cadastrado!")
                    st.rerun()
    
    # Lista de clientes
    st.subheader("📋 Lista de Clientes")
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT c.id, c.nome, c.telefone, c.modo_seguro, c.cpf,
               COUNT(p.id) as qtd_produtos,
               SUM(CASE WHEN p.pago = 0 THEN p.valor ELSE 0 END) as saldo
        FROM clientes c
        LEFT JOIN produtos p ON c.id = p.cliente_id
        GROUP BY c.id
        ORDER BY c.id DESC
    """, conn)
    conn.close()
    
    if not df.empty:
        df['saldo'] = df['saldo'].fillna(0)
        df['saldo_fmt'] = df['saldo'].apply(formata_moeda)
        df['modo'] = df['modo_seguro'].apply(lambda x: "🔒" if x else "📱")
        st.dataframe(
            df[['id', 'nome', 'telefone', 'saldo_fmt', 'modo']],
            column_config={"id": "ID", "nome": "Nome", "telefone": "Telefone", "saldo_fmt": "Saldo", "modo": ""},
            use_container_width=True
        )
        
        # Exclusão com autenticação
        st.divider()
        st.subheader("🗑️ Excluir Cliente")
        
        if not esta_autenticado():
            with st.expander("🔐 Autenticar para excluir", expanded=True):
                senha = st.text_input("Senha do gerente:", type="password")
                if st.button("Autenticar"):
                    if autentica(senha):
                        st.success("✅ Autenticado!")
                        st.rerun()
                    else:
                        st.error("❌ Senha incorreta!")
        else:
            st.success(f"🔓 Autenticado - Sessão válida por mais 30 min")
            if st.button("🚪 Desautenticar"):
                logout()
                st.rerun()
            
            cliente_id = st.selectbox(
                "Selecione o cliente para excluir",
                df['id'].tolist(),
                format_func=lambda x: df[df['id'] == x]['nome'].iloc[0]
            )
            
            if cliente_id:
                nome_cliente = df[df['id'] == cliente_id]['nome'].iloc[0]
                saldo = df[df['id'] == cliente_id]['saldo'].iloc[0]
                
                if saldo > 0:
                    st.warning(f"⚠️ Cliente tem saldo de {formata_moeda(saldo)} pendente!")
                
                confirmar = st.text_input(f"Digite o nome '{nome_cliente}' para confirmar:")
                
                if confirmar == nome_cliente:
                    if st.button("🗑️ EXCLUIR PERMANENTEMENTE", type="primary"):
                        conn = get_db()
                        c = conn.cursor()
                        c.execute("DELETE FROM pagamentos WHERE cliente_id = ?", (cliente_id,))
                        c.execute("DELETE FROM produtos WHERE cliente_id = ?", (cliente_id,))
                        c.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Cliente excluído!")
                        st.rerun()
    else:
        st.info("ℹ️ Nenhum cliente cadastrado.")

# -------------------- NOVA FICHINHA --------------------
elif menu == "📝 Nova Fichinha":
    st.title("📝 Nova Fichinha")
    
    conn = get_db()
    clientes = pd.read_sql_query("SELECT id, nome, modo_seguro FROM clientes ORDER BY nome", conn)
    conn.close()
    
    if clientes.empty:
        st.warning("⚠️ Cadastre um cliente primeiro!")
    else:
        cliente_id = st.selectbox(
            "Cliente",
            clientes['id'].tolist(),
            format_func=lambda x: f"{clientes[clientes['id'] == x]['nome'].iloc[0]} {'🔒' if clientes[clientes['id'] == x]['modo_seguro'].iloc[0] else ''}"
        )
        
        if cliente_id:
            saldo = calcula_saldo(cliente_id)
            st.info(f"💰 Saldo atual: {formata_moeda(saldo)}")
            
            if clientes[clientes['id'] == cliente_id]['modo_seguro'].iloc[0]:
                st.warning("🔒 Cliente em Modo Seguro")
            
            # ========== SEÇÃO: GERENCIAR PRODUTOS PADRÃO ==========
            with st.expander("🏷️ Gerenciar Produtos Padrão", expanded=False):
                st.caption("Cadastre produtos com preços fixos para agilizar o atendimento")
                
                # Formulário para cadastrar produto padrão
                with st.form("form_produto_padrao", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        nome_padrao = st.text_input("Nome do Produto*")
                    with col2:
                        valor_padrao = st.number_input(
                            "Valor Padrão (R$)*",
                            min_value=0.01,
                            value=0.01,
                            step=0.01,
                            format="%.2f"
                        )
                    
                    if st.form_submit_button("➕ Cadastrar Produto Padrão"):
                        if not nome_padrao:
                            st.error("❌ Nome do produto obrigatório")
                        elif valor_padrao <= 0:
                            st.error("❌ Valor deve ser maior que zero")
                        else:
                            try:
                                conn = get_db()
                                c = conn.cursor()
                                c.execute(
                                    "INSERT INTO produtos_padrao (nome, valor, data_cadastro) VALUES (?, ?, ?)",
                                    (nome_padrao, valor_padrao, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                )
                                conn.commit()
                                conn.close()
                                st.success(f"✅ Produto '{nome_padrao}' cadastrado com sucesso!")
                                st.rerun()
                            except sqlite3.IntegrityError:
                                st.error("❌ Produto já cadastrado!")
                
                # Lista de produtos padrão
                st.subheader("📋 Produtos Padrão Cadastrados")
                conn = get_db()
                df_padrao = pd.read_sql_query(
                    "SELECT id, nome, valor, data_cadastro FROM produtos_padrao ORDER BY nome",
                    conn
                )
                conn.close()
                
                if not df_padrao.empty:
                    df_padrao['valor_fmt'] = df_padrao['valor'].apply(formata_moeda)
                    st.dataframe(
                        df_padrao[['nome', 'valor_fmt', 'data_cadastro']],
                        column_config={
                            "nome": "Produto",
                            "valor_fmt": "Valor Padrão",
                            "data_cadastro": "Cadastrado em"
                        },
                        use_container_width=True
                    )
                    
                    # Botão para excluir produto padrão (com autenticação)
                    st.caption("🔒 Para excluir um produto padrão, autentique-se como gerente")
                    
                    if esta_autenticado():
                        produto_excluir = st.selectbox(
                            "Selecione o produto padrão para excluir",
                            df_padrao['id'].tolist(),
                            format_func=lambda x: f"{df_padrao[df_padrao['id'] == x]['nome'].iloc[0]} - {formata_moeda(df_padrao[df_padrao['id'] == x]['valor'].iloc[0])}",
                            key="excluir_padrao"
                        )
                        
                        if produto_excluir:
                            nome_excluir = df_padrao[df_padrao['id'] == produto_excluir]['nome'].iloc[0]
                            confirmar = st.text_input(f"Digite '{nome_excluir}' para confirmar exclusão:", key="confirma_padrao")
                            
                            if confirmar == nome_excluir:
                                if st.button("🗑️ Excluir Produto Padrão", type="primary"):
                                    conn = get_db()
                                    c = conn.cursor()
                                    c.execute("DELETE FROM produtos_padrao WHERE id = ?", (produto_excluir,))
                                    conn.commit()
                                    conn.close()
                                    st.success(f"✅ Produto '{nome_excluir}' excluído!")
                                    st.rerun()
                    else:
                        st.info("🔐 Autentique-se na seção 'Excluir Cliente' para excluir produtos padrão")
                else:
                    st.info("ℹ️ Nenhum produto padrão cadastrado ainda.")
            
            # ========== ADICIONAR PRODUTO À FICHINHA ==========
            st.divider()
            st.subheader("➕ Adicionar Produto à Fichinha")
            
            # Busca produtos padrão
            conn = get_db()
            df_padrao = pd.read_sql_query("SELECT id, nome, valor FROM produtos_padrao ORDER BY nome", conn)
            conn.close()
            
            # Opção de escolha: Produto Padrão ou Personalizado
            modo_adicao = st.radio(
                "Tipo de produto:",
                ["📦 Produto Padrão", "✏️ Valor Personalizado"],
                horizontal=True
            )
            
            if modo_adicao == "📦 Produto Padrão":
                # ========== PRODUTO PADRÃO ==========
                if df_padrao.empty:
                    st.warning("⚠️ Nenhum produto padrão cadastrado. Cadastre na seção 'Gerenciar Produtos Padrão'.")
                else:
                    with st.form("form_produto_padrao_ficha", clear_on_submit=True):
                        produto_selecionado = st.selectbox(
                            "Selecione o produto",
                            df_padrao['id'].tolist(),
                            format_func=lambda x: f"{df_padrao[df_padrao['id'] == x]['nome'].iloc[0]} - {formata_moeda(df_padrao[df_padrao['id'] == x]['valor'].iloc[0])}"
                        )
                        
                        if produto_selecionado:
                            nome_produto = df_padrao[df_padrao['id'] == produto_selecionado]['nome'].iloc[0]
                            valor_produto = df_padrao[df_padrao['id'] == produto_selecionado]['valor'].iloc[0]
                            
                            st.info(f"📦 Produto: **{nome_produto}** - Valor: {formata_moeda(valor_produto)}")
                            
                            if st.form_submit_button("✅ Adicionar à Fichinha"):
                                conn = get_db()
                                c = conn.cursor()
                                c.execute(
                                    "INSERT INTO produtos (cliente_id, nome, valor, data_compra, pago) VALUES (?, ?, ?, ?, 0)",
                                    (cliente_id, nome_produto, valor_produto, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                )
                                conn.commit()
                                conn.close()
                                st.success(f"✅ Produto '{nome_produto}' adicionado!")
                                st.rerun()
            
            else:
                # ========== VALOR PERSONALIZADO (Promoções) ==========
                with st.form("form_produto_personalizado", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        nome = st.text_input("Nome do Produto*")
                    with col2:
                        valor = st.number_input(
                            "Valor (R$)*",
                            min_value=0.01,
                            value=0.01,
                            step=0.01,
                            format="%.2f",
                            help="Use para promoções ou produtos sem preço fixo"
                        )
                    
                    st.caption("✏️ Valor personalizado - ideal para promoções e itens avulsos")
                    
                    if st.form_submit_button("✅ Adicionar à Fichinha"):
                        if not nome:
                            st.error("❌ Nome do produto obrigatório")
                        else:
                            conn = get_db()
                            c = conn.cursor()
                            c.execute(
                                "INSERT INTO produtos (cliente_id, nome, valor, data_compra, pago) VALUES (?, ?, ?, ?, 0)",
                                (cliente_id, nome, valor, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            )
                            conn.commit()
                            conn.close()
                            st.success(f"✅ Produto '{nome}' adicionado com valor personalizado!")
                            st.rerun()
            
            # ========== LISTA DE PRODUTOS PENDENTES ==========
            st.divider()
            st.subheader("📋 Fichinha Atual")
            
            conn = get_db()
            produtos = pd.read_sql_query(
                "SELECT id, nome, valor, data_compra FROM produtos WHERE cliente_id = ? AND pago = 0 ORDER BY id DESC",
                conn, params=(cliente_id,)
            )
            conn.close()
            
            if not produtos.empty:
                produtos['valor_fmt'] = produtos['valor'].apply(formata_moeda)
                st.dataframe(produtos[['nome', 'valor_fmt', 'data_compra']], use_container_width=True)
                st.metric("💰 Total da Fichinha", formata_moeda(produtos['valor'].sum()))
                
                # ========== EXCLUIR PRODUTO COM AUTENTICAÇÃO ==========
                st.divider()
                st.subheader("🗑️ Excluir Produto da Fichinha")
                st.caption("🔒 Requer autenticação do gerente para evitar exclusões acidentais")
                
                if not esta_autenticado():
                    with st.expander("🔐 Autentique-se para excluir produtos", expanded=False):
                        st.info("Digite a senha do gerente para habilitar a exclusão de produtos.")
                        senha = st.text_input("Senha do gerente:", type="password", key="senha_produto_ficha")
                        
                        if st.button("🔓 Autenticar", use_container_width=True):
                            if autentica(senha):
                                st.success("✅ Autenticado com sucesso!")
                                st.rerun()
                            else:
                                st.error("❌ Senha incorreta!")
                else:
                    st.success(f"🔓 Autenticado como gerente")
                    
                    col1, col2 = st.columns([3, 1])
                    with col2:
                        if st.button("🚪 Sair", use_container_width=True):
                            logout()
                            st.rerun()
                    
                    produto_id = st.selectbox(
                        "Selecione o produto para excluir",
                        produtos['id'].tolist(),
                        format_func=lambda x: f"{produtos[produtos['id'] == x]['nome'].iloc[0]} - {formata_moeda(produtos[produtos['id'] == x]['valor'].iloc[0])}"
                    )
                    
                    if produto_id:
                        nome_produto = produtos[produtos['id'] == produto_id]['nome'].iloc[0]
                        valor_produto = produtos[produtos['id'] == produto_id]['valor'].iloc[0]
                        
                        st.warning(f"⚠️ Você está prestes a excluir: **{nome_produto}** ({formata_moeda(valor_produto)})")
                        
                        confirmar = st.text_input(
                            f"Digite o nome do produto para confirmar:",
                            placeholder="Digite o nome exato do produto"
                        )
                        
                        if confirmar == nome_produto:
                            if st.button("🗑️ EXCLUIR PRODUTO", type="primary", use_container_width=True):
                                conn = get_db()
                                c = conn.cursor()
                                c.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
                                conn.commit()
                                conn.close()
                                st.success(f"✅ Produto excluído!")
                                st.rerun()
                        else:
                            if confirmar:
                                st.error("❌ Nome não corresponde!")
                            else:
                                st.info("Digite o nome do produto para habilitar a exclusão.")
            else:
                st.success("✅ Nenhum produto pendente!")

# -------------------- PAGAMENTOS --------------------
elif menu == "💰 Pagamentos":
    st.title("💰 Pagamentos")
    
    conn = get_db()
    clientes = pd.read_sql_query("SELECT id, nome FROM clientes ORDER BY nome", conn)
    conn.close()
    
    if clientes.empty:
        st.warning("⚠️ Cadastre um cliente primeiro!")
    else:
        cliente_id = st.selectbox(
            "Cliente",
            clientes['id'].tolist(),
            format_func=lambda x: clientes[clientes['id'] == x]['nome'].iloc[0]
        )
        
        if cliente_id:
            saldo = calcula_saldo(cliente_id)
            st.info(f"💰 Saldo atual: {formata_moeda(saldo)}")
            
            if saldo <= 0:
                st.success("✅ Cliente não possui débitos!")
            else:
                # Mostra produtos pendentes
                conn = get_db()
                produtos = pd.read_sql_query(
                    "SELECT id, nome, valor FROM produtos WHERE cliente_id = ? AND pago = 0 ORDER BY data_compra ASC",
                    conn, params=(cliente_id,)
                )
                conn.close()
                
                if not produtos.empty:
                    produtos['valor_fmt'] = produtos['valor'].apply(formata_moeda)
                    st.subheader("📋 Produtos em Aberto")
                    st.dataframe(produtos[['nome', 'valor_fmt']], use_container_width=True)
                    st.metric("💲 Total", formata_moeda(produtos['valor'].sum()))
                    
                    with st.form("form_pagamento"):
                        c1, c2 = st.columns(2)
                        with c1:
                            valor = st.number_input(
                                "Valor (R$)*",
                                min_value=0.01,
                                max_value=float(saldo),
                                value=min(10.00, float(saldo)),
                                step=0.01,
                                format="%.2f"
                            )
                        with c2:
                            tipo = st.selectbox(
                                "Forma",
                                ["dinheiro", "cartao", "pix"],
                                format_func=lambda x: {"dinheiro": "💵 Dinheiro", "cartao": "💳 Cartão", "pix": "📱 Pix"}[x]
                            )
                        
                        descricao = st.text_input("Descrição (opcional)")
                        
                        if st.form_submit_button("Registrar Pagamento"):
                            if valor > saldo:
                                st.error(f"❌ Valor excede o débito de {formata_moeda(saldo)}")
                            else:
                                conn = get_db()
                                c = conn.cursor()
                                
                                # Abate os produtos (FIFO)
                                valor_restante = valor
                                for _, row in produtos.iterrows():
                                    if valor_restante <= 0:
                                        break
                                    
                                    if row['valor'] <= valor_restante:
                                        c.execute(
                                            "UPDATE produtos SET pago = 1, tipo_pagamento = ?, data_pagamento = ? WHERE id = ?",
                                            (tipo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row['id'])
                                        )
                                        valor_restante -= row['valor']
                                    else:
                                        resto = row['valor'] - valor_restante
                                        c.execute(
                                            "UPDATE produtos SET pago = 1, tipo_pagamento = ?, data_pagamento = ? WHERE id = ?",
                                            (tipo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row['id'])
                                        )
                                        c.execute(
                                            "INSERT INTO produtos (cliente_id, nome, valor, data_compra, pago) VALUES (?, ?, ?, ?, 0)",
                                            (cliente_id, f"{row['nome']} (restante)", round(resto, 2),
                                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                        )
                                        valor_restante = 0
                                
                                # Registra no histórico
                                c.execute(
                                    "INSERT INTO pagamentos (cliente_id, valor, tipo, data_pagamento, descricao) VALUES (?, ?, ?, ?, ?)",
                                    (cliente_id, valor, tipo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), descricao)
                                )
                                
                                conn.commit()
                                novo_saldo = calcula_saldo(cliente_id)
                                conn.close()
                                
                                st.success(f"✅ Pagamento de {formata_moeda(valor)} registrado!")
                                st.info(f"💰 Novo saldo: {formata_moeda(novo_saldo)}")
                                if novo_saldo == 0:
                                    st.balloons()
                                st.rerun()
            
            # Histórico de pagamentos
            st.subheader("📋 Histórico de Pagamentos")
            conn = get_db()
            historico = pd.read_sql_query(
                "SELECT valor, tipo, data_pagamento, descricao FROM pagamentos WHERE cliente_id = ? ORDER BY id DESC LIMIT 30",
                conn, params=(cliente_id,)
            )
            conn.close()
            
            if not historico.empty:
                historico['valor_fmt'] = historico['valor'].apply(formata_moeda)
                historico['tipo'] = historico['tipo'].apply(
                    lambda x: {"dinheiro": "💵", "cartao": "💳", "pix": "📱"}.get(x, x)
                )
                st.dataframe(historico[['valor_fmt', 'tipo', 'data_pagamento', 'descricao']], use_container_width=True)

# -------------------- RELATÓRIOS --------------------
elif menu == "📊 Relatórios":
    st.title("📊 Relatórios")
    
    tab1, tab2, tab3 = st.tabs(["📈 Devedores", "🏷️ Produtos Padrão", "📤 Exportar"])
    
    with tab1:
        conn = get_db()
        df = pd.read_sql_query("""
            SELECT c.nome, c.telefone, c.modo_seguro, SUM(p.valor) as total, COUNT(p.id) as qtd
            FROM clientes c
            JOIN produtos p ON c.id = p.cliente_id
            WHERE p.pago = 0
            GROUP BY c.id
            ORDER BY total DESC
        """, conn)
        conn.close()
        
        if not df.empty:
            df['total_fmt'] = df['total'].apply(formata_moeda)
            df['modo'] = df['modo_seguro'].apply(lambda x: "🔒" if x else "📱")
            st.dataframe(df[['nome', 'telefone', 'modo', 'qtd', 'total_fmt']], use_container_width=True)
            
            st.subheader("📊 Gráfico")
            st.bar_chart(df.set_index('nome')[['total']])
        else:
            st.info("ℹ️ Nenhum devedor!")
    
    with tab2:
        st.subheader("🏷️ Produtos Padrão Cadastrados")
        conn = get_db()
        df_padrao = pd.read_sql_query(
            "SELECT nome, valor, data_cadastro FROM produtos_padrao ORDER BY nome",
            conn
        )
        conn.close()
        
        if not df_padrao.empty:
            df_padrao['valor_fmt'] = df_padrao['valor'].apply(formata_moeda)
            st.dataframe(
                df_padrao[['nome', 'valor_fmt', 'data_cadastro']],
                column_config={
                    "nome": "Produto",
                    "valor_fmt": "Valor",
                    "data_cadastro": "Cadastrado em"
                },
                use_container_width=True
            )
        else:
            st.info("ℹ️ Nenhum produto padrão cadastrado.")
    
    with tab3:
        st.subheader("📤 Exportar Dados")
        if st.button("Exportar Todos (CSV)"):
            conn = get_db()
            c = pd.read_sql_query("SELECT * FROM clientes", conn)
            p = pd.read_sql_query("SELECT * FROM produtos", conn)
            pg = pd.read_sql_query("SELECT * FROM pagamentos", conn)
            pp = pd.read_sql_query("SELECT * FROM produtos_padrao", conn)
            conn.close()
            
            col1, col2 = st.columns(2)
            with col1:
                st.download_button("📥 Clientes", c.to_csv(index=False).encode(), "clientes.csv", "text/csv")
                st.download_button("📥 Produtos", p.to_csv(index=False).encode(), "produtos.csv", "text/csv")
            with col2:
                st.download_button("📥 Pagamentos", pg.to_csv(index=False).encode(), "pagamentos.csv", "text/csv")
                st.download_button("📥 Produtos Padrão", pp.to_csv(index=False).encode(), "produtos_padrao.csv", "text/csv")