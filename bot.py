import os
import time
import json
import random
import logging
from datetime import datetime
from collections import deque
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired, TwoFactorRequired

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("InstagramBot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSION_FILE = os.path.join(BASE_DIR, "session.json")
HISTORICO_FILE = os.path.join(BASE_DIR, "historico.json")

def carregar_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Arquivo {CONFIG_FILE} não encontrado.")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def carregar_historico():
    padrao = {
        "comentarios_respondidos": [],
        "usuarios_notificados": [],
        "contatos_detalhados": []
    }
    if os.path.exists(HISTORICO_FILE):
        try:
            with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if not isinstance(dados, dict):
                    return padrao
                if "comentarios_respondidos" not in dados:
                    dados["comentarios_respondidos"] = []
                if "usuarios_notificados" not in dados:
                    dados["usuarios_notificados"] = []
                if "contatos_detalhados" not in dados:
                    # Gera a partir dos usuários já existentes
                    dados["contatos_detalhados"] = [
                        {
                            "username": u,
                            "data": "Anterior",
                            "comentario": "Varredura anterior",
                            "status": "DM Enviada"
                        }
                        for u in dados["usuarios_notificados"]
                    ]
                return dados
        except Exception:
            return padrao
    return padrao

def salvar_historico(historico):
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)

