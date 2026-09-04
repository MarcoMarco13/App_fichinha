import streamlit as st
import pandas as pd
from datetime import datetime
import re
import json
import base64
from supabase import create_client, Client
import time

# ====================================================================
# CONFIGURAÇÃO INICIAL
# ====================================================================
st.set_page_config(page_title="Controle de Fichinha", page_icon="📋", layout="wide")

# ====================================================================
# CONFIGURAÇÃO SUPABASE
# ====================================================================
SUPABASE_URL = "https://ffbhtykclphnbarvyyts.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZmYmh0eWtjbHBobmJhcnZ5eXRzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg1MDAyNjcsImV4cCI6MjEwNDA3NjI2N30.LHPs8_LH8dTIXbcW21BaoxHaknZPvHyza2NdjZIVwjo"

# Cache da conexão Supabase (reutiliza a mesma conexão)
@st.cache_resource(ttl=3600)
def get_supabase():
    """Retorna uma única instância do cliente Supabase"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ====================================================================
# CONSTANTES
# ====================================================================
SENHA_GERENTE = "Locadora2023."

# ====================================================================
# USUÁRIOS AUTORIZADOS
# ====================================================================
USUARIOS = {
    "admin": "Locadora2023.",
    "gerente": "Locadora2023.",
    "caixa": "Locadora2026."
}

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
if 'logado' not in st.session_state:
    st.session_state.logado = False
if 'usuario' not in st.session_state:
    st.session_state.usuario = None
if 'cache_timestamp' not in st.session_state:
    st.session_state.cache_timestamp = None

# ====================================================================
# FUNÇÕES DE AUTENTICAÇÃO
# ====================================================================
def verificar_login():
    return st.session_state.logado

def fazer_login(usuario, senha):
    if usuario in USUARIOS and USUARIOS[usuario] == senha:
        st.session_state.logado = True
        st.session_state.usuario = usuario
        return True
    return False

def fazer_logout():
    st.session_state.logado = False
    st.session_state.usuario = None
    st.cache_data.clear()
    st.rerun()

def tela_login():
    st.title("🔐 Controle de Fichinha")
    st.markdown("---")
    st.subheader("Faça login para acessar o sistema")
    
    with st.form("form_login"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        
        if st.form_submit_button("Entrar"):
            if fazer_login(usuario, senha):
                st.success(f"✅ Bem-vindo, {usuario}!")
                st.rerun()
            else:
                st.error("❌ Usuário ou senha inválidos!")
    
    st.markdown("---")
    st.caption("🔒 Sistema protegido | Acesso restrito")

# ====================================================================
# FUNÇÕES DE BANCO DE DADOS COM CACHE
# ====================================================================

CACHE_TTL = 60
CACHE_LONGO = 300

@st.cache_data(ttl=CACHE_TTL)
def query_to_list_cached(table, columns="*", filters=None, order=None):
    """Versão com cache da função query_to_list"""
    try:
        query = supabase.table(table).select(columns)
        
        if filters:
            for column, value in filters.items():
                if value is not None:
                    query = query.eq(column, value)
        
        if order:
            if isinstance(order, dict):
                query = query.order(order.get('column'), desc=order.get('desc', True))
            else:
                query = query.order(order, desc=True)
        
        response = query.execute()
        
        if response and hasattr(response, 'data'):
            return response.data if response.data else []
        return []
        
    except Exception as e:
        st.error(f"Erro ao buscar dados da tabela {table}: {e}")
        return []
    
@st.cache_data(ttl=CACHE_TTL)
def query_to_dict_cached(table, columns="*", filters=None):
    """Versão com cache da função query_to_dict"""
    results = query_to_list_cached(table, columns, filters)
    return results[0] if results else None

def query_to_list(table, columns="*", filters=None, order=None):
    return query_to_list_cached(table, columns, filters, order)

def query_to_dict(table, columns="*", filters=None):
    return query_to_dict_cached(table, columns, filters)

def insert_data(table, data):
    try:
        response = supabase.table(table).insert(data).execute()
        if response.data:
            st.cache_data.clear()
            return response.data[0]
        return None
    except Exception as e:
        st.error(f"Erro ao inserir dados na tabela {table}: {e}")
        return None

def update_data(table, data, filters):
    try:
        query = supabase.table(table).update(data)
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.execute()
        if response.data:
            st.cache_data.clear()
            return response.data[0]
        return None
    except Exception as e:
        st.error(f"Erro ao atualizar dados na tabela {table}: {e}")
        return None

def delete_data(table, filters):
    try:
        query = supabase.table(table).delete()
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.execute()
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao deletar dados da tabela {table}: {e}")
        return False

# ====================================================================
# FUNÇÕES DE LIMITE DE CRÉDITO (NOVAS)
# ====================================================================

@st.cache_data(ttl=CACHE_TTL)
def get_limite_cliente(cliente_id):
    """Retorna o limite de crédito do cliente"""
    cliente = query_to_dict_cached("clientes", "limite_credito, bloqueado, motivo_bloqueio", {"id": cliente_id})
    if cliente:
        return {
            'limite': float(cliente.get('limite_credito', 999999.99)),
            'bloqueado': cliente.get('bloqueado', False),
            'motivo': cliente.get('motivo_bloqueio', '')
        }
    return {'limite': 999999.99, 'bloqueado': False, 'motivo': ''}

@st.cache_data(ttl=CACHE_TTL)
def verificar_pode_comprar(cliente_id, valor_produto):
    """Verifica se o cliente pode comprar baseado no limite"""
    info = get_limite_cliente(cliente_id)
    
    if info['bloqueado']:
        return {'pode': False, 'motivo': f"🚫 Cliente BLOQUEADO! Motivo: {info['motivo'] or 'Não informado'}"}
    
    saldo_atual = calcula_saldo(cliente_id)
    
    if saldo_atual + valor_produto > info['limite']:
        return {
            'pode': False, 
            'motivo': f"⚠️ Limite excedido! Saldo atual: {formata_moeda(saldo_atual)} + R$ {valor_produto:.2f} > Limite: {formata_moeda(info['limite'])}"
        }
    
    return {'pode': True, 'motivo': ''}

def atualizar_limite_cliente(cliente_id, limite, bloqueado=False, motivo_bloqueio=''):
    """Atualiza o limite de crédito do cliente"""
    dados = {
        'limite_credito': limite,
        'bloqueado': bloqueado,
        'motivo_bloqueio': motivo_bloqueio if bloqueado else None
    }
    return update_data("clientes", dados, {"id": cliente_id})

# ====================================================================
# FUNÇÕES DE CONSULTA OTIMIZADAS COM CACHE
# ====================================================================

@st.cache_data(ttl=CACHE_TTL)
def get_all_clientes():
    """Busca todos os clientes (cacheado)"""
    return query_to_list_cached("clientes", order={"column": "nome", "desc": False})

@st.cache_data(ttl=CACHE_TTL)
def get_all_produtos_nao_pagos():
    """Busca todos os produtos não pagos (cacheado) - INCLUI cliente_id"""
    return query_to_list_cached("produtos", "id, cliente_id, valor", {"pago": False})

@st.cache_data(ttl=CACHE_TTL)
def get_produtos_nao_pagos_cliente(cliente_id):
    """Busca produtos não pagos de um cliente (cacheado)"""
    return query_to_list_cached(
        "produtos", 
        "id, nome, valor, data_compra", 
        {"cliente_id": cliente_id, "pago": False}
    )

@st.cache_data(ttl=CACHE_TTL)
def get_all_produtos_padrao():
    """Busca todos os produtos padrão (cacheado)"""
    return query_to_list_cached("produtos_padrao", order={"column": "nome", "desc": False})

@st.cache_data(ttl=CACHE_TTL)
def get_saldos_todos_clientes():
    """Calcula saldo de todos os clientes em uma única query (cacheado)"""
    produtos = get_all_produtos_nao_pagos()
    saldos = {}
    
    if produtos and isinstance(produtos, list):
        for p in produtos:
            if isinstance(p, dict) and 'cliente_id' in p:
                cliente_id = p['cliente_id']
                saldos[cliente_id] = saldos.get(cliente_id, 0) + float(p.get('valor', 0))
    
    return saldos

@st.cache_data(ttl=CACHE_TTL)
def get_clientes_com_saldo():
    """Retorna clientes com saldo calculado (cacheado)"""
    clientes = get_all_clientes()
    saldos = get_saldos_todos_clientes()
    
    resultado = []
    if clientes and isinstance(clientes, list):
        for cliente in clientes:
            if isinstance(cliente, dict):
                cliente_id = cliente.get('id')
                saldo = saldos.get(cliente_id, 0.0) if cliente_id else 0.0
                resultado.append({
                    **cliente,
                    'saldo': saldo,
                    'qtd_produtos': 0
                })
    
    return resultado

@st.cache_data(ttl=CACHE_TTL)
def get_historico_pagamentos(cliente_id, limit=30):
    """Busca histórico de pagamentos (cacheado)"""
    if not cliente_id:
        return []
    return query_to_list_cached(
        "pagamentos",
        "valor, tipo, data_pagamento, descricao",
        {"cliente_id": cliente_id},
        {"column": "id", "desc": True}
    )[:limit]

@st.cache_data(ttl=CACHE_LONGO)
def get_produtos_padrao_simples():
    """Busca produtos padrão para dropdown (cache longo)"""
    return query_to_list_cached("produtos_padrao", "id, nome, valor", order={"column": "nome", "desc": False})

# ====================================================================
# FUNÇÕES DE EDIÇÃO
# ====================================================================
def editar_cliente(cliente_id, dados_atualizados):
    return update_data("clientes", dados_atualizados, {"id": cliente_id})

def editar_produto_padrao(produto_id, novo_nome):
    return update_data("produtos_padrao", {"nome": novo_nome}, {"id": produto_id})

# ====================================================================
# FUNÇÕES DE SINCRONIZAÇÃO
# ====================================================================
def exportar_dados_json():
    clientes = query_to_list_cached("clientes")
    produtos = query_to_list_cached("produtos")
    pagamentos = query_to_list_cached("pagamentos")
    produtos_padrao = query_to_list_cached("produtos_padrao")
    
    dados = {
        'clientes': clientes,
        'produtos': produtos,
        'pagamentos': pagamentos,
        'produtos_padrao': produtos_padrao,
        'data_exportacao': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'versao': '1.0'
    }
    return json.dumps(dados, default=str, ensure_ascii=False)

def importar_dados_json(json_data):
    try:
        dados = json.loads(json_data)
        
        if 'clientes' not in dados or not dados['clientes']:
            st.error("❌ Nenhum cliente encontrado no JSON")
            return 0
        
        st.info(f"📥 Iniciando importação de {len(dados['clientes'])} clientes e {len(dados.get('produtos', []))} produtos...")
        
        mapa_ids = {}
        total_clientes = 0
        
        for cliente in dados['clientes']:
            try:
                id_antigo = cliente.get('id')
                
                cliente_data = {
                    'nome': cliente.get('nome', ''),
                    'telefone': cliente.get('telefone', '0'),
                    'data_cadastro': cliente.get('data_cadastro'),
                    'modo_seguro': bool(cliente.get('modo_seguro', 0)),
                    'cpf': cliente.get('cpf'),
                    'rg': cliente.get('rg'),
                    'data_nascimento': cliente.get('data_nascimento'),
                    'email': cliente.get('email'),
                    'celular': cliente.get('celular'),
                    'logradouro': cliente.get('logradouro'),
                    'numero': cliente.get('numero'),
                    'complemento': cliente.get('complemento'),
                    'bairro': cliente.get('bairro'),
                    'cidade': cliente.get('cidade'),
                    'estado': cliente.get('estado'),
                    'cep': cliente.get('cep'),
                    'aceite_lgpd': bool(cliente.get('aceite_lgpd', 0)),
                    'data_aceite_lgpd': cliente.get('data_aceite_lgpd'),
                    'observacoes': cliente.get('observacoes'),
                    'limite_credito': cliente.get('limite_credito', 999999.99),
                    'bloqueado': bool(cliente.get('bloqueado', 0)),
                    'motivo_bloqueio': cliente.get('motivo_bloqueio')
                }
                
                cliente_data = {k: v for k, v in cliente_data.items() if v is not None}
                result = insert_data("clientes", cliente_data)
                
                if result:
                    mapa_ids[id_antigo] = result['id']
                    total_clientes += 1
                    st.success(f"✅ Cliente '{cliente.get('nome')}' (ID {id_antigo} -> {result['id']})")
                else:
                    st.warning(f"⚠️ Falha ao importar cliente '{cliente.get('nome')}'")
                    
            except Exception as e:
                st.warning(f"⚠️ Erro no cliente {cliente.get('nome', 'desconhecido')}: {e}")
        
        total_produtos = 0
        
        if 'produtos' in dados and dados['produtos']:
            for produto in dados['produtos']:
                try:
                    cliente_id_antigo = produto.get('cliente_id')
                    
                    if cliente_id_antigo in mapa_ids:
                        produto_data = {
                            'cliente_id': mapa_ids[cliente_id_antigo],
                            'nome': produto.get('nome', ''),
                            'valor': float(produto.get('valor', 0)),
                            'data_compra': produto.get('data_compra'),
                            'pago': bool(produto.get('pago', 0)),
                            'tipo_pagamento': produto.get('tipo_pagamento'),
                            'data_pagamento': produto.get('data_pagamento')
                        }
                        
                        produto_data = {k: v for k, v in produto_data.items() if v is not None}
                        result = insert_data("produtos", produto_data)
                        
                        if result:
                            total_produtos += 1
                    else:
                        st.warning(f"⚠️ Produto '{produto.get('nome')}' ignorado - Cliente ID {cliente_id_antigo} não encontrado")
                        
                except Exception as e:
                    st.warning(f"⚠️ Erro no produto '{produto.get('nome', 'desconhecido')}': {e}")
        
        if 'pagamentos' in dados and dados['pagamentos']:
            for pagamento in dados['pagamentos']:
                try:
                    cliente_id_antigo = pagamento.get('cliente_id')
                    
                    if cliente_id_antigo in mapa_ids:
                        pagamento_data = {
                            'cliente_id': mapa_ids[cliente_id_antigo],
                            'valor': float(pagamento.get('valor', 0)),
                            'tipo': pagamento.get('tipo', 'dinheiro'),
                            'data_pagamento': pagamento.get('data_pagamento'),
                            'descricao': pagamento.get('descricao', '')
                        }
                        
                        pagamento_data = {k: v for k, v in pagamento_data.items() if v is not None}
                        insert_data("pagamentos", pagamento_data)
                        
                except Exception as e:
                    st.warning(f"⚠️ Erro no pagamento: {e}")
        
        if 'produtos_padrao' in dados and dados['produtos_padrao']:
            for produto in dados['produtos_padrao']:
                try:
                    produto_data = {
                        'nome': produto.get('nome', ''),
                        'valor': float(produto.get('valor', 0)),
                        'data_cadastro': produto.get('data_cadastro')
                    }
                    
                    produto_data = {k: v for k, v in produto_data.items() if v is not None}
                    insert_data("produtos_padrao", produto_data)
                    
                except Exception as e:
                    st.warning(f"⚠️ Erro no produto padrão: {e}")
        
        st.cache_data.clear()
        
        st.success(f"""
        ✅ **IMPORTAÇÃO CONCLUÍDA!**
        
        - 👤 {total_clientes} clientes importados
        - 📦 {total_produtos} produtos importados
        - 🔗 {len(mapa_ids)} relacionamentos mantidos
        """)
        
        if total_clientes > 0:
            st.balloons()
        
        return total_clientes
        
    except json.JSONDecodeError as e:
        st.error(f"❌ Erro ao decodificar JSON: {e}")
        return 0
    except Exception as e:
        st.error(f"❌ Erro geral na importação: {e}")
        st.exception(e)
        return 0

# ====================================================================
# FUNÇÕES DE VALIDAÇÃO E FORMATAÇÃO
# ====================================================================
def valida_cpf(cpf):
    if not cpf:
        return False
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
    if not cpf:
        return "Não informado"
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf

def formata_moeda(valor):
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@st.cache_data(ttl=CACHE_TTL)
def calcula_saldo(cliente_id):
    saldos = get_saldos_todos_clientes()
    return saldos.get(cliente_id, 0.0)

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
# FUNÇÃO PARA GERAR COMPROVANTE (HTML)
# ====================================================================
def gerar_comprovante_html(cliente_id, produtos):
    cliente = query_to_dict_cached("clientes", filters={"id": cliente_id})
    if not cliente:
        return None
    
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
            <p><strong>Nome:</strong> {cliente.get('nome', '')}</p>
            <p><strong>CPF:</strong> {formata_cpf(cliente.get('cpf'))}</p>
            <p><strong>Telefone:</strong> {cliente.get('telefone') or 'Não informado'}</p>
    """
    
    if cliente.get('celular'):
        html += f"<p><strong>Celular:</strong> {cliente['celular']}</p>"
    
    if cliente.get('logradouro'):
        html += f"""
            <p><strong>Endereço:</strong> {cliente['logradouro']}, {cliente.get('numero', '')}</p>
            <p><strong>Bairro:</strong> {cliente.get('bairro', '')}, {cliente.get('cidade', '')} - {cliente.get('estado', '')}</p>
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
    for i, p in enumerate(produtos, 1):
        html += f"""
            <tr>
                <td>{i}</td>
                <td>{p.get('nome', '')}</td>
                <td>{formata_moeda(p.get('valor', 0))}</td>
                <td>{p.get('data_compra', '')}</td>
            </tr>
        """
        total += p.get('valor', 0)
    
    html += f"""
            </table>
            <div class="total">
                💰 TOTAL DA DÍVIDA: <span class="destaque">{formata_moeda(total)}</span>
            </div>
        </div>
        
        <div class="footer">
            <p>Este documento tem validade como comprovante de dívida para fins de cobrança judicial ou extrajudicial,<br>
            conforme previsto no Código Civil Brasileiro (Lei nº 10.406/2002).</p>
        </div>
    </body>
    </html>
    """
    return html

# ====================================================================
# FUNÇÃO PARA LIMPAR CACHE MANUALMENTE
# ====================================================================
def limpar_cache():
    st.cache_data.clear()
    st.cache_resource.clear()
    st.success("✅ Cache limpo com sucesso!")

# ====================================================================
# VERIFICAR LOGIN
# ====================================================================
if not verificar_login():
    tela_login()
    st.stop()

# ====================================================================
# SIDEBAR
# ====================================================================
st.sidebar.title("📋 Fichinha")
st.sidebar.success(f"👋 Olá, {st.session_state.usuario}!")
st.sidebar.markdown("---")

if st.sidebar.button("🚪 Sair", use_container_width=True):
    fazer_logout()

if st.sidebar.button("🔄 Limpar Cache", use_container_width=True):
    limpar_cache()
    st.rerun()

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
    
    clientes_list = get_all_clientes()
    produtos_nao_pagos = get_all_produtos_nao_pagos()
    clientes_seguro = query_to_list_cached("clientes", "id", {"modo_seguro": True})
    produtos_padrao = get_all_produtos_padrao()
    
    total_clientes = len(clientes_list)
    total_pendentes = len(produtos_nao_pagos)
    valor_aberto = sum(float(p.get('valor', 0)) for p in produtos_nao_pagos)
    total_seguro = len(clientes_seguro)
    total_padrao = len(produtos_padrao)
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👤 Clientes", total_clientes)
    c2.metric("📝 Pendentes", total_pendentes)
    c3.metric("💰 Em Aberto", formata_moeda(valor_aberto))
    c4.metric("🔒 Modo Seguro", total_seguro)
    
    col1, col2, col3 = st.columns(3)
    col2.metric("🏷️ Produtos Padrão", total_padrao)

# -------------------- CLIENTES --------------------
elif menu == "👤 Clientes":
    st.title("👤 Clientes")
    
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
                    cliente_data = {
                        'nome': nome,
                        'telefone': telefone,
                        'data_cadastro': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'modo_seguro': st.session_state.modo_seguro,
                        'cpf': cpf,
                        'rg': rg,
                        'data_nascimento': str(data_nasc) if data_nasc else None,
                        'email': email,
                        'celular': celular,
                        'logradouro': logradouro,
                        'numero': numero,
                        'complemento': complemento,
                        'bairro': bairro,
                        'cidade': cidade,
                        'estado': estado,
                        'cep': cep,
                        'aceite_lgpd': aceite_lgpd,
                        'data_aceite_lgpd': datetime.now().strftime("%Y-%m-%d %H:%M:%S") if aceite_lgpd else None,
                        'observacoes': observacoes,
                        'limite_credito': 999999.99,
                        'bloqueado': False,
                        'motivo_bloqueio': None
                    }
                    
                    result = insert_data("clientes", cliente_data)
                    if result:
                        st.session_state.form_data = {}
                        st.success(f"✅ Cliente cadastrado!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao cadastrar cliente")
    
    st.subheader("📋 Lista de Clientes")
    
    clientes_com_saldo = get_clientes_com_saldo()
    
    if clientes_com_saldo:
        df = pd.DataFrame(clientes_com_saldo)
        df['saldo_fmt'] = df['saldo'].apply(formata_moeda)
        df['modo'] = df['modo_seguro'].apply(lambda x: "🔒" if x else "📱")
        
        # Adicionar status baseado no limite e bloqueio
        def get_status(row):
            if row.get('bloqueado', False):
                return "🚫 BLOQUEADO"
            elif row.get('limite_credito', 999999.99) < 999999.99:
                return f"💳 Limite: {formata_moeda(row.get('limite_credito', 0))}"
            return "✅ Ativo"
        
        df['status'] = df.apply(get_status, axis=1)
        
        st.dataframe(
            df[['id', 'nome', 'telefone', 'saldo_fmt', 'status', 'modo']],
            column_config={
                "id": "ID", 
                "nome": "Nome", 
                "telefone": "Telefone", 
                "saldo_fmt": "Saldo",
                "status": "Status",
                "modo": ""
            },
            use_container_width=True
        )
        
        # ========== EDIÇÃO DE CLIENTE ==========
        st.divider()
        st.subheader("✏️ Editar Cliente")
        st.caption("Edite os dados do cliente sem precisar apagar e recriar")
        
        clientes = get_all_clientes()
        
        cliente_editar = st.selectbox(
            "Selecione o cliente para editar",
            [c['id'] for c in clientes],
            format_func=lambda x: next(c['nome'] for c in clientes if c['id'] == x),
            key="editar_cliente"
        )
        
        if cliente_editar:
            cliente_dados = query_to_dict_cached("clientes", filters={"id": cliente_editar})
            
            if cliente_dados:
                st.info(f"✏️ Editando: **{cliente_dados['nome']}**")
                
                with st.expander("📝 Editar Dados do Cliente", expanded=True):
                    with st.form("form_editar_cliente"):
                        col1, col2 = st.columns(2)
                        with col1:
                            nome_edit = st.text_input("Nome*", value=cliente_dados.get('nome') or '')
                            telefone_edit = st.text_input("Telefone*", value=cliente_dados.get('telefone') or '')
                            cpf_edit = st.text_input("CPF", max_chars=11, value=cliente_dados.get('cpf') or '')
                            rg_edit = st.text_input("RG", value=cliente_dados.get('rg') or '')
                        with col2:
                            data_nasc_edit = st.date_input(
                                "Data de Nascimento",
                                value=datetime.strptime(cliente_dados['data_nascimento'], "%Y-%m-%d").date() if cliente_dados.get('data_nascimento') else None
                            )
                            email_edit = st.text_input("Email", value=cliente_dados.get('email') or '')
                            celular_edit = st.text_input("Celular", value=cliente_dados.get('celular') or '')
                        
                        st.subheader("📍 Endereço")
                        col1, col2, col3 = st.columns([3, 1, 1])
                        with col1:
                            logradouro_edit = st.text_input("Logradouro", value=cliente_dados.get('logradouro') or '')
                        with col2:
                            numero_edit = st.text_input("Número", value=cliente_dados.get('numero') or '')
                        with col3:
                            complemento_edit = st.text_input("Complemento", value=cliente_dados.get('complemento') or '')
                        
                        col1, col2, col3 = st.columns([2, 2, 1])
                        with col1:
                            bairro_edit = st.text_input("Bairro", value=cliente_dados.get('bairro') or '')
                        with col2:
                            cidade_edit = st.text_input("Cidade", value=cliente_dados.get('cidade') or '')
                        with col3:
                            estado_edit = st.text_input("UF", max_chars=2, value=cliente_dados.get('estado') or '')
                        
                        cep_edit = st.text_input("CEP", max_chars=8, value=cliente_dados.get('cep') or '')
                        observacoes_edit = st.text_area("Observações", value=cliente_dados.get('observacoes') or '')
                        
                        # ========== NOVO: CONTROLE DE CRÉDITO ==========
                        st.divider()
                        st.subheader("💰 Controle de Crédito")
                        st.caption("Defina um limite de crédito para este cliente")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            limite_edit = st.number_input(
                                "Limite de Crédito (R$)",
                                min_value=0.00,
                                max_value=999999.99,
                                value=float(cliente_dados.get('limite_credito', 999999.99)),
                                step=50.00,
                                format="%.2f",
                                help="Valor máximo que o cliente pode dever. 999999.99 = sem limite"
                            )
                        
                        with col2:
                            bloqueado_edit = st.checkbox(
                                "🚫 Bloquear Cliente",
                                value=cliente_dados.get('bloqueado', False),
                                help="Impede o cliente de fazer novas compras"
                            )
                        
                        with col3:
                            if bloqueado_edit:
                                motivo_bloqueio_edit = st.text_input(
                                    "Motivo do Bloqueio",
                                    value=cliente_dados.get('motivo_bloqueio', ''),
                                    placeholder="Ex: Inadimplente, Cheque devolvido..."
                                )
                            else:
                                motivo_bloqueio_edit = ''
                        
                        st.warning("⚠️ **IMPORTANTE:** O valor financeiro (saldo, produtos, pagamentos) NÃO pode ser editado para evitar fraudes.")
                        
                        if st.form_submit_button("💾 Salvar Alterações", type="primary"):
                            erros = []
                            if not nome_edit:
                                erros.append("Nome obrigatório")
                            if not telefone_edit:
                                erros.append("Telefone obrigatório")
                            if cpf_edit and not valida_cpf(cpf_edit):
                                erros.append("CPF inválido")
                            
                            if erros:
                                for erro in erros:
                                    st.error(f"❌ {erro}")
                            else:
                                dados_atualizados = {
                                    'nome': nome_edit,
                                    'telefone': telefone_edit,
                                    'cpf': cpf_edit if cpf_edit else None,
                                    'rg': rg_edit if rg_edit else None,
                                    'data_nascimento': str(data_nasc_edit) if data_nasc_edit else None,
                                    'email': email_edit if email_edit else None,
                                    'celular': celular_edit if celular_edit else None,
                                    'logradouro': logradouro_edit if logradouro_edit else None,
                                    'numero': numero_edit if numero_edit else None,
                                    'complemento': complemento_edit if complemento_edit else None,
                                    'bairro': bairro_edit if bairro_edit else None,
                                    'cidade': cidade_edit if cidade_edit else None,
                                    'estado': estado_edit if estado_edit else None,
                                    'cep': cep_edit if cep_edit else None,
                                    'observacoes': observacoes_edit if observacoes_edit else None,
                                    'limite_credito': limite_edit,
                                    'bloqueado': bloqueado_edit,
                                    'motivo_bloqueio': motivo_bloqueio_edit if bloqueado_edit else None
                                }
                                
                                if editar_cliente(cliente_editar, dados_atualizados):
                                    st.success(f"✅ Cliente '{nome_edit}' atualizado com sucesso!")
                                    st.rerun()
                                else:
                                    st.error("❌ Erro ao atualizar cliente")
        
        # ========== EXCLUIR CLIENTE ==========
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
                [c['id'] for c in clientes],
                format_func=lambda x: next(c['nome'] for c in clientes if c['id'] == x)
            )
            
            if cliente_id:
                nome_cliente = next(c['nome'] for c in clientes if c['id'] == cliente_id)
                saldo = next(c.get('saldo', 0) for c in clientes_com_saldo if c['id'] == cliente_id)
                
                if saldo > 0:
                    st.warning(f"⚠️ Cliente tem saldo de {formata_moeda(saldo)} pendente!")
                
                confirmar = st.text_input(f"Digite o nome '{nome_cliente}' para confirmar:")
                
                if confirmar == nome_cliente:
                    if st.button("🗑️ EXCLUIR PERMANENTEMENTE", type="primary"):
                        delete_data("pagamentos", {"cliente_id": cliente_id})
                        delete_data("produtos", {"cliente_id": cliente_id})
                        delete_data("clientes", {"id": cliente_id})
                        st.success(f"✅ Cliente excluído!")
                        st.rerun()
    else:
        st.info("ℹ️ Nenhum cliente cadastrado.")

# -------------------- NOVA FICHINHA --------------------
elif menu == "📝 Nova Fichinha":
    st.title("📝 Nova Fichinha")
    
    clientes = get_all_clientes()
    
    if not clientes:
        st.warning("⚠️ Cadastre um cliente primeiro!")
    else:
        cliente_id = st.selectbox(
            "Cliente",
            [c['id'] for c in clientes],
            format_func=lambda x: f"{next(c['nome'] for c in clientes if c['id'] == x)} {'🔒' if next(c['modo_seguro'] for c in clientes if c['id'] == x) else ''}"
        )
        
        if cliente_id:
            # ========== VERIFICAR LIMITE E BLOQUEIO ==========
            info_limite = get_limite_cliente(cliente_id)
            
            if info_limite['bloqueado']:
                st.error(f"🚫 **CLIENTE BLOQUEADO!** Motivo: {info_limite['motivo'] or 'Não informado'}")
                st.warning("Este cliente não pode fazer novas compras!")
                st.stop()
            
            saldo = calcula_saldo(cliente_id)
            limite = info_limite['limite']
            
            # Mostrar informações de limite
            if limite < 999999.99:
                disponivel = limite - saldo
                st.info(f"💰 Saldo atual: {formata_moeda(saldo)} | 💳 Limite: {formata_moeda(limite)} | 📊 Disponível: {formata_moeda(disponivel)}")
                
                if saldo / limite > 0.8:
                    st.warning(f"⚠️ ATENÇÃO: Saldo atual ({formata_moeda(saldo)}) está próximo do limite ({formata_moeda(limite)})!")
                
                if disponivel <= 0:
                    st.error(f"❌ Limite esgotado! Saldo: {formata_moeda(saldo)} | Limite: {formata_moeda(limite)}")
                    st.stop()
            else:
                st.info(f"💰 Saldo atual: {formata_moeda(saldo)} | ♾️ Sem limite definido")
            
            if next(c['modo_seguro'] for c in clientes if c['id'] == cliente_id):
                st.warning("🔒 Cliente em Modo Seguro")
            
            with st.expander("🏷️ Gerenciar Produtos Padrão", expanded=False):
                st.caption("Cadastre produtos com preços fixos para agilizar o atendimento")
                
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
                            produto_data = {
                                'nome': nome_padrao,
                                'valor': valor_padrao,
                                'data_cadastro': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            result = insert_data("produtos_padrao", produto_data)
                            if result:
                                st.success(f"✅ Produto '{nome_padrao}' cadastrado com sucesso!")
                                st.rerun()
                            else:
                                st.error("❌ Erro ao cadastrar produto")
                
                produtos_padrao = get_all_produtos_padrao()
                
                if produtos_padrao:
                    df_padrao = pd.DataFrame(produtos_padrao)
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
                    
                    st.caption("🔒 Para excluir um produto padrão, autentique-se como gerente")
                    
                    if esta_autenticado():
                        produto_excluir = st.selectbox(
                            "Selecione o produto padrão para excluir",
                            [p['id'] for p in produtos_padrao],
                            format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - {formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}",
                            key="excluir_padrao"
                        )
                        
                        if produto_excluir:
                            nome_excluir = next(p['nome'] for p in produtos_padrao if p['id'] == produto_excluir)
                            confirmar = st.text_input(f"Digite '{nome_excluir}' para confirmar exclusão:", key="confirma_padrao")
                            
                            if confirmar == nome_excluir:
                                if st.button("🗑️ Excluir Produto Padrão", type="primary"):
                                    delete_data("produtos_padrao", {"id": produto_excluir})
                                    st.success(f"✅ Produto '{nome_excluir}' excluído!")
                                    st.rerun()
                    else:
                        st.info("🔐 Autentique-se na seção 'Excluir Cliente' para excluir produtos padrão")
                    
                    # ========== EDIÇÃO DE PRODUTO PADRÃO ==========
                    st.divider()
                    st.subheader("✏️ Editar Produto Padrão")
                    st.caption("⚠️ Apenas o NOME pode ser editado. O VALOR permanece o mesmo para evitar fraudes.")
                    
                    produto_editar = st.selectbox(
                        "Selecione o produto padrão para editar",
                        [p['id'] for p in produtos_padrao],
                        format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - {formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}",
                        key="editar_padrao"
                    )
                    
                    if produto_editar:
                        produto_dados = query_to_dict_cached("produtos_padrao", filters={"id": produto_editar})
                        
                        if produto_dados:
                            with st.form("form_editar_produto_padrao"):
                                col1, col2 = st.columns(2)
                                with col1:
                                    nome_edit_padrao = st.text_input("Novo Nome do Produto*", value=produto_dados['nome'])
                                with col2:
                                    st.text_input("Valor (NÃO EDITÁVEL)", value=formata_moeda(produto_dados['valor']), disabled=True)
                                
                                st.warning("🔒 **O valor não pode ser alterado** para manter o histórico financeiro consistente.")
                                
                                if st.form_submit_button("💾 Salvar Alterações", type="primary"):
                                    if not nome_edit_padrao:
                                        st.error("❌ Nome do produto obrigatório")
                                    else:
                                        if editar_produto_padrao(produto_editar, nome_edit_padrao):
                                            st.success(f"✅ Produto atualizado para '{nome_edit_padrao}'!")
                                            st.rerun()
                                        else:
                                            st.error("❌ Erro ao atualizar produto")
                else:
                    st.info("ℹ️ Nenhum produto padrão cadastrado.")
            
            st.divider()
            st.subheader("➕ Adicionar Produto à Fichinha")
            
            produtos_padrao = get_all_produtos_padrao()
            
            modo_adicao = st.radio(
                "Tipo de produto:",
                ["📦 Produto Padrão", "✏️ Valor Personalizado"],
                horizontal=True
            )
            
            if modo_adicao == "📦 Produto Padrão":
                if not produtos_padrao:
                    st.warning("⚠️ Nenhum produto padrão cadastrado.")
                else:
                    with st.form("form_produto_padrao_ficha", clear_on_submit=True):
                        produto_selecionado = st.selectbox(
                            "Selecione o produto",
                            [p['id'] for p in produtos_padrao],
                            format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - {formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}"
                        )
                        
                        if produto_selecionado:
                            nome_produto = next(p['nome'] for p in produtos_padrao if p['id'] == produto_selecionado)
                            valor_produto = next(p['valor'] for p in produtos_padrao if p['id'] == produto_selecionado)
                            
                            st.info(f"📦 Produto: **{nome_produto}** - Valor: {formata_moeda(valor_produto)}")
                            
                            if st.form_submit_button("✅ Adicionar à Fichinha"):
                                # Verificar limite antes de adicionar
                                verificacao = verificar_pode_comprar(cliente_id, valor_produto)
                                if not verificacao['pode']:
                                    st.error(f"❌ {verificacao['motivo']}")
                                else:
                                    produto_data = {
                                        'cliente_id': cliente_id,
                                        'nome': nome_produto,
                                        'valor': valor_produto,
                                        'data_compra': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        'pago': False
                                    }
                                    if insert_data("produtos", produto_data):
                                        st.success(f"✅ Produto '{nome_produto}' adicionado!")
                                        st.rerun()
            else:
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
                            # Verificar limite antes de adicionar
                            verificacao = verificar_pode_comprar(cliente_id, valor)
                            if not verificacao['pode']:
                                st.error(f"❌ {verificacao['motivo']}")
                            else:
                                produto_data = {
                                    'cliente_id': cliente_id,
                                    'nome': nome,
                                    'valor': valor,
                                    'data_compra': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    'pago': False
                                }
                                if insert_data("produtos", produto_data):
                                    st.success(f"✅ Produto '{nome}' adicionado com valor personalizado!")
                                    st.rerun()
            
            st.divider()
            st.subheader("📋 Fichinha Atual")
            
            produtos = get_produtos_nao_pagos_cliente(cliente_id)
            
            if produtos:
                df_produtos = pd.DataFrame(produtos)
                df_produtos['valor_fmt'] = df_produtos['valor'].apply(formata_moeda)
                st.dataframe(df_produtos[['nome', 'valor_fmt', 'data_compra']], use_container_width=True)
                st.metric("💰 Total da Fichinha", formata_moeda(df_produtos['valor'].sum()))
                
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
                        [p['id'] for p in produtos],
                        format_func=lambda x: f"{next(p['nome'] for p in produtos if p['id'] == x)} - {formata_moeda(next(p['valor'] for p in produtos if p['id'] == x))}"
                    )
                    
                    if produto_id:
                        nome_produto = next(p['nome'] for p in produtos if p['id'] == produto_id)
                        valor_produto = next(p['valor'] for p in produtos if p['id'] == produto_id)
                        
                        st.warning(f"⚠️ Você está prestes a excluir: **{nome_produto}** ({formata_moeda(valor_produto)})")
                        
                        confirmar = st.text_input(
                            f"Digite o nome do produto para confirmar:",
                            placeholder="Digite o nome exato do produto"
                        )
                        
                        if confirmar == nome_produto:
                            if st.button("🗑️ EXCLUIR PRODUTO", type="primary", use_container_width=True):
                                delete_data("produtos", {"id": produto_id})
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
    
    clientes = get_all_clientes()
    
    if not clientes:
        st.warning("⚠️ Cadastre um cliente primeiro!")
    else:
        cliente_id = st.selectbox(
            "Cliente",
            [c['id'] for c in clientes],
            format_func=lambda x: next(c['nome'] for c in clientes if c['id'] == x)
        )
        
        if cliente_id:
            saldo = calcula_saldo(cliente_id)
            st.info(f"💰 Saldo atual: {formata_moeda(saldo)}")
            
            if saldo <= 0:
                st.success("✅ Cliente não possui débitos!")
            else:
                produtos = get_produtos_nao_pagos_cliente(cliente_id)
                
                if produtos:
                    df_produtos = pd.DataFrame(produtos)
                    df_produtos['valor_fmt'] = df_produtos['valor'].apply(formata_moeda)
                    st.subheader("📋 Produtos em Aberto")
                    st.dataframe(df_produtos[['nome', 'valor_fmt']], use_container_width=True)
                    st.metric("💲 Total", formata_moeda(df_produtos['valor'].sum()))
                    
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
                                valor_restante = valor
                                for row in produtos:
                                    if valor_restante <= 0:
                                        break
                                    
                                    if row['valor'] <= valor_restante:
                                        update_data("produtos", 
                                            {"pago": True, "tipo_pagamento": tipo, "data_pagamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                            {"id": row['id']}
                                        )
                                        valor_restante -= row['valor']
                                    else:
                                        resto = row['valor'] - valor_restante
                                        update_data("produtos",
                                            {"pago": True, "tipo_pagamento": tipo, "data_pagamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                            {"id": row['id']}
                                        )
                                        insert_data("produtos", {
                                            "cliente_id": cliente_id,
                                            "nome": f"{row['nome']} (restante)",
                                            "valor": round(resto, 2),
                                            "data_compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "pago": False
                                        })
                                        valor_restante = 0
                                
                                insert_data("pagamentos", {
                                    "cliente_id": cliente_id,
                                    "valor": valor,
                                    "tipo": tipo,
                                    "data_pagamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "descricao": descricao
                                })
                                
                                novo_saldo = calcula_saldo(cliente_id)
                                
                                st.success(f"✅ Pagamento de {formata_moeda(valor)} registrado!")
                                st.info(f"💰 Novo saldo: {formata_moeda(novo_saldo)}")
                                if novo_saldo == 0:
                                    st.balloons()
                                st.rerun()
            
            st.subheader("📋 Histórico de Pagamentos")
            historico = get_historico_pagamentos(cliente_id)
            
            if historico:
                df_historico = pd.DataFrame(historico)
                df_historico['valor_fmt'] = df_historico['valor'].apply(formata_moeda)
                df_historico['tipo'] = df_historico['tipo'].apply(
                    lambda x: {"dinheiro": "💵", "cartao": "💳", "pix": "📱"}.get(x, x)
                )
                st.dataframe(df_historico[['valor_fmt', 'tipo', 'data_pagamento', 'descricao']].head(30), use_container_width=True)

# -------------------- RELATÓRIOS --------------------
elif menu == "📊 Relatórios":
    st.title("📊 Relatórios")
    
    tab1, tab2, tab3, tab4 = st.tabs(["📈 Devedores", "🏷️ Produtos Padrão", "📤 Exportar", "🔄 Sincronizar"])
    
    with tab1:
        devedores = []
        clientes_all = get_all_clientes()
        
        for cliente in clientes_all:
            produtos_pendentes = get_produtos_nao_pagos_cliente(cliente['id'])
            if produtos_pendentes:
                total = sum(float(p.get('valor', 0)) for p in produtos_pendentes)
                devedores.append({
                    'nome': cliente['nome'],
                    'telefone': cliente.get('telefone', ''),
                    'modo_seguro': cliente.get('modo_seguro', False),
                    'total': total,
                    'qtd': len(produtos_pendentes)
                })
        
        if devedores:
            df = pd.DataFrame(devedores)
            df = df.sort_values('total', ascending=False)
            df['total_fmt'] = df['total'].apply(formata_moeda)
            df['modo'] = df['modo_seguro'].apply(lambda x: "🔒" if x else "📱")
            st.dataframe(df[['nome', 'telefone', 'modo', 'qtd', 'total_fmt']], use_container_width=True)
            
            st.subheader("📊 Gráfico")
            st.bar_chart(df.set_index('nome')[['total']])
        else:
            st.info("ℹ️ Nenhum devedor!")
    
    with tab2:
        st.subheader("🏷️ Produtos Padrão Cadastrados")
        produtos_padrao = get_all_produtos_padrao()
        
        if produtos_padrao:
            df = pd.DataFrame(produtos_padrao)
            df['valor_fmt'] = df['valor'].apply(formata_moeda)
            st.dataframe(
                df[['nome', 'valor_fmt', 'data_cadastro']],
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
        
        clientes_df = pd.DataFrame(get_all_clientes())
        produtos_df = pd.DataFrame(query_to_list_cached("produtos"))
        pagamentos_df = pd.DataFrame(query_to_list_cached("pagamentos"))
        pp_df = pd.DataFrame(get_all_produtos_padrao())
        
        col1, col2 = st.columns(2)
        with col1:
            if not clientes_df.empty:
                st.download_button("📥 Clientes", clientes_df.to_csv(index=False).encode(), "clientes.csv", "text/csv")
            if not produtos_df.empty:
                st.download_button("📥 Produtos", produtos_df.to_csv(index=False).encode(), "produtos.csv", "text/csv")
        with col2:
            if not pagamentos_df.empty:
                st.download_button("📥 Pagamentos", pagamentos_df.to_csv(index=False).encode(), "pagamentos.csv", "text/csv")
            if not pp_df.empty:
                st.download_button("📥 Produtos Padrão", pp_df.to_csv(index=False).encode(), "produtos_padrao.csv", "text/csv")
    
    with tab4:
        st.subheader("🔄 Sincronizar com Outra Versão")
        
        st.info("""
        **Como funciona:**
        1. Exporte os dados de uma versão (nuvem ou local)
        2. Importe na outra versão
        3. Os dados ficam iguais nos dois lugares
        """)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**📤 Exportar Dados (deste app)**")
            if st.button("📥 Exportar JSON", use_container_width=True):
                dados_json = exportar_dados_json()
                b64 = base64.b64encode(dados_json.encode()).decode()
                href = f'<a href="data:application/json;base64,{b64}" download="backup_fichinha_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json">📥 Baixar Backup</a>'
                st.markdown(href, unsafe_allow_html=True)
                st.success("✅ Dados exportados com sucesso!")
        
        with col2:
            st.markdown("**📥 Importar Dados (para este app)**")
            arquivo = st.file_uploader("Escolha o arquivo JSON", type=['json'])
            
            if arquivo and st.button("📥 Importar Dados", use_container_width=True):
                try:
                    dados_json = arquivo.read().decode('utf-8')
                    total = importar_dados_json(dados_json)
                    st.success(f"✅ {total} clientes importados com sucesso!")
                    st.balloons()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Erro ao importar: {e}")

# ====================================================================
# RODAPÉ
# ====================================================================
st.sidebar.markdown("---")
st.sidebar.caption("☁️ Dados salvos no Supabase")
st.sidebar.caption(f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}")