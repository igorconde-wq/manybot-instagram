import os
import sys
import json
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response

from bot import (
    InstagramCommentBot,
    carregar_config,
    carregar_historico,
    salvar_historico,
    CONFIG_FILE,
    SESSION_FILE,
    HISTORICO_FILE
)

app = Flask(__name__, template_folder="templates")
app.config["JSON_AS_ASCII"] = False

# Instância global do bot
bot_instance = InstagramCommentBot()
bot_worker_thread = None
bot_lock = threading.Lock()

def get_config_safe():
    try:
        return carregar_config()
    except Exception:
        return {
            "usuario": "",
            "senha": "",
            "post_url": "",
            "palavra_chave": "",
            "responder_todos": True,
            "responder_comentario": True,
            "respostas_comentario": ["Te mandei no direct! 🚀"],
            "enviar_direct": True,
            "mensagens_direct": ["Olá! Segue o link com as informações."],
            "delay_entre_envios_segundos": [15, 30],
            "intervalo_checagem_minutos": 1
        }

def save_config_safe(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status", methods=["GET"])
def get_status():
    historico = carregar_historico()
    cfg = get_config_safe()
    
    total = bot_instance.progresso_total
    atual = bot_instance.progresso_atual
    pct = int((atual / total) * 100) if total > 0 else 0

    return jsonify({
        "status": bot_instance.status,
        "is_running": bot_instance.running,
        "modo": bot_instance.modo_ativo,
        "progresso_atual": atual,
        "progresso_total": total,
        "progresso_pct": pct,
        "total_comentarios": len(historico.get("comentarios_respondidos", [])),
        "total_directs": len(historico.get("usuarios_notificados", [])),
        "total_contatos": len(historico.get("contatos_detalhados", [])),
        "tem_sessao": os.path.exists(SESSION_FILE),
        "usuario_conectado": cfg.get("usuario", ""),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(get_config_safe())
    
    dados = request.get_json(force=True)
    cfg = get_config_safe()

    for k in [
        "usuario", "senha", "post_url", "palavra_chave", 
        "responder_todos", "responder_comentario", "respostas_comentario",
        "enviar_direct", "mensagens_direct", "delay_entre_envios_segundos",
        "intervalo_checagem_minutos"
    ]:
        if k in dados:
            cfg[k] = dados[k]

    save_config_safe(cfg)
    return jsonify({"success": True, "message": "Configurações salvas com sucesso!", "config": cfg})

@app.route("/api/logs", methods=["GET"])
def get_logs():
    return jsonify(list(bot_instance.recent_logs))

@app.route("/api/contatos", methods=["GET"])
def get_contatos():
    historico = carregar_historico()
    contatos = historico.get("contatos_detalhados", [])
    q = request.args.get("q", "").strip().lower()
    if q:
        contatos = [c for c in contatos if q in c.get("username", "").lower() or q in c.get("comentario", "").lower()]
    return jsonify({"contatos": contatos, "total": len(contatos)})

@app.route("/api/start-retroativo", methods=["POST"])
def start_retroativo():
    global bot_worker_thread
    with bot_lock:
        if bot_instance.running:
            return jsonify({"success": False, "message": "O bot já está executando uma tarefa!"}), 400

        dados = request.get_json(silent=True) or {}
        cfg = get_config_safe()

        # Atualiza campos rápidos caso enviados
        if "post_url" in dados and dados["post_url"]:
            cfg["post_url"] = dados["post_url"]
        if "palavra_chave" in dados:
            cfg["palavra_chave"] = dados["palavra_chave"]
        if "responder_todos" in dados:
            cfg["responder_todos"] = bool(dados["responder_todos"])
        if "respostas_comentario" in dados:
            cfg["respostas_comentario"] = dados["respostas_comentario"]
        if "mensagens_direct" in dados:
            cfg["mensagens_direct"] = dados["mensagens_direct"]

        save_config_safe(cfg)

        pular_ja_atendidos = dados.get("pular_ja_atendidos", True)
        limite = int(dados.get("limite_comentarios", 0))

        def runner():
            try:
                bot_instance.processar_post_retroativo(
                    cfg, 
                    pular_ja_atendidos=pular_ja_atendidos, 
                    limite_comentarios=limite
                )
            except Exception as e:
                bot_instance.log(f"❌ Erro na varredura retroativa: {e}")
                bot_instance.status = "error"
            finally:
                bot_instance.running = False
                if bot_instance.status != "error":
                    bot_instance.status = "idle"

        bot_worker_thread = threading.Thread(target=runner, daemon=True)
        bot_worker_thread.start()

        return jsonify({"success": True, "message": "Varredura do post antigo iniciada!"})

@app.route("/api/start-monitoramento", methods=["POST"])
def start_monitoramento():
    global bot_worker_thread
    with bot_lock:
        if bot_instance.running:
            return jsonify({"success": False, "message": "O bot já está executando uma tarefa!"}), 400

        dados = request.get_json(silent=True) or {}
        cfg = get_config_safe()
        if "post_url" in dados and dados["post_url"]:
            cfg["post_url"] = dados["post_url"]
            save_config_safe(cfg)

        def runner():
            try:
                bot_instance.iniciar_loop_monitoramento()
            except Exception as e:
                bot_instance.log(f"❌ Erro no monitoramento 24/7: {e}")
                bot_instance.status = "error"
            finally:
                bot_instance.running = False
                if bot_instance.status != "error":
                    bot_instance.status = "idle"

        bot_worker_thread = threading.Thread(target=runner, daemon=True)
        bot_worker_thread.start()

        return jsonify({"success": True, "message": "Monitoramento Contínuo 24/7 ativado com sucesso!"})

@app.route("/api/stop", methods=["POST"])
def stop_bot():
    with bot_lock:
        if not bot_instance.running:
            return jsonify({"success": False, "message": "O bot já está parado."})
        
        bot_instance.parar()
        return jsonify({"success": True, "message": "Solicitação de parada enviada ao bot."})

@app.route("/api/limpar-historico", methods=["POST"])
def limpar_historico():
    with bot_lock:
        vazio = {
            "comentarios_respondidos": [],
            "usuarios_notificados": [],
            "contatos_detalhados": []
        }
        salvar_historico(vazio)
        bot_instance.log("🧹 Histórico de comentários e contatos foi resetado.")
        return jsonify({"success": True, "message": "Histórico limpo com sucesso!"})

@app.route("/api/test-conexao", methods=["POST"])
def test_conexao():
    cfg = get_config_safe()
    usuario = cfg.get("usuario")
    senha = cfg.get("senha")
    if not usuario or not senha:
        return jsonify({"success": False, "message": "Informe usuário e senha nas configurações!"}), 400

    def runner():
        try:
            bot_instance.log(f"Testando conexão para @{usuario}...")
            bot_instance.autenticar(usuario, senha)
            bot_instance.log(f"✅ Conexão bem-sucedida para @{usuario}!")
        except Exception as e:
            bot_instance.log(f"❌ Falha no teste de conexão: {e}")

    threading.Thread(target=runner, daemon=True).start()
    return jsonify({"success": True, "message": "Teste de conexão iniciado em background. Acompanhe os logs!"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Para produção / nuvem ou local
    app.run(host="0.0.0.0", port=port, debug=False)
