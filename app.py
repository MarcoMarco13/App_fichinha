"""
====================================================================
CONTROLE DE FICHINHA - Versão Segura Refatorada (Arquivo Único)
+ Integração WhatsApp (wa.me) — envio MANUAL, sem automação
====================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import re
import json
import base64
import time
import bcrypt
import streamlit.components.v1 as components
from supabase import create_client, Client
from urllib.parse import quote  # <-- ADICIONADO: para URL-encode da mensagem

# Configuração da Página
st.set_page_config(page_title="Controle de Fichinha", page_icon="📋", layout="wide")

CACHE_TTL = 60
CACHE_LONGO = 300

# ====================================================================
# =============== CONEXÃO SUPABASE ===================================
# ====================================================================

@st.cache_resource(ttl=3600)
def get_supabase() -> Client:
    """Cliente Supabase (singleton) — credenciais via secrets."""
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"]
    )

def _sb() -> Client:
    return get_supabase()

# ====================================================================
# =============== SEGURANÇA E SANITIZAÇÃO ============================
# ====================================================================

def verificar_senha(senha_digitada: str, hash_armazenado: str) -> bool:
    """Compara senha com hash bcrypt (tempo constante)."""
    if not senha_digitada or not hash_armazenado:
        return False
    try:
        return bcrypt.checkpw(
            senha_digitada.encode('utf-8'),
            hash_armazenado.encode('utf-8')
        )
    except (ValueError, TypeError):
        return False


def sanitizar_texto(texto: str, max_len: int = 500) -> str:
    """Remove caracteres de controlo e limita o tamanho do texto."""
    if not texto or not isinstance(texto, str):
        return ""
    texto = ''.join(c for c in texto if c.isprintable() or c in '\n\t')
    return texto.strip()[:max_len]


def validar_telefone(tel: str) -> bool:
    if not tel:
        return False
    d = ''.join(c for c in tel if c.isdigit())
    return len(d) in (10, 11)


def valida_cpf(cpf: str) -> bool:
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


def formata_cpf(cpf) -> str:
    """Formata CPF. Aceita None, float/NaN, int ou str (tolerante a dados do Pandas)."""
    if cpf is None:
        return "Não informado"
    try:
        if isinstance(cpf, float) and pd.isna(cpf):
            return "Não informado"
    except Exception:
        pass
    cpf_str = str(cpf).strip()
    if not cpf_str or cpf_str.lower() in ("nan", "none", "null", "<na>"):
        return "Não informado"
    if cpf_str.endswith(".0"):
        cpf_str = cpf_str[:-2]
    cpf_digits = re.sub(r'[^0-9]', '', cpf_str)
    if len(cpf_digits) == 11:
        return f"{cpf_digits[:3]}.{cpf_digits[3:6]}.{cpf_digits[6:9]}-{cpf_digits[9:]}"
    if not cpf_digits:
        return "Não informado"
    return cpf_digits


def mascarar_cpf(cpf) -> str:
    """Mascara CPF para exibição (LGPD). Aceita None, float/NaN, int ou str."""
    if cpf is None:
        return "***.***.***-**"
    try:
        if isinstance(cpf, float) and pd.isna(cpf):
            return "***.***.***-**"
    except Exception:
        pass
    cpf_str = str(cpf).strip()
    if not cpf_str or cpf_str.lower() in ("nan", "none", "null", "<na>"):
        return "***.***.***-**"
    if cpf_str.endswith(".0"):
        cpf_str = cpf_str[:-2]
    cpf_digits = re.sub(r'[^0-9]', '', cpf_str)
    if len(cpf_digits) == 11:
        return f"***.***.{cpf_digits[6:9]}-**"
    return "***.***.***-**"

def formata_moeda(valor: float) -> str:
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class RateLimiter:
    """Rate limiter em memória por sessão de utilizador."""
    def __init__(self, max_tentativas: int, janela_minutos: int):
        self.max_tentativas = max_tentativas
        self.janela_seg = janela_minutos * 60

    def _key(self, ident: str) -> str:
        return f"_rl_{ident}"

    def _tentativas(self, ident: str) -> list:
        agora = time.time()
        t = st.session_state.get(self._key(ident), [])
        t = [x for x in t if agora - x < self.janela_seg]
        st.session_state[self._key(ident)] = t
        return t

    def pode_tentar(self, ident: str):
        t = self._tentativas(ident)
        if len(t) >= self.max_tentativas:
            restante = int(self.janela_seg - (time.time() - t[0]))
            return False, max(0, restante)
        return True, 0

    def registrar(self, ident: str) -> None:
        t = st.session_state.get(self._key(ident), [])
        t.append(time.time())
        st.session_state[self._key(ident)] = t

    def limpar(self, ident: str) -> None:
        st.session_state.pop(self._key(ident), None)

# ====================================================================
# =============== AUDITORIA ==========================================
# ====================================================================

def log_auditoria(acao: str, detalhes: dict) -> None:
    """Regista ação na tabela de auditoria (falha silenciosa)."""
    try:
        _sb().table("auditoria").insert({
            "usuario": st.session_state.get('usuario', 'desconhecido'),
            "acao": acao,
            "detalhes": json.dumps(detalhes, default=str, ensure_ascii=False),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}")

# ====================================================================
# =============== ESTADO DA SESSÃO E AUTENTICAÇÃO ====================
# ====================================================================

def init_session_state():
    defaults = {
        'form_data': {},
        'modo_seguro': False,
        'autenticado': False,
        'tempo_autenticacao': None,
        'logado': False,
        'usuario': None,
        'role': None,
        'last_activity': None,
        'session_timeout': None,
        'cache_timestamp': None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session_state()


def verificar_login() -> bool:
    """Verifica se a sessão está válida (com timeout)."""
    if not st.session_state.get('logado', False):
        return False
    agora = time.time()
    last = st.session_state.get('last_activity', 0) or 0
    timeout = st.session_state.get('session_timeout', 3600)
    if agora - last > timeout:
        fazer_logout()
        return False
    st.session_state.last_activity = agora
    return True


def fazer_login(usuario: str, senha: str):
    """Tenta autenticar. Retorna (sucesso, mensagem)."""
    usuario = (usuario or "").strip().lower()
    if not usuario or not senha:
        return False, "❌ Informe utilizador e senha."

    limiter = RateLimiter(
        max_tentativas=st.secrets.get("MAX_TENTATIVAS_LOGIN", 5),
        janela_minutos=st.secrets.get("BLOQUEIO_MINUTOS", 15)
    )

    pode, restante = limiter.pode_tentar(usuario)
    if not pode:
        return False, f"⛔ Muitas tentativas. Tente em {restante}s."

    hashes = st.secrets.get("usuarios", {})
    hash_arm = hashes.get(usuario)

    senha_ok = verificar_senha(senha, hash_arm) if hash_arm else False

    if not senha_ok:
        limiter.registrar(usuario)
        return False, "❌ Utilizador ou senha inválidos."

    limiter.limpar(usuario)
    role = "gerente" if usuario in ("admin", "gerente") else "caixa"
    st.session_state.logado = True
    st.session_state.usuario = usuario
    st.session_state.role = role
    st.session_state.last_activity = time.time()
    st.session_state.session_timeout = st.secrets.get("SESSION_TIMEOUT_MINUTOS", 60) * 60
    return True, f"✅ Bem-vindo, {usuario}!"


def fazer_logout():
    for k in ['logado', 'usuario', 'role', 'last_activity',
              'session_timeout', 'autenticado', 'tempo_autenticacao']:
        st.session_state.pop(k, None)
    st.cache_data.clear()
    st.rerun()


def tela_login():
    st.title("🔐 Controle de Fichinha")
    st.markdown("---")
    st.subheader("Faça login para aceder ao sistema")

    with st.form("form_login"):
        usuario = st.text_input("Utilizador", max_chars=50)
        senha = st.text_input("Senha", type="password", max_chars=200)

        if st.form_submit_button("Entrar", use_container_width=True):
            ok, msg = fazer_login(usuario, senha)
            if ok:
                st.success(msg)
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(msg)

    st.markdown("---")
    st.caption("🔒 Sistema protegido | Acesso restrito | Tentativas limitadas")


def autentica(senha: str) -> bool:
    """Autentica gerente para ações críticas."""
    hashes = st.secrets.get("usuarios", {})
    hash_g = hashes.get("gerente")
    if hash_g and verificar_senha(senha, hash_g):
        st.session_state.autenticado = True
        st.session_state.tempo_autenticacao = datetime.now()
        return True
    return False


def logout():
    """Desautentica gerente (mantém o login principal)."""
    st.session_state.autenticado = False
    st.session_state.tempo_autenticacao = None


def esta_autenticado() -> bool:
    """Verifica se o gerente está autenticado (timeout configurável)."""
    if st.session_state.get('autenticado') and st.session_state.get('tempo_autenticacao'):
        timeout_min = st.secrets.get("GERENTE_TIMEOUT_MINUTOS", 30)
        elapsed = (datetime.now() - st.session_state.tempo_autenticacao).total_seconds()
        if elapsed > timeout_min * 60:
            logout()
            return False
        return True
    return False


def render_autenticacao_gerente(key_suffix: str = "") -> bool:
    """Helper reutilizável para autenticação de gerente na interface."""
    if esta_autenticado():
        st.success(f"🔓 Autenticado como gerente (Válido por {st.secrets.get('GERENTE_TIMEOUT_MINUTOS', 30)} min)")
        if st.button("🚪 Desautenticar", key=f"btn_logout_gerente_{key_suffix}"):
            logout()
            st.rerun()
        return True

    with st.expander("🔐 Autenticação de Gerente Requerida", expanded=True):
        senha = st.text_input("Senha do gerente:", type="password", key=f"senha_gerente_{key_suffix}")
        if st.button("🔓 Autenticar", key=f"btn_login_gerente_{key_suffix}"):
            if autentica(senha):
                st.success("✅ Autenticado com sucesso!")
                st.rerun()
            else:
                st.error("❌ Senha incorreta!")
    return False

# ====================================================================
# =============== BANCO DE DADOS =====================================
# ====================================================================

@st.cache_data(ttl=CACHE_TTL)
def query_to_list_cached(table, columns="*", filters=None, order=None):
    try:
        query = _sb().table(table).select(columns)
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
        print(f"[DB ERROR] query_to_list({table}): {e}")
        return []


@st.cache_data(ttl=CACHE_TTL)
def query_to_dict_cached(table, columns="*", filters=None):
    results = query_to_list_cached(table, columns, filters)
    return results[0] if results else None


def query_to_list(table, columns="*", filters=None, order=None):
    return query_to_list_cached(table, columns, filters, order)


def query_to_dict(table, columns="*", filters=None):
    return query_to_dict_cached(table, columns, filters)


def insert_data(table, data):
    try:
        response = _sb().table(table).insert(data).execute()
        if response.data:
            st.cache_data.clear()
            return response.data[0]
        return None
    except Exception as e:
        print(f"[DB ERROR] insert_data({table}): {e}")
        return None


def update_data(table, data, filters):
    try:
        query = _sb().table(table).update(data)
        for column, value in filters.items():
            query = query.eq(column, value)
        response = query.execute()
        if response.data:
            st.cache_data.clear()
            return response.data[0]
        return None
    except Exception as e:
        print(f"[DB ERROR] update_data({table}): {e}")
        return None


def delete_data(table, filters):
    try:
        query = _sb().table(table).delete()
        for column, value in filters.items():
            query = query.eq(column, value)
        query.execute()
        st.cache_data.clear()
        return True
    except Exception as e:
        print(f"[DB ERROR] delete_data({table}): {e}")
        return False

# ====================================================================
# =============== REGRAS DE NEGÓCIO E CONSULTAS ======================
# ====================================================================

@st.cache_data(ttl=CACHE_TTL)
def get_limite_cliente(cliente_id):
    cliente = query_to_dict_cached("clientes", "limite_credito, bloqueado, motivo_bloqueio", {"id": cliente_id})
    if cliente:
        return {
            'limite': float(cliente.get('limite_credito', 999999.99)),
            'bloqueado': cliente.get('bloqueado', False),
            'motivo': cliente.get('motivo_bloqueio', '') or ''
        }
    return {'limite': 999999.99, 'bloqueado': False, 'motivo': ''}


def verificar_pode_comprar(cliente_id, valor_produto):
    info = get_limite_cliente(cliente_id)
    if info['bloqueado']:
        return {'pode': False,
                'motivo': f"🚫 Cliente BLOQUEADO! Motivo: {info['motivo'] or 'Não informado'}"}
    saldo_atual = calcula_saldo(cliente_id)
    if saldo_atual + valor_produto > info['limite']:
        return {
            'pode': False,
            'motivo': (f"⚠️ Limite excedido! Saldo atual: {formata_moeda(saldo_atual)} + "
                       f"R$ {valor_produto:.2f} > Limite: {formata_moeda(info['limite'])}")
        }
    return {'pode': True, 'motivo': ''}


def atualizar_limite_cliente(cliente_id, limite, bloqueado=False, motivo_bloqueio=''):
    dados = {
        'limite_credito': limite,
        'bloqueado': bloqueado,
        'motivo_bloqueio': motivo_bloqueio if bloqueado else None
    }
    return update_data("clientes", dados, {"id": cliente_id})


@st.cache_data(ttl=CACHE_TTL)
def get_all_clientes():
    return query_to_list_cached("clientes", order={"column": "nome", "desc": False})


@st.cache_data(ttl=CACHE_TTL)
def get_all_produtos_nao_pagos():
    return query_to_list_cached("produtos", "id, cliente_id, valor", {"pago": False})


@st.cache_data(ttl=CACHE_TTL)
def get_produtos_nao_pagos_cliente(cliente_id):
    return query_to_list_cached(
        "produtos",
        "id, nome, valor, data_compra",
        {"cliente_id": cliente_id, "pago": False}
    )


@st.cache_data(ttl=CACHE_TTL)
def get_all_produtos_padrao():
    return query_to_list_cached("produtos_padrao", order={"column": "nome", "desc": False})


@st.cache_data(ttl=CACHE_TTL)
def get_saldos_todos_clientes():
    produtos = get_all_produtos_nao_pagos()
    saldos = {}
    if produtos and isinstance(produtos, list):
        for p in produtos:
            if isinstance(p, dict) and 'cliente_id' in p:
                cid = p['cliente_id']
                saldos[cid] = saldos.get(cid, 0) + float(p.get('valor', 0))
    return saldos


@st.cache_data(ttl=CACHE_TTL)
def get_clientes_com_saldo():
    clientes = get_all_clientes()
    saldos = get_saldos_todos_clientes()
    resultado = []
    if clientes and isinstance(clientes, list):
        for cliente in clientes:
            if isinstance(cliente, dict):
                cid = cliente.get('id')
                saldo = saldos.get(cid, 0.0) if cid else 0.0
                resultado.append({**cliente, 'saldo': saldo, 'qtd_produtos': 0})
    return resultado


@st.cache_data(ttl=CACHE_TTL)
def get_historico_pagamentos(cliente_id, limit=30):
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
    return query_to_list_cached("produtos_padrao", "id, nome, valor",
                                order={"column": "nome", "desc": False})


def editar_cliente(cliente_id, dados_atualizados):
    return update_data("clientes", dados_atualizados, {"id": cliente_id})


def editar_produto_padrao(produto_id, novo_nome):
    return update_data("produtos_padrao", {"nome": novo_nome}, {"id": produto_id})


@st.cache_data(ttl=CACHE_TTL)
def calcula_saldo(cliente_id):
    saldos = get_saldos_todos_clientes()
    return saldos.get(cliente_id, 0.0)

# ====================================================================
# =============== JSON IMPORTAR / EXPORTAR (MANTIDO INTACTO) =========
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

        st.info(f"📥 Iniciando importação de {len(dados['clientes'])} clientes e "
                f"{len(dados.get('produtos', []))} produtos...")

        mapa_ids = {}
        total_clientes = 0

        for cliente in dados['clientes']:
            try:
                id_antigo = cliente.get('id')
                cliente_data = {
                    'nome': sanitizar_texto(cliente.get('nome', ''), 200),
                    'telefone': sanitizar_texto(cliente.get('telefone', '0'), 20),
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
                        if insert_data("produtos", produto_data):
                            total_produtos += 1
                    else:
                        st.warning(f"⚠️ Produto '{produto.get('nome')}' ignorado - Cliente ID {cliente_id_antigo} não encontrado")
                except Exception as e:
                    st.warning(f"⚠️ Erro no produto '{produto.get('nome', 'desconhecido')}': {e}")

        if 'pagamentos' in dados and dados['pagamentos']:
            for pagamento in dados['pagamentos']:
                try:
                    cid_antigo = pagamento.get('cliente_id')
                    if cid_antigo in mapa_ids:
                        pagamento_data = {
                            'cliente_id': mapa_ids[cid_antigo],
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
        return 0

# ====================================================================
# =============== GERADOR DE COMPROVANTE (HTML) ======================
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
# =============== IMPRESSÃO TÉRMICA — COMPROVANTE DE DÍVIDA ==========
# ====================================================================
# Formato compatível com impressoras térmicas 58mm/80mm.
# O navegador abrirá automaticamente o diálogo de impressão.
# ====================================================================

def gerar_texto_comprovante_impressao(cliente_id) -> str:
    """
    Gera o texto do comprovante no formato exigido para validação
    em processos criminais (confissão de dívida + assinatura).

    Retorna string pronta para impressão em impressora térmica.
    """
    cliente = query_to_dict_cached("clientes", filters={"id": cliente_id})
    if not cliente:
        return ""

    produtos = get_produtos_nao_pagos_cliente(cliente_id) or []
    total = sum(float(p.get('valor', 0)) for p in produtos)

    linhas = []
    linhas.append("================================")
    linhas.append("        CAFÉ HAUS               ")
    linhas.append("================================")
    linhas.append(f"Data: {datetime.now().strftime('%d/%m/%Y')}")
    linhas.append(f"Cliente: {cliente.get('nome', '')}")
    linhas.append(f"CPF: {formata_cpf(cliente.get('cpf'))}")
    linhas.append("")
    linhas.append("ITENS DA COMPRA:")
    linhas.append("")
    for p in produtos:
        nome = str(p.get('nome', ''))[:26]
        valor = formata_moeda(p.get('valor', 0))
        linhas.append(f"{nome:<26}{valor:>10}")
    linhas.append("--------------------------------")
    linhas.append(f"VALOR TOTAL: {formata_moeda(total)}")
    linhas.append("================================")
    linhas.append("DECLARAÇÃO DE DÍVIDA:")
    linhas.append("Reconheço e confesso a dívida")
    linhas.append("acima descrita, comprometendo-")
    linhas.append("me a quitá-la até o vencimento")
    linhas.append("(dia 10 do próximo mês)")
    linhas.append("")
    linhas.append("________________________________")
    linhas.append("       Assinatura do Cliente")
    linhas.append("================================")

    return "\n".join(linhas)


def imprimir_comprovante_cliente(cliente_id, auto_print: bool = True) -> None:
    """
    Renderiza o comprovante em HTML e dispara automaticamente o diálogo
    de impressão do navegador (Ctrl+P) — compatível com impressoras
    térmicas 58mm e 80mm.

    Se auto_print=False, apenas pré-visualiza sem imprimir.
    """
    texto = gerar_texto_comprovante_impressao(cliente_id)
    if not texto:
        st.error("❌ Cliente não encontrado para impressão.")
        return

    texto_escapado = (
        texto.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
    )

    auto_script = (
        "<script>window.onload=function(){setTimeout(function(){window.print();},350);};</script>"
        if auto_print else ""
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Comprovante de Dívida</title>
        <style>
            @page {{ size: 80mm auto; margin: 4mm; }}
            html, body {{
                font-family: 'Courier New', Courier, monospace;
                font-size: 12px;
                line-height: 1.35;
                color: #000;
                background: #fff;
                margin: 0;
                padding: 8px;
            }}
            pre {{
                margin: 0;
                white-space: pre;
                font-family: 'Courier New', Courier, monospace;
                font-size: 12px;
            }}
            @media print {{
                body {{ padding: 0; }}
            }}
        </style>
    </head>
    <body>
        <pre>{texto_escapado}</pre>
        {auto_script}
    </body>
    </html>
    """

    components.html(html, height=650, scrolling=True)


