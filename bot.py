import os
import re
import time
import json
import random
import logging
import difflib
import unicodedata
from pathlib import Path
from datetime import datetime
from collections import deque
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, 
    ChallengeRequired, 
    TwoFactorRequired, 
    FeedbackRequired, 
    RateLimitError,
    ClientError
)

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
MIDIAS_DIR = os.path.join(BASE_DIR, "midias")
os.makedirs(MIDIAS_DIR, exist_ok=True)

def limpar_url_instagram(url):
    """Remove parâmetros de tracking e padroniza URLs do Instagram."""
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"\?.*$", "", url)
    if not url.endswith("/"):
        url += "/"
    return url

def resolver_spintax(texto):
    """Resolve spintax recursivamente: {Olá|Oi|Opa} escolhe uma opção aleatória."""
    if not texto:
        return ""
    padrao = re.compile(r"\{([^{}]+)\}")
    while True:
        match = padrao.search(texto)
        if not match:
            break
        opcoes = match.group(1).split("|")
        escolha = random.choice(opcoes)
        texto = texto[:match.start()] + escolha + texto[match.end():]
    return texto

def formatar_mensagem_personalizada(template, username, full_name=""):
    """
    Substitui tags dinâmicas como {username}, {nome} e resolve spintax {Oi|Olá}.
    Gera textos únicos e humanizados para evitar bloqueios do Instagram.
    """
    if not template:
        return ""

    texto = template
    texto = texto.replace("{username}", f"@{username}")
    texto = texto.replace("{@username}", f"@{username}")

    primeiro_nome = ""
    if full_name:
        partes = full_name.strip().split()
        if partes:
            primeiro_nome = partes[0].capitalize()

    if not primeiro_nome:
        primeiro_nome = username

    texto = texto.replace("{nome}", primeiro_nome)
    texto = texto.replace("{Nome}", primeiro_nome)

    texto = resolver_spintax(texto)
    return texto.strip()

