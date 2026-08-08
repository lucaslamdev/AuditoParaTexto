---
name: Subagent dispatch — UI glass preto
overview: Dispatch Task-por-todo do plano ui-glass-preto (PyWebView overlay + settings).
source_plan: .cursor/plans/ui-glass-preto.plan.md
spec: docs/superpowers/specs/2026-08-07-glass-ui-design.md
isProject: true
---

# Subagent Dispatch — UI glass preto

## Objetivo

Overlay flutuante + painel settings em vidro preto (PyWebView), `config.json` fonte da verdade, integrado ao motor em `assistente_voz.py`.

**Done:** UI sobe com `python assistente_voz.py`; hotkey atualiza overlay; settings salvam e aplicam; README atualizado.

**Non-goals:** tray complexo, .exe, waveform 3D, gravador de hotkey.

## Regras parent

1. 1 Task por todo; barreiras de fase.
2. Dentro da fase: groups A → B; paralelo no mesmo group.
3. Atualizar statuses em `ui-glass-preto.plan.md`.
4. Phase 2: 2-1 (CSS/HTML visual) e 2-2 (JS) — se conflito no HTML, preferir 2-1 primeiro depois 2-2, ou regiões `#overlay` / `#settings` / scripts separados.
5. Não regressar hotkey/STT existentes.

## Integração

Após cada batch: conferir outputs e critérios. Falha → não avançar fase.

---

## Phase 1 — Config e scaffold

### Batch 1-A (paralelo)

#### Task 1-1
| Campo | Valor |
|-------|--------|
| todo_id | `1-1` |
| description | `1-1 config.json` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 1-1
Repo: c:\Users\lucas\Desktop\AuditoParaTexto
Spec: docs/superpowers/specs/2026-08-07-glass-ui-design.md
Leia assistente_voz.py (seção CONFIG).

Implementar:
1) config.json na raiz com defaults iguais ao CONFIG atual (ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, DEVICE, OUTPUT_MODE, HOTKEY, VOSK_MODEL_PATH, SAMPLE_RATE, MIN_AUDIO_SECONDS).
2) Funções load_config() / save_config(dict) / apply_config_to_globals() em assistente_voz.py.
3) No startup (main), se config.json não existir, criar com defaults; senão carregar e aplicar às variáveis de módulo.

Não implementar UI ainda. Critério: round-trip load/save funciona; chaves validadas.
```

#### Task 1-2
| Campo | Valor |
|-------|--------|
| todo_id | `1-2` |
| description | `1-2 scaffold ui` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 1-2
Repo: c:\Users\lucas\Desktop\AuditoParaTexto
Spec: docs/superpowers/specs/2026-08-07-glass-ui-design.md

Criar pasta ui/ com:
- index.html — estrutura: #overlay (pill) + #settings (drawer oculto) + script app.js + link styles.css
- styles.css — só variáveis CSS placeholder dos tokens do spec
- app.js — stubs: setStatus(state), openSettings(), closeSettings()

Sem depender de frameworks. Sem roxo. Comentários mínimos em PT.
Critério: arquivos existem e HTML é válido.
```

**Handoff → Phase 2**

---

## Phase 2 — Visual e JS

### Batch 2-A

#### Task 2-1
| Campo | Valor |
|-------|--------|
| todo_id | `2-1` |
| description | `2-1 CSS glass` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 2-1
Editar ui/styles.css e ui/index.html (markup visual apenas).
Spec tokens: bg rgba(12,12,14,0.72), blur 24px, border rgba(255,255,255,0.08), accent neutro, --rec #ff5c5c.
Overlay pill canto inferior-direito; drawer settings glass.
Estados .state-idle .state-recording .state-transcribing (pulse no recording).
Sem tema roxo/indigo; body transparente para webview frameless.
Critério: visual “vidro preto” Cursor-like.
```

#### Task 2-2
| Campo | Valor |
|-------|--------|
| todo_id | `2-2` |
| description | `2-2 JS UI` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 2-2
Editar ui/app.js e campos do formulário em index.html se necessário.

Implementar:
- updateStatus({state, last_text, engine}) global para Python chamar
- open/close settings
- form com ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, DEVICE, OUTPUT_MODE, HOTKEY, VOSK_MODEL_PATH
- save: se window.pywebview?.api → save_config; senão mock localStorage
- get_config no load quando api disponível
- poll get_status a cada 400ms como fallback se eventos falharem

Critério: drawer e form funcionam; bridge-ready.
```