class InstagramCommentBot:
    def __init__(self, callback_log=None, callback_progresso=None):
        self.cl = Client()
        self.callback_log = callback_log or (lambda msg: logger.info(msg))
        self.callback_progresso = callback_progresso or (lambda atual, total: None)
        self.running = False
        self.status = "idle"  # idle, running_retroactive, running_monitoring, stopping, error
        self.progresso_atual = 0
        self.progresso_total = 0
        self.modo_ativo = "Nenhum"
        self.recent_logs = deque(maxlen=200)

    def log(self, mensagem):
        hora = datetime.now().strftime("%H:%M:%S")
        tipo = "info"
        if "❌" in mensagem or "Erro" in mensagem:
            tipo = "error"
        elif "⚠️" in mensagem:
            tipo = "warning"
        elif "✅" in mensagem or "🎉" in mensagem or "✨" in mensagem:
            tipo = "success"

        item = {"hora": hora, "msg": mensagem, "tipo": tipo}
        self.recent_logs.append(item)
        try:
            self.callback_log(mensagem)
        except Exception:
            pass

    def progresso(self, atual, total):
        self.progresso_atual = atual
        self.progresso_total = total
        try:
            self.callback_progresso(atual, total)
        except Exception:
            pass

    def autenticar(self, usuario, senha):
        self.cl.delay_range = [2, 5]

        # Tenta restaurar sessão salva
        if os.path.exists(SESSION_FILE):
            self.log("Tentando restaurar sessão anterior salva em session.json...")
            try:
                self.cl.load_settings(SESSION_FILE)
                self.cl.login(usuario, senha)
                self.log("✅ Sessão restaurada com sucesso!")
                return True
            except Exception as e:
                self.log(f"⚠️ Sessão anterior expirada ou inválida: {e}. Fazendo novo login...")

        self.log(f"Iniciando login no Instagram para @{usuario}...")
        try:
            self.cl.login(usuario, senha)
            self.cl.dump_settings(SESSION_FILE)
            self.log("✅ Login realizado com sucesso! Sessão salva em session.json.")
            return True
        except TwoFactorRequired:
            self.status = "error"
            self.log("❌ Autenticação de dois fatores (2FA) necessária. Configure sem 2FA ou use código.")
            raise
        except ChallengeRequired:
            self.status = "error"
            self.log("❌ Instagram solicitou desafio/verificação de segurança no app.")
            raise
        except Exception as e:
            self.status = "error"
            self.log(f"❌ Erro ao autenticar: {e}")
            raise

    def processar_post_retroativo(self, config, pular_ja_atendidos=True, limite_comentarios=0):
        """
        Processa todos os comentários de um post antigo de uma só vez:
        Busca todos os comentários históricos, responde cada um e envia direct para todos os @.
        """
        self.running = True
        self.status = "running_retroactive"
        self.modo_ativo = "Varredura Retroativa"
        
        post_url = config.get("post_url", "").strip()
        responder_todos = config.get("responder_todos", False)
        palavra_chave = "" if responder_todos else config.get("palavra_chave", "").strip().upper()
        responder_comentario = config.get("responder_comentario", True)
        respostas_comentario = config.get("respostas_comentario", [])
        enviar_direct = config.get("enviar_direct", True)
        mensagens_direct = config.get("mensagens_direct", [])
        delay_range = config.get("delay_entre_envios_segundos", [15, 30])

        if not post_url or "instagram.com" not in post_url:
            self.log("❌ URL do post inválida.")
            self.status = "idle"
            return

        if responder_todos:
            self.log("🔓 Modo ativo: Responder a TODOS os comentários (independente de palavra-chave).")
        elif palavra_chave:
            self.log(f"🎯 Modo ativo: Filtrando apenas comentários com '{palavra_chave}'.")
        else:
            self.log("ℹ️ Palavra-chave vazia: respondendo a todos os comentários.")

        usuario = config.get("usuario")
        senha = config.get("senha")
        try:
            self.autenticar(usuario, senha)
        except Exception:
            self.status = "error"
            return

        try:
            self.log(f"🔎 Extraindo ID da publicação a partir do link...")
            media_pk = self.cl.media_pk_from_url(post_url)
        except Exception as e:
            self.log(f"❌ Erro ao extrair ID do post da URL: {e}")
            self.status = "error"
            return

        historico = carregar_historico()
        comentarios_respondidos = set(historico.get("comentarios_respondidos", []))
        usuarios_notificados = set(historico.get("usuarios_notificados", []))
        contatos_detalhados = historico.get("contatos_detalhados", [])

        limite_msg = "TODOS" if limite_comentarios == 0 else str(limite_comentarios)
        self.log(f"📥 Baixando comentários do post antigo (alvo: {limite_msg} comentários)... Isso pode levar alguns segundos.")
        try:
            comments = self.cl.media_comments(media_pk, amount=limite_comentarios)
        except Exception as e:
            self.log(f"❌ Erro ao buscar comentários da publicação: {e}")
            self.status = "error"
            return

        total_encontrados = len(comments)
        self.log(f"📊 Total de comentários baixados do post: {total_encontrados}")

        comentarios_elegiveis = []
        for c in comments:
            username = c.user.username
            comment_id = str(c.pk)
            texto_comentario = (c.text or "").strip()

            # Pula comentários da própria conta
            if username.lower() == self.cl.username.lower():
                continue

            # Filtra por histórico se habilitado
            if pular_ja_atendidos and comment_id in comentarios_respondidos:
                continue

            # Filtra por palavra-chave se NÃO for para responder todos
            if not responder_todos and palavra_chave and palavra_chave not in texto_comentario.upper():
                continue

            comentarios_elegiveis.append(c)

        total_elegiveis = len(comentarios_elegiveis)
        self.log(f"🎯 Comentários elegíveis para responder/enviar direct: {total_elegiveis}")

        if total_elegiveis == 0:
            self.log("✅ Nenhum comentário pendente para processar neste post.")
            self.progresso(0, 0)
            self.status = "idle"
            return

        processados = 0
        for idx, c in enumerate(comentarios_elegiveis, 1):
            if not self.running:
                self.log("⏹️ Processamento interrompido pelo usuário.")
                break

            comment_id = str(c.pk)
            username = c.user.username
            user_pk = c.user.pk
            texto_comentario = (c.text or "").strip()

            self.log(f"\n[{idx}/{total_elegiveis}] Processando @{username}: '{texto_comentario}'")
            self.progresso(idx, total_elegiveis)

            status_acao = []

            # 1. Responder o comentário no post antigo
            if responder_comentario and respostas_comentario:
                resposta = random.choice(respostas_comentario)
                try:
                    self.cl.media_comment(media_pk, f"@{username} {resposta}", replied_to_comment_id=c.pk)
                    self.log(f"  💬 Comentário respondido no post: '{resposta}'")
                    status_acao.append("Respondido no Post")
                except Exception as e:
                    self.log(f"  ⚠️ Erro ao responder comentário de @{username}: {e}")

            # 2. Enviar Direct para o usuário
            if enviar_direct and mensagens_direct:
                if (not pular_ja_atendidos) or (username not in usuarios_notificados):
                    msg_dm = random.choice(mensagens_direct)
                    try:
                        self.cl.direct_send(msg_dm, user_ids=[user_pk])
                        self.log(f"  📩 Direct enviado para @{username}!")
                        usuarios_notificados.add(username)
                        status_acao.append("Direct Enviado")
                    except Exception as e:
                        self.log(f"  ⚠️ Erro ao enviar Direct para @{username}: {e}")
                else:
                    self.log(f"  ℹ️ @{username} já estava no histórico de directs (ignorado envio duplo).")
                    status_acao.append("Já Atendido")

            # Registra no histórico detalhado
            comentarios_respondidos.add(comment_id)
            historico["comentarios_respondidos"] = list(comentarios_respondidos)
            historico["usuarios_notificados"] = list(usuarios_notificados)

            # Adiciona ao histórico detalhado se novo
            contatos_detalhados.insert(0, {
                "username": username,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "comentario": texto_comentario,
                "status": " & ".join(status_acao) if status_acao else "Processado",
                "post_url": post_url
            })
            historico["contatos_detalhados"] = contatos_detalhados[:500]  # Limita aos últimos 500

            salvar_historico(historico)
            processados += 1

            # Pausa humanizada se ainda houver próximos
            if idx < total_elegiveis and self.running:
                delay = random.randint(delay_range[0], delay_range[1])
                self.log(f"  ⏳ Aguardando {delay}s (delay de segurança anti-spam)...")
                for _ in range(delay):
                    if not self.running:
                        break
                    time.sleep(1)

        self.log(f"\n🎉 Varredura concluída! {processados} comentários de @ atendidos com sucesso.")
        self.status = "idle"

    def processar_post(self, config):
        """Modo de monitoramento contínuo (pega novos comentários a cada ciclo)."""
        post_url = config.get("post_url", "").strip()
        responder_todos = config.get("responder_todos", False)
        palavra_chave = "" if responder_todos else config.get("palavra_chave", "").strip().upper()
        responder_comentario = config.get("responder_comentario", True)
        respostas_comentario = config.get("respostas_comentario", [])
        enviar_direct = config.get("enviar_direct", True)
        mensagens_direct = config.get("mensagens_direct", [])
        delay_range = config.get("delay_entre_envios_segundos", [15, 30])

        if not post_url or "instagram.com" not in post_url:
            self.log("❌ URL do post inválida.")
            return

        try:
            media_pk = self.cl.media_pk_from_url(post_url)
        except Exception as e:
            self.log(f"❌ Erro ao extrair ID do post da URL: {e}")
            return

        historico = carregar_historico()
        comentarios_respondidos = set(historico.get("comentarios_respondidos", []))
        usuarios_notificados = set(historico.get("usuarios_notificados", []))
        contatos_detalhados = historico.get("contatos_detalhados", [])

        self.log(f"🔎 Verificando novos comentários no post (ID: {media_pk})...")
        try:
            comments = self.cl.media_comments(media_pk, amount=50)
        except Exception as e:
            self.log(f"❌ Erro ao buscar comentários: {e}")
            return

        novos_processados = 0
        for c in comments:
            if not self.running:
                break

            comment_id = str(c.pk)
            username = c.user.username
            user_pk = c.user.pk
            texto_comentario = (c.text or "").strip()

            if comment_id in comentarios_respondidos:
                continue

            if username.lower() == self.cl.username.lower():
                continue

            if not responder_todos and palavra_chave and palavra_chave not in texto_comentario.upper():
                continue

            self.log(f"🎯 Novo comentário detectado de @{username}: '{texto_comentario}'")
            status_acao = []

            if responder_comentario and respostas_comentario:
                resposta = random.choice(respostas_comentario)
                try:
                    self.cl.media_comment(media_pk, f"@{username} {resposta}", replied_to_comment_id=c.pk)
                    self.log(f"  💬 Comentário respondido para @{username}: '{resposta}'")
                    status_acao.append("Respondido no Post")
                except Exception as e:
                    self.log(f"  ⚠️ Erro ao responder comentário de @{username}: {e}")

            if enviar_direct and mensagens_direct:
                if username not in usuarios_notificados:
                    msg_dm = random.choice(mensagens_direct)
                    try:
                        self.cl.direct_send(msg_dm, user_ids=[user_pk])
                        self.log(f"  📩 Direct enviado para @{username}!")
                        usuarios_notificados.add(username)
                        status_acao.append("Direct Enviado")
                    except Exception as e:
                        self.log(f"  ⚠️ Erro ao enviar Direct para @{username}: {e}")
                else:
                    self.log(f"  ℹ️ @{username} já havia recebido Direct anteriormente.")
                    status_acao.append("Já Notificado")

            comentarios_respondidos.add(comment_id)
            historico["comentarios_respondidos"] = list(comentarios_respondidos)
            historico["usuarios_notificados"] = list(usuarios_notificados)
            contatos_detalhados.insert(0, {
                "username": username,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "comentario": texto_comentario,
                "status": " & ".join(status_acao) if status_acao else "Processado",
                "post_url": post_url
            })
            historico["contatos_detalhados"] = contatos_detalhados[:500]

            salvar_historico(historico)
            novos_processados += 1

            delay = random.randint(delay_range[0], delay_range[1])
            self.log(f"  ⏳ Aguardando {delay}s antes da próxima interação...")
            for _ in range(delay):
                if not self.running:
                    break
                time.sleep(1)

        if novos_processados == 0:
            self.log("✅ Nenhum novo comentário elegível encontrado nesta checagem.")
        else:
            self.log(f"✨ Ciclo concluído! {novos_processados} novos comentários atendidos.")

    def iniciar_loop_monitoramento(self):
        self.running = True
        self.status = "running_monitoring"
        self.modo_ativo = "Monitoramento 24/7"
        
        config = carregar_config()
        usuario = config.get("usuario")
        senha = config.get("senha")
        intervalo_minutos = config.get("intervalo_checagem_minutos", 1)

        if not usuario or not senha:
            self.log("❌ Usuário e senha não configurados em config.json.")
            self.status = "error"
            return

        try:
            self.autenticar(usuario, senha)
        except Exception:
            self.status = "error"
            return

        self.log(f"🚀 Monitoramento contínuo ManyChat iniciado! Checando a cada {intervalo_minutos} minuto(s)...")
        while self.running:
            try:
                config = carregar_config()
                self.processar_post(config)
            except Exception as e:
                self.log(f"⚠️ Erro inesperado no ciclo: {e}")

            for _ in range(int(intervalo_minutos * 60)):
                if not self.running:
                    break
                time.sleep(1)

        self.log("⏹️ Monitoramento interrompido.")
        self.status = "idle"

    def parar(self):
        self.status = "stopping"
        self.running = False
        self.log("Solicitando parada imediata do bot...")

if __name__ == "__main__":
    bot = InstagramCommentBot()
    try:
        bot.iniciar_loop_monitoramento()
    except KeyboardInterrupt:
        bot.parar()
