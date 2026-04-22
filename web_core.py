"""
=============================================================================
SYZYGY C2 NEURAL - COMMAND & CONTROL MASTER NODE
Versão: 30.0 (Omni-Sync & OSINT Master - Full Stable)
Operação: Equipe MÁFIA
Arquitetura: Flask + PostgreSQL + Vue.js + Discord.py
Padrão de Código: PEP8 Strict / Fully Expanded Blocks
=============================================================================
"""

import os
import hashlib
import psycopg2
import psycopg2.extras
import jwt
import datetime
import json
import requests
import uuid
import threading
import asyncio
import time

from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context
from functools import wraps
from dotenv import load_dotenv
import PyPDF2

# Integração Google SSO
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Integração Discord (Comando e Controle)
import discord
from discord.ext import commands

# ==========================================
# 1. INICIALIZAÇÃO DE VARIÁVEIS E AMBIENTE
# ==========================================
load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get('JWT_SECRET', 'syzygy_master_key_2026')

DATABASE_URL = os.environ.get('DATABASE_URL')
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK')

SYSTEM_NAME = "SYZYGY Neural"
GOOGLE_CLIENT_ID = "420487619791-sgpt2dbmq5eqobqv34qcgacrecnklfuf.apps.googleusercontent.com"


# ==========================================
# 2. MOTOR DE TELEMETRIA (DISCORD WEBHOOKS)
# ==========================================
def send_discord_webhook(title, description, color=0x8a2be2):
    """ Envia alertas silenciosos de sistema para o canal de Defesa & Logs """
    if not DISCORD_WEBHOOK:
        return
        
    data = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "SYZYGY C2 • Operação MÁFIA"},
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
        ]
    }
    
    def post_request():
        try:
            requests.post(DISCORD_WEBHOOK, json=data, timeout=10)
        except Exception as e:
            print(f"[ALARME CRÍTICO] Falha ao despachar telemetria: {e}")
            
    threading.Thread(target=post_request, daemon=True).start()