**Handoff → Phase 3**

---

## Phase 3 — Bridge e motor

### Batch 3-A

#### Task 3-1
| Campo | Valor |
|-------|--------|
| todo_id | `3-1` |
| description | `3-1 PyWebView Api` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 3-1
Editar assistente_voz.py + requirements.txt.
Leia ui/ e helpers de config.

Implementar classe Api com:
- get_config() -> dict
- save_config(data) -> {ok, error?}
- get_status() -> {state, last_text, engine}

Criar run_ui():
- webview.create_window(..., url=ui/index.html, frameless=True, on_top=True, transparent=True, easy_drag se suportado)
- tamanho compacto (~360x120 overlay; settings pode expandir via JS ou janela ~420x520)
- webview.start(debug=False)

main() deve iniciar hotkey em thread e depois run_ui() (ou hotkey antes do start blocking).
Adicionar pywebview em requirements.txt.

Critério: app abre janela; bridge get/save config funciona.
Windows: WebView2 runtime — documentar no log se falhar.
```

### Batch 3-B

#### Task 3-2
| Campo | Valor |
|-------|--------|
| todo_id | `3-2` |
| description | `3-2 Wire estados` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 3-2
Editar assistente_voz.py e ui/app.js se preciso.

Conectar:
- Mudanças de estado (idle/recording/transcribing) e last_text → window.evaluate_js("updateStatus(...)") quando webview window disponível
- save_config/apply atualiza ENGINE, OUTPUT_MODE, etc. em runtime (reload modelo na próxima transcrição se engine/size mudar — invalidar cache _vosk_model/_whisper_model)
- Overlay botão settings chama openSettings no JS

Critério: hotkey reflete no overlay; salvar settings persiste em config.json e afeta próxima transcrição.
Não quebrar gravação/STT.
```

**Handoff → Phase 4**

---

## Phase 4 — Docs e QA

### Batch 4-A

#### Task 4-1
| Campo | Valor |
|-------|--------|
| todo_id | `4-1` |
| description | `4-1 README UI` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 4-1
Atualizar README.md em PT:
- pip install (inclui pywebview)
- WebView2 no Windows
- python assistente_voz.py abre UI
- config.json como fonte da verdade
- overlay + settings
Não remover seções Vosk/Whisper existentes; integrar.
```

### Batch 4-B

#### Task 4-2
| Campo | Valor |
|-------|--------|
| todo_id | `4-2` |
| description | `4-2 Verificar UI` |
| subagent_type | `shell` |

**Prompt:**
```
Todo: 4-2
Repo: c:\Users\lucas\Desktop\AuditoParaTexto
python -m py_compile assistente_voz.py
Listar ui/* e confirmar config.json
Escrever .cursor/plans/artifacts/verificacao-ui-glass.md com checklist pass/fail (compile, arquivos ui, bridge methods no código via rg).
```

---

## Ordem de lançamento

| # | Fase | Batch | Todos | Concorrência |
|---|------|-------|-------|--------------|
| 1 | 1 | A | 1-1, 1-2 | 2 |
| 2 | 2 | A | 2-1, 2-2 | 2* |
| 3 | 3 | A | 3-1 | 1 |
| 4 | 3 | B | 3-2 | 1 |
| 5 | 4 | A | 4-1 | 1 |
| 6 | 4 | B | 4-2 | 1 |

\*Se conflito HTML: serializar 2-1 → 2-2.

**Total:** 8 Tasks · pico paralelismo 2
