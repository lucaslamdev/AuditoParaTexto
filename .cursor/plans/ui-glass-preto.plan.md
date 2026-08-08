---
name: UI glass preto híbrida
overview: Overlay + settings em PyWebView (vidro preto), config.json como fonte da verdade, integrado ao motor existente.
todos:
  - id: 1-1
    content: "Objetivo: extrair/carregar schema config.json a partir do CONFIG atual e do design. Paths: config.json, docs/superpowers/specs/2026-08-07-glass-ui-design.md, assistente_voz.py. Output: load_config/save_config helpers + arquivo default. Critério: sem config.json gera defaults; com arquivo carrega e valida chaves."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: []
    inputs: ["assistente_voz.py", "docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
    outputs: ["config.json", "assistente_voz.py"]
  - id: 1-2
    content: "Objetivo: scaffold ui/ (index.html, styles.css, app.js) vazio com tokens glass e estrutura overlay+drawer. Critério: arquivos existem; HTML abre no browser sem Python ainda."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: []
    inputs: ["docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
    outputs: ["ui/index.html", "ui/styles.css", "ui/app.js"]
  - id: 2-1
    content: "Objetivo: implementar CSS vidro preto (blur, border, states idle/rec/transcribing, tipografia). Path: ui/styles.css + index. Critério: visual glass; sem tema roxo; contraste AA no texto principal."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["1-2"]
    inputs: ["ui/index.html", "ui/styles.css", "docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
    outputs: ["ui/styles.css", "ui/index.html"]
  - id: 2-2
    content: "Objetivo: UI JS — estados, abrir/fechar settings, formulário, polling/bridge stubs. Path: ui/app.js. Critério: drawer funciona offline com mock; chama pywebview.api quando disponível."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["1-2"]
    inputs: ["ui/app.js", "ui/index.html"]
    outputs: ["ui/app.js", "ui/index.html"]
  - id: 3-1
    content: "Objetivo: Api bridge PyWebView (get_config, save_config, get_status) + janela frameless transparente pointing to ui/index.html; adicionar pywebview em requirements.txt. Critério: python sobe janela glass; bridge round-trip config."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["1-1", "2-1", "2-2"]
    inputs: ["assistente_voz.py", "ui/", "config.json", "requirements.txt"]
    outputs: ["assistente_voz.py", "requirements.txt"]
  - id: 3-2
    content: "Objetivo: conectar estados do motor (idle/recording/transcribing/last_text) → evaluate_js updateStatus; settings apply_config atualiza globals runtime. Critério: hotkey muda overlay; salvar settings persiste e afeta ENGINE/OUTPUT_MODE."
    status: pending
    agent_type: generalPurpose
    parallel_group: B
    depends_on: ["3-1"]
    inputs: ["assistente_voz.py", "ui/app.js"]
    outputs: ["assistente_voz.py", "ui/app.js"]
  - id: 4-1
    content: "Objetivo: README — como rodar UI, config.json, deps pywebview/WebView2; atualizar .gitignore se necessário. Critério: usuário novo sobe UI só com README."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["3-2"]
    inputs: ["README.md", "requirements.txt", "config.json"]
    outputs: ["README.md"]
  - id: 4-2
    content: "Objetivo: py_compile + checklist visual/bridge em artifact. Critério: compile OK; bridge methods documentados."
    status: pending
    agent_type: shell
    parallel_group: B
    depends_on: ["4-1"]
    inputs: ["assistente_voz.py", "ui/", "README.md"]
    outputs: [".cursor/plans/artifacts/verificacao-ui-glass.md"]
isProject: true
phases:
  - name: Phase 1 - Config e scaffold UI
    todos:
      - id: 1-1
        content: "Objetivo: extrair/carregar schema config.json a partir do CONFIG atual e do design. Paths: config.json, docs/superpowers/specs/2026-08-07-glass-ui-design.md, assistente_voz.py. Output: load_config/save_config helpers + arquivo default. Critério: sem config.json gera defaults; com arquivo carrega e valida chaves."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: []
        inputs: ["assistente_voz.py", "docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
        outputs: ["config.json", "assistente_voz.py"]
      - id: 1-2
        content: "Objetivo: scaffold ui/ (index.html, styles.css, app.js) vazio com tokens glass e estrutura overlay+drawer. Critério: arquivos existem; HTML abre no browser sem Python ainda."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: []
        inputs: ["docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
        outputs: ["ui/index.html", "ui/styles.css", "ui/app.js"]
  - name: Phase 2 - Visual e interação frontend
    todos:
      - id: 2-1
        content: "Objetivo: implementar CSS vidro preto (blur, border, states idle/rec/transcribing, tipografia). Path: ui/styles.css + index. Critério: visual glass; sem tema roxo; contraste AA no texto principal."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["1-2"]
        inputs: ["ui/index.html", "ui/styles.css", "docs/superpowers/specs/2026-08-07-glass-ui-design.md"]
        outputs: ["ui/styles.css", "ui/index.html"]
      - id: 2-2
        content: "Objetivo: UI JS — estados, abrir/fechar settings, formulário, polling/bridge stubs. Path: ui/app.js. Critério: drawer funciona offline com mock; chama pywebview.api quando disponível."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["1-2"]
        inputs: ["ui/app.js", "ui/index.html"]
        outputs: ["ui/app.js", "ui/index.html"]
  - name: Phase 3 - Bridge e integração motor
    todos:
      - id: 3-1
        content: "Objetivo: Api bridge PyWebView (get_config, save_config, get_status) + janela frameless transparente pointing to ui/index.html; adicionar pywebview em requirements.txt. Critério: python sobe janela glass; bridge round-trip config."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["1-1", "2-1", "2-2"]
        inputs: ["assistente_voz.py", "ui/", "config.json", "requirements.txt"]
        outputs: ["assistente_voz.py", "requirements.txt"]
      - id: 3-2
        content: "Objetivo: conectar estados do motor (idle/recording/transcribing/last_text) → evaluate_js updateStatus; settings apply_config atualiza globals runtime. Critério: hotkey muda overlay; salvar settings persiste e afeta ENGINE/OUTPUT_MODE."
        status: pending
        agent_type: generalPurpose
        parallel_group: B
        depends_on: ["3-1"]
        inputs: ["assistente_voz.py", "ui/app.js"]
        outputs: ["assistente_voz.py", "ui/app.js"]
  - name: Phase 4 - Docs e verificação
    todos:
      - id: 4-1
        content: "Objetivo: README — como rodar UI, config.json, deps pywebview/WebView2; atualizar .gitignore se necessário. Critério: usuário novo sobe UI só com README."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["3-2"]
        inputs: ["README.md", "requirements.txt", "config.json"]
        outputs: ["README.md"]
      - id: 4-2
        content: "Objetivo: py_compile + checklist visual/bridge em artifact. Critério: compile OK; bridge methods documentados."
        status: pending
        agent_type: shell
        parallel_group: B
        depends_on: ["4-1"]
        inputs: ["assistente_voz.py", "ui/", "README.md"]
        outputs: [".cursor/plans/artifacts/verificacao-ui-glass.md"]
---

# UI vidro preto híbrida

Implementar overlay + painel settings com PyWebView, estética Cursor/OpenCode, `config.json` como fonte da verdade, integrado ao `assistente_voz.py` existente.

## Context

- Spec: `docs/superpowers/specs/2026-08-07-glass-ui-design.md`
- Motor STT/hotkey já existe em `assistente_voz.py`
- Decisões: híbrido C, config A, stack PyWebView

## Delegation Map

```
Phase 1
  Group A: [1-1], [1-2] paralelo (~2)

Phase 2 (após 1-2; 1-1 pode já ter terminado)
  Group A: [2-1], [2-2] paralelo (~2)
  Nota: ambos tocam ui/ — coordenar regiões HTML vs CSS vs JS

Phase 3
  Group A: [3-1]
  Group B: [3-2] depends 3-1

Phase 4
  Group A: [4-1]
  Group B: [4-2] depends 4-1
```

## Todos

### Phase 1
- **1-1** config.json + load/save no Python
- **1-2** scaffold ui/

### Phase 2
- **2-1** CSS glass
- **2-2** JS overlay/settings

### Phase 3
- **3-1** PyWebView + Api
- **3-2** Wire estados motor ↔ UI

### Phase 4
- **4-1** README
- **4-2** Verificação

## Success criteria

- Overlay glass + settings persistentes
- Hotkey atualiza UI
- `pip install -r requirements.txt` inclui pywebview
- Compile OK
