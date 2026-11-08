import os
import sys
import time
import json
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from werkzeug.utils import secure_filename

from bot import (
    InstagramCommentBot,
    carregar_config,
    carregar_historico,
    salvar_historico,
    CONFIG_FILE,
    SESSION_FILE,
    HISTORICO_FILE,
    MIDIAS_DIR
)

app = Flask(__name__, template_folder="templates")
app.config["JSON_AS_ASCII"] = False
app.config["UPLOAD_FOLDER"] = MIDIAS_DIR
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # Limite de 50MB para vídeos/áudios

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
            "alvo_posts": "especifico",
            "quantidade_posts_monitorar": 15,
            "post_url": "",
            "palavra_chave": "QUERO",
            "reconhecer_variacoes_digitadas_errado": True,
            "responder_todos": False,
            "auto_iniciar_monitoramento": True,
            "responder_comentario": True,
            "respostas_comentario": ["Te mandei no direct! 🚀"],
            "enviar_direct": True,
            "mensagens_direct": ["Olá {nome}! Segue o link com as informações."],
            "anexar_midia": False,
            "tipo_midia": "nenhum",
            "caminho_midia": "",
            "delay_entre_envios_segundos": [15, 30],
            "intervalo_checagem_minutos": 1
        }

def save_config_safe(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/midias/<path:filename>")
def serve_midia(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/api/upload-midia", methods=["POST"])
def upload_midia():
    if "arquivo" not in request.files:
        return jsonify({"success": False, "message": "Nenhum arquivo enviado!"}), 400

    file = request.files["arquivo"]
    if file.filename == "":
        return jsonify({"success": False, "message": "Nome do arquivo vazio!"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    tipo = "nenhum"
    if ext in [".jpg", ".jpeg", ".png", ".webp"]:
        tipo = "foto"
    elif ext in [".mp4", ".mov", ".mkv"]:
        tipo = "video"
    elif ext in [".mp3", ".m4a", ".wav", ".ogg"]:
        tipo = "audio"
    else:
        return jsonify({"success": False, "message": f"Formato não suportado ({ext}). Use imagem, vídeo ou áudio."}), 400

    slot = request.form.get("slot", "").strip().lower()
    if slot not in ["foto", "video", "audio"]:
        slot = tipo

    filename = secure_filename(f"anexo_{slot}_{int(time.time())}_{file.filename}")
    caminho_salvo = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(caminho_salvo)

    caminho_relativo = os.path.join("midias", filename)

    cfg = get_config_safe()
    if slot == "foto":
        cfg["anexar_foto"] = True
        cfg["caminho_foto"] = caminho_relativo
        cfg["nome_foto"] = file.filename
    elif slot == "video":
        cfg["anexar_video"] = True
        cfg["caminho_video"] = caminho_relativo
        cfg["nome_video"] = file.filename
    elif slot == "audio":
        cfg["anexar_audio"] = True
        cfg["caminho_audio"] = caminho_relativo
        cfg["nome_audio"] = file.filename

    # Manter compatibilidade legada
    cfg["anexar_midia"] = cfg.get("anexar_foto") or cfg.get("anexar_video") or cfg.get("anexar_audio")
    save_config_safe(cfg)

    bot_instance.log(f"📎 Mídia anexada para o Direct: {slot.upper()} ({file.filename}) - Enviada antes do texto!")

    return jsonify({
        "success": True,
        "message": f"{slot.capitalize()} anexado com sucesso!",
        "slot": slot,
        "tipo_midia": slot,
        "caminho_midia": caminho_relativo,
        "url_preview": f"/midias/{filename}",
        "nome_arquivo": file.filename
    })

@app.route("/api/remover-midia", methods=["POST"])
def remover_midia():
    dados = request.get_json(silent=True) or {}
    slot = dados.get("slot") or dados.get("tipo") or "todos"
    cfg = get_config_safe()

    if slot in ["foto", "todos"]:
        cfg["anexar_foto"] = False
        cfg["caminho_foto"] = ""
        cfg["nome_foto"] = ""
    if slot in ["video", "todos"]:
        cfg["anexar_video"] = False
        cfg["caminho_video"] = ""
        cfg["nome_video"] = ""
    if slot in ["audio", "todos"]:
        cfg["anexar_audio"] = False
        cfg["caminho_audio"] = ""
        cfg["nome_audio"] = ""

    cfg["anexar_midia"] = cfg.get("anexar_foto") or cfg.get("anexar_video") or cfg.get("anexar_audio")
    save_config_safe(cfg)
    bot_instance.log(f"📎 Mídia ({slot}) removida.")
    return jsonify({"success": True, "message": f"Mídia ({slot}) removida com sucesso!"})

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
        "anexar_foto": cfg.get("anexar_foto", False),
        "caminho_foto": cfg.get("caminho_foto", ""),
        "nome_foto": cfg.get("nome_foto", ""),
        "anexar_video": cfg.get("anexar_video", False),
        "caminho_video": cfg.get("caminho_video", ""),
        "nome_video": cfg.get("nome_video", ""),
        "anexar_audio": cfg.get("anexar_audio", False),
        "caminho_audio": cfg.get("caminho_audio", ""),
        "nome_audio": cfg.get("nome_audio", ""),
        "anexar_midia": cfg.get("anexar_midia", False),
        "tipo_midia": cfg.get("tipo_midia", "nenhum"),
        "caminho_midia": cfg.get("caminho_midia", ""),
        "auto_iniciar": cfg.get("auto_iniciar_monitoramento", True),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(get_config_safe())

    dados = request.get_json(force=True)
    cfg = get_config_safe()

    campos = [
        "usuario", "senha", "alvo_posts", "quantidade_posts_monitorar",
        "post_url", "palavra_chave", "reconhecer_variacoes_digitadas_errado",
        "responder_todos", "auto_iniciar_monitoramento", "responder_comentario",
        "respostas_comentario", "enviar_direct", "mensagens_direct",
        "anexar_foto", "caminho_foto", "nome_foto",
        "anexar_video", "caminho_video", "nome_video",
        "anexar_audio", "caminho_audio", "nome_audio",
        "anexar_midia", "tipo_midia", "caminho_midia",
        "delay_entre_envios_segundos", "intervalo_checagem_minutos"
    ]
    for k in campos:
        if k in dados:
            cfg[k] = dados[k]

    save_config_safe(cfg)
    return jsonify({"success": True, "message": "Configurações salvas com sucesso!", "config": cfg})

@app.route("/api/logs", methods=["GET"])
def get_logs():
    return jsonify(list(bot_instance.recent_logs))

@app.route("/api/limpar-logs", methods=["POST"])
def limpar_logs():
    bot_instance.recent_logs.clear()
    return jsonify({"success": True, "message": "Logs limpos!"})

@app.route("/api/contatos", methods=["GET"])
def get_contatos():
    historico = carregar_historico()
    contatos = historico.get("contatos_detalhados", [])
    q = request.args.get("q", "").strip().lower()
    if q:
        contatos = [c for c in contatos if q in c.get("username", "").lower() or q in c.get("comentario", "").lower()]
    return jsonify({"contatos": contatos, "total": len(contatos)})

@app.route("/api/exportar-csv", methods=["GET"])
def exportar_csv():
    historico = carregar_historico()
    contatos = historico.get("contatos_detalhados", [])
    
    csv_content = "\ufeffUsuario;Data;Comentario;Status;Link_Publicacao\n"
    for c in contatos:
        u = c.get("username", "")
        d = c.get("data", "")
        com = (c.get("comentario", "") or "").replace('"', '""').replace("\n", " ")
        st = c.get("status", "")
        post = c.get("post_url", "")
        csv_content += f'"{u}";"{d}";"{com}";"{st}";"{post}"\n'

    filename = f"contatos_instagram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )

@app.route("/api/start-retroativo", methods=["POST"])
def start_retroativo():
    global bot_worker_thread
    with bot_lock:
        if bot_instance.running:
            return jsonify({"success": False, "message": "O bot já está executando uma tarefa!"}), 400

        dados = request.get_json(silent=True) or {}
        cfg = get_config_safe()

        for k in [
            "post_url", "alvo_posts", "quantidade_posts_monitorar", "palavra_chave", 
            "reconhecer_variacoes_digitadas_errado", "responder_todos", 
            "respostas_comentario", "mensagens_direct", "anexar_midia", "tipo_midia", "caminho_midia"
        ]:
            if k in dados:
                cfg[k] = dados[k]

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

        return jsonify({"success": True, "message": "Varredura iniciada com sucesso!"})

@app.route("/api/start-monitoramento", methods=["POST"])
def start_monitoramento():
    global bot_worker_thread
    with bot_lock:
        if bot_instance.running:
            return jsonify({"success": False, "message": "O bot já está executando uma tarefa!"}), 400

        dados = request.get_json(silent=True) or {}
        cfg = get_config_safe()
        for k in ["post_url", "alvo_posts", "palavra_chave", "reconhecer_variacoes_digitadas_errado", "responder_todos", "anexar_midia", "tipo_midia", "caminho_midia"]:
            if k in dados:
                cfg[k] = dados[k]
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

def tentar_auto_iniciar():
    cfg = get_config_safe()
    if cfg.get("auto_iniciar_monitoramento", True):
        usuario = cfg.get("usuario")
        senha = cfg.get("senha")
        if usuario and senha:
            def runner_auto():
                time.sleep(3)
                try:
                    bot_instance.log("🚀 [Auto-Start] Inicializando monitoramento 24/7 automaticamente...")
                    bot_instance.iniciar_loop_monitoramento()
                except Exception as e:
                    bot_instance.log(f"⚠️ [Auto-Start] Erro na inicialização automática: {e}")
            threading.Thread(target=runner_auto, daemon=True).start()

tentar_auto_iniciar()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
