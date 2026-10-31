#!/usr/bin/env python3
"""Seletor interativo seguro de provedores, sem guardar API keys ou disparar e-mails."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from urllib.parse import urlsplit

import diagnosticos
from cerebro_ia import ENV_KEYS, validar_url_api, ProvedorIndisponivel


def perguntar_int(prompt: str, padrao: int, maximo: int) -> int:
    raw = input(f"{prompt} [{padrao}]: ").strip()
    try:
        num = int(raw) if raw else padrao
    except ValueError:
        raise ValueError("Quantidade inválida")
    if not 1 <= num <= maximo:
        raise ValueError(f"Valor deve estar entre 1 e {maximo}")
    return num


def executar():
    opcoes = {
        "1": ("regras", "", ""),
        "2": ("ollama", "", "qwen3:1.7b"),
        "3": ("api", "openai", ""),
        "4": ("api", "compativel", ""),
        "5": ("api", "anthropic", ""),
        "6": ("api", "gemini", ""),
        "7": ("codex", "", ""),
        "8": ("claude", "", ""),
    }
    print("CSA | Escolha o cérebro para os diagnósticos (não envia e-mails)")
    print("1. Regras Python: gratuito, zero IA")
    print("2. Ollama local: gratuito, exige modelo instalado")
    print("3. OpenAI API: cobrada pela OpenAI, usa OPENAI_API_KEY ou LLM_API_KEY")
    print("4. API OpenAI-compatible: Groq, DeepSeek, OpenRouter, LM Studio etc.")
    print("5. Anthropic (Claude): API key")
    print("6. Google Gemini: API key")
    print("7. Codex CLI: login ChatGPT (limites do plano)")
    print("8. Claude CLI: login Claude Code (limites da assinatura)")
    escolha = input("Opção [1]: ").strip() or "1"
    if escolha not in opcoes:
        raise ValueError("Opção inválida")
    modo, provedor, modelo = opcoes[escolha]
    url = ""
    if modo == "claude":
        from cerebro_ia import verificar_claude
        verificar_claude()
        modelo = input("Modelo Claude (vazio: padrão da sua CLI): ").strip()
    if modo == "codex":
        try:
            diagnosticos.verificar_codex()
        except diagnosticos.ProvedorIndisponivel as exc:
            print(f"Codex não está pronto: {exc}")
            if input("Quer abrir o login oficial do Codex CLI agora? [S/N]: ").strip().lower() != "s":
                raise
            # Abre o fluxo de login oficial, sem ler, copiar ou salvar tokens.
            try:
                retorno = subprocess.run(["codex", "login"], check=False)
            except OSError as err:
                raise ValueError("Codex CLI não instalado ou inacessível") from err
            if retorno.returncode != 0:
                raise ValueError("Login do Codex não foi concluído")
            diagnosticos.verificar_codex()
        modelo = input("Modelo Codex (vazio: padrão da sua CLI): ").strip()
    elif modo == "ollama":
        modelo = input(f"Modelo Ollama [{modelo}]: ").strip() or modelo
    elif modo == "api":
        modelo = input("Nome EXATO do modelo autorizado pela sua conta/API: ").strip()
        if not modelo:
            raise ValueError("Modelo obrigatório")
        if provedor == "compativel":
            url = validar_url_api(input("Base URL da API, com /v1 se necessário: ").strip())
        local = provedor == "compativel" and (urlsplit(url).hostname or "").lower() in (
            "localhost", "127.0.0.1", "::1")
        if not local and not any(os.getenv(k) for k in ENV_KEYS[provedor]):
            key = getpass.getpass("Chave de API (oculta; somente memória): ").strip()
            if not key:
                raise ValueError("Chave não informada")
            os.environ["LLM_API_KEY"] = key
    limite = perguntar_int("Quantos sites processar?", 3, 100000)
    if modo in ("api", "codex", "claude"):
        print("AVISO: uso remoto pode cobrar por tokens ou consumir sua franquia do Codex.")
        if input("Autoriza até " + str(limite) + " requisições a esse provedor? [S/N]: ").strip().lower() != "s":
            print("Cancelado; nenhuma requisição de IA foi feita.")
            return 0
    args = ["diagnosticos.py", "diagnosticar", "--limite", str(limite), "--modo", modo]
    if modelo:
        args += ["--modelo", modelo]
    if provedor:
        args += ["--provedor", provedor]
    if url:
        args += ["--base-url", url]
    sys.argv = args
    result = diagnosticos.main()
    if result == 0:
        sys.argv = ["diagnosticos.py", "exportar"]
        result = diagnosticos.main()
    # Nunca transferir credencial para outro processo, nem salvar em arquivo.
    os.environ.pop("LLM_API_KEY", None)
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(executar())
    except (ValueError, ProvedorIndisponivel, diagnosticos.IAIndisponivel) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(1)
