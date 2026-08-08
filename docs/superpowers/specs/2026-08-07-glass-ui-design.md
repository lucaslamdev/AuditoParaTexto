# Design: UI vidro preto (híbrida) — Assistente de Voz

**Data:** 2026-08-07  
**Status:** aprovado (arquitetura + stack + config)  
**Repo:** AuditoParaTexto

## Decisões

| Tema | Escolha |
|------|---------|
| Formato | Híbrido: overlay compacto + painel de settings |
| Config | `config.json` = fonte da verdade |
| Stack | PyWebView + HTML/CSS/JS |
| Estética | Vidro preto (Cursor / OpenCode) |
| Hotkey | Continua global; overlay só reflete estado |

## Arquitetura

```
assistente_voz.py     motor (áudio, hotkey, STT, deliver) + bridge API
ui/
  index.html          shell (overlay + drawer settings)
  styles.css          glass tokens
  app.js              estado UI ↔ pywebview.api
config.json           ENGINE, WHISPER_*, DEVICE, OUTPUT_MODE, HOTKEY, paths
```

Fluxo: startup → load config.json → hotkey thread + webview → UI espelha estado → settings escrevem config e aplicam runtime quando possível.

## Overlay (sempre visível / always-on-top)

- Pill flutuante canto inferior-direito (arrastável se trivial; senão posição fixa v1)
- Conteúdo: indicador de estado (dot + label), engine atual, hotspot “⚙” / clique abre settings
- Estados: `idle` | `recording` | `transcribing` — animação sutil no recording (pulse)
- Última linha de texto transcrito (truncada, ~1 linha)
- Sem cards pesados; composição única glass

## Painel settings

- Drawer/modal glass sobre o overlay ou janela um pouco maior
- Campos: ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, DEVICE, OUTPUT_MODE, HOTKEY, VOSK_MODEL_PATH
- Salvar → grava `config.json` + chama `api.apply_config()`
- Feedback toast curto (“Salvo”)

## Visual (tokens)

```
--bg: rgba(12, 12, 14, 0.72)
--border: rgba(255,255,255,0.08)
--text: rgba(255,255,255,0.92)
--muted: rgba(255,255,255,0.45)
--accent: rgba(255,255,255,0.14)   /* sem roxo genérico */
--rec: #ff5c5c
blur: 24px; radius: 14–16px
font: system UI stack refinada (Segoe UI / Inter local opcional — evitar Inter CDN se possível; preferir "Segoe UI", "SF Pro", system-ui)
```

Fundo da janela: transparente; conteúdo com backdrop-filter blur. Frameless webview.

## Bridge Python ↔ JS

| Método | Direção | Função |
|--------|---------|--------|
| `get_config()` | JS←Py | lê config efetiva |
| `save_config(dict)` | JS→Py | valida + grava JSON + aplica |
| `get_status()` | JS←Py | `{state, last_text, engine}` |
| `open_settings` / eventos | Py→JS | `window.updateStatus(...)` via `evaluate_js` |

## Config schema (`config.json`)

Mesmas chaves do contrato atual + defaults iguais ao script.  
Migração: se `config.json` ausente, gerar a partir dos defaults do código.

## Non-goals (v1)

- Tray icon complexo, histórico longo, waveforms 3D, login, cloud
- Empacotar .exe
- Editar hotkey com “gravar tecla” (texto livre ok na v1)

## Critérios de sucesso

- Overlay glass preto legível no Windows
- Toggle hotkey atualiza UI em tempo quase real
- Settings persistem em `config.json` e sobrevivem ao restart
- CLI/motor existente continua funcional; UI é entrypoint preferido (`python assistente_voz.py` sobe UI)
