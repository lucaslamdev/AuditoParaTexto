---
name: Assistente voz offline
overview: Script Python offline de digitação por voz com hotkeys globais, Vosk/Faster-Whisper e saída tipar ou clipboard.
todos:
  - id: 1-1
    content: "Objetivo: mapear estrutura mínima do projeto greenfield (script único + deps + README). Paths: raiz do repo AuditoParaTexto/. Output: lista de arquivos a criar e convenções. Critério: estrutura alinhada a 'script simples' com config no topo."
    status: pending
    agent_type: explore
    parallel_group: A
    depends_on: []
    inputs: ["c:/Users/lucas/Desktop/AuditoParaTexto/"]
    outputs: [".cursor/plans/artifacts/estrutura-projeto.md"]
  - id: 1-2
    content: "Objetivo: definir contrato de configuração (ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, OUTPUT_MODE, HOTKEY, SAMPLE_RATE, VOSK_MODEL_PATH, DEVICE). Paths: documentar em artifact. Output: tabela de variáveis com defaults e valores válidos. Critério: cobre Vosk + tiny/base/small/medium/turbo + int8/float16 + type/clipboard."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: []
    inputs: []
    outputs: [".cursor/plans/artifacts/config-contrato.md"]
  - id: 2-1
    content: "Objetivo: criar requirements.txt com sounddevice, soundfile, numpy, pynput, pyperclip, vosk, faster-whisper; comentários CUDA (cublas/cudnn via extras ou nota pip). Path: requirements.txt. Critério: pip install -r requirements.txt funciona em CPU; README parcial com bloco GPU opcional."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["1-1", "1-2"]
    inputs: [".cursor/plans/artifacts/estrutura-projeto.md", ".cursor/plans/artifacts/config-contrato.md"]
    outputs: ["requirements.txt"]
  - id: 2-2
    content: "Objetivo: implementar esqueleto assistente_voz.py com seção CONFIG, argparse opcional mínimo ou só constantes, logging em PT, main() e handlers de sinal. Path: assistente_voz.py. Critério: script importa e imprime 'aguardando hotkey' sem crash se microfone ausente (erro tratado)."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["1-1", "1-2"]
    inputs: [".cursor/plans/artifacts/estrutura-projeto.md", ".cursor/plans/artifacts/config-contrato.md"]
    outputs: ["assistente_voz.py"]
  - id: 3-1
    content: "Objetivo: módulo/funções de gravação com sounddevice (callback ou stream) + buffer numpy/soundfile WAV em memória/temp; detectar InputStream errors. Integrar em assistente_voz.py. Critério: start/stop gravação retorna áudio 16k mono PCM válido ou mensagem clara se mic não encontrado."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["2-2"]
    inputs: ["assistente_voz.py"]
    outputs: ["assistente_voz.py"]
  - id: 3-2
    content: "Objetivo: hotkey global com pynput (preferido vs keyboard por menos exigência de admin); toggle gravação no mesmo atalho; feedback console (REC/STOP). Path: assistente_voz.py. Critério: Ctrl+Shift+Space (configurável) inicia e para; thread-safe com flag de estado."
    status: pending
    agent_type: generalPurpose
    parallel_group: B
    depends_on: ["3-1"]
    inputs: ["assistente_voz.py"]
    outputs: ["assistente_voz.py"]
  - id: 4-1
    content: "Objetivo: backend Vosk offline PT — carregar vosk-model-small-pt de VOSK_MODEL_PATH, recognizer 16k, transcrever WAV/PCM, erros se modelo ausente com instrução de download. Path: assistente_voz.py. Critério: com modelo presente retorna texto PT; sem modelo mensagem acionável."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["3-2"]
    inputs: ["assistente_voz.py", ".cursor/plans/artifacts/config-contrato.md"]
    outputs: ["assistente_voz.py"]
  - id: 4-2
    content: "Objetivo: backend Faster-Whisper — WhisperModel(size, device=auto/cpu/cuda, compute_type int8|float16|float32), language=pt, sizes tiny|base|small|medium|turbo; fallback CPU se CUDA falhar. Path: assistente_voz.py. Critério: ENGINE=whisper troca tamanho só pela config; funciona em CPU por padrão."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["3-2"]
    inputs: ["assistente_voz.py", ".cursor/plans/artifacts/config-contrato.md"]
    outputs: ["assistente_voz.py"]
  - id: 5-1
    content: "Objetivo: saída tipar (pynput keyboard type / clipboard+paste fallback) ou só pyperclip.copy conforme OUTPUT_MODE; tratar Unicode PT. Path: assistente_voz.py. Critério: type injeta no foco; clipboard só copia; erros de permissão reportados."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["4-1", "4-2"]
    inputs: ["assistente_voz.py"]
    outputs: ["assistente_voz.py"]
  - id: 5-2
    content: "Objetivo: orquestrar pipeline completo hotkey→grava→transcreve→entrega; tratamento erros mic/modelo/CUDA; mensagens PT. Path: assistente_voz.py. Critério: um único fluxo end-to-end estável; estado idle/recording/transcribing."
    status: pending
    agent_type: generalPurpose
    parallel_group: B
    depends_on: ["5-1"]
    inputs: ["assistente_voz.py"]
    outputs: ["assistente_voz.py"]
  - id: 6-1
    content: "Objetivo: README.md em PT com pip install completo, nota CUDA/GPU Nvidia, download modelo Vosk, como alternar ENGINE/WHISPER_SIZE/OUTPUT_MODE/HOTKEY, permissões Windows/Linux/Mac (acessibilidade/input monitoring). Paths: README.md. Critério: usuário novo consegue instalar e rodar só lendo o README."
    status: pending
    agent_type: generalPurpose
    parallel_group: A
    depends_on: ["5-2", "2-1"]
    inputs: ["assistente_voz.py", "requirements.txt"]
    outputs: ["README.md"]
  - id: 6-2
    content: "Objetivo: verificação estática — python -m py_compile assistente_voz.py; revisar config docs vs código; checklist de aceitação. Path: shell + anotação. Critério: compile OK; variáveis de config documentadas batem com o script."
    status: pending
    agent_type: shell
    parallel_group: B
    depends_on: ["6-1"]
    inputs: ["assistente_voz.py", "README.md", "requirements.txt"]
    outputs: [".cursor/plans/artifacts/verificacao.md"]