# ====================================================================
# =============== INTEGRAÇÃO WHATSAPP — ENVIO MANUAL =================
# ====================================================================
# Deep link (whatsapp://) → abre o APP diretamente, SEM página
# intermediária. Fallback wa.me para quando o app não está instalado.
# NÃO há envio automático: o utilizador revê e envia manualmente.
# ====================================================================

def formatar_telefone_whatsapp(telefone) -> str:
    """
    Normaliza o telefone para o formato aceito pelo wa.me / whatsapp://
    (padrão E.164 sem o '+'): 55 + DDD + número.
    """
    if telefone is None:
        return ""
    try:
        if isinstance(telefone, float) and pd.isna(telefone):
            return ""
    except Exception:
        pass

    tel_str = str(telefone).strip()
    if not tel_str or tel_str.lower() in ("nan", "none", "null", "<na>"):
        return ""

    if tel_str.endswith(".0"):
        tel_str = tel_str[:-2]

    digitos = re.sub(r'[^0-9]', '', tel_str)
    if not digitos:
        return ""

    digitos = digitos.lstrip('0')
    if not digitos:
        return ""

    if digitos.startswith('55') and len(digitos) in (12, 13):
        return digitos

    if len(digitos) in (10, 11):
        return f"55{digitos}"

    if len(digitos) in (12, 13):
        return f"55{digitos}"

    return digitos


