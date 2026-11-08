import os
import sys
import json
import threading
import customtkinter as ctk
from bot import InstagramCommentBot, carregar_config, CONFIG_FILE, HISTORICO_FILE, salvar_historico

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Instagram — Auto Resposta de Comentários & Direct")
        self.geometry("930, 800")
        self.minsize(830, 680)

        self.bot = None
        self.bot_thread = None

        self.criar_widgets()
        self.carregar_dados_iniciais()

    def criar_widgets(self):
        # Container principal com scroll
        self.scroll = ctk.CTkScrollableFrame(self, corner_radius=10)
        self.scroll.pack(fill="both", expand=True, padx=15, pady=15)

        # Cabeçalho
        lbl_titulo = ctk.CTkLabel(
            self.scroll, 
            text="📸 Instagram Auto Comentários & Direct (instagrapi)",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        lbl_titulo.pack(pady=(5, 10))

        # --- SEÇÃO 1: MODO DE OPERAÇÃO ---
        frame_modo = ctk.CTkFrame(self.scroll)
        frame_modo.pack(fill="x", padx=10, pady=6)

        lbl_modo = ctk.CTkLabel(frame_modo, text="⚙️ Modo de Operação:", font=ctk.CTkFont(weight="bold"))
        lbl_modo.pack(side="left", padx=15, pady=10)

        self.seg_modo = ctk.CTkSegmentedButton(
            frame_modo,
            values=["📦 Varrer Post Antigo (Todos)", "📡 Monitorar em Tempo Real"],
            command=self.ao_mudar_modo
        )
        self.seg_modo.set("📦 Varrer Post Antigo (Todos)")
        self.seg_modo.pack(side="left", fill="x", expand=True, padx=15, pady=10)

        # --- SEÇÃO 2: CREDENCIAIS ---
        frame_cred = ctk.CTkFrame(self.scroll)
        frame_cred.pack(fill="x", padx=10, pady=6)

        lbl_cred = ctk.CTkLabel(frame_cred, text="🔐 Credenciais do Instagram", font=ctk.CTkFont(weight="bold"))
        lbl_cred.grid(row=0, column=0, columnspan=4, padx=10, pady=(6, 2), sticky="w")

        ctk.CTkLabel(frame_cred, text="Usuário (@):").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        self.entry_user = ctk.CTkEntry(frame_cred, placeholder_text="seu_usuario")
        self.entry_user.grid(row=1, column=1, padx=5, pady=4, sticky="ew")

        ctk.CTkLabel(frame_cred, text="Senha:").grid(row=1, column=2, padx=10, pady=4, sticky="e")
        self.entry_pass = ctk.CTkEntry(frame_cred, placeholder_text="sua_senha", show="*")
        self.entry_pass.grid(row=1, column=3, padx=5, pady=4, sticky="ew")

        frame_cred.grid_columnconfigure((1, 3), weight=1)

        # --- SEÇÃO 3: CONFIGURAÇÃO DO POST E FILTROS ---
        frame_post = ctk.CTkFrame(self.scroll)
        frame_post.pack(fill="x", padx=10, pady=6)

        lbl_post = ctk.CTkLabel(frame_post, text="🎯 Publicação Alvo & Regras", font=ctk.CTkFont(weight="bold"))
        lbl_post.grid(row=0, column=0, columnspan=4, padx=10, pady=(6, 2), sticky="w")

        ctk.CTkLabel(frame_post, text="Link do Post:").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        self.entry_post_url = ctk.CTkEntry(frame_post, placeholder_text="https://www.instagram.com/p/... (ou /reel/...)")
        self.entry_post_url.grid(row=1, column=1, columnspan=3, padx=5, pady=4, sticky="ew")

        # Palavra-chave e opção de responder todos
        ctk.CTkLabel(frame_post, text="Palavra-chave:").grid(row=2, column=0, padx=10, pady=4, sticky="e")
        self.entry_keyword = ctk.CTkEntry(frame_post, placeholder_text="Ex: QUERO")
        self.entry_keyword.grid(row=2, column=1, padx=5, pady=4, sticky="ew")

        self.chk_responder_todos = ctk.CTkCheckBox(
            frame_post, 
            text="🔓 Responder TODOS (ignorar palavra-chave)",
            font=ctk.CTkFont(weight="bold"),
            command=self.ao_alternar_responder_todos
        )
        self.chk_responder_todos.grid(row=2, column=2, columnspan=2, padx=10, pady=4, sticky="w")

        # Ações
        self.chk_responder = ctk.CTkCheckBox(frame_post, text="Responder no Post")
        self.chk_responder.grid(row=3, column=1, padx=5, pady=4, sticky="w")

        self.chk_direct = ctk.CTkCheckBox(frame_post, text="Enviar Direct (DM)")
        self.chk_direct.grid(row=3, column=2, padx=10, pady=4, sticky="w")

        # Opções extras de varredura
        self.chk_pular_historico = ctk.CTkCheckBox(frame_post, text="Pular quem já recebeu antes")
        self.chk_pular_historico.select()
        self.chk_pular_historico.grid(row=4, column=1, padx=5, pady=(4, 8), sticky="w")

        ctk.CTkLabel(frame_post, text="Limite (0 = Todos):").grid(row=4, column=2, padx=10, pady=(4, 8), sticky="e")
        self.entry_limite = ctk.CTkEntry(frame_post, width=70)
        self.entry_limite.insert(0, "0")
        self.entry_limite.grid(row=4, column=3, padx=5, pady=(4, 8), sticky="w")

        frame_post.grid_columnconfigure(1, weight=1)

        # --- SEÇÃO 4: MENSAGENS E TEMPLATES ---
        frame_msgs = ctk.CTkFrame(self.scroll)
        frame_msgs.pack(fill="x", padx=10, pady=6)

        lbl_msgs = ctk.CTkLabel(frame_msgs, text="✍️ Mensagens (Uma variação por linha para rotação anti-spam)", font=ctk.CTkFont(weight="bold"))
        lbl_msgs.pack(anchor="w", padx=10, pady=(6, 2))

        ctk.CTkLabel(frame_msgs, text="Respostas ao Comentário no Post (marca o @ automaticamente):").pack(anchor="w", padx=10, pady=(2, 0))
        self.txt_respostas = ctk.CTkTextbox(frame_msgs, height=65)
        self.txt_respostas.pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkLabel(frame_msgs, text="Mensagens do Direct (DM com seu link ou material):").pack(anchor="w", padx=10, pady=(2, 0))
        self.txt_directs = ctk.CTkTextbox(frame_msgs, height=80)
        self.txt_directs.pack(fill="x", padx=10, pady=(2, 6))

        # --- SEÇÃO 5: PROGRESSO E CONTROLES ---
        frame_controles = ctk.CTkFrame(self.scroll)
        frame_controles.pack(fill="x", padx=10, pady=8)

        # Barra de progresso
        self.lbl_progresso = ctk.CTkLabel(frame_controles, text="Progresso: Aguardando início...", font=ctk.CTkFont(size=12))
        self.lbl_progresso.pack(anchor="w", padx=15, pady=(6, 2))

        self.bar_progresso = ctk.CTkProgressBar(frame_controles)
        self.bar_progresso.set(0)
        self.bar_progresso.pack(fill="x", padx=15, pady=(2, 8))

        # Botões
        frame_botoes = ctk.CTkFrame(frame_controles, fg_color="transparent")
        frame_botoes.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_iniciar = ctk.CTkButton(
            frame_botoes, 
            text="▶ PROCESSAR TODOS OS COMENTÁRIOS DO POST", 
            fg_color="#10b981", 
            hover_color="#059669",
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.iniciar_automacao
        )
        self.btn_iniciar.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_parar = ctk.CTkButton(
            frame_botoes, 
            text="⏹ PARAR", 
            fg_color="#ef4444", 
            hover_color="#dc2626",
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled",
            command=self.parar_automacao
        )
        self.btn_parar.pack(side="left", fill="x", expand=False, padx=5)

        self.btn_limpar_historico = ctk.CTkButton(
            frame_botoes,
            text="🗑️ Limpar Histórico",
            fg_color="#475569",
            hover_color="#334155",
            height=42,
            width=130,
            command=self.limpar_historico
        )
        self.btn_limpar_historico.pack(side="left", padx=5)

        # --- SEÇÃO 6: LOGS EM TEMPO REAL ---
        lbl_log = ctk.CTkLabel(self.scroll, text="📋 Histórico e Logs em Tempo Real", font=ctk.CTkFont(weight="bold"))
        lbl_log.pack(anchor="w", padx=10, pady=(6, 2))

        self.txt_log = ctk.CTkTextbox(self.scroll, height=180)
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(2, 6))

    def ao_alternar_responder_todos(self):
        if self.chk_responder_todos.get():
            self.entry_keyword.configure(state="disabled")
            self.log_gui("🔓 Opção marcada: O bot responderá a TODOS os comentários, sem exigir palavra-chave.")
        else:
            self.entry_keyword.configure(state="normal")
            self.log_gui("🎯 Opção desmarcada: O bot filtrará pela palavra-chave definida.")

    def ao_mudar_modo(self, valor):
        if "Post Antigo" in valor:
            self.btn_iniciar.configure(text="▶ PROCESSAR TODOS OS COMENTÁRIOS DO POST")
            self.log_gui("Modo: Varrer post antigo (pega todos os comentários existentes e dispara).")
        else:
            self.btn_iniciar.configure(text="▶ INICIAR MONITORAMENTO CONTÍNUO")
            self.log_gui("Modo: Monitoramento contínuo (aguarda novos comentários a cada 1 minuto).")

    def log_gui(self, mensagem):
        self.txt_log.after(0, self._append_log, mensagem)

    def _append_log(self, mensagem):
        self.txt_log.insert("end", f"{mensagem}\n")
        self.txt_log.see("end")

    def atualizar_progresso_gui(self, atual, total):
        def _atualizar():
            if total > 0:
                perc = atual / total
                self.bar_progresso.set(perc)
                self.lbl_progresso.configure(text=f"Progresso: {atual} de {total} processados ({int(perc * 100)}%)")
            else:
                self.bar_progresso.set(0)
                self.lbl_progresso.configure(text="Progresso: Concluído.")
        self.after(0, _atualizar)

    def limpar_historico(self):
        salvar_historico({"comentarios_respondidos": [], "usuarios_notificados": []})
        self.log_gui("🗑️ Histórico de comentários e directs limpo com sucesso! Agora você pode reprocessar posts.")

    def carregar_dados_iniciais(self):
        try:
            config = carregar_config()
            self.entry_user.insert(0, config.get("usuario", ""))
            self.entry_pass.insert(0, config.get("senha", ""))
            self.entry_post_url.insert(0, config.get("post_url", ""))
            self.entry_keyword.insert(0, config.get("palavra_chave", ""))
            
            responder_todos = config.get("responder_todos", False)
            if responder_todos:
                self.chk_responder_todos.select()
                self.entry_keyword.configure(state="disabled")
            else:
                self.chk_responder_todos.deselect()
                self.entry_keyword.configure(state="normal")

            if config.get("responder_comentario", True):
                self.chk_responder.select()
            else:
                self.chk_responder.deselect()

            if config.get("enviar_direct", True):
                self.chk_direct.select()
            else:
                self.chk_direct.deselect()

            self.txt_respostas.insert("1.0", "\n".join(config.get("respostas_comentario", [])))
            self.txt_directs.insert("1.0", "\n".join(config.get("mensagens_direct", [])))
            self.log_gui("Configurações carregadas com sucesso.")
        except Exception as e:
            self.log_gui(f"Erro ao carregar configurações: {e}")

    def salvar_config_atual(self):
        respostas = [r.strip() for r in self.txt_respostas.get("1.0", "end").strip().split("\n") if r.strip()]
        directs = [d.strip() for d in self.txt_directs.get("1.0", "end").strip().split("\n") if d.strip()]

        config = {
            "usuario": self.entry_user.get().strip(),
            "senha": self.entry_pass.get().strip(),
            "post_url": self.entry_post_url.get().strip(),
            "palavra_chave": self.entry_keyword.get().strip(),
            "responder_todos": bool(self.chk_responder_todos.get()),
            "responder_comentario": bool(self.chk_responder.get()),
            "respostas_comentario": respostas,
            "enviar_direct": bool(self.chk_direct.get()),
            "mensagens_direct": directs,
            "delay_entre_envios_segundos": [15, 30],
            "intervalo_checagem_minutos": 1
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return config

    def iniciar_automacao(self):
        config = self.salvar_config_atual()
        if not config["usuario"] or not config["senha"]:
            self.log_gui("❌ Preencha seu usuário e senha antes de iniciar.")
            return

        if not config["post_url"]:
            self.log_gui("❌ Cole o link do post que você quer processar.")
            return

        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self.bar_progresso.set(0)

        modo = self.seg_modo.get()
        eh_retroativo = "Post Antigo" in modo

        try:
            limite = int(self.entry_limite.get().strip() or "0")
        except ValueError:
            limite = 0

        pular_ja_atendidos = bool(self.chk_pular_historico.get())

        self.bot = InstagramCommentBot(
            callback_log=self.log_gui,
            callback_progresso=self.atualizar_progresso_gui
        )

        target_fn = self._executar_retroativo if eh_retroativo else self._executar_monitoramento
        args = (config, pular_ja_atendidos, limite) if eh_retroativo else (config,)

        self.bot_thread = threading.Thread(target=target_fn, args=args, daemon=True)
        self.bot_thread.start()

    def _executar_retroativo(self, config, pular_ja_atendidos, limite):
        try:
            self.log_gui("🚀 Iniciando varredura retroativa de comentários do post...")
            self.bot.processar_post_retroativo(
                config, 
                pular_ja_atendidos=pular_ja_atendidos, 
                limite_comentarios=limite
            )
        except Exception as e:
            self.log_gui(f"❌ Erro na varredura: {e}")
        finally:
            self.btn_iniciar.after(0, lambda: self.btn_iniciar.configure(state="normal"))
            self.btn_parar.after(0, lambda: self.btn_parar.configure(state="disabled"))

    def _executar_monitoramento(self, config):
        try:
            self.bot.iniciar_loop_monitoramento()
        except Exception as e:
            self.log_gui(f"❌ Erro no monitoramento: {e}")
        finally:
            self.btn_iniciar.after(0, lambda: self.btn_iniciar.configure(state="normal"))
            self.btn_parar.after(0, lambda: self.btn_parar.configure(state="disabled"))

    def parar_automacao(self):
        if self.bot:
            self.bot.parar()
        self.btn_parar.configure(state="disabled")

if __name__ == "__main__":
    app = App()
    app.mainloop()