isProject: true
phases:
  - name: Phase 1 - Descoberta e contrato
    todos:
      - id: 1-1
        content: "Objetivo: mapear estrutura mínima do projeto greenfield (script único + deps + README). Paths: raiz do repo AuditoParaTexto/. Output: lista de arquivos a criar e convenções. Critério: estrutura alinhada a 'script simples' com config no topo."
        status: pending
        agent_type: explore
        parallel_group: A
        depends_on: []
        inputs: ["c:/Users/lucas/Desktop/AuditoParaTexto/"]
        outputs: [".cursor/plans/artifacts/estrutura-projeto.md"]
      - id: 1-2
        content: "Objetivo: definir contrato de configuração (ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, OUTPUT_MODE, HOTKEY, SAMPLE_RATE, VOSK_MODEL_PATH, DEVICE). Paths: documentar em artifact. Output: tabela de variáveis com defaults e valores válidos. Critério: cobre Vosk + tiny/base/small/medium/turbo + int8/float16 + type/clipboard."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: []
        inputs: []
        outputs: [".cursor/plans/artifacts/config-contrato.md"]
  - name: Phase 2 - Scaffold e dependências
    todos:
      - id: 2-1
        content: "Objetivo: criar requirements.txt com sounddevice, soundfile, numpy, pynput, pyperclip, vosk, faster-whisper; comentários CUDA (cublas/cudnn via extras ou nota pip). Path: requirements.txt. Critério: pip install -r requirements.txt funciona em CPU; README parcial com bloco GPU opcional."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["1-1", "1-2"]
        inputs: [".cursor/plans/artifacts/estrutura-projeto.md", ".cursor/plans/artifacts/config-contrato.md"]
        outputs: ["requirements.txt"]
      - id: 2-2
        content: "Objetivo: implementar esqueleto assistente_voz.py com seção CONFIG, argparse opcional mínimo ou só constantes, logging em PT, main() e handlers de sinal. Path: assistente_voz.py. Critério: script importa e imprime 'aguardando hotkey' sem crash se microfone ausente (erro tratado)."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["1-1", "1-2"]
        inputs: [".cursor/plans/artifacts/estrutura-projeto.md", ".cursor/plans/artifacts/config-contrato.md"]
        outputs: ["assistente_voz.py"]
  - name: Phase 3 - Áudio e hotkeys
    todos:
      - id: 3-1
        content: "Objetivo: módulo/funções de gravação com sounddevice (callback ou stream) + buffer numpy/soundfile WAV em memória/temp; detectar InputStream errors. Integrar em assistente_voz.py. Critério: start/stop gravação retorna áudio 16k mono PCM válido ou mensagem clara se mic não encontrado."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["2-2"]
        inputs: ["assistente_voz.py"]
        outputs: ["assistente_voz.py"]
      - id: 3-2
        content: "Objetivo: hotkey global com pynput (preferido vs keyboard por menos exigência de admin); toggle gravação no mesmo atalho; feedback console (REC/STOP). Path: assistente_voz.py. Critério: Ctrl+Shift+Space (configurável) inicia e para; thread-safe com flag de estado."
        status: pending
        agent_type: generalPurpose
        parallel_group: B
        depends_on: ["3-1"]
        inputs: ["assistente_voz.py"]
        outputs: ["assistente_voz.py"]
  - name: Phase 4 - Motores de transcrição
    todos:
      - id: 4-1
        content: "Objetivo: backend Vosk offline PT — carregar vosk-model-small-pt de VOSK_MODEL_PATH, recognizer 16k, transcrever WAV/PCM, erros se modelo ausente com instrução de download. Path: assistente_voz.py. Critério: com modelo presente retorna texto PT; sem modelo mensagem acionável."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["3-2"]
        inputs: ["assistente_voz.py", ".cursor/plans/artifacts/config-contrato.md"]
        outputs: ["assistente_voz.py"]
      - id: 4-2
        content: "Objetivo: backend Faster-Whisper — WhisperModel(size, device=auto/cpu/cuda, compute_type int8|float16|float32), language=pt, sizes tiny|base|small|medium|turbo; fallback CPU se CUDA falhar. Path: assistente_voz.py. Critério: ENGINE=whisper troca tamanho só pela config; funciona em CPU por padrão."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["3-2"]
        inputs: ["assistente_voz.py", ".cursor/plans/artifacts/config-contrato.md"]
        outputs: ["assistente_voz.py"]
  - name: Phase 5 - Saída e orquestração
    todos:
      - id: 5-1
        content: "Objetivo: saída tipar (pynput keyboard type / clipboard+paste fallback) ou só pyperclip.copy conforme OUTPUT_MODE; tratar Unicode PT. Path: assistente_voz.py. Critério: type injeta no foco; clipboard só copia; erros de permissão reportados."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["4-1", "4-2"]
        inputs: ["assistente_voz.py"]
        outputs: ["assistente_voz.py"]
      - id: 5-2
        content: "Objetivo: orquestrar pipeline completo hotkey→grava→transcreve→entrega; tratamento erros mic/modelo/CUDA; mensagens PT. Path: assistente_voz.py. Critério: um único fluxo end-to-end estável; estado idle/recording/transcribing."
        status: pending
        agent_type: generalPurpose
        parallel_group: B
        depends_on: ["5-1"]
        inputs: ["assistente_voz.py"]
        outputs: ["assistente_voz.py"]
  - name: Phase 6 - Docs e verificação
    todos:
      - id: 6-1
        content: "Objetivo: README.md em PT com pip install completo, nota CUDA/GPU Nvidia, download modelo Vosk, como alternar ENGINE/WHISPER_SIZE/OUTPUT_MODE/HOTKEY, permissões Windows/Linux/Mac (acessibilidade/input monitoring). Paths: README.md. Critério: usuário novo consegue instalar e rodar só lendo o README."
        status: pending
        agent_type: generalPurpose
        parallel_group: A
        depends_on: ["5-2", "2-1"]
        inputs: ["assistente_voz.py", "requirements.txt"]
        outputs: ["README.md"]
      - id: 6-2
        content: "Objetivo: verificação estática — python -m py_compile assistente_voz.py; revisar config docs vs código; checklist de aceitação. Path: shell + anotação. Critério: compile OK; variáveis de config documentadas batem com o script."
        status: pending
        agent_type: shell
        parallel_group: B
        depends_on: ["6-1"]
        inputs: ["assistente_voz.py", "README.md", "requirements.txt"]
        outputs: [".cursor/plans/artifacts/verificacao.md"]
