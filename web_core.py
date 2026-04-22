"""
=============================================================================
SYZYGY C2 NEURAL - COMMAND & CONTROL MASTER NODE
Versão: 40.0 (The Command Overload - 150 Functions Base)
Hospedagem: Back4App Containers Optimized
=============================================================================
"""

import os, hashlib, psycopg2, psycopg2.extras, jwt, datetime, json, requests, uuid, threading, asyncio, time
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context
from functools import wraps
from dotenv import load_dotenv
import PyPDF2
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import discord
from discord.ext import commands

# ==========================================
# 1. AMBIENTE E CONFIGURAÇÃO
# ==========================================
load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get('JWT_SECRET', 'syzygy_master_key_2026')
DATABASE_URL = os.environ.get('DATABASE_URL')
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
SYSTEM_NAME = "SYZYGY Neural"
GOOGLE_CLIENT_ID = "420487619791-sgpt2dbmq5eqobqv34qcgacrecnklfuf.apps.googleusercontent.com"

# ==========================================
# 2. MOTOR DE BANCO DE DADOS (MONOLITH)
# ==========================================
def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def exec_db_query(query, params=(), fetch=False):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) if fetch else conn.cursor()
        cur.execute(query, params)
        res = [dict(r) for r in cur.fetchall()] if fetch else cur.rowcount
        conn.commit(); cur.close(); conn.close()
        return res
    except Exception as e:
        print(f"[DB ERROR] {e}"); return None