def obter_telefone_cliente(cliente: dict) -> str:
    """Retorna o melhor telefone do cliente (celular > telefone)."""
    if not cliente:
        return ""
    for campo in ('celular', 'telefone'):
        valor = cliente.get(campo)
        if valor:
            formatado = formatar_telefone_whatsapp(valor)
            if formatado:
                return formatado
    return ""


def gerar_mensagem_cobranca(nome_cliente: str, valor_total: float) -> str:
    """Monta a mensagem padrão de cobrança."""
    nome_limpo = sanitizar_texto(nome_cliente or "", 100).strip() or "cliente"
    partes = nome_limpo.split()
    primeiro_nome = partes[0] if partes else nome_limpo
    valor_fmt = formata_moeda(valor_total)

    return (
        f"Olá {primeiro_nome}, tudo bem? Estou passando para lembrar que você "
        f"possui um valor de {valor_fmt} em aberto na sua fichinha. Quando puder "
        f"dar uma olhada, me avise por aqui! Obrigado."
    )


def gerar_links_whatsapp_cliente(cliente_id) -> tuple:
    """
    Gera os links de cobrança para o cliente.

    Retorna (web_direct, deep_link, web_fallback, erro).

    Ordem de prioridade:
    1. web_direct:  'https://web.whatsapp.com/send?phone=...&text=...'
       → abre o WhatsApp Web DIRETAMENTE no chat, sem página intermediária.
    2. deep_link:   'whatsapp://send?phone=...&text=...'
       → abre o APP instalado (celular ou WhatsApp Desktop).
    3. web_fallback: 'https://wa.me/...'
       → fallback universal (mostra página intermediária, mas funciona sempre).
    """
    try:
        if not cliente_id:
            return "", "", "", "Cliente inválido."

        cliente = query_to_dict_cached("clientes", filters={"id": cliente_id})
        if not cliente:
            return "", "", "", "Cliente não encontrado no banco de dados."

        telefone = obter_telefone_cliente(cliente)
        if not telefone:
            return "", "", "", "Cliente não possui telefone/celular válido cadastrado."

        saldo = calcula_saldo(cliente_id)
        if saldo is None or saldo <= 0:
            return "", "", "", "Cliente não possui saldo em aberto."

        mensagem = gerar_mensagem_cobranca(cliente.get('nome', ''), saldo)
        msg = quote(mensagem)

        web_direct = f"https://web.whatsapp.com/send?phone={telefone}&text={msg}"
        deep_link = f"whatsapp://send?phone={telefone}&text={msg}"
        web_fallback = f"https://wa.me/{telefone}?text={msg}"

        return web_direct, deep_link, web_fallback, ""
    except Exception as e:
        print(f"[WHATSAPP ERROR] {e}")
        return "", "", "", f"Erro ao gerar link: {e}"