---

# Assistente de digitação por voz offline

Plano para entregar um **script Python único**, offline, multiplataforma (Windows/Linux/Mac), com hotkey global, motores Vosk e Faster-Whisper em PT, e saída tipar ou clipboard.

## Context

- Workspace `AuditoParaTexto` está **vazio** (greenfield).
- Prioridades: flexibilidade, privacidade (tudo local), usabilidade prática.
- Stack: `sounddevice` + `soundfile`, `pynput`, `pyperclip`, `vosk`, `faster-whisper`.
- GPU CUDA é **opcional**; default CPU.

## Assumptions (aceitas sem elicitação extra)

| Decisão | Valor |
|---------|--------|
| Forma | Um arquivo `assistente_voz.py` + `requirements.txt` + `README.md` |
| Config | Constantes no topo do script (não UI/menu nesta versão) |
| Hotkey lib | `pynput` (menos fricção de admin que `keyboard` no Windows) |
| Digitação | `pynput` Controller; fallback clipboard+Ctrl+V se type falhar em caracteres especiais |
| Idioma STT | Português (`language="pt"` / modelo Vosk PT) |
| Sample rate | 16000 Hz mono |
| Modelo Vosk | Path local configurável; download manual documentado (~31MB `vosk-model-small-pt`) |
| Whisper turbo | Usar id `turbo` do faster-whisper (alias community / modelo distil quando aplicável) |