# ==========================================
# 3. FUNÇÕES DE ACESSO AO BANCO DE DADOS
# ==========================================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    print("[SYSTEM BOOT] Iniciando verificação de topologia de Banco de Dados...")
    try:
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, login TEXT UNIQUE, senha TEXT, is_admin BOOLEAN DEFAULT False, 
                auth_provider TEXT DEFAULT 'local', status TEXT DEFAULT 'ativo', tier TEXT DEFAULT 'free'
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ias (
                nome TEXT PRIMARY KEY, model TEXT, url TEXT, env TEXT, 
                prompt TEXT, tier_req TEXT DEFAULT 'free', config TEXT DEFAULT '{}'
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS system_limits (
                tier TEXT PRIMARY KEY, max_msgs INT, reset_hours INT, features TEXT
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY, user_login TEXT, key_val TEXT UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
                login TEXT PRIMARY KEY, msg_count INT DEFAULT 0, last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS api_vault (
                env_name TEXT PRIMARY KEY, api_key TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        migracoes = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider TEXT DEFAULT 'local'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ativo'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'free'",
            "ALTER TABLE ias ADD COLUMN IF NOT EXISTS tier_req TEXT DEFAULT 'free'",
            "ALTER TABLE ias ADD COLUMN IF NOT EXISTS config TEXT DEFAULT '{}'",
            "ALTER TABLE system_limits ADD COLUMN IF NOT EXISTS features TEXT",
            "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ]
        
        for migracao in migracoes:
            try: cur.execute(migracao)
            except Exception: conn.rollback()

        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cur.execute("""
            INSERT INTO users (login, senha, is_admin, tier) VALUES (%s, %s, True, 'ultra') 
            ON CONFLICT (login) DO NOTHING
        """, ('local_admin', pw_hash))
        
        dados_tiers = [
            ('free', 15, 3, 'Modelos Básicos,15 Mensagens a cada 3 Horas'),
            ('plus', 50, 2, 'Modelos de Resposta Rápida (Flash),Prioridade de Rede'),
            ('pro', 200, 1, 'Modelos de Alta Complexidade (GPT-4/Claude)'),
            ('ultra', 999, 1, 'Modelos de Raciocínio,Limites Extremos')
        ]
        
        for t in dados_tiers:
            cur.execute("""
                INSERT INTO system_limits (tier, max_msgs, reset_hours, features) VALUES (%s, %s, %s, %s) 
                ON CONFLICT (tier) DO NOTHING
            """, t)
        
        conn.commit()
        cur.close()
        conn.close()
        print("[SYSTEM BOOT] Estrutura Monolítica de Banco de Dados Pronta.")
        
    except Exception as e:
        print(f"[Erro Crítico DB Init] {e}")

init_db()

def exec_db_query(query, params=(), fetch=False):
    try:
        conn = get_db()
        if fetch:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(query, params)
            res = [dict(r) for r in cur.fetchall()]
        else:
            cur = conn.cursor()
            cur.execute(query, params)
            res = cur.rowcount
            conn.commit()
        cur.close()
        conn.close()
        return res
    except Exception as e:
        print(f"[Database Error] Query: {query} | Error: {e}")
        return None

def get_api_key_from_vault(env_name):
    res = exec_db_query("SELECT api_key FROM api_vault WHERE env_name = %s", (env_name,), fetch=True)
    if res and len(res) > 0:
        return res[0]['api_key']
    return os.environ.get(env_name)


# ==========================================
# 4. CÉREBRO DO BOT DISCORD (C2 COMMANDER)
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print("="*50)
    print(f"🟢 [SUCESSO TÁTICO] O MÓDULO MILITAR CONECTOU: {bot.user}")
    print("="*50)
    canal_status = discord.utils.get(bot.get_all_channels(), name="status-nuvem")
    if canal_status:
        embed = discord.Embed(
            title="🟢 NÚCLEO SYZYGY ONLINE", 
            description="Servidor V30.0 (Omni-Sync & OSINT Master). \nO Motor de Reconhecimento foi estabilizado.", 
            color=0x10a37f
        )
        await canal_status.send(embed=embed)
    else:
        print("🔴 [ALERTA] Canal 'status-nuvem' não encontrado. Notificação suprimida.")

@bot.group(name="api", invoke_without_command=True)
async def api_group(ctx):
    if ctx.channel.name != "terminal-master": return
    embed = discord.Embed(title="🌐 RADAR OSINT DE APIs (28 Funções)", color=0x10a37f)
    embed.add_field(name="O Cofre", value="1. `vault_add`\n2. `vault_list`\n3. `vault_del`\n26. `key_info`\n27. `nuke_vault`", inline=True)
    embed.add_field(name="Mapeamento", value="4. `list_providers`\n5. `get_endpoints`\n6. `scan_free_apis`\n7. `scan_paid_apis`", inline=True)
    embed.add_field(name="Descoberta", value="8. `scan_models`\n9. `search_model`\n10. `global_radar`\n16. `hunt_models`\n18. `spy_model`\n19. `top_trending`", inline=True)
    embed.add_field(name="Diagnóstico", value="11. `test_key`\n12. `ping_provider`\n13. `compare_speed`\n14. `check_credits`\n20. `provider_health`", inline=True)
    embed.add_field(name="Live Sync", value="15. `auto_inject`\n21. `lock_route`\n22. `unlock_route`\n23. `clone_route`\n28. `del_route`", inline=True)
    embed.add_field(name="Auditoria", value="17. `report`\n24. `audit_op`\n25. `force_reset`", inline=True)
    await ctx.send(embed=embed)

@api_group.command(name="vault_add")
async def cmd_vault_add(ctx, env_name: str, api_key: str):
    if ctx.channel.name != "terminal-master": return
    query = "INSERT INTO api_vault (env_name, api_key) VALUES (%s, %s) ON CONFLICT (env_name) DO UPDATE SET api_key = EXCLUDED.api_key"
    exec_db_query(query, (env_name, api_key))
    await ctx.send(f"🔐 Cofre: A chave `{env_name}` foi blindada.")
    await ctx.message.delete()

@api_group.command(name="vault_list")
async def cmd_vault_list(ctx):
    if ctx.channel.name != "terminal-master": return
    rows = exec_db_query("SELECT env_name, api_key FROM api_vault", fetch=True)
    if not rows:
        await ctx.send("O cofre está vazio.")
        return
    msg = "**🔐 Cofre Ativo:**\n"
    for r in rows:
        km = r['api_key'][:6] + "..." + r['api_key'][-4:] if r['api_key'] else "NULL"
        msg += f"🔹 `{r['env_name']}` -> `{km}`\n"
    await ctx.send(msg)

@api_group.command(name="vault_del")
async def cmd_vault_del(ctx, env_name: str):
    if ctx.channel.name != "terminal-master": return
    exec_db_query("DELETE FROM api_vault WHERE env_name = %s", (env_name,))
    await ctx.send(f"🗑️ Cofre: A chave `{env_name}` foi destruída.")

@api_group.command(name="test_key")
async def cmd_test_key(ctx, env_name: str, url: str):
    if ctx.channel.name != "terminal-master": return
    key = get_api_key_from_vault(env_name)
    if not key:
        await ctx.send(f"🔴 Chave `{env_name}` não existe.")
        return
    try:
        r = requests.get(url.replace("/chat/completions", "/models"), headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200: await ctx.send(f"🟢 Chave `{env_name}` válida!")
        else: await ctx.send(f"🔴 Chave inválida (Erro {r.status_code}).")
    except Exception as e: await ctx.send(f"🔴 Erro de rede: {e}")

@api_group.command(name="list_providers")
async def cmd_list_providers(ctx):
    if ctx.channel.name != "terminal-master": return
    await ctx.send("**🌍 Provedores:** OpenRouter, Groq, Together AI, OpenAI, Anthropic, DeepSeek, Local.")

@api_group.command(name="get_endpoints")
async def cmd_get_endpoints(ctx, provedor: str):
    if ctx.channel.name != "terminal-master": return
    endpoints = {"openrouter": "https://openrouter.ai/api/v1/chat/completions", "groq": "https://api.groq.com/openai/v1/chat/completions"}
    await ctx.send(f"📍 Endpoint: `{endpoints.get(provedor.lower(), 'N/A')}`")

@api_group.command(name="scan_models")
async def cmd_scan_models(ctx, url: str, env_name: str):
    if ctx.channel.name != "terminal-master": return
    key = get_api_key_from_vault(env_name)
    try:
        r = requests.get(url.replace("/chat/completions", "/models"), headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if r.status_code == 200:
            md = r.json().get("data", [])
            msg = f"🟢 **Scan Concluído:** {len(md)} modelos encontrados!\n```text\n"
            for m in md[:10]: msg += f"- {m.get('id')}\n"
            await ctx.send(msg + "```")
    except Exception as e: await ctx.send(f"🔴 Falha: {e}")

@api_group.command(name="search_model")
async def cmd_search_model(ctx, url: str, env_name: str, termo: str):
    if ctx.channel.name != "terminal-master": return
    key = get_api_key_from_vault(env_name)
    try:
        r = requests.get(url.replace("/chat/completions", "/models"), headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if r.status_code == 200:
            found = [m.get('id') for m in r.json().get("data", []) if termo.lower() in m.get('id', '').lower()]
            if found:
                msg = f"🟢 {len(found)} modelos encontrados:\n```text\n"
                for m in found[:15]: msg += f"- {m}\n"
                await ctx.send(msg + "```")
            else: await ctx.send("⚠️ Nenhum modelo.")
    except Exception as e: await ctx.send(f"🔴 Falha: {e}")

@api_group.command(name="scan_free_apis")
async def cmd_scan_free_apis(ctx):
    if ctx.channel.name != "terminal-master": return
    await ctx.send("**💸 Gratuitas:** Groq, Together, Google AI Studio, OpenRouter (:free).")

@api_group.command(name="scan_paid_apis")
async def cmd_scan_paid_apis(ctx):
    if ctx.channel.name != "terminal-master": return
    await ctx.send("**💳 Premium:** Anthropic, OpenAI, Mistral.")

@api_group.command(name="ping_provider")
async def cmd_ping_provider(ctx, url: str):
    if ctx.channel.name != "terminal-master": return
    try:
        start = time.time()
        requests.get(url.split("/v1")[0] if "/v1" in url else url, timeout=5)
        await ctx.send(f"🟢 Latência: `{round((time.time() - start) * 1000)}ms`.")
    except: await ctx.send("🔴 Servidor inatingível.")

@api_group.command(name="compare_speed")
async def cmd_compare_speed(ctx, u1: str, e1: str, u2: str, e2: str, m1: str, m2: str):
    if ctx.channel.name != "terminal-master": return
    await ctx.send("🏁 Em desenvolvimento. Use provider_health.")

@api_group.command(name="check_credits")
async def cmd_check_credits(ctx, env_name: str):
    if ctx.channel.name != "terminal-master": return
    key = get_api_key_from_vault(env_name)
    try:
        r = requests.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200:
            d = r.json().get("data", {})
            await ctx.send(f"💰 Limite: `{d.get('limit')}` | Uso: `{d.get('usage')}`")
    except: await ctx.send("🔴 Falha na verificação.")

@api_group.command(name="global_radar")
async def cmd_global_radar(ctx, termo: str = "all", limite: int = 5):
    """Busca APIs mundiais (Fatiado para suportar +100 itens)."""
    if ctx.channel.name != "terminal-master": return
    msg_status = await ctx.send(f"🌐 **[RADAR]** Mapeando rede global para `{termo.upper()}`...")
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
        if r.status_code == 200:
            data = r.json().get('data', [])
            if termo.lower() != "all":
                data = [m for m in data if termo.lower() in m.get('id', '').lower() or termo.lower() in m.get('name', '').lower()]

            if not data:
                await msg_status.edit(content=f"⚠️ Nada encontrado para `{termo}`.")
                return

            alvos = data[:limite]
            await msg_status.delete()

            for i in range(0, len(alvos), 10):
                chunk = alvos[i:i+10]
                embed = discord.Embed(title=f"🌍 APIs Detectadas ({i+1} a {i+len(chunk)})", color=0x10a37f)
                for m in chunk:
                    pricing = m.get('pricing') or {}
                    p_prompt = float(pricing.get('prompt') or 0) * 1000000
                    custo = "🟢 GRATUITO" if p_prompt == 0 else f"💳 PAGO (~${p_prompt:.2f}/1M tokens)"
                    info = f"**ID:** `{m.get('id')}`\n**Max:** `{m.get('context_length')}`\n**Custo:** {custo}"
                    embed.add_field(name=f"🤖 {m.get('name')}", value=info, inline=False)
                await ctx.send(embed=embed)
        else:
            await msg_status.edit(content="🔴 Erro de sincronismo.")
    except Exception as e:
        await msg_status.edit(content=f"🔴 Colapso no Radar: {e}")

@api_group.command(name="auto_inject")
async def cmd_auto_inject(ctx, nv: str, mid: str, url: str, env: str, tr: str = "free"):
    if ctx.channel.name != "terminal-master": return
    exec_db_query("INSERT INTO ias (nome, model, url, env, tier_req) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (nome) DO UPDATE SET model=EXCLUDED.model", (nv, mid, url, env, tr))
    await ctx.send(f"🔌 Injeção OSINT: `{nv}` adicionada ao tier `{tr}`.")

@api_group.command(name="hunt_models")
async def cmd_hunt_models(ctx, termo: str = "llm", limite: int = 5):
    if ctx.channel.name != "terminal-master": return
    msg = await ctx.send(f"🔄 Varrendo Hub por `{termo}`...")
    try:
        r = requests.get(f"https://huggingface.co/api/models?search={termo}&limit={limite}&sort=downloads&direction=-1", timeout=15)
        if r.status_code == 200 and r.json():
            embed = discord.Embed(title=f"🎯 Alvos: {termo.upper()}", color=0x8a2be2)
            for m in r.json(): embed.add_field(name=m.get('id'), value=f"⬇️ {m.get('downloads'):,} dl", inline=False)
            await msg.delete(); await ctx.send(embed=embed)
    except: await msg.edit(content="🔴 Erro.")

@api_group.command(name="report")
async def cmd_report(ctx, formato: str = "txt"):
    if ctx.channel.name != "terminal-master": return
    await ctx.send("⚙️ Em desenvolvimento para o Cloud Storage.")

@api_group.command(name="spy_model")
async def cmd_spy_model(ctx, *, termo: str):
    """Extrai metadados de forma inteligente."""
    if ctx.channel.name != "terminal-master": return
    msg = await ctx.send(f"🔍 Rastreando `{termo}`...")
    try:
        r = requests.get(f"https://huggingface.co/api/models?search={termo}&limit=1&sort=downloads&direction=-1", timeout=10)
        if r.status_code == 200 and r.json():
            model_id = r.json()[0]['id']
            r2 = requests.get(f"https://huggingface.co/api/models/{model_id}", timeout=10)
            d = r2.json()
            tags = d.get('tags', [])
            lic = next((t.replace('license:', '') for t in tags if t.startswith('license:')), 'Desconhecida')
            idiomas = [t for t in tags if len(t) == 2 and t.islower()]
            embed = discord.Embed(title=f"🧠 Intel: {model_id}", color=0x3b82f6)
            embed.add_field(name="Org", value=d.get('author', 'N/A'), inline=True)
            embed.add_field(name="Downloads", value=f"{d.get('downloads', 0):,}", inline=True)
            embed.add_field(name="Licença", value=lic.upper(), inline=True)
            embed.add_field(name="Idiomas", value=", ".join(idiomas).upper() if idiomas else "Geral", inline=True)
            await msg.delete(); await ctx.send(embed=embed)
        else: await msg.edit(content="🔴 Alvo não encontrado.")
    except: await msg.edit(content="🔴 Erro.")

@api_group.command(name="top_trending")
async def cmd_top_trending(ctx, limite: int = 5):
    if ctx.channel.name != "terminal-master": return
    try:
        r = requests.get(f"https://huggingface.co/api/models?sort=trending_score&direction=-1&limit={limite}", timeout=10)
        msg = "**🔥 TRENDING:**\n"
        for i, m in enumerate(r.json(), 1): msg += f"{i}. `{m['id']}`\n"
        await ctx.send(msg)
    except: await ctx.send("🔴 Erro.")

@api_group.command(name="provider_health")
async def cmd_provider_health(ctx, url: str):
    if ctx.channel.name != "terminal-master": return
    try:
        start = time.time(); requests.get(url, timeout=5, verify=True)
        await ctx.send(f"🟢 Saudável | Ping: `{round((time.time()-start)*1000)}ms`")
    except: await ctx.send("🔴 Falha no SSL ou TimeOut.")

@api_group.command(name="lock_route")
async def cmd_lock_route(ctx, nome_ia: str):
    if ctx.channel.name != "terminal-master": return
    exec_db_query("UPDATE ias SET tier_req = 'ultra' WHERE nome = %s", (nome_ia,))
    await ctx.send(f"🔒 **[LIVE]** `{nome_ia}` travada em 2s.")

@api_group.command(name="unlock_route")
async def cmd_unlock_route(ctx, nome_ia: str, tier: str = "free"):
    if ctx.channel.name != "terminal-master": return
    exec_db_query("UPDATE ias SET tier_req = %s WHERE nome = %s", (tier, nome_ia))
    await ctx.send(f"🔓 **[LIVE]** `{nome_ia}` liberada para {tier.upper()}.")

@api_group.command(name="clone_route")
async def cmd_clone_route(ctx, orig: str, novo: str):
    if ctx.channel.name != "terminal-master": return
    l = exec_db_query("SELECT * FROM ias WHERE nome = %s", (orig,), fetch=True)
    if l:
        ia = l[0]
        exec_db_query("INSERT INTO ias (nome, model, url, env, prompt, tier_req) VALUES (%s, %s, %s, %s, %s, %s)", (novo, ia['model'], ia['url'], ia['env'], ia['prompt'], ia['tier_req']))
        await ctx.send(f"🧬 Clonado: `{orig}` -> `{novo}`")

@api_group.command(name="audit_op")
async def cmd_audit_op(ctx, login: str):
    """Puxa a ficha de consumo de IA de um usuário."""
    if ctx.channel.name != "terminal-master": return
    uso = exec_db_query("SELECT msg_count, last_reset FROM user_usage WHERE login = %s", (login,), fetch=True)
    user = exec_db_query("SELECT tier, status FROM users WHERE login = %s", (login,), fetch=True)
    if not user:
        await ctx.send("🔴 Operador não existe.")
        return
    msgs = uso[0]['msg_count'] if uso else 0
    embed = discord.Embed(title=f"👤 Dossiê: {login}", color=0x10a37f)
    embed.add_field(name="Nível", value=f"`{user[0]['tier'].upper()}`", inline=True)
    embed.add_field(name="Consumo Web", value=f"`{msgs}` msgs", inline=True)
    await ctx.send(embed=embed)

@api_group.command(name="force_reset")
async def cmd_force_reset(ctx, login: str):
    if ctx.channel.name != "terminal-master": return
    exec_db_query("UPDATE user_usage SET msg_count = 0 WHERE login = %s", (login,))
    await ctx.send(f"🔄 Limite de `{login}` zerado.")

@api_group.command(name="key_info")
async def cmd_key_info(ctx, env_name: str):
    if ctx.channel.name != "terminal-master": return
    db = exec_db_query("SELECT api_key FROM api_vault WHERE env_name = %s", (env_name,), fetch=True)
    chave = db[0]['api_key'] if db else os.environ.get(env_name)
    ori = "Banco de Dados" if db else "Servidor Raiz (.env)"
    if chave: await ctx.send(f"🔑 **Cofre:** `{env_name}`\nOrigem: `{ori}`\nTamanho: `{len(chave)}`\nPrefixo: `{chave[:5]}***`")
    else: await ctx.send("🔴 Vazio.")

@api_group.command(name="nuke_vault")
async def cmd_nuke_vault(ctx, c: str = ""):
    if ctx.channel.name != "terminal-master": return
    if c != "CONFIRMAR": await ctx.send("⚠️ Digite !api nuke_vault CONFIRMAR")
    else:
        exec_db_query("TRUNCATE TABLE api_vault")
        await ctx.send("☢️ Cofre purgado.")

@api_group.command(name="del_route")
async def cmd_del_route(ctx, nome_ia: str):
    if ctx.channel.name != "terminal-master": return
    res = exec_db_query("DELETE FROM ias WHERE nome = %s", (nome_ia,))
    if res and res > 0: await ctx.send(f"🗑️ `{nome_ia}` removida do Live Sync.")
    else: await ctx.send("⚠️ IA não achada.")

# ==========================================
# 5. MIDDLEWARES FLASK (PROTEÇÃO DE ROTAS)
# ==========================================
def token_req(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header: return jsonify({'erro': 'Token ausente.'}), 401
        try: 
            token = auth_header.split(" ")[1]
            request.user_data = jwt.decode(token, app.secret_key, algorithms=['HS256'])
        except: return jsonify({'erro': 'Sessão Expirada.'}), 401
        return f(*args, **kwargs)
    return decorated_function

def api_key_req(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        chave_api = request.headers.get('X-API-KEY')
        if not chave_api: return jsonify({'erro': 'X-API-KEY ausente.'}), 401
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT user_login FROM api_keys WHERE key_val = %s", (chave_api,))
            chave_db = cur.fetchone()
            if not chave_db: return jsonify({'erro': 'Chave Inválida.'}), 403
            cur.execute("SELECT * FROM users WHERE login = %s", (chave_db['user_login'],))
            usuario_db = dict(cur.fetchone())
            usuario_db['user'] = usuario_db['login'] 
            request.user_data = usuario_db
            cur.close()
            conn.close()
        except: return jsonify({'erro': 'Erro no Cofre.'}), 500
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 6. HTML, CSS, JAVASCRIPT (FRONT-END MONOLITH)
# ==========================================

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{{ sys_name }} - Gateway de Acesso</title>
    
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;800&display=swap');
        
        :root { 
            --bg: #050505; 
            --card: #0a0a0a; 
            --accent: #8a2be2; 
            --text: #ffffff; 
            --border: #1f1f1f; 
        } 
        
        * { 
            box-sizing: border-box; 
            margin: 0; 
            padding: 0;
        } 
        
        body { 
            background: var(--bg); 
            color: var(--text); 
            font-family: 'Inter', sans-serif; 
            display: flex; 
            justify-content: center; 
            align-items: center; 
            height: 100dvh; 
            background-image: radial-gradient(circle at 50% 0%, rgba(138,43,226,0.15), transparent 50%); 
            overflow: hidden;
        } 
        
        .box { 
            background: rgba(10,10,10,0.85); 
            backdrop-filter: blur(25px); 
            padding: 45px 40px; 
            border-radius: 24px; 
            width: 90%; 
            max-width: 440px; 
            text-align: center; 
            border: 1px solid rgba(255,255,255,0.08); 
            box-shadow: 0 40px 80px rgba(0,0,0,0.9); 
            position: relative;
        } 
        
        .logo { 
            font-size: 34px; 
            font-weight: 800; 
            margin-bottom: 35px; 
            letter-spacing: -1.5px; 
            background: linear-gradient(135deg, #ffffff 40%, var(--accent)); 
            -webkit-background-clip: text; 
            -webkit-text-fill-color: transparent; 
        } 
        
        .tabs { 
            display: flex; 
            background: rgba(255,255,255,0.03); 
            border-radius: 12px; 
            padding: 6px; 
            margin-bottom: 25px; 
            position: relative;
        }
        
        .tab { 
            flex: 1; 
            padding: 12px; 
            cursor: pointer; 
            color: #888; 
            font-weight: 600; 
            font-size: 14px; 
            z-index: 2; 
            transition: color 0.3s ease; 
            border-radius: 8px;
        }
        
        .tab.active { 
            color: #fff; 
        }
        
        .tab-bg { 
            position: absolute; 
            top: 6px; 
            bottom: 6px; 
            width: calc(50% - 6px); 
            background: #1f1f1f; 
            border-radius: 8px; 
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
            z-index: 1;
        }
        
        input { 
            width: 100%; 
            background: #000; 
            border: 1px solid var(--border); 
            color: #fff; 
            padding: 16px; 
            border-radius: 12px; 
            margin-bottom: 16px; 
            font-size: 15px; 
            outline: none; 
            transition: all 0.3s ease;
        } 
        
        input:focus { 
            border-color: var(--accent); 
            box-shadow: 0 0 0 3px rgba(138,43,226,0.25);
        }
        
        button.btn { 
            width: 100%; 
            background: var(--accent); 
            color: #fff; 
            border: none; 
            padding: 16px; 
            border-radius: 12px; 
            font-weight: 700; 
            font-size: 15px; 
            cursor: pointer; 
            transition: all 0.2s ease;
            letter-spacing: 0.5px;
        } 
        
        button.btn:hover { 
            filter: brightness(1.15); 
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(138,43,226,0.3);
        }
        
        button.btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        
        .divider { 
            margin: 30px 0; 
            color: #555; 
            font-size: 11px; 
            font-weight: 700; 
            text-transform: uppercase; 
            display: flex; 
            align-items: center; 
            letter-spacing: 1px;
        } 
        
        .divider::before, .divider::after { 
            content: ""; 
            flex: 1; 
            border-bottom: 1px solid var(--border); 
            margin: 0 15px; 
        }
        
        .g_id_signin { 
            display: flex; 
            justify-content: center; 
        }
        
        .msg { 
            margin-top: 20px; 
            font-size: 14px; 
            font-weight: 500; 
            min-height: 20px; 
            transition: opacity 0.3s;
        } 
        
        .error { color: #ef4444; } 
        .success { color: #10a37f; }
        
        .tier-info {
            text-align: left; 
            font-size: 12px; 
            color: #888; 
            margin-bottom: 12px;
            padding: 0 5px;
        }
    </style>
</head>
<body>
    <div id="app" class="box">
        <div class="logo">{{ sys_name|upper }}</div>
        
        {% raw %}
        <div class="tabs">
            <div class="tab-bg" :style="{ transform: mode === 'login' ? 'translateX(0)' : 'translateX(100%)' }"></div>
            <div class="tab" :class="{active: mode==='login'}" @click="mode='login'">Acesso</div>
            <div class="tab" :class="{active: mode==='register'}" @click="mode='register'">Novo Operador</div>
        </div>
        
        <div v-show="mode==='register'" class="tier-info">
            Licença Padrão Atribuída: <b style="color:#10a37f;">FREE</b>
        </div>
        
        <input v-model="email" type="email" placeholder="E-mail Operacional" autocomplete="email">
        <input v-model="senha" type="password" placeholder="Chave de Criptografia" @keydown.enter="auth">
        
        <button class="btn" @click="auth" :disabled="loading">
            {{ mode === 'login' ? 'INICIAR SESSÃO NA MATRIZ' : 'FORJAR IDENTIDADE' }}
        </button>
        
        <div class="msg" :class="msgType">{{ mensagem }}</div>
        {% endraw %}
        
        <div class="divider">Autorização Externa (SSO)</div>
        
        <div id="g_id_onload" 
             data-client_id="{{ GOOGLE_CLIENT_ID }}" 
             data-context="signin" 
             data-ux_mode="popup" 
             data-callback="handleCredentialResponse" 
             data-auto_prompt="false">
        </div>
        <div class="g_id_signin" 
             data-type="standard" 
             data-shape="pill" 
             data-theme="filled_black" 
             data-text="continue_with" 
             data-size="large" 
             data-logo_alignment="left">
        </div>
    </div>
    
    <script>
        const vueApp = Vue.createApp({
            data() { 
                return { 
                    mode: 'login', 
                    email: '', 
                    senha: '', 
                    mensagem: '', 
                    msgType: '', 
                    loading: false 
                } 
            },
            methods: {
                async auth() {
                    if(!this.email || !this.senha) { 
                        this.mostrarMsg('Campos de acesso incompletos.', 'error'); 
                        return; 
                    }
                    
                    this.loading = true; 
                    const endpoint = this.mode === 'login' ? '/api/login' : '/api/register';
                    
                    try {
                        const res = await fetch(endpoint, { 
                            method: 'POST', 
                            headers: {'Content-Type': 'application/json'}, 
                            body: JSON.stringify({ login: this.email, senha: this.senha }) 
                        });
                        
                        const d = await res.json();
                        
                        if(res.ok && d.sucesso) { 
                            this.mostrarMsg('Credencial Verificada. Descriptografando interface...', 'success'); 
                            localStorage.setItem('sys_jwt', d.token); 
                            setTimeout(() => { window.location.href = "/chat"; }, 1200); 
                        } else { 
                            this.mostrarMsg(d.erro || 'O servidor recusou a credencial.', 'error'); 
                        }
                    } catch(e) { 
                        this.mostrarMsg('Falha de Roteamento. Link quebrado.', 'error'); 
                    }
                    
                    this.loading = false;
                },
                mostrarMsg(txt, tipo) { 
                    this.mensagem = txt; 
                    this.msgType = tipo; 
                }
            }
        }).mount('#app');
        
        // Callback nativo do Google
        function handleCredentialResponse(response) {
            fetch('/api/auth/google', { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify({ token: response.credential }) 
            })
            .then(res => res.json())
            .then(d => { 
                if(d.sucesso) { 
                    localStorage.setItem('sys_jwt', d.token); 
                    window.location.href = "/chat"; 
                } else { 
                    alert("Acesso Negado via Google: " + d.erro); 
                }
            });
        }
    </script>
</body>
</html>
"""

HTML_CHAT = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{{ sys_name }} - Chat Operacional</title>
    
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        
        :root { 
            --bg: #101010; 
            --side: #0a0a0a; 
            --bubble-usr: #222222; 
            --text: #ececec; 
            --accent: #8a2be2; 
        } 
        
        * { 
            box-sizing: border-box; 
            margin: 0; 
            padding: 0; 
        } 
        
        ::-webkit-scrollbar { width: 6px; height: 6px; } 
        ::-webkit-scrollbar-track { background: transparent; } 
        ::-webkit-scrollbar-thumb { background: #444; border-radius: 6px; } 
        
        body { 
            background: var(--bg); 
            color: var(--text); 
            font-family: 'Inter', sans-serif; 
            display: flex; 
            height: 100dvh; 
            width: 100vw; 
            overflow: hidden; 
            position: fixed; 
            inset: 0;
        } 
        
        /* ---------------- SIDEBAR ---------------- */
        .sidebar { 
            width: 280px; 
            height: 100%; 
            background: var(--side); 
            border-right: 1px solid #222; 
            display: flex; 
            flex-direction: column; 
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
            z-index: 100; 
            flex-shrink: 0;
        } 
        
        .brand-header { 
            padding: 24px 20px; 
            border-bottom: 1px solid #1a1a1a; 
            display: flex; 
            align-items: center; 
            gap: 15px; 
            background: linear-gradient(180deg, #161616, transparent);
        }
        
        .brand-avatar { 
            width: 44px; 
            height: 44px; 
            border-radius: 12px; 
            background: #000; 
            border: 1px solid #333; 
            display: flex; 
            justify-content: center; 
            align-items: center; 
            font-size: 22px; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.6);
        }
        
        .side-actions { 
            padding: 20px 15px; 
            display: flex; 
            flex-direction: column; 
            gap: 12px;
        }
        
        .action-btn { 
            width: 100%; 
            background: transparent; 
            color: var(--text); 
            border: 1px solid #333; 
            padding: 14px; 
            border-radius: 12px; 
            font-weight: 500; 
            font-size: 14px; 
            cursor: pointer; 
            text-align: left; 
            transition: all 0.2s ease;
        }
        
        .action-btn:hover { 
            background: #1a1a1a; 
            border-color: #555; 
            transform: translateX(4px);
        }
        
        .master-btn { 
            background: linear-gradient(45deg, #8a2be2, #ef4444); 
            color: #fff; 
            padding: 14px; 
            border-radius: 12px; 
            text-align: center; 
            font-weight: bold; 
            text-decoration: none; 
            font-size: 14px; 
            box-shadow: 0 4px 20px rgba(239, 68, 68, 0.2); 
            transition: all 0.2s ease;
        }
        
        .master-btn:hover { 
            filter: brightness(1.2); 
            transform: translateY(-2px);
        }
        
        /* ---------------- CHAT MAIN ---------------- */
        .chat-main { 
            flex: 1; 
            display: flex; 
            flex-direction: column; 
            position: relative; 
            min-width: 0; 
            height: 100%; 
            background: #101010;
        } 
        
        .topbar { 
            position: absolute; 
            top: 0; 
            left: 0; 
            right: 0; 
            padding: 16px 20px; 
            display: flex; 
            align-items: center; 
            justify-content: space-between; 
            z-index: 50; 
            background: rgba(16, 16, 16, 0.75); 
            backdrop-filter: blur(24px); 
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        
        .custom-dropdown { position: relative; }
        
        .dropdown-btn { 
            display: flex; 
            align-items: center; 
            gap: 10px; 
            background: rgba(255,255,255,0.05); 
            border: 1px solid rgba(255,255,255,0.1); 
            color: var(--text); 
            font-size: 14px; 
            font-weight: 600; 
            cursor: pointer; 
            padding: 10px 16px; 
            border-radius: 12px; 
            transition: background 0.2s;
        }
        
        .dropdown-btn:hover { background: rgba(255,255,255,0.1); }
        
        .dropdown-menu { 
            position: absolute; 
            top: calc(100% + 8px); 
            left: 0; 
            background: rgba(20,20,22,0.95); 
            backdrop-filter: blur(20px); 
            border: 1px solid #333; 
            border-radius: 16px; 
            width: 290px; 
            box-shadow: 0 20px 40px rgba(0,0,0,0.8); 
            z-index: 200; 
            padding: 8px; 
        }
        
        .dropdown-item { 
            display: flex; 
            align-items: center; 
            justify-content: space-between; 
            padding: 12px; 
            cursor: pointer; 
            border-radius: 10px; 
            transition: all 0.2s;
        }
        
        .dropdown-item:hover { 
            background: rgba(138,43,226,0.15); 
            border-left: 3px solid var(--accent); 
        }
        
        .chat-history { 
            flex: 1; 
            overflow-y: auto; 
            padding: 90px 20px 20px 20px; 
            display: flex; 
            flex-direction: column; 
            gap: 24px; 
            scroll-behavior: smooth; 
        } 
        
        .msg-row { 
            display: flex; 
            width: 100%; 
            max-width: 850px; 
            margin: 0 auto; 
            gap: 18px; 
        } 
        
        .bubble { 
            flex: 1; 
            line-height: 1.6; 
            font-size: 15px; 
            overflow-wrap: break-word; 
        } 
        
        .user { justify-content: flex-end; } 
        .user .bubble { 
            background: var(--bubble-usr); 
            padding: 14px 20px; 
            border-radius: 20px; 
            border-bottom-right-radius: 4px; 
            max-width: 85%; 
            flex: none; 
            border: 1px solid #2a2a2a;
        } 
        
        pre { 
            background: #000; 
            padding: 16px; 
            border-radius: 12px; 
            margin: 12px 0; 
            border: 1px solid #333; 
            overflow-x: auto; 
            font-size: 13px;
        }
        
        p { margin-bottom: 10px; } 
        code { background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 4px;}
        
        /* ---------------- INPUT AREA ---------------- */
        .input-wrapper { 
            padding: 0 20px 20px 20px; 
            display: flex; 
            flex-direction: column; 
            align-items: center; 
            background: linear-gradient(0deg, var(--bg) 85%, transparent); 
        } 
        
        .input-box { 
            width: 100%; 
            max-width: 850px; 
            background: #161616; 
            border-radius: 20px; 
            padding: 10px 15px; 
            display: flex; 
            flex-direction: column; 
            border: 1px solid #333; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.5); 
            transition: all 0.3s ease;
        } 
        
        .input-box:focus-within { 
            border-color: #555; 
            background: #1a1a1a; 
            box-shadow: 0 10px 30px rgba(138,43,226,0.15);
        }
        
        textarea { 
            flex: 1; 
            background: transparent; 
            border: none; 
            color: var(--text); 
            resize: none; 
            outline: none; 
            min-height: 24px; 
            max-height: 200px; 
            font-family: inherit; 
            font-size: 15px; 
            padding: 10px 0;
        } 
        
        /* ---------------- MODALS & UI ---------------- */
        .modal { 
            position: fixed; 
            top: 50%; 
            left: 50%; 
            transform: translate(-50%, -50%); 
            background: #111; 
            padding: 30px; 
            border-radius: 20px; 
            border: 1px solid #333; 
            z-index: 300; 
            width: 90%; 
            max-width: 480px; 
            display: none; 
            flex-direction: column; 
            gap: 20px; 
            box-shadow: 0 30px 60px rgba(0,0,0,0.9);
        }
        
        .modal.show { display: flex; animation: fadeIn 0.2s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translate(-50%, -48%); } to { opacity: 1; transform: translate(-50%, -50%); } }
        
        .api-card { 
            background: #000; 
            border: 1px solid #222; 
            padding: 16px; 
            border-radius: 12px; 
            margin-bottom: 10px;
        }
        
        .tier-badge { 
            font-size: 10px; 
            padding: 4px 8px; 
            border-radius: 6px; 
            font-weight: 800; 
            text-transform: uppercase; 
            letter-spacing: 0.5px;
        }
        
        .tier-free { background: #333; color: #aaa; } 
        .tier-plus { background: rgba(44, 82, 130, 0.3); color: #90cdf4; border: 1px solid #2c5282; } 
        .tier-pro { background: rgba(138,43,226,0.2); color: #d8b4fe; border: 1px solid var(--accent); } 
        .tier-ultra { background: rgba(255, 140, 0, 0.2); color: #ffd700; border: 1px solid #ff8c00; }
        
        .hamburger-btn { display: none; background: none; border: none; color: #fff; font-size: 26px; cursor: pointer; padding: 5px;}
        
        @media (max-width: 768px) { 
            .sidebar { position: absolute; height: 100dvh; transform: translateX(-100%); } 
            .sidebar.open { transform: translateX(0); box-shadow: 10px 0 40px rgba(0,0,0,0.9); } 
            .dropdown-menu { position: fixed; top: 75px; left: 50%; transform: translateX(-50%); width: 92vw; } 
            .hamburger-btn { display: block !important; } 
        }
    </style>
</head>
<body>
    <div id="app" style="display:flex; width:100%; height: 100dvh;">
        
        <div class="overlay" v-if="menuOpen || showModal || showAPIModal" @click="menuOpen=false; showModal=false; showAPIModal=false; dropdownOpen=false" style="position:fixed; inset:0; background:rgba(0,0,0,0.85); backdrop-filter: blur(5px); z-index:90;"></div>
        
        {% raw %}
        <div class="modal" :class="{ show: showModal }">
            <h2 style="margin:0; font-size:22px; display:flex; justify-content:space-between; align-items:center;">
                Credencial Operacional
                <span :class="'tier-badge tier-'+(isAdmin ? 'ultra' : userTier)">{{ isAdmin ? 'MASTER' : userTier }}</span>
            </h2>
            <div style="background: #1a1a1a; padding: 16px; border-radius: 12px; border: 1px solid #2a2a2a; font-size: 14px; color: #bbb; line-height: 1.6;">
                {{ isAdmin ? '⚠️ Autoridade Máxima Detectada. Limites físicos ignorados.' : 'Seus limites estão sincronizados.' }}
            </div>
            <button @click="showModal=false" style="background:#fff; color:#000; border:none; padding:14px; border-radius:12px; font-weight:bold; cursor:pointer;">Concluir</button>
        </div>

        <div class="modal" :class="{ show: showAPIModal }">
            <h2 style="margin:0; font-size:20px; display:flex; align-items:center; gap:10px;">🔌 Cofre de API</h2>
            <p style="font-size:13px; color:#888; margin:0;">Gere chaves blindadas para acessar a Matriz via Terminal.</p>
            <button @click="gerarNovaChave" style="background:var(--accent); color:#fff; border:none; padding:15px; border-radius:12px; font-weight:bold; cursor:pointer;">+ Forjar Nova Chave</button>
            
            <div style="max-height: 240px; overflow-y: auto; padding-right: 5px;">
                <div v-for="k in apiKeys" :key="k.id" class="api-card">
                    <div style="display:flex; justify-content:space-between; font-size:11px; color:#777; margin-bottom:10px; font-weight:700;">
                        <span>ROTA EXTERNA ATIVA</span>
                        <span @click="revogarChave(k.id)" style="color:#ef4444; cursor:pointer;">[REVOGAR ACESSO]</span>
                    </div>
                    <div style="background:#111; padding:12px; border-radius:8px; border:1px solid #333; display:flex; justify-content:space-between; align-items:center;">
                        <code style="font-size:13px; color:var(--accent); word-break: break-all;">{{ k.key }}</code>
                        <button @click="copy(k.key)" title="Copiar" style="background:none; border:none; color:#fff; cursor:pointer; font-size:18px;">📋</button>
                    </div>
                </div>
            </div>
            <button @click="showAPIModal=false" style="background:#222; color:#fff; border:none; padding:14px; border-radius:12px; font-weight:bold; cursor:pointer;">Fechar</button>
        </div>

        <div class="sidebar" :class="{ open: menuOpen }">
            <div class="brand-header">
                <div class="brand-avatar">🕴️</div>
                <div>
                    <div style="font-weight:800; font-size:17px; color:#fff;">SYZYGY</div>
                    <div style="font-size:10px; font-weight:700; color:var(--accent); letter-spacing:1.5px;">EQUIPE MÁFIA</div>
                </div>
            </div>
            
            <div class="side-actions">
                <button class="action-btn" @click="clearChat">✨ Nova Transmissão</button>
                <button class="action-btn" @click="showAPIModal=true">🔌 Cofre Rest Externo</button>
                <a v-if="isAdmin" href="/admin" class="master-btn">⚙️ ACESSO MASTER MONOPOLY</a>
            </div>
            
            <div style="flex:1;"></div>
            
            <div style="padding:20px; border-top:1px solid #1a1a1a;">
                <div @click="showModal=true" style="background:#111; padding:14px 15px; border-radius:12px; display:flex; justify-content:space-between; align-items:center; cursor:pointer; border:1px solid #222;">
                    <div>
                        <div style="font-size:10px; color:#666; font-weight:700; text-transform:uppercase;">Identidade</div>
                        <div style="font-size:14px; font-weight:bold; color:#fff;">Operador</div>
                    </div>
                    <span :class="'tier-badge tier-'+(isAdmin ? 'ultra' : userTier)">{{ isAdmin ? 'MASTER' : userTier }}</span>
                </div>
            </div>
        </div>
        
        <div class="chat-main">
            <div class="topbar">
                <div style="display:flex; align-items:center; gap:15px;">
                    <button @click="menuOpen=!menuOpen" class="hamburger-btn">☰</button>
                    <div class="custom-dropdown">
                        <button class="dropdown-btn" @click="dropdownOpen = !dropdownOpen" v-if="currentIA">
                            <span style="font-size:12px; color:#10a37f;">🔒</span>
                            <img :src="getLogo(ias[currentIA]?.model)" width="20" style="border-radius:4px; background:#fff; padding:2px;"> 
                            {{ currentIA }} ▾
                        </button>
                        
                        <div class="dropdown-menu" v-if="dropdownOpen">
                            <div style="padding: 10px 12px 6px; font-size: 10px; color: #666; font-weight: bold; text-transform: uppercase;">Matriz Neuronal (Live)</div>
                            <div class="dropdown-item" v-for="(ia, key) in ias" :key="key" @click="selectIA(key)">
                                <div style="display:flex; align-items:center; gap:12px;">
                                    <img :src="getLogo(ia.model)" width="24" style="border-radius:6px; background:#fff; padding:3px;">
                                    <div>
                                        <span style="font-size:14px; font-weight:600; display:block; color:#fff;">{{ key }}</span>
                                        <span style="font-size:11px; color:#777;">{{ ia.model.split('/').pop() }}</span>
                                    </div>
                                </div>
                                <span :class="'tier-badge tier-'+ia.tier_req">{{ ia.tier_req }}</span>
                            </div>
                        </div>
                    </div>
                </div>
                <div></div>
            </div>
            
            <div class="chat-history" ref="chatBox">
                <div v-if="messages.length === 0" style="display:flex; flex-direction:column; justify-content:center; align-items:center; height:100%; gap:20px; opacity:0.7;">
                    <img src="https://upload.wikimedia.org/wikipedia/commons/0/04/ChatGPT_logo.svg" width="70" style="filter: brightness(0) invert(1); opacity:0.15;">
                    <h1 style="font-size:36px; font-weight:800; letter-spacing:-1.5px; margin:0;">Conexão Estabelecida.</h1>
                    <p style="font-size:14px; color:#666;">Sistema Multi-Provider (OSINT) operacional e aguardando.</p>
                </div>
                
                <div v-for="(msg, idx) in messages" :key="idx" :class="['msg-row', msg.role]">
                    <img v-if="msg.role === 'ia'" :src="getLogo(ias[currentIA]?.model)" style="width:36px; height:36px; border-radius:10px; background:#fff; padding:4px; flex-shrink:0; box-shadow:0 4px 10px rgba(0,0,0,0.5);">
                    <div class="bubble" v-html="msg.parsed"></div>
                </div>
            </div>
            
            <div class="input-wrapper">
                <div class="input-box">
                    <div style="display:flex; align-items:flex-end; gap:12px;">
                        <input type="file" id="fileup" style="display:none;" @change="handleUpload" accept=".txt,.md,.pdf">
                        <button @click="triggerUp" title="Anexar Documento" style="background:none; border:none; color:#888; font-size:24px; cursor:pointer; padding:6px; transition:0.2s;">📎</button>
                        
                        <textarea v-model="promptText" placeholder="Insira o comando tático..." @keydown.enter.exact.prevent="send"></textarea>
                        
                        <button @click="send" :disabled="isTyping" style="background:#fff; color:#000; border:none; width:40px; height:40px; border-radius:12px; font-weight:bold; font-size:18px; cursor:pointer; display:flex; justify-content:center; align-items:center; transition:0.2s;">↑</button>
                    </div>
                </div>
                <div style="font-size:11px; color:#444; margin-top:12px; font-weight:500;">Sincronismo Silencioso Ativo. Conexão End-to-End.</div>
            </div>
        </div>
        {% endraw %}
    </div>
    
    <script>
        const { createApp, ref, onMounted, onUnmounted } = Vue;
        
        createApp({
            setup() {
                const ias = ref({}); 
                const messages = ref([]); 
                const currentIA = ref(""); 
                const promptText = ref(""); 
                
                const isAdmin = ref(false); 
                const userTier = ref("free"); 
                const apiKeys = ref([]); 
                
                const menuOpen = ref(false);
                const dropdownOpen = ref(false);
                const showModal = ref(false); 
                const showAPIModal = ref(false); 
                
                const fileContext = ref("");
                const isTyping = ref(false);
                const chatBox = ref(null);
                
                let syncInterval = null;

                const getAuth = () => { return { 'Authorization': 'Bearer ' + localStorage.getItem('sys_jwt') }; };
                const parseJwt = (token) => { try { return JSON.parse(atob(token.split('.')[1])); } catch (e) { return null; } };

                const carregar = async () => {
                    const tk = localStorage.getItem('sys_jwt'); 
                    if(!tk) { window.location.href = '/'; return; }
                    
                    const p = parseJwt(tk); 
                    if(p) { isAdmin.value = p.is_admin; userTier.value = p.tier || 'free'; }
                    
                    try {
                        const res = await fetch('/api/init', { headers: getAuth() });
                        if(res.status === 401) throw new Error("Sessão Expirada");
                        
                        const d = await res.json(); 
                        ias.value = d.ias; 
                        
                        if (Object.keys(d.ias).length > 0 && !currentIA.value) {
                            currentIA.value = Object.keys(d.ias)[0];
                        }
                        
                        carregarAPIKeys();
                    } catch(e) { window.location.href = '/'; }
                };
                
                const carregarSilencioso = async () => {
                    try {
                        const res = await fetch('/api/init', { headers: getAuth() });
                        if(res.ok) {
                            const d = await res.json();
                            ias.value = d.ias;
                            // Previne o chat quebrar se a IA atual for deletada pelo admin
                            if(currentIA.value && !d.ias[currentIA.value]) {
                                currentIA.value = Object.keys(d.ias)[0] || "";
                            }
                        }
                    } catch(e) {}
                };
                
                const carregarAPIKeys = async () => {
                    try {
                        const res = await fetch('/api/user/keys', { headers: getAuth() });
                        const d = await res.json(); apiKeys.value = d.keys;
                    } catch (e) {}
                };
                
                const gerarNovaChave = async () => { await fetch('/api/user/keys', { method: 'POST', headers: getAuth() }); carregarAPIKeys(); };
                const revogarChave = async (id) => { await fetch(`/api/user/keys?id=${id}`, { method: 'DELETE', headers: getAuth() }); carregarAPIKeys(); };
                const copy = (texto) => { navigator.clipboard.writeText(texto); alert("Chave copiada para a Área de Transferência."); };
                
                const getLogo = (m) => {
                    if(!m) return 'https://upload.wikimedia.org/wikipedia/commons/0/04/ChatGPT_logo.svg'; 
                    const mod = m.toLowerCase();
                    if(mod.includes('gemini')) return 'https://www.gstatic.com/lamda/images/gemini_sparkle_v002_d4735304ff6292a690345.svg';
                    if(mod.includes('claude')||mod.includes('anthropic')) return 'https://upload.wikimedia.org/wikipedia/commons/c/c2/Anthropic_logo.svg';
                    if(mod.includes('llama')||mod.includes('meta')) return 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Meta-Logo.png';
                    if(mod.includes('deepseek')) return 'https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://deepseek.com&size=128';
                    return 'https://upload.wikimedia.org/wikipedia/commons/0/04/ChatGPT_logo.svg';
                };
                
                const selectIA = (key) => { currentIA.value = key; dropdownOpen.value = false; clearChat(); };
                const clearChat = () => { messages.value = []; menuOpen.value = false; };

                const send = async () => {
                    const msg = promptText.value.trim(); 
                    if(!msg && !fileContext.value) return;
                    
                    let finalPrompt = msg; let displayMsg = msg;
                    
                    if(fileContext.value) {
                        displayMsg = `📄 **[Documento Injetado no Contexto]**\n\n${msg}`;
                        finalPrompt = `CONTEXTO RAG FORNECIDO:\n${fileContext.value}\n\nCOMANDO:\n${msg}`;
                    }

                    messages.value.push({ role: 'user', parsed: marked.parse(displayMsg) }); 
                    promptText.value = ""; fileContext.value = ""; isTyping.value = true;
                    
                    const iaIdx = messages.value.length; 
                    messages.value.push({ role: 'ia', parsed: '<span style="color:#666; font-style:italic;">Buscando resposta na Matriz...</span>' });
                    
                    if(chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight;
                    
                    try {
                        const res = await fetch('/api/chat', { 
                            method: 'POST', 
                            headers: { 'Content-Type': 'application/json', ...getAuth() }, 
                            body: JSON.stringify({ ia: currentIA.value, mensagem: finalPrompt }) 
                        });
                        
                        if(!res.ok) { 
                            const errD = await res.json(); 
                            messages.value[iaIdx].parsed = `<strong><span style="color:#ef4444;">Bloqueio Tático:</span></strong><br>${errD.erro}`; 
                            isTyping.value = false; return; 
                        }
                        
                        const reader = res.body.getReader(); 
                        const decoder = new TextDecoder(); 
                        let txt = "";
                        
                        while(true) { 
                            const { value, done } = await reader.read(); 
                            if(done) break; 
                            
                            txt += decoder.decode(value); 
                            
                            if(txt.includes("[ERRO CRÍTICO NA NUVEM]") || txt.includes("[TIMEOUT]")) {
                                messages.value[iaIdx].parsed = `<strong><span style="color:#ef4444;">${txt.trim()}</span></strong>`;
                                break;
                            }
                            
                            messages.value[iaIdx].parsed = marked.parse(txt);
                            if(chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight;
                        }
                    } catch(e) {
                        messages.value[iaIdx].parsed = `<span style="color:#ef4444;">Falha grave de comunicação com o servidor C2. O link quebrou.</span>`;
                    }
                    isTyping.value = false;
                };
                
                const triggerUp = () => { document.getElementById('fileup').click(); };
                const handleUpload = async (e) => {
                    const f = e.target.files[0]; if(!f) return;
                    const fd = new FormData(); fd.append('file', f);
                    try {
                        const res = await fetch('/api/upload', { method: 'POST', headers: getAuth(), body: fd });
                        const d = await res.json(); 
                        if(d.sucesso) { fileContext.value = d.texto; alert("Documento carregado no Cérebro!");} 
                        else { alert("O servidor rejeitou o documento: " + d.erro); }
                    } catch(err) { alert("Falha extrema de Upload."); }
                    e.target.value = '';
                };
                
                onMounted(() => {
                    carregar();
                    syncInterval = setInterval(carregarSilencioso, 2000);
                }); 
                
                onUnmounted(() => {
                    if (syncInterval) clearInterval(syncInterval);
                });
                
                return { 
                    ias, messages, currentIA, promptText, send, 
                    menuOpen, showModal, showAPIModal, dropdownOpen, 
                    isAdmin, userTier, apiKeys, gerarNovaChave, revogarChave, 
                    clearChat, getLogo, copy, selectIA, triggerUp, handleUpload, isTyping, chatBox
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SYZYGY Monopoly Master</title>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        :root { --bg: #09090b; --card: #18181b; --accent: #8a2be2; --text: #ececec; --border: #27272a;} 
        body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; padding: 30px 20px; margin: 0; min-height: 100dvh;} 
        .container { max-width: 1200px; margin: 0 auto; display: flex; gap: 30px; }
        
        .sidebar { width: 270px; flex-shrink: 0; }
        .menu-item { display: block; padding: 15px 20px; color: #a1a1aa; text-decoration: none; border-radius: 12px; margin-bottom: 10px; font-weight: 600; font-size: 14px; cursor: pointer; transition: all 0.2s ease;}
        .menu-item:hover, .menu-item.active { background: #27272a; color: #fff;}
        
        .main-content { flex: 1; min-width: 0;}
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; padding-bottom: 15px; border-bottom: 1px solid var(--border);}
        h2 { margin: 0; font-weight: 700; font-size: 26px; letter-spacing: -0.5px;}
        
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 30px; margin-bottom: 30px; overflow-x: auto; box-shadow: 0 10px 30px rgba(0,0,0,0.3);} 
        .card-title { font-size: 18px; font-weight: 700; margin-bottom: 25px; color: #fff; display: flex; justify-content: space-between; align-items: center;}
        
        table { width: 100%; border-collapse: collapse; min-width: 600px;} 
        th, td { padding: 16px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px;} 
        th { color: #888; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 1px;} 
        
        .btn { padding: 12px 20px; background: #27272a; border: none; color: #fff; border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.2s ease;} 
        .btn:hover { background: #3f3f46; transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.4);} 
        .btn-primary { background: var(--accent); } .btn-success { background: #10a37f; } .btn-danger { background: #ef4444; } .btn-outline { background: transparent; border: 1px solid #555;}
        
        input, select, textarea { background: #09090b; border: 1px solid var(--border); color: #fff; padding: 12px 15px; border-radius: 10px; width: 100%; outline: none; margin-bottom: 15px; font-family: 'Inter', sans-serif; font-size: 14px; transition: border-color 0.2s ease;}
        input:focus, select:focus, textarea:focus { border-color: var(--accent);}
        
        .badge { padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px;}
        .b-free { background: #333; color: #aaa; } .b-plus { background: rgba(44, 82, 130, 0.3); color: #90cdf4; border: 1px solid #2c5282; } .b-pro { background: rgba(138,43,226,0.2); color: var(--accent); border: 1px solid var(--accent); } .b-ultra { background: rgba(255, 140, 0, 0.2); color: #ffd700; border: 1px solid #ff8c00; }
        
        @media (max-width: 768px) { .container { flex-direction: column; } .sidebar { width: 100%; display: flex; overflow-x: auto; padding-bottom: 10px; border-bottom: 1px solid var(--border); margin-bottom: 20px;} .menu-item { white-space: nowrap; margin-bottom: 0; margin-right: 10px;} body { padding: 15px; } }
    </style>
</head>
<body>
    <div id="admin-app" class="container">
        
        <div class="sidebar">
            <h3 style="padding-left:20px; color:#fff; font-size:22px; font-weight:800; letter-spacing:-0.5px; margin-bottom:30px;">C2 Master</h3>
            <div class="menu-item" :class="{active: tab==='users'}" @click="tab='users'">👥 1. Operadores e Tiers</div>
            <div class="menu-item" :class="{active: tab==='ias'}" @click="tab='ias'">🧠 2. Matriz de IAs</div>
            <div class="menu-item" :class="{active: tab==='limits'}" @click="tab='limits'">⚙️ 3. Física (Rate Limit)</div>
            <div class="menu-item" :class="{active: tab==='osint'}" @click="tab='osint'">🌐 4. Radar OSINT (APIs)</div>
            <a href="/chat" class="menu-item" style="color:var(--accent); margin-top:40px;">← Sair do Painel</a>
        </div>
        
        <div class="main-content">
            {% raw %}
            <div class="header">
                <h2>{{ tabNames[tab] }}</h2>
                <span style="font-size:12px; color:#10a37f; font-weight:bold; background:rgba(16,163,127,0.1); padding:6px 12px; border-radius:20px; border:1px solid #10a37f;">● ACESSO VERIFICADO</span>
            </div>
            
            <div v-if="tab==='users'" class="card">
                <div class="card-title">Autorizações e Upgrade/Downgrade Manual</div>
                <table>
                    <tr><th>Identidade Registrada</th><th>Saúde da Conta</th><th>Licença Atribuída</th><th>Alteração Rápida</th></tr>
                    <tr v-for="u in users" :key="u.id">
                        <td><strong style="font-size:15px;">{{ u.login }}</strong></td>
                        <td :style="{color: u.status==='ativo'?'#10a37f':'#ef4444', fontWeight:'bold'}">● {{ u.status.toUpperCase() }}</td>
                        <td><span :class="'badge b-'+u.tier">{{ u.tier }}</span></td>
                        <td>
                            <select :value="u.tier" @change="mudarTier(u.login, $event.target.value)" style="width:140px; padding:10px; margin:0; font-weight:bold;">
                                <option value="free">FREE (Padrão)</option><option value="plus">PLUS</option><option value="pro">PRO</option><option value="ultra">ULTRA</option>
                            </select>
                        </td>
                    </tr>
                </table>
            </div>
            
            <div v-if="tab==='ias'" class="card">
                <div class="card-title">Bloqueios de Paywall e Roteamento <button class="btn btn-primary" @click="showIA=!showIA">+ Adicionar Rota Manual</button></div>
                <div v-if="showIA" style="background:#050505; padding:30px; border-radius:12px; margin-bottom:25px; border:1px solid #27272a;">
                    <h4 style="margin-top:0; color:#fff; margin-bottom:20px;">Criação de Rota Multi-API</h4>
                    <label style="font-size:12px; color:#888; font-weight:bold;">Nome Visual no App:</label>
                    <input v-model="novaIA.nome" placeholder="Ex: Pesquisador Acadêmico">
                    
                    <label style="font-size:12px; color:#888; font-weight:bold;">String do Modelo Original:</label>
                    <input v-model="novaIA.model" placeholder="Ex: openai/gpt-3.5-turbo">
                    
                    <label style="font-size:12px; color:#888; font-weight:bold;">URL da API Rest (Endpoint de Completions):</label>
                    <input v-model="novaIA.url" placeholder="Padrão: https://openrouter.ai/api/v1/chat/completions">

                    <label style="font-size:12px; color:#888; font-weight:bold;">Nome da Chave no Cofre (Vault Env):</label>
                    <input v-model="novaIA.env" placeholder="Padrão: OPENROUTER_KEY">

                    <label style="font-size:12px; color:#888; font-weight:bold;">Barreira de Paywall Mínima:</label>
                    <select v-model="novaIA.tier_req">
                        <option value="free">Livre para Todos os Planos</option><option value="plus">Restrito a PLUS, PRO e ULTRA</option><option value="pro">Restrito a PRO e ULTRA</option><option value="ultra">Plano Exclusivo: ULTRA</option>
                    </select>
                    <button class="btn btn-success" @click="salvarIA" style="margin-top:15px; width:100%; padding:15px; font-size:15px;">Salvar e Aplicar Imediatamente</button>
                </div>
                <table>
                    <tr><th>Interface de Chat</th><th>Motor Real (Backend)</th><th>Nível de Bloqueio (Live)</th><th>Segurança</th></tr>
                    <tr v-for="ia in ias" :key="ia.nome">
                        <td><strong style="color:#fff; font-size:15px;">{{ ia.nome }}</strong></td>
                        <td><code style="background:#000; padding:6px 10px; border-radius:8px; color:#a1a1aa; font-size:12px; border:1px solid #222;">{{ ia.model }}</code></td>
                        <td>
                            <select :value="ia.tier_req" @change="mudarReqIA(ia.nome, $event.target.value)" style="width:130px; padding:8px; margin:0; font-size:12px; font-weight:bold; text-transform:uppercase;">
                                <option value="free">Free</option><option value="plus">Plus</option><option value="pro">Pro</option><option value="ultra">Ultra</option>
                            </select>
                        </td>
                        <td><button class="btn btn-danger" @click="delIA(ia.nome)">Derrubar Rota</button></td>
                    </tr>
                </table>
            </div>
            
            <div v-if="tab==='limits'" class="card">
                <div class="card-title">Definições Físicas da API (Controle de Custo)</div>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap:30px;">
                    <div v-for="lim in limits" :key="lim.tier" style="background:#050505; padding:30px; border-radius:16px; border:1px solid #27272a; position:relative; overflow:hidden; box-shadow:0 10px 20px rgba(0,0,0,0.5);">
                        <div :style="'position:absolute; top:0; left:0; right:0; height:5px; background:' + (lim.tier==='free'?'#444':lim.tier==='plus'?'#2c5282':lim.tier==='pro'?'#8a2be2':'#ff8c00')"></div>
                        <h3 style="margin-top:5px; color:#fff; text-transform:uppercase; font-size:22px; letter-spacing:1px;">{{ lim.tier }}</h3>
                        <div style="display:flex; gap:20px; margin-top:25px;">
                            <div style="flex:1;"><label style="font-size:12px; color:#888; font-weight:bold;">Limite de Mensagens:</label><input type="number" v-model="lim.max_msgs" style="font-size:16px; font-weight:bold;"></div>
                            <div style="flex:1;"><label style="font-size:12px; color:#888; font-weight:bold;">Cooldown (Horas):</label><input type="number" v-model="lim.reset_hours" style="font-size:16px; font-weight:bold;"></div>
                        </div>
                        <label style="font-size:12px; color:#888; font-weight:bold;">Vantagens Exibidas na Modal (CSV):</label>
                        <textarea v-model="lim.features" rows="4" placeholder="Ex: Modelos Básicos, Acesso Padrão..." style="resize:none; line-height:1.5;"></textarea>
                        <button class="btn btn-primary" style="width:100%; margin-top:10px; padding:15px; font-size:14px;" @click="salvarLimites(lim)">Injetar Modificação</button>
                    </div>
                </div>
            </div>

            <div v-if="tab==='osint'" class="card">
                <div class="card-title" style="color:#10a37f;">Painel de Controle OSINT (Acesso a Provedores Mundiais)</div>
                <p style="font-size:14px; color:#888; line-height:1.6; margin-bottom:25px;">
                    Este é o motor avançado de descoberta. Você pode gerenciar chaves no cofre, escanear URLs de APIs concorrentes, puxar a lista de modelos de IA ao vivo e injetá-los na sua matriz com um clique. Ações rápidas aqui refletem os comandos <code>!api</code> do Discord.
                </p>

                <div style="background:#050505; padding:20px; border-radius:12px; border:1px solid #27272a; margin-bottom: 25px;">
                    <h4 style="margin-top:0; color:#fff;">🔐 Cofre de Provedores (Vault)</h4>
                    <div style="display:flex; gap:10px; margin-bottom:15px;">
                        <input v-model="novoVault.nome" placeholder="Nome da Chave (Ex: GROQ_KEY)" style="flex:1; margin:0;">
                        <input v-model="novoVault.chave" placeholder="sk-..." type="password" style="flex:2; margin:0;">
                        <button class="btn btn-success" @click="addVault">Blindar Chave</button>
                    </div>
                    <table v-if="vaultList.length > 0" style="margin-top:15px; background:#111; border-radius:8px;">
                        <tr v-for="v in vaultList" :key="v.env_name">
                            <td><strong style="color:var(--accent);">{{ v.env_name }}</strong></td>
                            <td style="color:#888; font-family:monospace;">{{ maskKey(v.api_key) }}</td>
                            <td style="text-align:right;"><button class="btn btn-danger" style="padding:6px 12px; font-size:11px;" @click="delVault(v.env_name)">X</button></td>
                        </tr>
                    </table>
                </div>

                <div style="background:#050505; padding:20px; border-radius:12px; border:1px solid #27272a;">
                    <h4 style="margin-top:0; color:#fff;">📡 Terminal de Descoberta (Scan Models)</h4>
                    <p style="font-size:12px; color:#888;">Forneça a URL de Completions e a Chave do Cofre para mapear os modelos daquela API.</p>
                    
                    <div style="display:flex; gap:10px; margin-top:15px;">
                        <input v-model="scan.url" placeholder="URL (Ex: https://api.groq.com/openai/v1/chat/completions)" style="flex:3; margin:0;">
                        <input v-model="scan.env" placeholder="Nome no Cofre (Ex: GROQ_KEY)" style="flex:1; margin:0;">
                        <button class="btn btn-primary" @click="runScan">Iniciar Scan</button>
                    </div>

                    <div v-if="scanResults.length > 0" style="margin-top:20px; background:#111; padding:15px; border-radius:8px; max-height:400px; overflow-y:auto; border:1px solid #333;">
                        <h5 style="color:#10a37f; margin-top:0;">Modelos Detectados ({{scanResults.length}}):</h5>
                        <div v-for="m in scanResults" :key="m.id" style="display:flex; justify-content:space-between; align-items:center; padding:10px; border-bottom:1px solid #222;">
                            <code style="color:#fff; font-size:13px;">{{ m.id }}</code>
                            <button class="btn btn-outline" style="font-size:11px; padding:6px 12px; border-color:var(--accent); color:var(--accent);" @click="prepInject(m.id)">Injetar na Matriz</button>
                        </div>
                    </div>
                    
                    <div v-if="scanLoading" style="margin-top:15px; color:#aaa; font-style:italic;">Rastreando Servidores... Aguarde.</div>
                </div>
            </div>
            {% endraw %}
        </div>
    </div>
    
    <script>
        const { createApp, ref, onMounted } = Vue;
        
        createApp({
            setup() {
                const tab = ref('osint'); // Deixando a nova aba aberta por padrão para demonstração
                const users = ref([]); 
                const ias = ref([]); 
                const limits = ref([]);
                const vaultList = ref([]);
                
                const showIA = ref(false); 
                const novaIA = ref({nome: '', model: '', url: 'https://openrouter.ai/api/v1/chat/completions', env: 'OPENROUTER_KEY', tier_req: 'free'});
                
                const novoVault = ref({nome: '', chave: ''});
                const scan = ref({url: 'https://openrouter.ai/api/v1/chat/completions', env: 'OPENROUTER_KEY'});
                const scanResults = ref([]);
                const scanLoading = ref(false);

                const tabNames = { 
                    users: 'Privilégios e Autorizações', 
                    ias: 'Controle Estrutural Multi-API', 
                    limits: 'Engenharia de Proteção de Caixa',
                    osint: 'Motor de Descoberta Global de IAs'
                };
                
                const getAuth = () => ({ 
                    'Content-Type': 'application/json', 
                    'Authorization': 'Bearer ' + localStorage.getItem('sys_jwt') 
                });
                
                const loadData = async () => {
                    try {
                        const res = await fetch('/api/admin/data', { headers: getAuth() });
                        if(res.status === 401 || res.status === 403) { window.location.href = '/'; return; }
                        const d = await res.json(); 
                        users.value = d.users; 
                        ias.value = d.ias; 
                        limits.value = d.limits;
                        vaultList.value = d.vault || [];
                    } catch(e) { alert("Erro de comunicação HTTP."); }
                };
                
                const mudarTier = async (login, t) => { 
                    await fetch('/api/admin/tier', { method: 'POST', headers: getAuth(), body: JSON.stringify({login: login, tier: t})}); 
                    loadData(); 
                };
                
                const salvarIA = async () => { 
                    if(!novaIA.value.nome || !novaIA.value.model) { alert("Preencha Nome e Modelo Original."); return; }
                    await fetch('/api/admin/ias', { method: 'POST', headers: getAuth(), body: JSON.stringify(novaIA.value) }); 
                    loadData(); 
                    showIA.value = false; 
                    novaIA.value = {nome: '', model: '', url: 'https://openrouter.ai/api/v1/chat/completions', env: 'OPENROUTER_KEY', tier_req: 'free'};
                    alert("Rota Adicionada à Matriz!");
                };
                
                const delIA = async (n) => { 
                    if(confirm("Operação Destrutiva: Excluir Rota?")) { 
                        await fetch('/api/admin/ias?nome='+n, { method: 'DELETE', headers: getAuth() }); 
                        loadData(); 
                    }
                };
                
                const mudarReqIA = async (nome, req) => { 
                    await fetch('/api/admin/ia_req', { method: 'POST', headers: getAuth(), body: JSON.stringify({nome: nome, tier_req: req})}); 
                    loadData(); 
                };
                
                const salvarLimites = async (lim) => { 
                    await fetch('/api/admin/limits', { method: 'POST', headers: getAuth(), body: JSON.stringify(lim) }); 
                    alert('Gravado no DB.'); 
                };

                // Funções do OSINT Radar
                const maskKey = (k) => {
                    if(!k) return "NULL";
                    if(k.length < 10) return "****";
                    return k.substring(0,6) + "..." + k.substring(k.length-4);
                };

                const addVault = async () => {
                    if(!novoVault.value.nome || !novoVault.value.chave) return;
                    await fetch('/api/admin/vault', { method: 'POST', headers: getAuth(), body: JSON.stringify(novoVault.value) });
                    novoVault.value = {nome:'', chave:''};
                    loadData();
                };

                const delVault = async (n) => {
                    if(confirm("Destruir chave?")) {
                        await fetch('/api/admin/vault?env_name='+n, { method: 'DELETE', headers: getAuth() });
                        loadData();
                    }
                };

                const runScan = async () => {
                    if(!scan.value.url || !scan.value.env) { alert("Preencha URL e o Cofre correspondente."); return; }
                    scanLoading.value = true;
                    scanResults.value = [];
                    try {
                        const res = await fetch('/api/admin/scan', { method: 'POST', headers: getAuth(), body: JSON.stringify(scan.value) });
                        const d = await res.json();
                        if(d.sucesso) {
                            scanResults.value = d.data;
                        } else {
                            alert("Falha no Scan: " + d.erro);
                        }
                    } catch(e) {
                        alert("Erro Crítico de Rede durante o rastreio.");
                    }
                    scanLoading.value = false;
                };

                const prepInject = (model_id) => {
                    // Preenche o formulário de Injeção e muda de aba
                    novaIA.value.nome = "Nova IA (" + model_id.split('/').pop() + ")";
                    novaIA.value.model = model_id;
                    novaIA.value.url = scan.value.url;
                    novaIA.value.env = scan.value.env;
                    novaIA.value.tier_req = 'free';
                    tab.value = 'ias';
                    showIA.value = true;
                };
                
                onMounted(loadData); 
                
                return { 
                    tab, tabNames, users, ias, limits, vaultList, showIA, novaIA, 
                    novoVault, scan, scanResults, scanLoading,
                    salvarIA, delIA, mudarTier, mudarReqIA, salvarLimites, maskKey, addVault, delVault, runScan, prepInject
                };
            }
        }).mount('#admin-app');
    </script>
</body>
</html>
"""


# ==========================================
# 7. ROTAS FLASK DO C2 E MOTOR DE DESCOBERTA
# ==========================================

@app.route('/')
def index():
    return render_template_string(HTML_LOGIN, sys_name=SYSTEM_NAME, GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID)

@app.route('/chat')
def chat_panel():
    return render_template_string(HTML_CHAT, sys_name=SYSTEM_NAME)

@app.route('/admin')
def admin_panel():
    return render_template_string(HTML_ADMIN)

@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        d = request.json
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM users WHERE login = %s AND auth_provider = 'local'", (d['login'],))
        u = cur.fetchone()
        conn.close()
        
        if u and u['senha'] == hashlib.sha256(d['senha'].encode()).hexdigest():
            if u.get('status') != 'ativo': 
                return jsonify({"erro": "Conta Suspensa. Contacte a Equipe Master."}), 403
            
            tk = jwt.encode({
                'user': d['login'], 
                'is_admin': u['is_admin'], 
                'tier': u.get('tier', 'free'), 
                'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
            }, app.secret_key)
            
            return jsonify({"sucesso": True, "token": tk})
            
        return jsonify({"erro": "Credenciais Operacionais Inválidas."}), 401
    except Exception as e:
        return jsonify({"erro": f"Erro interno do servidor: {e}"}), 500

@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        d = request.json
        conn = get_db()
        cur = conn.cursor()
        pw = hashlib.sha256(d['senha'].encode()).hexdigest()
        
        cur.execute("INSERT INTO users (login, senha, is_admin, auth_provider, tier) VALUES (%s, %s, False, 'local', 'free')", (d['login'], pw))
        conn.commit()
        conn.close()
        
        send_discord_webhook("🛡️ NOVO OPERADOR REGISTRADO", f"Identidade de Acesso: **{d['login']}**", 0x10a37f)
        
        tk = jwt.encode({
            'user': d['login'], 
            'is_admin': False, 
            'tier': 'free', 
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }, app.secret_key)
        
        return jsonify({"sucesso": True, "token": tk})
    except psycopg2.IntegrityError:
        return jsonify({"erro": "Identidade já existente no banco de dados."}), 400

@app.route('/api/auth/google', methods=['POST'])
def auth_google():
    try:
        token = request.json['token']
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo['email']
        
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("INSERT INTO users (login, is_admin, auth_provider, tier) VALUES (%s, False, 'google', 'free') ON CONFLICT (login) DO NOTHING", (email,))
        cur.execute("SELECT is_admin, status, tier FROM users WHERE login = %s", (email,))
        user_db = cur.fetchone()
        conn.commit()
        conn.close()
        
        if user_db['status'] != 'ativo': 
            return jsonify({"erro": "O seu acesso foi Suspenso."}), 403
            
        tk = jwt.encode({
            'user': email, 
            'is_admin': user_db['is_admin'], 
            'tier': user_db.get('tier', 'free'), 
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }, app.secret_key)
        
        return jsonify({"sucesso": True, "token": tk})
    except ValueError:
        return jsonify({"erro": "Falha na verificação de Assinatura do Google SSO."}), 401

@app.route('/api/user/keys', methods=['GET', 'POST', 'DELETE'])
@token_req
def manage_keys():
    """ Gerenciamento do Cofre Pessoal do Usuário """
    user = request.user_data['user']
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    if request.method == 'GET':
        cur.execute("SELECT id, key_val as key FROM api_keys WHERE user_login = %s", (user,))
        keys = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"keys": keys})
        
    elif request.method == 'POST':
        new_key = f"syz_{uuid.uuid4().hex}"
        cur.execute("INSERT INTO api_keys (user_login, key_val) VALUES (%s, %s)", (user, new_key))
        conn.commit()
        conn.close()
        send_discord_webhook("🔑 COFRE API ACIONADO", f"O Operador **{user}** gerou uma nova chave Rest Externa.", 0xf59e0b)
        return jsonify({"sucesso": True})
        
    elif request.method == 'DELETE':
        cur.execute("DELETE FROM api_keys WHERE id = %s AND user_login = %s", (request.args.get('id'), user))
        conn.commit()
        conn.close()
        return jsonify({"sucesso": True})

@app.route('/api/upload', methods=['POST'])
@token_req
def upload_file():
    tier = request.user_data.get('tier', 'free')
    is_admin = request.user_data.get('is_admin')
    
    if tier == 'free' and not is_admin: 
        return jsonify({'erro': 'Upload bloqueado pelo Paywall. Exige Licença PLUS.'}), 403
        
    f = request.files['file']
    txt = ""
    try:
        if f.filename.endswith('.pdf'):
            for p in PyPDF2.PdfReader(f).pages: 
                txt += p.extract_text() + "\n"
        else: 
            txt = f.read().decode('utf-8', errors='ignore')
        return jsonify({'sucesso': True, 'texto': txt.strip()})
    except Exception as e: 
        return jsonify({'erro': f'Falha Crítica no Motor RAG: {e}'}), 500

@app.route('/api/init', methods=['GET'])
@token_req
def api_init():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT nome, model, tier_req FROM ias")
    ias = {r['nome']: {'model': r['model'], 'tier_req': r.get('tier_req','free')} for r in cur.fetchall()}
    
    cur.execute("SELECT * FROM system_limits")
    limits = {r['tier']: dict(r) for r in cur.fetchall()}
    
    conn.close()
    return jsonify({"sucesso": True, "ias": ias, "limits_dict": limits})

# ==========================================
# 8. MIDDLEWARES E MOTORES FÍSICOS
# ==========================================
def check_rate_limit_and_permissions(ia_name, user_data):
    """ Motor Físico: Rastreia Tokens para Todos, inclusive Admins """
    try:
        user = user_data['user']
        user_tier = user_data.get('tier', 'free')
        is_admin = user_data.get('is_admin')
        
        res_ia = exec_db_query("SELECT * FROM ias WHERE nome = %s", (ia_name,), fetch=True)
        if not res_ia: return {"erro": "Nó Neural não encontrado.", "code": 404}
        ia = res_ia[0]
        
        tier_levels = {'free': 0, 'plus': 1, 'pro': 2, 'ultra': 3}
        if not is_admin and tier_levels.get(user_tier, 0) < tier_levels.get(ia.get('tier_req', 'free'), 0):
            return {"erro": f"Exige Licença {ia.get('tier_req').upper()}.", "code": 403}

        agora = datetime.datetime.now()
        usage = exec_db_query("SELECT * FROM user_usage WHERE login = %s", (user,), fetch=True)
        
        if not usage:
            exec_db_query("INSERT INTO user_usage (login, msg_count, last_reset) VALUES (%s, 1, %s)", (user, agora))
        else:
            if not is_admin:
                limites = exec_db_query("SELECT * FROM system_limits WHERE tier = %s", (user_tier,), fetch=True)
                limite = limites[0] if limites else {'reset_hours': 3, 'max_msgs': 15}
                decorrido = (agora - usage[0]['last_reset']).total_seconds()
                
                if decorrido > (limite['reset_hours'] * 3600):
                    exec_db_query("UPDATE user_usage SET msg_count = 1, last_reset = %s WHERE login = %s", (agora, user))
                elif usage[0]['msg_count'] >= limite['max_msgs']:
                    return {"erro": "Cota Física Esgotada.", "code": 429}
                else:
                    exec_db_query("UPDATE user_usage SET msg_count = msg_count + 1 WHERE login = %s", (user,))
            else:
                exec_db_query("UPDATE user_usage SET msg_count = msg_count + 1 WHERE login = %s", (user,))

        key = get_api_key_from_vault(ia.get('env', 'OPENROUTER_KEY'))
        if not key: return {"erro": "Chave não encontrada.", "code": 500}
            
        return {"ia": dict(ia), "key": key, "code": 200}
    except Exception as e:
        print(f"[CRITICAL] Erro no Motor Físico: {e}")
        return {"erro": "Falha C2.", "code": 500}

@app.route('/api/chat', methods=['POST'])
@token_req
def api_chat():
    d = request.json
    res = check_rate_limit_and_permissions(d['ia'], request.user_data)
    if res['code'] != 200: return jsonify({"erro": res['erro']}), res['code']
    
    ia = res['ia']
    key = res['key']
    
    def generate():
        payload = {
            "model": ia['model'], 
            "messages": [
                {"role": "system", "content": ia.get('prompt', '')}, 
                {"role": "user", "content": d['mensagem']}
            ], 
            "stream": True
        }
        try:
            r = requests.post(
                ia.get('url', 'https://openrouter.ai/api/v1/chat/completions'), 
                headers={"Authorization": f"Bearer {key}"}, json=payload, stream=True, timeout=30
            )
            if r.status_code != 200:
                yield f"\n\n[ERRO CRÍTICO NA NUVEM] O Provedor rejeitou a conexão (HTTP {r.status_code})"
                return
            for line in r.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        l = decoded_line[6:]
                        if l == '[DONE]': break
                        try: 
                            data_json = json.loads(l)
                            if 'error' in data_json:
                                yield f"\n\n[ERRO DE STREAM] A IA falhou: {data_json['error']['message']}"
                                break
                            yield data_json.get('choices', [{}])[0].get('delta', {}).get('content', '')
                        except: pass
        except requests.exceptions.Timeout:
            yield "\n\n[TIMEOUT] A IA excedeu o tempo máximo."
        except Exception as err:
            yield f"\n\n[ERRO CRÍTICO NA NUVEM] O link principal quebrou: {err}"

    return Response(stream_with_context(generate()), mimetype='text/plain')

# ==========================================
# 9. ROTAS DO PAINEL MASTER (MONOPOLY DASHBOARD)
# ==========================================
@app.route('/api/admin/data', methods=['GET'])
@token_req
def admin_data():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Acesso Master Requerido. Permissão Negada.'}), 403
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT id, login, status, tier FROM users")
        users = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT nome, model, url, env, tier_req FROM ias")
        ias = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM system_limits ORDER BY CASE WHEN tier='free' THEN 1 WHEN tier='plus' THEN 2 WHEN tier='pro' THEN 3 ELSE 4 END")
        limits = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT env_name, api_key FROM api_vault")
        vault = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"users": users, "ias": ias, "limits": limits, "vault": vault})
    except Exception as e: return jsonify({'erro': f'Erro de banco de dados: {e}'}), 500

@app.route('/api/admin/tier', methods=['POST'])
@token_req
def admin_tier():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    d = request.json; conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET tier = %s WHERE login = %s", (d['tier'], d['login']))
    conn.commit(); conn.close()
    return jsonify({"sucesso": True})

@app.route('/api/admin/ias', methods=['POST', 'DELETE'])
@token_req
def admin_ias():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        d = request.json
        cur.execute("""INSERT INTO ias (nome, model, url, env, tier_req) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (nome) DO UPDATE SET model=EXCLUDED.model, url=EXCLUDED.url, env=EXCLUDED.env, tier_req=EXCLUDED.tier_req""", (d['nome'], d['model'], d.get('url', 'https://openrouter.ai/api/v1/chat/completions'), d.get('env', 'OPENROUTER_KEY'), d['tier_req']))
    else: cur.execute("DELETE FROM ias WHERE nome = %s", (request.args.get('nome'),))
    conn.commit(); conn.close()
    return jsonify({"sucesso": True})

@app.route('/api/admin/ia_req', methods=['POST'])
@token_req
def admin_ia_req():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    d = request.json; conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE ias SET tier_req = %s WHERE nome = %s", (d['tier_req'], d['nome']))
    conn.commit(); conn.close()
    return jsonify({"sucesso": True})

@app.route('/api/admin/limits', methods=['POST'])
@token_req
def admin_limits():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    d = request.json; conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE system_limits SET max_msgs = %s, reset_hours = %s, features = %s WHERE tier = %s", (d['max_msgs'], d['reset_hours'], d['features'], d['tier']))
    conn.commit(); conn.close()
    return jsonify({"sucesso": True})

@app.route('/api/admin/vault', methods=['POST', 'DELETE'])
@token_req
def admin_vault():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        d = request.json
        cur.execute("INSERT INTO api_vault (env_name, api_key) VALUES (%s, %s) ON CONFLICT (env_name) DO UPDATE SET api_key=EXCLUDED.api_key", (d['nome'], d['chave']))
    else: cur.execute("DELETE FROM api_vault WHERE env_name = %s", (request.args.get('env_name'),))
    conn.commit(); conn.close()
    return jsonify({"sucesso": True})

@app.route('/api/admin/scan', methods=['POST'])
@token_req
def admin_scan():
    if not request.user_data.get('is_admin'): return jsonify({'erro': 'Negado.'}), 403
    d = request.json
    key = get_api_key_from_vault(d.get('env'))
    if not key: return jsonify({"sucesso": False, "erro": "Chave ausente."})
    try:
        r = requests.get(d.get('url').replace("/chat/completions", "/models"), headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if r.status_code == 200: return jsonify({"sucesso": True, "data": r.json().get("data", [])})
        else: return jsonify({"sucesso": False, "erro": f"Erro HTTP {r.status_code}"})
    except Exception as e: return jsonify({"sucesso": False, "erro": str(e)})

# ==========================================
# 10. INICIALIZAÇÃO MONOLÍTICA (PRODUÇÃO V30.2)
# ==========================================
def run_flask_server():
    """ O Flask assume a Main Thread para garantir que o Container fique Online """
    # Back4App injeta a variável PORT, caso contrário usa 8080
    porta = int(os.environ.get("PORT", 8080))
    print(f"[SISTEMA] Servidor Web (Flask) operando na porta {porta}")
    # use_reloader=False é vital para não duplicar o bot no background
    app.run(host='0.0.0.0', port=porta, debug=False, use_reloader=False)

async def main_bot():
    """ Motor Principal do Bot com Auto-Reconexão Blindada """
    while True:
        try:
            print("[SISTEMA] Módulo Militar tentando conexão com Discord...")
            await bot.start(DISCORD_TOKEN)
        except Exception as e:
            print(f"🔴 [ERRO DISCORD] Falha de handshake: {e}. Reiniciando em 7s...")
            await asyncio.sleep(7)

def start_bot_thread():
    """ Dispara o loop do Discord em uma Thread isolada e protegida """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_bot())

if __name__ == "__main__": 
    # 1. Dispara o Bot do Discord no background
    d_thread = threading.Thread(target=start_bot_thread, daemon=True)
    d_thread.start()
    
    # 2. O Flask assume a linha principal (Main Thread) 
    # Isso mantém o container ativo e responde aos pings de saúde da plataforma
    run_flask_server()