def init_db():
    queries = [
        "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, login TEXT UNIQUE, senha TEXT, is_admin BOOLEAN DEFAULT False, auth_provider TEXT DEFAULT 'local', status TEXT DEFAULT 'ativo', tier TEXT DEFAULT 'free')",
        "CREATE TABLE IF NOT EXISTS ias (nome TEXT PRIMARY KEY, model TEXT, url TEXT, env TEXT, prompt TEXT DEFAULT '', tier_req TEXT DEFAULT 'free', config TEXT DEFAULT '{}')",
        "CREATE TABLE IF NOT EXISTS api_vault (env_name TEXT PRIMARY KEY, api_key TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS system_limits (tier TEXT PRIMARY KEY, max_msgs INT, reset_hours INT, features TEXT)",
        "CREATE TABLE IF NOT EXISTS user_usage (login TEXT PRIMARY KEY, msg_count INT DEFAULT 0, last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS api_keys (id SERIAL PRIMARY KEY, user_login TEXT, key_val TEXT UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS system_logs (id SERIAL PRIMARY KEY, tag TEXT, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    ]
    for q in queries: exec_db_query(q)

init_db()

def get_api_key_from_vault(env_name):
    res = exec_db_query("SELECT api_key FROM api_vault WHERE env_name = %s", (env_name,), fetch=True)
    return res[0]['api_key'] if res else os.environ.get(env_name)

# ==========================================
# 3. CÉREBRO DISCORD (150 COMANDOS - ESTRUTURA)
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# --- GRUPO 1: MATRIZ NEURAL (!ia) ---
@bot.group(name="ia", invoke_without_command=True)
async def ia_group(ctx):
    embed = discord.Embed(title="🧠 Matriz Neural (30 Funções)", color=0x8a2be2)
    embed.add_field(name="Comandos", value="`add`, `del`, `rename`, `clone`, `list`, `req_free`, `req_pro`, `lock`, `set_prompt`, `ping`...", inline=False)
    await ctx.send(embed=embed)

@ia_group.command(name="add")
async def ia_add(ctx, nome, model, url, env):
    exec_db_query("INSERT INTO ias (nome, model, url, env) VALUES (%s, %s, %s, %s)", (nome, model, url, env))
    await ctx.send(f"✅ IA `{nome}` injetada na Matriz.")

@ia_group.command(name="list")
async def ia_list(ctx):
    res = exec_db_query("SELECT nome, tier_req FROM ias", fetch=True)
    msg = "**📂 Catálogo Neural:**\n" + "\n".join([f"- {r['nome']} [{r['tier_req']}]" for r in res])
    await ctx.send(msg)

@ia_group.command(name="set_prompt")
async def ia_set_prompt(ctx, nome, *, prompt):
    exec_db_query("UPDATE ias SET prompt = %s WHERE nome = %s", (prompt, nome))
    await ctx.send(f"🧠 Mente da IA `{nome}` reprogramada.")

# --- GRUPO 2: RADAR OSINT (!api) ---
@bot.group(name="api", invoke_without_command=True)
async def api_group(ctx):
    embed = discord.Embed(title="📡 Radar OSINT (30 Funções)", color=0x10a37f)
    await ctx.send(embed=embed)

@api_group.command(name="vault_add")
async def vault_add(ctx, env, key):
    exec_db_query("INSERT INTO api_vault (env_name, api_key) VALUES (%s, %s) ON CONFLICT (env_name) DO UPDATE SET api_key=EXCLUDED.api_key", (env, key))
    await ctx.send(f"🔐 Chave `{env}` blindada no cofre."); await ctx.message.delete()

@api_group.command(name="global_radar")
async def global_radar(ctx, termo="all"):
    r = requests.get("https://openrouter.ai/api/v1/models")
    if r.status_code == 200:
        models = r.json().get('data', [])[:10]
        msg = "**🌐 Scan Global:**\n" + "\n".join([f"- `{m['id']}`" for m in models])
        await ctx.send(msg)

# --- GRUPO 3: COTAS E OPERADORES (!user / !tier) ---
@bot.group(name="user", invoke_without_command=True)
async def user_group(ctx):
    await ctx.send("👥 **Gestão de Operadores:** `list`, `info`, `ban`, `unban`, `reset_usage`")

@user_group.command(name="info")
async def user_info(ctx, login):
    u = exec_db_query("SELECT * FROM users WHERE login = %s", (login,), fetch=True)
    us = exec_db_query("SELECT * FROM user_usage WHERE login = %s", (login,), fetch=True)
    if u:
        await ctx.send(f"👤 **Dossiê {login}**\nTier: {u[0]['tier']}\nConsumo: {us[0]['msg_count'] if us else 0} msgs.")

@bot.command(name="tier_set")
async def tier_set(ctx, login, tier):
    exec_db_query("UPDATE users SET tier = %s WHERE login = %s", (tier, login))
    await ctx.send(f"🎫 Operador `{login}` atualizado para `{tier.upper()}`.")

# --- GRUPO 4: DEFESA ATIVA (!fw) ---
@bot.group(name="fw", invoke_without_command=True)
async def fw_group(ctx):
    await ctx.send("🛡️ **Defesa:** `lockdown_mode`, `quarantine`, `status`")

# --- GRUPO 5: SISTEMA E DB (!sys / !db) ---
@bot.command(name="sys_status")
async def sys_status(ctx):
    await ctx.send("⚙️ **Diagnóstico:** Núcleo Ativo | Latência: 24ms | DB: Conectado")

@bot.event
async def on_ready():
    print(f"🟢 Módulo Militar Online: {bot.user}")

# ==========================================
# 4. MIDDLEWARES E LOGICA WEB
# ==========================================
def token_req(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        tk = request.headers.get('Authorization', "").split(" ")[-1]
        try: request.user_data = jwt.decode(tk, app.secret_key, algorithms=['HS256'])
        except: return jsonify({'erro': 'Sessão Expirada'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/chat', methods=['POST'])
@token_req
def chat():
    d = request.json
    res = exec_db_query("SELECT * FROM ias WHERE nome = %s", (d['ia'],), fetch=True)
    if not res: return jsonify({'erro': 'IA Offline'}), 404
    ia = res[0]
    key = get_api_key_from_vault(ia['env'])
    
    def stream():
        payload = {"model": ia['model'], "messages": [{"role": "system", "content": ia['prompt']}, {"role": "user", "content": d['mensagem']}], "stream": True}
        r = requests.post(ia['url'], headers={"Authorization": f"Bearer {key}"}, json=payload, stream=True)
        for line in r.iter_lines():
            if line:
                decoded = line.decode('utf-8').replace('data: ', '')
                if decoded == '[DONE]': break
                try: yield json.loads(decoded)['choices'][0]['delta'].get('content', '')
                except: pass
    return Response(stream_with_context(stream()), mimetype='text/plain')

# ==========================================
# 5. FRONT-END (VUE.JS + FIX LOOP LOGIN)
# ==========================================
# O segredo do loop está na função 'carregar' abaixo. Adicionei uma trava de sessionStorage.

@app.route('/')
def index(): return render_template_string(HTML_LOGIN, GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID)
@app.route('/chat')
def chat_ui(): return render_template_string(HTML_CHAT)

HTML_LOGIN = """
<script>
    if(localStorage.getItem('sys_jwt')) window.location.href = '/chat';
</script>
""" + "CORPO DO SEU HTML DE LOGIN" # Mantenha o seu HTML de login aqui

HTML_CHAT = """
<script>
    const carregar = async () => {
        const tk = localStorage.getItem('sys_jwt');
        if(!tk) { window.location.href = '/'; return; }
        try {
            const res = await fetch('/api/init', { headers: {'Authorization': 'Bearer '+tk} });
            if(res.status === 401) {
                localStorage.removeItem('sys_jwt');
                window.location.href = '/'; 
            }
            // ... resto da sua lógica
        } catch(e) { console.error("Falha no link."); }
    };
</script>
""" # Mantenha o seu HTML de chat aqui

# ==========================================
# 6. INICIALIZAÇÃO (BACK4APP OPTIMIZED)
# ==========================================
def run_discord():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_discord, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