## Requirements

1. Hotkey toggle: inicia gravação → segundo press para e transcreve.
2. `OUTPUT_MODE`: `type` | `clipboard`.
3. `ENGINE`: `vosk` | `whisper`.
4. Whisper sizes: `tiny`, `base`, `small`, `medium`, `turbo` + `compute_type` INT8/Float16.
5. Erros comuns tratados (mic ausente, modelo ausente, CUDA falha → CPU).
6. Docs: pip install + nota GPU + como trocar modelos.

## Non-goals

- GUI / tray icon.
- Diarização, pontuação avançada pós-processada, cloud APIs.
- Empacotamento `.exe` / instalador.
- Treino/fine-tune de modelos.

## Architecture (alvo)

```
[Hotkey pynput] → toggle
      ↓
[sounddevice InputStream] → buffer PCM 16k mono
      ↓
[ENGINE]
  vosk → KaldiRecognizer
  whisper → faster_whisper.WhisperModel
      ↓
[OUTPUT_MODE]
  type → pynput type / paste fallback
  clipboard → pyperclip
```

## Phases

### Phase 1 — Descoberta e contrato
Estrutura de arquivos + contrato de variáveis de config.

### Phase 2 — Scaffold e dependências
`requirements.txt` e esqueleto do script com CONFIG e logging.

### Phase 3 — Áudio e hotkeys
Gravação robusta + hotkey thread-safe.

### Phase 4 — Motores
Vosk e Faster-Whisper em paralelo após hotkeys estáveis.

### Phase 5 — Saída e orquestração
Entrega do texto + pipeline unificado.

### Phase 6 — Docs e verificação
README completo + `py_compile` + checklist.

## Delegation Map