def normalizar_texto(texto):
    """Remove acentos, reduz letras repetidas excessivas e converte para minúsculas."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ASCII", "ignore").decode("ASCII")
    texto = texto.lower()
    texto = re.sub(r"[^\w\s]", " ", texto)
    texto = re.sub(r"(.)\1{2,}", r"\1\1", texto)
    return texto.strip()

def verificar_correspondencia_palavra_chave(texto_comentario, palavras_chave_config, modo_inteligente=True):
    """
    Verifica se o texto do comentário contém a palavra-chave configurada
    ou uma variação digitada errado com intenção (ex: 'quero', 'queroo', 'kero', 'qro', 'link', 'lik', 'linck').
    """
    if not palavras_chave_config:
        return True

    texto_norm = normalizar_texto(texto_comentario)
    if not texto_norm:
        return False

    tokens = texto_norm.split()

    if isinstance(palavras_chave_config, str):
        chaves = [p.strip() for p in palavras_chave_config.split(",") if p.strip()]
    else:
        chaves = list(palavras_chave_config)

    if not chaves:
        return True

    aliases_intencao = {
        "chambra": [
            "chama", "chamla", "chambara", "chamba", "chambla", "chamra", 
            "chambre", "chambro", "chambri", "chamb", "xambra", "xama", 
            "xamla", "xambara", "xamba", "xambla", "xamra", "chambrou", 
            "chambrei", "chambramo", "chamma", "chamna", "chambarr", 
            "chambar", "chamva", "chanbra", "xanbra", "ch", "chhh", "chhhh"
        ],
        "chama": [
            "chambra", "chamla", "chambara", "chamba", "chambla", "xama", 
            "xambra", "chambre", "chamb", "ch"
        ],
        "quero": ["kero", "qro", "qero", "queroo", "keru", "qeuru", "quro", "keruu", "queruh", "euquero", "quero!", "queruuu", "eu kero"],
        "link": ["lik", "linck", "linkk", "lnk", "linnk", "linq", "linkzinho", "mandao link", "mandaolink"],
        "ingresso": ["ingreco", "ingreso", "ingressos", "ingrecos", "ingrecus", "ingreço", "convite", "bilhete"],
        "cupom": ["cupon", "cupom", "copom", "kpom", "desconto"],
        "show": ["shou", "xou", "xow", "showw", "sou"],
        "valor": ["preco", "quanto", "preço", "custo", "valr", "qto"]
    }

    for chave in chaves:
        chave_norm = normalizar_texto(chave)
        if not chave_norm:
            continue

        if chave_norm in texto_norm:
            return True

        if modo_inteligente:
            # Regra Especial para CHAMBRA / CHAMA (Público de Forró & Eventos)
            if chave_norm in ["chambra", "chama"]:
                # Se tiver 'ch' isolado ou repetido (ex: 'ch', 'chhh')
                if "ch" in tokens or any(t.startswith("ch") and set(t) <= {"c", "h"} for t in tokens):
                    return True

                # Qualquer palavra que comece com 'chamb', 'xamb', 'cham' ou 'xam'
                for token in tokens:
                    if token.startswith(("chamb", "xamb", "chanb", "xanb")):
                        return True
                    if token.startswith(("cham", "xam")) and len(token) >= 4:
                        return True

            if chave_norm in aliases_intencao:
                for alias in aliases_intencao[chave_norm]:
                    if alias in tokens or alias in texto_norm:
                        return True

            for token in tokens:
                if len(token) >= 3 and abs(len(token) - len(chave_norm)) <= 2:
                    ratio = difflib.SequenceMatcher(None, chave_norm, token).ratio()
                    if ratio >= 0.72:
                        return True

                token_sem_duplicadas = re.sub(r"(.)\1+", r"\1", token)
                chave_sem_duplicadas = re.sub(r"(.)\1+", r"\1", chave_norm)
                if len(chave_sem_duplicadas) >= 3 and (chave_sem_duplicadas in token_sem_duplicadas or token_sem_duplicadas in chave_sem_duplicadas):
                    return True

    return False

def carregar_config():
    padrao = {
        "usuario": "",
        "senha": "",
        "alvo_posts": "especifico",  # 'especifico' ou 'todos'
        "quantidade_posts_monitorar": 15,
        "post_url": "",
        "palavra_chave": "QUERO",
        "reconhecer_variacoes_digitadas_errado": True,
        "responder_todos": False,
        "auto_iniciar_monitoramento": True,
        "responder_comentario": True,
        "respostas_comentario": [
            "Te mandei no direct! 🚀",
            "Acabei de te enviar no direct, confere lá! 😉",
            "Enviado no direct! Dá uma olhada nas suas mensagens 📩"
        ],
        "enviar_direct": True,
        "mensagens_direct": [
            "Olá {nome}! Segue o link com todas as informações."
        ],
        "anexar_midia": False,
        "tipo_midia": "nenhum",  # 'foto', 'video', 'audio'
        "caminho_midia": "",
        "delay_entre_envios_segundos": [15, 30],
        "intervalo_checagem_minutos": 1
    }
    if not os.path.exists(CONFIG_FILE):
        return padrao
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in padrao.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        return padrao

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
        self.status = "idle"
        self.progresso_atual = 0
        self.progresso_total = 0
        self.modo_ativo = "Nenhum"
        self.recent_logs = deque(maxlen=300)

    def log(self, mensagem):
        hora = datetime.now().strftime("%H:%M:%S")
        tipo = "info"
        if "❌" in mensagem or "Erro" in mensagem:
            tipo = "error"
        elif "⚠️" in mensagem or "pausa" in mensagem.lower():
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

    def obter_lista_posts_alvo(self, config):
        """Retorna uma lista de tuplas (media_pk, url_amigavel) baseada na configuração."""
        alvo_posts = config.get("alvo_posts", "especifico")
        posts_alvo = []

        if alvo_posts == "todos":
            qtd = int(config.get("quantidade_posts_monitorar", 15))
            self.log(f"🌐 Modo 'TODOS OS POSTS' ativo: buscando as últimas {qtd} publicações do perfil...")
            try:
                medias = self.cl.user_medias(self.cl.user_id, amount=qtd)
                for m in medias:
                    url = f"https://www.instagram.com/p/{m.code}/"
                    posts_alvo.append((m.pk, url))
                self.log(f"✅ Encontradas {len(posts_alvo)} publicações ativas no perfil para monitorar.")
            except Exception as e:
                self.log(f"⚠️ Erro ao listar posts do perfil: {e}. Tentando fallback por link específico...")
                post_url = limpar_url_instagram(config.get("post_url", ""))
                if post_url and "instagram.com" in post_url:
                    pk = self.cl.media_pk_from_url(post_url)
                    posts_alvo.append((pk, post_url))
        else:
            post_url = limpar_url_instagram(config.get("post_url", ""))
            if not post_url or "instagram.com" not in post_url:
                self.log("❌ URL da publicação inválida ou em branco.")
                return []
            try:
                pk = self.cl.media_pk_from_url(post_url)
                posts_alvo.append((pk, post_url))
            except Exception as e:
                self.log(f"❌ Erro ao extrair ID da publicação: {e}")
                return []

        return posts_alvo

    def enviar_midia_direct(self, user_pk, username, tipo_midia, caminho_midia):
        """Envia Foto, Vídeo ou Áudio de voz no Direct do usuário."""
        if not caminho_midia or tipo_midia not in ["foto", "video", "audio"]:
            return False

        # Resolve caminho absoluto
        caminho_absoluto = os.path.abspath(caminho_midia) if os.path.isabs(caminho_midia) else os.path.join(BASE_DIR, caminho_midia)
        if not os.path.exists(caminho_absoluto):
            self.log(f"  ⚠️ Arquivo de mídia não encontrado no servidor: {caminho_absoluto}")
            return False

        path_obj = Path(caminho_absoluto)

        try:
            if tipo_midia == "foto":
                self.cl.direct_send_photo(path_obj, user_ids=[user_pk])
                self.log(f"  🖼️ Foto anexada enviada no direct para @{username}!")
                return "Foto Enviada"
            elif tipo_midia == "video":
                self.cl.direct_send_video(path_obj, user_ids=[user_pk])
                self.log(f"  🎬 Vídeo anexado enviado no direct para @{username}!")
                return "Vídeo Enviado"
            elif tipo_midia == "audio":
                self.cl.direct_send_voice(path_obj, user_ids=[user_pk])
                self.log(f"  🎙️ Áudio de voz enviado no direct para @{username}!")
                return "Áudio de Voz Enviado"
        except FeedbackRequired:
            self.log(f"  ⚠️ Rate limit no envio de mídia. Aguardando pausa...")
            time.sleep(180)
        except Exception as e:
            self.log(f"  ⚠️ Erro ao enviar {tipo_midia} para @{username}: {e}")

        return False

    def enviar_fluxo_direct(self, user_pk, username, full_name, config):
        """
        Envia o fluxo privado no Direct do Instagram:
        1. Mídias (Foto, Vídeo e/ou Áudio de voz) são enviadas PRIMEIRO na ordem configurada.
        2. Mensagem de texto personalizada com link é enviada DEPOIS.
        """
        status_envios = []
        mensagens_direct = config.get("mensagens_direct", [])

        # Flags e caminhos individuais por tipo de mídia
        anexar_foto = config.get("anexar_foto", False)
        caminho_foto = config.get("caminho_foto", "")
        anexar_video = config.get("anexar_video", False)
        caminho_video = config.get("caminho_video", "")
        anexar_audio = config.get("anexar_audio", False)
        caminho_audio = config.get("caminho_audio", "")

        # Fallback de compatibilidade
        if config.get("anexar_midia"):
            t_leg = config.get("tipo_midia")
            c_leg = config.get("caminho_midia")
            if t_leg == "foto" and not caminho_foto:
                anexar_foto, caminho_foto = True, c_leg
            elif t_leg == "video" and not caminho_video:
                anexar_video, caminho_video = True, c_leg
            elif t_leg == "audio" and not caminho_audio:
                anexar_audio, caminho_audio = True, c_leg

        # 1. Enviar Foto (SEMPRE ANTES DO TEXTO)
        if anexar_foto and caminho_foto:
            res_foto = self.enviar_midia_direct(user_pk, username, "foto", caminho_foto)
            if res_foto:
                status_envios.append(res_foto)
            time.sleep(2)

        # 2. Enviar Vídeo (SEMPRE ANTES DO TEXTO)
        if anexar_video and caminho_video:
            res_video = self.enviar_midia_direct(user_pk, username, "video", caminho_video)
            if res_video:
                status_envios.append(res_video)
            time.sleep(2)

        # 3. Enviar Áudio de Voz (SEMPRE ANTES DO TEXTO)
        if anexar_audio and caminho_audio:
            res_audio = self.enviar_midia_direct(user_pk, username, "audio", caminho_audio)
            if res_audio:
                status_envios.append(res_audio)
            time.sleep(2)

        # 4. Enviar Mensagem de Texto no Direct (ENVIADA DEPOIS DAS MÍDIAS)
        if mensagens_direct:
            tpl_dm = random.choice(mensagens_direct)
            msg_dm_formatada = formatar_mensagem_personalizada(tpl_dm, username, full_name)
            try:
                self.cl.direct_send(msg_dm_formatada, user_ids=[user_pk])
                self.log(f"  📩 Direct (texto) enviado para @{username}: '{msg_dm_formatada[:45]}...'")
                status_envios.append("Texto DM")
            except FeedbackRequired:
                self.log(f"  ⚠️ Rate limit no Direct. Aguardando 5 minutos...")
                time.sleep(300)
            except Exception as e:
                self.log(f"  ⚠️ Erro ao enviar Direct para @{username}: {e}")

        return " & ".join(status_envios) if status_envios else "Direct Enviado"

    def processar_post_retroativo(self, config, pular_ja_atendidos=True, limite_comentarios=0):
        self.running = True
        self.status = "running_retroactive"
        self.modo_ativo = "Varredura Retroativa"

        alvo_posts = config.get("alvo_posts", "especifico")
        responder_todos = config.get("responder_todos", False)
        palavra_chave = config.get("palavra_chave", "").strip()
        modo_inteligente = config.get("reconhecer_variacoes_digitadas_errado", True)
        responder_comentario = config.get("responder_comentario", True)
        respostas_comentario = config.get("respostas_comentario", [])
        enviar_direct = config.get("enviar_direct", True)
        mensagens_direct = config.get("mensagens_direct", [])
        delay_range = config.get("delay_entre_envios_segundos", [15, 30])

        anexar_midia = config.get("anexar_midia", False)
        tipo_midia = config.get("tipo_midia", "nenhum")
        caminho_midia = config.get("caminho_midia", "")

        usuario = config.get("usuario")
        senha = config.get("senha")
        try:
            self.autenticar(usuario, senha)
        except Exception:
            self.status = "error"
            return

        posts_alvo = self.obter_lista_posts_alvo(config)
        if not posts_alvo:
            self.log("❌ Nenhuma publicação encontrada para processar.")
            self.status = "idle"
            return

        if responder_todos:
            self.log("🔓 Modo ativo: Responder a TODOS os comentários (sem restrição).")
        elif palavra_chave:
            tipo_filtro = "com inteligência de variações/erros de digitação 🧠" if modo_inteligente else "correspondência exata"
            self.log(f"🎯 Modo ativo: Filtrando palavras-chave '{palavra_chave}' ({tipo_filtro}).")

        midias_ativas = []
        if (config.get("anexar_foto") and config.get("caminho_foto")) or (config.get("anexar_midia") and config.get("tipo_midia") == "foto"):
            midias_ativas.append("FOTO")
        if (config.get("anexar_video") and config.get("caminho_video")) or (config.get("anexar_midia") and config.get("tipo_midia") == "video"):
            midias_ativas.append("VÍDEO")
        if (config.get("anexar_audio") and config.get("caminho_audio")) or (config.get("anexar_midia") and config.get("tipo_midia") == "audio"):
            midias_ativas.append("ÁUDIO DE VOZ")
        if midias_ativas:
            self.log(f"📎 Mídias anexadas para o Direct (enviadas ANTES do texto): {', '.join(midias_ativas)}")

        historico = carregar_historico()
        comentarios_respondidos = set(historico.get("comentarios_respondidos", []))
        usuarios_notificados = set(historico.get("usuarios_notificados", []))
        contatos_detalhados = historico.get("contatos_detalhados", [])

        todos_comentarios_elegiveis = []
        for media_pk, post_url in posts_alvo:
            if not self.running:
                break
            try:
                self.log(f"📥 Baixando comentários da publicação {post_url}...")
                comments = self.cl.media_comments(media_pk, amount=limite_comentarios if limite_comentarios > 0 else 100)
            except Exception as e:
                self.log(f"⚠️ Erro ao baixar comentários do post {media_pk}: {e}")
                continue

            for c in comments:
                username = c.user.username
                comment_id = str(c.pk)
                texto_comentario = (c.text or "").strip()

                if username.lower() == self.cl.username.lower():
                    continue

                if pular_ja_atendidos and comment_id in comentarios_respondidos:
                    continue

                if not responder_todos and palavra_chave:
                    match = verificar_correspondencia_palavra_chave(
                        texto_comentario, 
                        palavra_chave, 
                        modo_inteligente=modo_inteligente
                    )
                    if not match:
                        continue

                todos_comentarios_elegiveis.append((c, media_pk, post_url))

        total_elegiveis = len(todos_comentarios_elegiveis)
        self.log(f"🎯 Total de comentários elegíveis para responder/enviar direct: {total_elegiveis}")

        if total_elegiveis == 0:
            self.log("✅ Nenhum comentário pendente para processar.")
            self.progresso(0, 0)
            self.status = "idle"
            return

        processados = 0
        for idx, (c, media_pk, post_url) in enumerate(todos_comentarios_elegiveis, 1):
            if not self.running:
                self.log("⏹️ Processamento interrompido pelo usuário.")
                break

            comment_id = str(c.pk)
            username = c.user.username
            full_name = getattr(c.user, "full_name", "") or ""
            user_pk = c.user.pk
            texto_comentario = (c.text or "").strip()

            self.log(f"\n[{idx}/{total_elegiveis}] Processando @{username}: '{texto_comentario}'")
            self.progresso(idx, total_elegiveis)

            status_acao = []

            # 1. Responder o comentário no post
            if responder_comentario and respostas_comentario:
                tpl_resposta = random.choice(respostas_comentario)
                resposta_formatada = formatar_mensagem_personalizada(tpl_resposta, username, full_name)
                try:
                    self.cl.media_comment(media_pk, f"@{username} {resposta_formatada}", replied_to_comment_id=c.pk)
                    self.log(f"  💬 Comentário respondido no post: '{resposta_formatada}'")
                    status_acao.append("Respondido no Post")
                except FeedbackRequired:
                    self.log(f"  ⚠️ Rate limit no comentário. Aguardando 4 minutos...")
                    time.sleep(240)
                except Exception as e:
                    self.log(f"  ⚠️ Erro ao responder comentário de @{username}: {e}")

            # 2. Enviar Direct para o usuário (Mídias ANTES do texto)
            if enviar_direct:
                tem_conteudo = (
                    bool(mensagens_direct) or
                    (config.get("anexar_foto") and config.get("caminho_foto")) or
                    (config.get("anexar_video") and config.get("caminho_video")) or
                    (config.get("anexar_audio") and config.get("caminho_audio")) or
                    (config.get("anexar_midia") and config.get("caminho_midia"))
                )
                if tem_conteudo:
                    if (not pular_ja_atendidos) or (username not in usuarios_notificados):
                        res_direct = self.enviar_fluxo_direct(user_pk, username, full_name, config)
                        usuarios_notificados.add(username)
                        status_acao.append(res_direct)
                    else:
                        self.log(f"  ℹ️ @{username} já estava no histórico de directs.")
                        status_acao.append("Já Atendido")

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
            processados += 1

            if idx < total_elegiveis and self.running:
                delay = random.randint(delay_range[0], delay_range[1])
                self.log(f"  ⏳ Aguardando {delay}s (delay de segurança anti-spam)...")
                for _ in range(delay):
                    if not self.running:
                        break
                    time.sleep(1)

        self.log(f"\n🎉 Varredura concluída! {processados} comentários atendidos com sucesso.")
        self.status = "idle"

    def processar_post(self, config):
        """Modo de monitoramento contínuo (pega novos comentários a cada ciclo)."""
        responder_todos = config.get("responder_todos", False)
        palavra_chave = config.get("palavra_chave", "").strip()
        modo_inteligente = config.get("reconhecer_variacoes_digitadas_errado", True)
        responder_comentario = config.get("responder_comentario", True)
        respostas_comentario = config.get("respostas_comentario", [])
        enviar_direct = config.get("enviar_direct", True)
        mensagens_direct = config.get("mensagens_direct", [])
        delay_range = config.get("delay_entre_envios_segundos", [15, 30])

        anexar_midia = config.get("anexar_midia", False)
        tipo_midia = config.get("tipo_midia", "nenhum")
        caminho_midia = config.get("caminho_midia", "")

        posts_alvo = self.obter_lista_posts_alvo(config)
        if not posts_alvo:
            return

        historico = carregar_historico()
        comentarios_respondidos = set(historico.get("comentarios_respondidos", []))
        usuarios_notificados = set(historico.get("usuarios_notificados", []))
        contatos_detalhados = historico.get("contatos_detalhados", [])

        novos_processados = 0
        for media_pk, post_url in posts_alvo:
            if not self.running:
                break

            try:
                comments = self.cl.media_comments(media_pk, amount=30)
            except Exception as e:
                self.log(f"⚠️ Erro ao checar comentários da publicação {post_url}: {e}")
                continue

            for c in comments:
                if not self.running:
                    break

                comment_id = str(c.pk)
                username = c.user.username
                full_name = getattr(c.user, "full_name", "") or ""
                user_pk = c.user.pk
                texto_comentario = (c.text or "").strip()

                if comment_id in comentarios_respondidos:
                    continue

                if username.lower() == self.cl.username.lower():
                    continue

                if not responder_todos and palavra_chave:
                    match = verificar_correspondencia_palavra_chave(
                        texto_comentario, 
                        palavra_chave, 
                        modo_inteligente=modo_inteligente
                    )
                    if not match:
                        continue

                self.log(f"🎯 Novo comentário elegível de @{username} no post {post_url}: '{texto_comentario}'")
                status_acao = []

                if responder_comentario and respostas_comentario:
                    tpl_resposta = random.choice(respostas_comentario)
                    resposta_formatada = formatar_mensagem_personalizada(tpl_resposta, username, full_name)
                    try:
                        self.cl.media_comment(media_pk, f"@{username} {resposta_formatada}", replied_to_comment_id=c.pk)
                        self.log(f"  💬 Comentário respondido para @{username}: '{resposta_formatada}'")
                        status_acao.append("Respondido no Post")
                    except FeedbackRequired:
                        self.log(f"  ⚠️ Rate limit detectado. Pausando por 4 minutos...")
                        time.sleep(240)
                    except Exception as e:
                        self.log(f"  ⚠️ Erro ao responder comentário de @{username}: {e}")

                if enviar_direct:
                    tem_conteudo = (
                        bool(mensagens_direct) or
                        (config.get("anexar_foto") and config.get("caminho_foto")) or
                        (config.get("anexar_video") and config.get("caminho_video")) or
                        (config.get("anexar_audio") and config.get("caminho_audio")) or
                        (config.get("anexar_midia") and config.get("caminho_midia"))
                    )
                    if tem_conteudo:
                        if username not in usuarios_notificados:
                            res_direct = self.enviar_fluxo_direct(user_pk, username, full_name, config)
                            usuarios_notificados.add(username)
                            status_acao.append(res_direct)
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

        if novos_processados > 0:
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
            self.log("❌ Usuário e senha não configurados.")
            self.status = "error"
            return

        try:
            self.autenticar(usuario, senha)
        except Exception:
            self.status = "error"
            return

        self.log(f"🚀 Monitoramento contínuo 24/7 iniciado! Checando publicações a cada {intervalo_minutos} minuto(s)...")
        while self.running:
            try:
                config = carregar_config()
                self.processar_post(config)
            except FeedbackRequired:
                self.log("⚠️ Instagram solicitou pausa temporária de segurança (Rate Limit). Aguardando 10 minutos...")
                self.status = "paused_ratelimit"
                for _ in range(600):
                    if not self.running:
                        break
                    time.sleep(1)
                self.status = "running_monitoring"
            except Exception as e:
                self.log(f"⚠️ Erro no ciclo de checagem: {e}")

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