# Compatibilidade com o nome antigo
def gerar_link_whatsapp_cliente(cliente_id) -> tuple:
    """Wrapper de compatibilidade. Retorna (web_direct, erro)."""
    web_direct, _deep, _web, erro = gerar_links_whatsapp_cliente(cliente_id)
    return web_direct, erro


def _render_link_button_seguro(rotulo: str, link: str,
                                target: str = "_blank",
                                cor: str = "#25D366") -> None:
    """
    Renderiza um botão-link em HTML puro.
    Usa target nomeado para reutilizar abas já abertas.
    """
    st.markdown(
        f'<a href="{link}" target="{target}" '
        f'style="display:block;padding:0.55rem 1rem;background-color:{cor};'
        f'color:white;text-decoration:none;border-radius:0.5rem;text-align:center;'
        f'width:100%;font-weight:bold;box-sizing:border-box;">{rotulo}</a>',
        unsafe_allow_html=True
    )


def _render_botao_aba_nomeada(rotulo: str, link: str,
                               nome_aba: str = "whatsapp_web_tab",
                               cor: str = "#25D366",
                               altura: int = 44) -> None:
    """
    Renderiza um botão que abre OU reutiliza uma aba com nome fixo.

    Usa window.open(url, nome_aba). O navegador reutiliza a aba com
    esse nome a partir da primeira vez que este botão foi clicado —
    mesmo que o Streamlit recarregue, mude de página, etc.

    Limitação do navegador: NÃO é possível detetar abas abertas
    manualmente pelo utilizador, por razões de segurança.
    """
    link_js = link.replace("\\", "\\\\").replace("'", "\\'")
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:0;background:transparent;">
      <button
        onclick="window.open('{link_js}', '{nome_aba}');"
        style="width:100%;height:{altura - 8}px;padding:0 16px;
               background:{cor};color:#fff;border:none;border-radius:8px;
               font-weight:600;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
               font-size:14px;cursor:pointer;transition:filter 0.15s;"
        onmouseover="this.style.filter='brightness(0.92)'"
        onmouseout="this.style.filter='brightness(1)'">
        {rotulo}
      </button>
    </body>
    </html>
    """
    components.html(html, height=altura, scrolling=False)


def render_botao_whatsapp(cliente_id, key_suffix: str = "",
                          rotulo: str = "💬 Enviar cobrança via WhatsApp") -> None:
    """
    Renderiza o botão de cobrança via WhatsApp.

    - Botão principal: WhatsApp Web com aba nomeada (reutiliza a mesma
      aba após o primeiro clique).
    - Fallback 1: deep link nativo (abre o app instalado).
    - Fallback 2: wa.me (universal, mas mostra página intermediária).
    """
    web_direct, deep_link, web_fallback, erro = gerar_links_whatsapp_cliente(cliente_id)

    if not web_direct:
        try:
            st.button(rotulo, disabled=True, use_container_width=True,
                      key=f"btn_wa_disabled_{key_suffix}")
        except Exception:
            st.warning(rotulo)
        if erro:
            st.caption(f"⚠️ {erro}")
        return

    # Prévia da mensagem
    try:
        cliente = query_to_dict_cached("clientes", filters={"id": cliente_id})
        saldo = calcula_saldo(cliente_id)
        preview = gerar_mensagem_cobranca(cliente.get('nome', ''), saldo)
        with st.expander("👁️ Ver mensagem que será enviada", expanded=False):
            st.code(preview, language=None)
            st.caption(
                "✏️ Você pode editar livremente a mensagem dentro do WhatsApp "
                "antes de enviar."
            )
    except Exception:
        pass

    # Botão principal → WhatsApp Web com aba nomeada (reutiliza após 1º clique)
    _render_botao_aba_nomeada(
        rotulo,
        web_direct,
        nome_aba="whatsapp_web_tab",
        cor="#25D366"
    )

    # Alternativas em caso de falha
    with st.expander("🔄 Alternativas (se o botão acima não funcionar)", expanded=False):
        st.caption("**Opção 1 — Abrir no aplicativo instalado** (celular ou WhatsApp Desktop):")
        _render_botao_aba_nomeada(
            "📱 Abrir no aplicativo WhatsApp",
            deep_link,
            nome_aba="whatsapp_app_tab",
            cor="#128C7E"
        )

        st.caption("**Opção 2 — Link universal** (mostra página intermediária, mas funciona sempre):")
        _render_link_button_seguro(
            "🌐 Abrir via wa.me",
            web_fallback,
            target="_blank",
            cor="#075E54"
        )

        st.caption(
            "ℹ️ No **computador**, o botão principal abre o WhatsApp Web diretamente. "
            "No **celular**, prefira a Opção 1 (abre o app)."
        )

    st.caption(
        "💡 **O envio é manual** — sem automação, sem risco de banimento. "
        "Após o 1º clique, o botão reutiliza a aba do WhatsApp Web."
    )
    
# ====================================================================
# =============== UTILITÁRIOS FINAIS =================================
# ====================================================================

def limpar_cache():
    st.cache_data.clear()
    st.cache_resource.clear()
    st.success("✅ Cache limpo com sucesso!")

# ====================================================================
# =============== VERIFICAÇÃO DE LOGIN PRINCIPAL =====================
# ====================================================================

if not verificar_login():
    tela_login()
    st.stop()

# ====================================================================
# =============== SIDEBAR DE NAVEGAÇÃO ===============================
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

if st.sidebar.button("🔒" if not st.session_state.modo_seguro else "🔓",
                     help="Clique para ativar/desativar Modo Seguro"):
    st.session_state.modo_seguro = not st.session_state.modo_seguro

if st.session_state.modo_seguro:
    st.sidebar.warning("🔒 Modo Seguro ATIVADO")
else:
    st.sidebar.info("📱 Modo Normal")

st.sidebar.markdown("---")
st.sidebar.caption("☁️ Dados salvos no Supabase")
st.sidebar.caption(f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}")

# ====================================================================
# =============== PÁGINAS DA APLICAÇÃO ===============================
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
                nome_limpo = sanitizar_texto(nome, 200)
                tel_limpo = sanitizar_texto(telefone, 20)

                if not nome_limpo:
                    erros.append("Nome obrigatório")
                if not tel_limpo:
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
                        'nome': nome_limpo,
                        'telefone': tel_limpo,
                        'data_cadastro': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'modo_seguro': st.session_state.modo_seguro,
                        'cpf': sanitizar_texto(cpf, 14) if cpf else None,
                        'rg': sanitizar_texto(rg, 30) if rg else None,
                        'data_nascimento': str(data_nasc) if data_nasc else None,
                        'email': sanitizar_texto(email, 200) if email else None,
                        'celular': sanitizar_texto(celular, 20) if celular else None,
                        'logradouro': sanitizar_texto(logradouro, 200) if logradouro else None,
                        'numero': sanitizar_texto(numero, 20) if numero else None,
                        'complemento': sanitizar_texto(complemento, 100) if complemento else None,
                        'bairro': sanitizar_texto(bairro, 100) if bairro else None,
                        'cidade': sanitizar_texto(cidade, 100) if cidade else None,
                        'estado': sanitizar_texto(estado, 2) if estado else None,
                        'cep': sanitizar_texto(cep, 8) if cep else None,
                        'aceite_lgpd': aceite_lgpd,
                        'data_aceite_lgpd': datetime.now().strftime("%Y-%m-%d %H:%M:%S") if aceite_lgpd else None,
                        'observacoes': sanitizar_texto(observacoes, 1000) if observacoes else None,
                        'limite_credito': 999999.99,
                        'bloqueado': False,
                        'motivo_bloqueio': None
                    }

                    result = insert_data("clientes", cliente_data)
                    if result:
                        log_auditoria("criar_cliente", {"nome": nome_limpo})
                        st.session_state.form_data = {}
                        st.success("✅ Cliente cadastrado!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao cadastrar cliente")

    st.subheader("📋 Lista de Clientes")
    clientes_com_saldo = get_clientes_com_saldo()

    if clientes_com_saldo:
        df = pd.DataFrame(clientes_com_saldo)
        df['saldo_fmt'] = df['saldo'].apply(formata_moeda)
        df['modo'] = df['modo_seguro'].apply(lambda x: "🔒" if x else "📱")
        df['cpf_mascarado'] = df['cpf'].apply(mascarar_cpf)

        def get_status(row):
            if row.get('bloqueado', False):
                return "🚫 BLOQUEADO"
            elif row.get('limite_credito', 999999.99) < 999999.99:
                return f"💳 Limite: {formata_moeda(row.get('limite_credito', 0))}"
            return "✅ Ativo"

        df['status'] = df.apply(get_status, axis=1)

        st.dataframe(
            df[['id', 'nome', 'telefone', 'cpf_mascarado', 'saldo_fmt', 'status', 'modo']],
            column_config={
                "id": "ID", "nome": "Nome", "telefone": "Telefone",
                "cpf_mascarado": "CPF", "saldo_fmt": "Saldo",
                "status": "Status", "modo": ""
            },
            use_container_width=True
        )

        # =====================================================
        # 💬 COBRANÇA VIA WHATSAPP (ENVIO MANUAL) — NOVO
        # =====================================================
        st.divider()
        st.subheader("💬 Cobrança via WhatsApp")
        st.caption(
            "Gera a mensagem pronta e abre o WhatsApp. **O envio é manual**"
        )

        # Apenas clientes com saldo em aberto fazem sentido para cobrança,
        # mas mantemos todos como fallback para não travar a UI.
        clientes_para_cobranca = [c for c in clientes_com_saldo if c.get('saldo', 0) > 0]

        if clientes_para_cobranca:
            cliente_wa = st.selectbox(
                "Selecione o cliente para cobrança",
                [c['id'] for c in clientes_para_cobranca],
                format_func=lambda x: next(
                    (f"{c['nome']} — {formata_moeda(c.get('saldo', 0))}"
                     for c in clientes_para_cobranca if c['id'] == x),
                    str(x)
                ),
                key="sel_wa_cobranca_clientes"
            )

            if cliente_wa:
                render_botao_whatsapp(
                    cliente_wa,
                    key_suffix="clientes",
                    rotulo="💬 Enviar cobrança pelo WhatsApp"
                )
        else:
            st.info("ℹ️ Nenhum cliente com saldo em aberto no momento.")

        # EDIÇÃO DE CLIENTE
        st.divider()
        st.subheader("✏️ Editar Cliente")
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
                                value=datetime.strptime(cliente_dados['data_nascimento'], "%Y-%m-%d").date()
                                if cliente_dados.get('data_nascimento') else None
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

                        st.divider()
                        st.subheader("💰 Controle de Crédito")

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            limite_edit = st.number_input(
                                "Limite de Crédito (R$)",
                                min_value=0.00, max_value=999999.99,
                                value=float(cliente_dados.get('limite_credito', 999999.99)),
                                step=50.00, format="%.2f"
                            )
                        with col2:
                            bloqueado_edit = st.checkbox(
                                "🚫 Bloquear Cliente",
                                value=cliente_dados.get('bloqueado', False)
                            )
                        with col3:
                            motivo_bloqueio_edit = st.text_input(
                                "Motivo do Bloqueio",
                                value=cliente_dados.get('motivo_bloqueio', '') if bloqueado_edit else ''
                            )

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
                                    'nome': sanitizar_texto(nome_edit, 200),
                                    'telefone': sanitizar_texto(telefone_edit, 20),
                                    'cpf': sanitizar_texto(cpf_edit, 14) if cpf_edit else None,
                                    'rg': sanitizar_texto(rg_edit, 30) if rg_edit else None,
                                    'data_nascimento': str(data_nasc_edit) if data_nasc_edit else None,
                                    'email': sanitizar_texto(email_edit, 200) if email_edit else None,
                                    'celular': sanitizar_texto(celular_edit, 20) if celular_edit else None,
                                    'logradouro': sanitizar_texto(logradouro_edit, 200) if logradouro_edit else None,
                                    'numero': sanitizar_texto(numero_edit, 20) if numero_edit else None,
                                    'complemento': sanitizar_texto(complemento_edit, 100) if complemento_edit else None,
                                    'bairro': sanitizar_texto(bairro_edit, 100) if bairro_edit else None,
                                    'cidade': sanitizar_texto(cidade_edit, 100) if cidade_edit else None,
                                    'estado': sanitizar_texto(estado_edit, 2) if estado_edit else None,
                                    'cep': sanitizar_texto(cep_edit, 8) if cep_edit else None,
                                    'observacoes': sanitizar_texto(observacoes_edit, 1000) if observacoes_edit else None,
                                    'limite_credito': limite_edit,
                                    'bloqueado': bloqueado_edit,
                                    'motivo_bloqueio': sanitizar_texto(motivo_bloqueio_edit, 200) if bloqueado_edit else None
                                }

                                if editar_cliente(cliente_editar, dados_atualizados):
                                    log_auditoria("editar_cliente", {"cliente_id": cliente_editar, "campos": list(dados_atualizados.keys())})
                                    st.success(f"✅ Cliente '{nome_edit}' atualizado com sucesso!")
                                    st.rerun()
                                else:
                                    st.error("❌ Erro ao atualizar cliente")

        # EXCLUIR CLIENTE
        st.divider()
        st.subheader("🗑️ Excluir Cliente")

        if render_autenticacao_gerente("excluir_cliente"):
            cliente_id = st.selectbox(
                "Selecione o cliente para excluir",
                [c['id'] for c in clientes],
                format_func=lambda x: next(c['nome'] for c in clientes if c['id'] == x),
                key="sel_del_cliente_main"
            )

            if cliente_id:
                nome_cliente = next(c['nome'] for c in clientes if c['id'] == cliente_id)
                saldo = next(c.get('saldo', 0) for c in clientes_com_saldo if c['id'] == cliente_id)

                if saldo > 0:
                    st.warning(f"⚠️ Cliente tem saldo de {formata_moeda(saldo)} pendente!")

                confirmar = st.text_input(f"Digite o nome '{nome_cliente}' para confirmar:", key="conf_del_cliente")

                if confirmar == nome_cliente:
                    if st.button("🗑️ EXCLUIR PERMANENTEMENTE", type="primary", key="btn_del_cliente"):
                        log_auditoria("excluir_cliente", {"cliente_id": cliente_id, "nome": nome_cliente})
                        delete_data("pagamentos", {"cliente_id": cliente_id})
                        delete_data("produtos", {"cliente_id": cliente_id})
                        delete_data("clientes", {"id": cliente_id})
                        st.success("✅ Cliente excluído!")
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
            format_func=lambda x: f"{next(c['nome'] for c in clientes if c['id'] == x)} "
                                  f"{'🔒' if next(c['modo_seguro'] for c in clientes if c['id'] == x) else ''}"
        )

        if cliente_id:
            info_limite = get_limite_cliente(cliente_id)

            if info_limite['bloqueado']:
                st.error(f"🚫 **CLIENTE BLOQUEADO!** Motivo: {info_limite['motivo'] or 'Não informado'}")
                st.warning("Este cliente não pode fazer novas compras!")
                st.stop()

            saldo = calcula_saldo(cliente_id)
            limite = info_limite['limite']

            if limite < 999999.99:
                disponivel = limite - saldo
                st.info(f"💰 Saldo atual: {formata_moeda(saldo)} | 💳 Limite: {formata_moeda(limite)} | "
                        f"📊 Disponível: {formata_moeda(disponivel)}")
                if saldo / limite > 0.8:
                    st.warning(f"⚠️ ATENÇÃO: Saldo atual ({formata_moeda(saldo)}) está próximo do limite "
                               f"({formata_moeda(limite)})!")
                if disponivel <= 0:
                    st.error(f"❌ Limite esgotado! Saldo: {formata_moeda(saldo)} | Limite: {formata_moeda(limite)}")
                    st.stop()
            else:
                st.info(f"💰 Saldo atual: {formata_moeda(saldo)} | ♾️ Sem limite definido")

            if next(c['modo_seguro'] for c in clientes if c['id'] == cliente_id):
                st.warning("🔒 Cliente em Modo Seguro")

            with st.expander("🏷️ Gerenciar Produtos Padrão", expanded=False):
                with st.form("form_produto_padrao", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        nome_padrao = st.text_input("Nome do Produto*")
                    with col2:
                        valor_padrao = st.number_input(
                            "Valor Padrão (R$)*", min_value=0.01,
                            value=0.01, step=0.01, format="%.2f"
                        )

                    if st.form_submit_button("➕ Cadastrar Produto Padrão"):
                        nome_padrao_limpo = sanitizar_texto(nome_padrao, 200)
                        if not nome_padrao_limpo:
                            st.error("❌ Nome do produto obrigatório")
                        elif valor_padrao <= 0:
                            st.error("❌ Valor deve ser maior que zero")
                        else:
                            produto_data = {
                                'nome': nome_padrao_limpo,
                                'valor': valor_padrao,
                                'data_cadastro': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            result = insert_data("produtos_padrao", produto_data)
                            if result:
                                log_auditoria("criar_produto_padrao",
                                              {"nome": nome_padrao_limpo, "valor": valor_padrao})
                                st.success(f"✅ Produto '{nome_padrao_limpo}' cadastrado com sucesso!")
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

                    if render_autenticacao_gerente("padrao"):
                        produto_excluir = st.selectbox(
                            "Selecione o produto padrão para excluir",
                            [p['id'] for p in produtos_padrao],
                            format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - "
                                                  f"{formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}",
                            key="excluir_padrao"
                        )

                        if produto_excluir:
                            nome_excluir = next(p['nome'] for p in produtos_padrao if p['id'] == produto_excluir)
                            confirmar = st.text_input(f"Digite '{nome_excluir}' para confirmar exclusão:",
                                                      key="confirma_padrao")

                            if confirmar == nome_excluir:
                                if st.button("🗑️ Excluir Produto Padrão", type="primary", key="btn_del_padrao"):
                                    log_auditoria("excluir_produto_padrao",
                                                  {"id": produto_excluir, "nome": nome_excluir})
                                    delete_data("produtos_padrao", {"id": produto_excluir})
                                    st.success(f"✅ Produto '{nome_excluir}' excluído!")
                                    st.rerun()

                    # EDIÇÃO DE PRODUTO PADRÃO
                    st.divider()
                    st.subheader("✏️ Editar Produto Padrão")
                    st.caption("⚠️ Apenas o NOME pode ser editado. O VALOR permanece o mesmo para evitar fraudes.")

                    produto_editar = st.selectbox(
                        "Selecione o produto padrão para editar",
                        [p['id'] for p in produtos_padrao],
                        format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - "
                                              f"{formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}",
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
                                    nome_edit_padrao_limpo = sanitizar_texto(nome_edit_padrao, 200)
                                    if not nome_edit_padrao_limpo:
                                        st.error("❌ Nome do produto obrigatório")
                                    else:
                                        if editar_produto_padrao(produto_editar, nome_edit_padrao_limpo):
                                            log_auditoria("editar_produto_padrao",
                                                          {"id": produto_editar, "novo_nome": nome_edit_padrao_limpo})
                                            st.success(f"✅ Produto atualizado para '{nome_edit_padrao_limpo}'!")
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
                            format_func=lambda x: f"{next(p['nome'] for p in produtos_padrao if p['id'] == x)} - "
                                                  f"{formata_moeda(next(p['valor'] for p in produtos_padrao if p['id'] == x))}"
                        )

                        if produto_selecionado:
                            nome_produto = next(p['nome'] for p in produtos_padrao if p['id'] == produto_selecionado)
                            valor_produto = next(p['valor'] for p in produtos_padrao if p['id'] == produto_selecionado)

                            st.info(f"📦 Produto: **{nome_produto}** - Valor: {formata_moeda(valor_produto)}")

                            if st.form_submit_button("✅ Adicionar à Fichinha"):
                                verificacao = verificar_pode_comprar(cliente_id, valor_produto)
                                if not verificacao['pode']:
                                    st.error(f"❌ {verificacao['motivo']}")
                                else:
                                    produto_data = {
                                        'cliente_id': cliente_id,
                                        'nome': sanitizar_texto(nome_produto, 200),
                                        'valor': valor_produto,
                                        'data_compra': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        'pago': False
                                    }
                                    if insert_data("produtos", produto_data):
                                        log_auditoria("add_produto_ficha",
                                                      {"cliente_id": cliente_id,
                                                       "produto": nome_produto, "valor": valor_produto})
                                        st.success(f"✅ Produto '{nome_produto}' adicionado!")
                                        st.rerun()
            else:
                with st.form("form_produto_personalizado", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        nome = st.text_input("Nome do Produto*")
                    with col2:
                        valor = st.number_input(
                            "Valor (R$)*", min_value=0.01,
                            value=0.01, step=0.01, format="%.2f"
                        )

                    if st.form_submit_button("✅ Adicionar à Fichinha"):
                        nome_limpo = sanitizar_texto(nome, 200)
                        if not nome_limpo:
                            st.error("❌ Nome do produto obrigatório")
                        else:
                            verificacao = verificar_pode_comprar(cliente_id, valor)
                            if not verificacao['pode']:
                                st.error(f"❌ {verificacao['motivo']}")
                            else:
                                produto_data = {
                                    'cliente_id': cliente_id,
                                    'nome': nome_limpo,
                                    'valor': valor,
                                    'data_compra': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    'pago': False
                                }
                                if insert_data("produtos", produto_data):
                                    log_auditoria("add_produto_ficha",
                                                  {"cliente_id": cliente_id, "produto": nome_limpo, "valor": valor})
                                    st.success(f"✅ Produto '{nome_limpo}' adicionado!")
                                    st.rerun()

            st.divider()
            st.subheader("📋 Fichinha Atual")
            produtos = get_produtos_nao_pagos_cliente(cliente_id)

            if produtos:
                df_produtos = pd.DataFrame(produtos)
                df_produtos['valor_fmt'] = df_produtos['valor'].apply(formata_moeda)
                st.dataframe(df_produtos[['nome', 'valor_fmt', 'data_compra']], use_container_width=True)
                st.metric("💰 Total da Fichinha", formata_moeda(df_produtos['valor'].sum()))

                # =====================================================
                # 🖨️ IMPRESSÃO DO COMPROVANTE DE DÍVIDA
                # =====================================================
                st.divider()
                st.subheader("🖨️ Imprimir Comprovante de Dívida")
                st.caption(
                    "Em casos de pessoas inadimplentes, EXIJA a assinatura do comprovante.(Código Civil, art. 408) "
                )

                col_imp1, col_imp2 = st.columns([1, 1])
                with col_imp1:
                    if st.button("🖨️ IMPRIMIR COMPROVANTE", type="primary",
                                 use_container_width=True, key="btn_imprimir_ficha"):
                        log_auditoria("imprimir_comprovante",
                                      {"cliente_id": cliente_id, "total_produtos": len(produtos)})
                        st.session_state['_print_cliente_id'] = cliente_id
                        st.session_state['_print_source'] = 'ficha'
                with col_imp2:
                    if st.button("👁️ Pré-visualizar (sem imprimir)",
                                 use_container_width=True, key="btn_preview_ficha"):
                        st.session_state['_print_cliente_id'] = cliente_id
                        st.session_state['_print_source'] = 'preview'
                        st.session_state['_print_auto'] = False

                # Renderiza a área de impressão quando acionada
                if st.session_state.get('_print_cliente_id') == cliente_id:
                    auto = st.session_state.get('_print_source', 'ficha') == 'ficha'
                    imprimir_comprovante_cliente(cliente_id, auto_print=auto)
                    if st.button("❌ Fechar área de impressão", key="btn_fechar_print_ficha"):
                        st.session_state.pop('_print_cliente_id', None)
                        st.session_state.pop('_print_source', None)
                        st.rerun()

                # =====================================================
                # 💬 COBRANÇA VIA WHATSAPP (ENVIO MANUAL) — NOVO
                # =====================================================
                st.divider()
                st.subheader("💬 Cobrança via WhatsApp")
                st.caption(
                    "Gera a mensagem pronta e abre o WhatsApp. **O envio é manual**"
                )
                render_botao_whatsapp(
                    cliente_id,
                    key_suffix="ficha",
                    rotulo="💬 Enviar cobrança pelo WhatsApp"
                )

                st.divider()
                st.subheader("🗑️ Excluir Produto da Fichinha")

                if render_autenticacao_gerente("ficha"):
                    produto_id = st.selectbox(
                        "Selecione o produto para excluir",
                        [p['id'] for p in produtos],
                        format_func=lambda x: f"{next(p['nome'] for p in produtos if p['id'] == x)} - "
                                              f"{formata_moeda(next(p['valor'] for p in produtos if p['id'] == x))}",
                        key="sel_del_produto_ficha"
                    )

                    if produto_id:
                        nome_produto = next(p['nome'] for p in produtos if p['id'] == produto_id)
                        valor_produto = next(p['valor'] for p in produtos if p['id'] == produto_id)

                        st.warning(f"⚠️ Você está prestes a excluir: **{nome_produto}** ({formata_moeda(valor_produto)})")

                        confirmar = st.text_input(
                            "Digite o nome do produto para confirmar:",
                            key="conf_del_produto_ficha"
                        )

                        if confirmar == nome_produto:
                            if st.button("🗑️ EXCLUIR PRODUTO", type="primary", use_container_width=True, key="btn_del_prod_ficha"):
                                log_auditoria("excluir_produto_ficha",
                                              {"produto_id": produto_id, "nome": nome_produto})
                                delete_data("produtos", {"id": produto_id})
                                st.success("✅ Produto excluído!")
                                st.rerun()
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
            format_func=lambda x: next(c['nome'] for c in clientes if c['id'] == x),
            key="sel_cliente_pag"
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

                    # =====================================================
                    # 🖨️ IMPRESSÃO DO COMPROVANTE DE DÍVIDA
                    # =====================================================
                    st.divider()
                    st.subheader("🖨️ Imprimir Comprovante de Dívida")
                    st.caption(
                        "Imprima o comprovante com a declaração de dívida e campo de "
                        "assinatura para recolher do cliente antes de registrar o pagamento."
                    )

                    col_imp1, col_imp2 = st.columns([1, 1])
                    with col_imp1:
                        if st.button("🖨️ IMPRIMIR COMPROVANTE", type="primary",
                                     use_container_width=True, key="btn_imprimir_pag"):
                            log_auditoria("imprimir_comprovante",
                                          {"cliente_id": cliente_id, "total_produtos": len(produtos),
                                           "origem": "pagamentos"})
                            st.session_state['_print_cliente_id'] = cliente_id
                            st.session_state['_print_source'] = 'ficha'
                    with col_imp2:
                        if st.button("👁️ Pré-visualizar (sem imprimir)",
                                     use_container_width=True, key="btn_preview_pag"):
                            st.session_state['_print_cliente_id'] = cliente_id
                            st.session_state['_print_source'] = 'preview'

                    if st.session_state.get('_print_cliente_id') == cliente_id:
                        auto = st.session_state.get('_print_source', 'ficha') == 'ficha'
                        imprimir_comprovante_cliente(cliente_id, auto_print=auto)
                        if st.button("❌ Fechar área de impressão", key="btn_fechar_print_pag"):
                            st.session_state.pop('_print_cliente_id', None)
                            st.session_state.pop('_print_source', None)
                            st.rerun()

                    # =====================================================
                    # 💬 COBRANÇA VIA WHATSAPP (ENVIO MANUAL) — NOVO
                    # =====================================================
                    st.divider()
                    st.subheader("💬 Cobrança via WhatsApp")
                    st.caption(
                        "Gera a mensagem pronta e abre o WhatsApp. **O envio é manual**"
                    )
                    render_botao_whatsapp(
                        cliente_id,
                        key_suffix="pag",
                        rotulo="💬 Enviar cobrança pelo WhatsApp"
                    )

                    st.divider()

                    with st.form("form_pagamento"):
                        c1, c2 = st.columns(2)
                        with c1:
                            valor = st.number_input(
                                "Valor (R$)*", min_value=0.01,
                                max_value=float(saldo),
                                value=min(10.00, float(saldo)),
                                step=0.01, format="%.2f"
                            )
                        with c2:
                            tipo = st.selectbox(
                                "Forma",
                                ["dinheiro", "cartao", "pix"],
                                format_func=lambda x: {"dinheiro": "💵 Dinheiro",
                                                        "cartao": "💳 Cartão",
                                                        "pix": "📱 Pix"}[x]
                            )

                        descricao = st.text_input("Descrição (opcional)", max_chars=500)

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
                                            {"pago": True, "tipo_pagamento": tipo,
                                             "data_pagamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                            {"id": row['id']})
                                        valor_restante -= row['valor']
                                    else:
                                        resto = row['valor'] - valor_restante
                                        update_data("produtos",
                                            {"pago": True, "tipo_pagamento": tipo,
                                             "data_pagamento": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                            {"id": row['id']})
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
                                    "descricao": sanitizar_texto(descricao, 500)
                                })

                                log_auditoria("registrar_pagamento",
                                              {"cliente_id": cliente_id, "valor": valor, "tipo": tipo})

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
                st.dataframe(df_historico[['valor_fmt', 'tipo', 'data_pagamento', 'descricao']].head(30),
                             use_container_width=True)

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

            # =====================================================
            # 💬 COBRANÇA EM MASSA — SELEÇÃO INDIVIDUAL VIA WHATSAPP
            # =====================================================
            st.divider()
            st.subheader("💬 Cobrança via WhatsApp (Devedores)")
            st.caption(
                "Selecione um devedor e gere a mensagem pronta. "
            )

            devedores_ordenados = sorted(devedores, key=lambda x: x['total'], reverse=True)
            mapa_nome_id = {c['nome']: c['id'] for c in clientes_all}

            devedor_sel_nome = st.selectbox(
                "Selecione o devedor",
                [d['nome'] for d in devedores_ordenados],
                format_func=lambda n: f"{n} — {formata_moeda(next(d['total'] for d in devedores_ordenados if d['nome'] == n))}",
                key="sel_wa_devedor"
            )

            if devedor_sel_nome and devedor_sel_nome in mapa_nome_id:
                render_botao_whatsapp(
                    mapa_nome_id[devedor_sel_nome],
                    key_suffix="relatorio",
                    rotulo="💬 Enviar cobrança pelo WhatsApp"
                )
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
        st.caption("⚠️ Exportação registrada em auditoria.")

        clientes_df = pd.DataFrame(get_all_clientes())
        produtos_df = pd.DataFrame(query_to_list_cached("produtos"))
        pagamentos_df = pd.DataFrame(query_to_list_cached("pagamentos"))
        pp_df = pd.DataFrame(get_all_produtos_padrao())

        if st.button("📥 Registrar Exportação (CSV)"):
            log_auditoria("exportar_csv", {"usuario": st.session_state.usuario})

        col1, col2 = st.columns(2)
        with col1:
            if not clientes_df.empty:
                st.download_button("📥 Clientes", clientes_df.to_csv(index=False).encode(),
                                   "clientes.csv", "text/csv")
            if not produtos_df.empty:
                st.download_button("📥 Produtos", produtos_df.to_csv(index=False).encode(),
                                   "produtos.csv", "text/csv")
        with col2:
            if not pagamentos_df.empty:
                st.download_button("📥 Pagamentos", pagamentos_df.to_csv(index=False).encode(),
                                   "pagamentos.csv", "text/csv")
            if not pp_df.empty:
                st.download_button("📥 Produtos Padrão", pp_df.to_csv(index=False).encode(),
                                   "produtos_padrao.csv", "text/csv")

    with tab4:
        st.subheader("🔄 Sincronizar com Outra Versão")

        st.info("""
        **Como funciona:**
        1. Exporte os dados de uma versão (nuvem ou local)
        2. Importe na outra versão
        3. Os dados ficam iguais nos dois lugares

        ⚠️ **Importação requer autenticação do gerente** (para evitar importação indevida).
        """)

        if render_autenticacao_gerente("sync"):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**📤 Exportar Dados (deste app)**")
                if st.button("📥 Exportar JSON", use_container_width=True):
                    log_auditoria("exportar_json", {"usuario": st.session_state.usuario})
                    dados_json = exportar_dados_json()
                    b64 = base64.b64encode(dados_json.encode()).decode()
                    href = (f'<a href="data:application/json;base64,{b64}" '
                            f'download="backup_fichinha_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json">'
                            f'📥 Baixar Backup</a>')
                    st.markdown(href, unsafe_allow_html=True)
                    st.success("✅ Dados exportados com sucesso!")

            with col2:
                st.markdown("**📥 Importar Dados (para este app)**")
                arquivo = st.file_uploader("Escolha o arquivo JSON", type=['json'])

                if arquivo and st.button("📥 Importar Dados", use_container_width=True):
                    try:
                        log_auditoria("importar_json", {"usuario": st.session_state.usuario})
                        dados_json = arquivo.read().decode('utf-8')
                        total = importar_dados_json(dados_json)
                        st.success(f"✅ {total} clientes importados com sucesso!")
                        st.balloons()
                        st.rerun()
                    except Exception as e:
                        print(f"[IMPORT ERROR] {e}")
                        st.error("❌ Erro ao importar arquivo.")