```
Phase 1 - Descoberta e contrato
  Group A: [1-1 explore], [1-2 generalPurpose]  (paralelo; ~2 subagents)

Phase 2 - Scaffold e dependências  (após Phase 1)
  Group A: [2-1 generalPurpose], [2-2 generalPurpose]  (paralelo; ~2)
  Handoff: artifacts estrutura + config → requirements + esqueleto

Phase 3 - Áudio e hotkeys  (após Phase 2)
  Group A: [3-1 generalPurpose]
  Group B: [3-2 generalPurpose] (depends 3-1)
  Handoff: gravação estável antes de hotkey toggle

Phase 4 - Motores  (após Phase 3)
  Group A: [4-1 generalPurpose], [4-2 generalPurpose]  (paralelo; ~2)
  Atenção: ambos editam assistente_voz.py — serializar merges no parent se conflito

Phase 5 - Saída e orquestração  (após Phase 4)
  Group A: [5-1 generalPurpose]
  Group B: [5-2 generalPurpose] (depends 5-1)

Phase 6 - Docs e verificação  (após Phase 5)
  Group A: [6-1 generalPurpose]
  Group B: [6-2 shell] (depends 6-1)
```

**Nota de merge Phase 4:** como 4-1 e 4-2 tocam o mesmo arquivo, o parent deve: (a) disparar em paralelo com regiões nomeadas distintas (`transcribe_vosk` / `transcribe_whisper`), ou (b) rodar 4-2 após 4-1 se houver conflito de edição. Preferir (a) com funções isoladas e um único ponto de dispatch.

## Config preview (a implementar)

```python
ENGINE = "vosk"              # "vosk" | "whisper"
WHISPER_SIZE = "base"        # tiny | base | small | medium | turbo
WHISPER_COMPUTE = "int8"     # int8 | float16 | float32
DEVICE = "cpu"               # cpu | cuda | auto
OUTPUT_MODE = "type"         # type | clipboard
HOTKEY = "<ctrl>+<shift>+<space>"
VOSK_MODEL_PATH = "./models/vosk-model-small-pt"
SAMPLE_RATE = 16000
```

## Success criteria

- [ ] `pip install -r requirements.txt` documentado; bloco CUDA opcional no README.
- [ ] Hotkey toggle grava/para/transcreve em background.
- [ ] Troca Vosk ↔ Whisper e tamanhos só por variáveis.
- [ ] `OUTPUT_MODE` type e clipboard funcionam.
- [ ] Mic/modelo/CUDA com mensagens claras em português.
- [ ] `python -m py_compile assistente_voz.py` OK.

## Todos

### Phase 1 — Descoberta e contrato
- **1-1** [explore] Estrutura mínima do projeto
- **1-2** [generalPurpose] Contrato de configuração

### Phase 2 — Scaffold e dependências
- **2-1** [generalPurpose] `requirements.txt` (+ notas CUDA)
- **2-2** [generalPurpose] Esqueleto `assistente_voz.py`

### Phase 3 — Áudio e hotkeys
- **3-1** [generalPurpose] Gravação sounddevice
- **3-2** [generalPurpose] Hotkey pynput toggle

### Phase 4 — Motores de transcrição
- **4-1** [generalPurpose] Backend Vosk
- **4-2** [generalPurpose] Backend Faster-Whisper

### Phase 5 — Saída e orquestração
- **5-1** [generalPurpose] Type / clipboard
- **5-2** [generalPurpose] Pipeline end-to-end

### Phase 6 — Docs e verificação
- **6-1** [generalPurpose] README.md PT
- **6-2** [shell] py_compile + checklist

## Verdict / Recommendation

Executar via **subagent-build-plan** com barreiras de fase. Fase 4 exige coordenação de merge no mesmo arquivo. Entrega final: 3 arquivos na raiz (`assistente_voz.py`, `requirements.txt`, `README.md`) prontos para uso.

**Próximo passo:** abrir chat de execução e seguir `.cursor/plans/subagent-assistente-voz-offline.plan.md` (dispatch).
