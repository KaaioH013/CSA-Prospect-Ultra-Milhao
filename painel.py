#!/usr/bin/env python3
"""CSA Prospect: painel local. Zero envios SMTP, URLs apenas em localhost.

API padrão em 127.0.0.1; exige token CSRF para operações POST.
Nenhuma API key é persistida: ela existe apenas durante o processo solicitado.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import gestor
from pipeline import DB_DEFAULT, ROOT
from cerebro_ia import validar_url_api, ProvedorIndisponivel

UI = ROOT / "ui"
PROVIDERS = {"regras", "ollama", "codex", "claude", "openai", "anthropic", "gemini", "compativel"}
ACTIONS = {"auditar", "contatos", "diagnosticar", "sincronizar", "importar", "exportar"}


def bounded_int(value, lo, hi, default):
    try:
        i = int(value)
    except (ValueError, TypeError):
        i = default
    if not lo <= i <= hi:
        raise ValueError(f"Valor permitido: {lo} a {hi}")
    return i


def cli_status(binary, args):
    if not shutil.which(binary):
        return {"installed": False, "authenticated": False, "description": "CLI não instalado"}
    try:
        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LLM_API_KEY"):
            env.pop(key, None)
        proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=9, env=env)
        if proc.returncode:
            return {"installed": True, "authenticated": False, "description": "Login necessário"}
        if binary == "claude":
            data = json.loads(proc.stdout)
            ok = data.get("authMethod") == "claude.ai"
        else:
            output = (proc.stdout + proc.stderr).lower()
            ok = not ("api key" in output or "api_key" in output)
        return {"installed": True, "authenticated": ok,
                "description": "Login de assinatura identificado" if ok else "Verifique o modo de autenticação"}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return {"installed": True, "authenticated": False, "description": "Não foi possível verificar o login"}


def metrics(conn):
    """Lê contagens sem fabricar registros. Banco vazio vira gráfico vazio."""
    def count(table, where=""):
        return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]
    stage = {
        "empresas": count("websites"),
        "auditorias": count("auditorias", "WHERE status='ok'"),
        "diagnosticos": count("diagnosticos", "WHERE status='gerado'"),
        "contatos": count("contatos_publicos"),
        "leads": count("leads_campanha"),
        "aprovados": count("leads_campanha", "WHERE status='aprovado'"),
        "respostas": count("leads_campanha", "WHERE resposta_em IS NOT NULL"),
        "propostas": count("leads_campanha", "WHERE proposta_em IS NOT NULL"),
        "ganhos": count("leads_campanha", "WHERE ganho_em IS NOT NULL"),
        "bloqueios": count("bloqueios_prospeccao"),
    }
    top = [dict(r) for r in conn.execute("""SELECT a.dominio, a.pontos_prioridade AS pontos,
      a.status, COALESCE(e.nome,a.dominio) AS empresa, d.foco_evidencia AS evidencia
      FROM auditorias a LEFT JOIN estabelecimentos e ON e.id_fonte=(
        SELECT MIN(x.id_fonte) FROM estabelecimentos x WHERE x.dominio=a.dominio)
      LEFT JOIN diagnosticos d ON d.dominio=a.dominio
      WHERE a.status='ok' ORDER BY a.pontos_prioridade DESC, a.dominio LIMIT 8""")]
    reasons = []
    for r in conn.execute("SELECT problemas_json FROM auditorias WHERE status='ok' AND pontos_prioridade>0 ORDER BY pontos_prioridade DESC LIMIT 5000"):
        try:
            val = json.loads(r[0])
            for f in val:
                if isinstance(f, str): reasons.append(f[:60])
                elif isinstance(f, dict) and isinstance(f.get('codigo'), str): reasons.append(f['codigo'][:60])
        except (TypeError, ValueError):
            pass
    from collections import Counter
    issues = [{"nome": k, "total": v} for k, v in Counter(reasons).most_common(6)]
    leads = [dict(r) for r in conn.execute("""SELECT l.id,l.empresa,l.dominio,l.status,l.pontos_prioridade AS pontos,
      l.contato_tipo,l.contato_valor,l.assunto,l.mensagem,l.evidencia,l.campanha_id
      FROM leads_campanha l ORDER BY l.atualizado_em DESC LIMIT 8""")]
    activity = [dict(r) for r in conn.execute("""SELECT h.registrado_em AS data,h.evento,h.status_novo,
      l.empresa FROM historico_comercial h JOIN leads_campanha l ON l.id=h.lead_id
      ORDER BY h.id DESC LIMIT 12""")]
    funnel = [dict(r) for r in conn.execute("SELECT status AS nome,COUNT(*) AS total FROM leads_campanha GROUP BY status ORDER BY total DESC")]
    return {"counts": stage, "top_sites": top, "issues": issues,
            "recent_leads": leads, "activity": activity, "funnel": funnel,
            "campaigns": [dict(r) for r in conn.execute("SELECT id,nome,criada_em FROM campanhas ORDER BY id DESC")]}


class JobManager:
    """Apenas um trabalho em execução; subprocessos com argumentos validados."""
    def __init__(self, db):
        self.db = Path(db)
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.history = deque(maxlen=25)

    def snapshot(self):
        with self.lock:
            current = self.job
            return {"current": dict(current) if current else None,
                    "history": [dict(j) for j in list(self.history)[:8]]}

    def start(self, action, options):
        if action not in ACTIONS:
            raise ValueError("Ação indisponível")
        with self.lock:
            if self.job and self.job["status"] == "executando":
                raise ValueError("Já existe uma operação em execução. Encerre ou aguarde.")
            args, env = self._command(action, options)
            token = secrets.token_hex(8)
            j = {"id": token, "action": action, "status": "executando", "started": datetime.now(timezone.utc).isoformat(timespec='seconds'), "finished": None, "lines": [], "returncode": None}
            self.job = j
            # Não guardar API keys nem comando/env no histórico ou logs.
            threading.Thread(target=self._run, args=(j, args, env), daemon=True).start()
            return dict(j)

    def _command(self, action, opts):
        if action not in ACTIONS:
            raise ValueError("Operação não permitida")
        n = bounded_int(opts.get("limite"), 1, 10000, 30)
        env = os.environ.copy()
        base = [sys.executable]
        if action == "auditar":
            workers = bounded_int(opts.get("workers"), 1, 8, 4)
            return base + ["auditor.py", "--db", str(self.db), "auditar", "--limite", str(n), "--workers", str(workers)], env
        if action == "contatos":
            workers = bounded_int(opts.get("workers"), 1, 6, 3)
            return base + ["contatos.py", "--db", str(self.db), "coletar", "--limite", str(n), "--workers", str(workers)], env
        if action == "sincronizar":
            camp = str(opts.get("campanha", "CSA Geral")).strip()[:100]
            if len(camp) < 2: raise ValueError("Nome de campanha muito curto")
            return base + ["gestor.py", "--db", str(self.db), "sincronizar", "--campanha", camp, "--limite", str(n)], env
        if action == "importar":
            arquivo = str(opts.get("arquivo", "demo"))
            if arquivo == "demo":
                path = ROOT / "exemplos" / "empresas_exemplo.csv"
            else:
                if not re.fullmatch(r"[a-f0-9]{24}\.csv", arquivo):
                    raise ValueError("Identificador de CSV inválido")
                path = ROOT / "dados" / "importacoes" / arquivo
                if not path.is_file():
                    raise ValueError("CSV ainda não foi enviado")
            return base + ["pipeline.py", "--db", str(self.db), "importar-csv", "--arquivo", str(path)], env
        if action == "exportar":
            return base + ["gestor.py", "--db", str(self.db), "exportar", "--campanha", "CSA Geral"], env
        provider = str(opts.get("provider", "regras"))
        if provider not in PROVIDERS: raise ValueError("Provedor inválido")
        model = str(opts.get("modelo", "")).strip()
        if len(model) > 130 or re.search(r"[\r\n\x00]", model): raise ValueError("Modelo inválido")
        cmd = base + ["diagnosticos.py", "--db", str(self.db), "diagnosticar", "--limite", str(n), "--min-pontos", "1"]
        if provider in ("regras", "ollama", "codex", "claude"):
            cmd += ["--modo", provider]
        else:
            cmd += ["--modo", "api", "--provedor", provider]
            if not model: raise ValueError("Informe o nome exato do modelo")
        if model: cmd += ["--modelo", model]
        elif provider == "ollama": cmd += ["--modelo", "qwen3:1.7b"]
        if provider == "compativel":
            url = validar_url_api(str(opts.get("base_url", "")))
            cmd += ["--base-url", url]
        if provider in ("openai", "anthropic", "gemini", "compativel"):
            key = str(opts.get("api_key", "")).strip()
            if not key and provider != "compativel":
                # Acesso por variáveis de ambiente existentes é permitido.
                from cerebro_ia import ENV_KEYS
                if not any(env.get(k) for k in ENV_KEYS[provider]): raise ValueError("Informe uma API key para esta execução")
            if key:
                env['LLM_API_KEY'] = key
        else:
            # Protege logins CLI de serem substituídos por chave de API.
            for k in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
                if provider in ("codex", "claude"): env.pop(k, None)
        return cmd, env

    def _run(self, job, args, env):
        try:
            with subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1) as proc:
                with self.lock: self.process = proc
                for line in proc.stdout:
                    # Nunca logar secrets. A saída dos scripts existentes não inclui tokens.
                    with self.lock:
                        job["lines"] = (job["lines"] + [line.strip()[:350]])[-35:]
                status = "concluido" if proc.wait() == 0 else "erro"
                with self.lock: job["returncode"] = proc.returncode
        except (OSError, ValueError) as exc:
            status = "erro"
            with self.lock: job["lines"].append("Falha local ao iniciar o processo")
        finally:
            env.pop("LLM_API_KEY", None)
            with self.lock:
                if job["status"] != "cancelado": job["status"] = status
                job["finished"] = datetime.now(timezone.utc).isoformat(timespec='seconds')
                self.history.appendleft({k:v for k,v in job.items() if k != 'lines'})
                self.process = None

    def cancel(self):
        with self.lock:
            if not self.job or self.job['status'] != 'executando':
                raise ValueError('Nenhuma tarefa em execução')
            if not self.process:
                raise ValueError('Processo ainda iniciando, tente novamente')
            self.job['status'] = 'cancelado'
            self.process.terminate()


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "CSA-Painel/1.0"

    def _headers(self, status=200, mime='application/json; charset=utf-8', length=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if length is not None: self.send_header('Content-Length', str(length))
        self.end_headers()

    def respond(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False, default=str).encode()
        self._headers(code, length=len(data))
        self.wfile.write(data)

    def allowed(self):
        host = self.headers.get('Host','')
        return host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed(): return self.respond({'erro':'Host não autorizado'}, 403)
        path = urllib.parse.urlsplit(self.path).path
        if path in ('/', '/index.html', '/app.js', '/style.css'):
            f = UI / ('index.html' if path == '/' else path[1:])
            data = f.read_bytes()
            mime = ('text/html' if f.suffix == '.html' else 'text/javascript' if f.suffix == '.js' else 'text/css') + '; charset=utf-8'
            self._headers(200, mime, len(data)); self.wfile.write(data); return
        if path == '/api/csrf': return self.respond({'csrf': self.server.csrf})
        if path == '/api/metrics':
            with gestor.preparar(self.server.db) as conn:
                return self.respond(metrics(conn))
        if path == '/api/jobs': return self.respond(self.server.jobs.snapshot())
        if path == '/api/providers':
            return self.respond({'codex': cli_status('codex',['login','status']),
                                 'claude':cli_status('claude',['auth','status']),
                                 'ollama':{'installed':shutil.which('ollama') is not None},
                                 'api_env':{p:any(os.environ.get(k) for k in keys) for p,keys in {
                                     'openai':['OPENAI_API_KEY'],'anthropic':['ANTHROPIC_API_KEY'],
                                     'gemini':['GEMINI_API_KEY','GOOGLE_API_KEY'],
                                     'compativel':['LLM_API_KEY']}.items()}})
        if path == '/api/leads':
            q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            page=bounded_int(q.get('page',['1'])[0],1,100000,1)
            status=q.get('status',[''])[0]
            if status and status not in gestor.STATUSES: return self.respond({'erro':'Status inválido'},400)
            search=q.get('q',[''])[0][:80]
            with gestor.preparar(self.server.db) as conn:
                cid=gestor.campanha(conn,'CSA Geral')
                rows,total=gestor.listar(conn,cid,status,search,page,20)
                return self.respond({'items':[dict(r) for r in rows],'total':total,'page':page})
        return self.respond({'erro':'Rota desconhecida'},404)

    def do_POST(self):
        if not self.allowed(): return self.respond({'erro':'Host não autorizado'},403)
        origin=self.headers.get('Origin', '')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.respond({'erro':'Origem não autorizada'},403)
        if not secrets.compare_digest(self.headers.get('X-CSA-CSRF',''),self.server.csrf):
            return self.respond({'erro':'Token de segurança inválido'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= (2 * 1024 * 1024 if self.path == '/api/upload-csv' else 8192): raise ValueError('Tamanho de requisição inválido')
            if self.path == '/api/upload-csv':
                if self.headers.get('Content-Type','').split(';')[0] != 'text/csv':
                    raise ValueError('Informe um arquivo CSV')
                content = self.rfile.read(length)
                try:
                    sample = content.decode('utf-8-sig')
                except UnicodeError:
                    raise ValueError('CSV precisa estar em UTF-8')
                first = sample.splitlines()[0].lower() if sample else ''
                if not all(h in first for h in ('nome', 'site')):
                    raise ValueError('CSV deve conter pelo menos nome e site no cabeçalho')
                target = ROOT / 'dados' / 'importacoes'
                target.mkdir(parents=True,exist_ok=True)
                name = secrets.token_hex(12) + '.csv'
                (target/name).write_bytes(content)
                return self.respond({'arquivo':name})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError('JSON inválido')
            path=urllib.parse.urlsplit(self.path).path
            if path == '/api/providers/test':
                provider=str(data.get('provider',''))
                if provider == 'codex':
                    return self.respond({'provider':provider, **cli_status('codex',['login','status'])})
                if provider == 'claude':
                    return self.respond({'provider':provider, **cli_status('claude',['auth','status'])})
                if provider == 'regras':
                    return self.respond({'provider':provider,'authenticated':True,'description':'Regras disponíveis, sem API'})
                if provider == 'ollama':
                    import diagnosticos
                    diagnosticos.verificar_ollama(str(data.get('modelo','qwen3:1.7b'))[:130])
                    return self.respond({'provider':provider,'authenticated':True,'description':'Modelo Ollama disponível'})
                if provider not in ('openai','anthropic','gemini','compativel'):
                    raise ValueError('Provedor inválido')
                if data.get('confirmacao_custo') is not True:
                    raise ValueError('É preciso autorizar uma chamada pequena de teste, que pode ser cobrada')
                model=str(data.get('modelo','')).strip()
                key=str(data.get('api_key','')).strip()
                url=str(data.get('base_url','')).strip() if provider == 'compativel' else None
                if not model or len(model)>130: raise ValueError('Informe modelo válido')
                from cerebro_ia import executar_api
                result=executar_api(provider,model, 'Responda somente JSON: {"foco_codigo":"sem_title","tom":"direto","cta":"conversa","justificativa":"teste"}',20,url, api_key=key or None)
                return self.respond({'provider':provider,'authenticated':True,'description':'Resposta JSON recebida com sucesso','keys':list(result.keys())})
            if path == '/api/jobs':
                if data.get('action') == 'diagnosticar' and data.get('provider') not in ('regras','ollama') and data.get('confirmacao_custo') is not True:
                    raise ValueError('Confirme o uso de franquia ou cobrança da IA remota')
                return self.respond(self.server.jobs.start(data.get('action'),data))
            if path == '/api/jobs/cancel':
                self.server.jobs.cancel(); return self.respond({'ok':True})
            if path == '/api/leads/action':
                lid=bounded_int(data.get('id'),1,999999999,0)
                action=data.get('action')
                with gestor.preparar(self.server.db) as conn:
                    r=gestor.lead(conn,lid)
                    if action=='approve':
                        if data.get('conferi') is not True: raise ValueError('Marque que revisou o contato e o diagnóstico')
                        gestor.aprovar(conn,lid,True)
                    elif action=='save':
                        gestor.editar(conn,lid,str(data.get('assunto',''))[:200],str(data.get('mensagem',''))[:4000],str(data.get('anotacoes',''))[:1000])
                    elif action=='transition':
                        gestor.transicao(conn,lid,str(data.get('status','')),str(data.get('nota',''))[:400])
                    elif action=='block':
                        gestor.bloquear(conn,'dominio',r['dominio'],'Exclusão manual no painel')
                    else: raise ValueError('Ação desconhecida')
                return self.respond({'ok':True})
            return self.respond({'erro':'Rota desconhecida'},404)
        except (ValueError, KeyError, sqlite3.Error, ProvedorIndisponivel) as exc:
            return self.respond({'erro':str(exc)[:250]},400)

    def log_message(self,*args): pass


def run(db=DB_DEFAULT, port=8785, open_browser=True):
    with gestor.preparar(db): pass
    class PanelServer(ThreadingHTTPServer):
        daemon_threads = True
    srv=PanelServer(('127.0.0.1',port),PanelHandler)
    srv.db=db; srv.csrf=secrets.token_urlsafe(32); srv.jobs=JobManager(db)
    print(f'CSA Prospect em http://127.0.0.1:{port} | operações locais, nenhum envio automático',flush=True)
    if open_browser: webbrowser.open(f'http://127.0.0.1:{port}')
    try: srv.serve_forever()
    finally: srv.server_close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path,default=DB_DEFAULT)
    p.add_argument('--porta',type=int,default=8785)
    p.add_argument('--sem-navegador',action='store_true')
    a=p.parse_args()
    if not 1024 <= a.porta <= 65535: p.error('Porta inválida')
    run(a.db,a.porta,not a.sem_navegador)

if __name__=='__main__': main()
