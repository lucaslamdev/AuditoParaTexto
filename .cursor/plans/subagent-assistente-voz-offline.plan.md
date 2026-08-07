---
name: Subagent dispatch — Assistente voz offline
overview: Dispatch Task-por-todo do plano assistente-voz-offline, com barreiras de fase e batches por parallel_group.
source_plan: .cursor/plans/assistente-voz-offline.plan.md
isProject: true
---

# Subagent Dispatch Plan — Assistente voz offline

## Objetivo

Implementar assistente de digitação por voz offline (hotkey + Vosk/Faster-Whisper + type/clipboard) no repo `c:\Users\lucas\Desktop\AuditoParaTexto`.

**Definition of done:** `assistente_voz.py` + `requirements.txt` + `README.md` compilam/documentam; config no topo cobre engines e modos; erros de mic/modelo/CUDA tratados.

**Non-goals:** GUI, cloud, .exe, fine-tune.

## Regras de execução (parent)

1. Uma **Task por todo id**; não mesclar todos.
2. **Barreira de fase:** só avança para fase N+1 quando todos os todos da fase N estão `completed`.
3. Dentro da fase: grupos **A → B → C**; no grupo ativo, lançar em paralelo todos com `depends_on` satisfeito.
4. Atualizar status no `assistente-voz-offline.plan.md` (`pending` → `in_progress` → `completed`).
5. Após cada batch: sintetizar artifacts/paths; falha = não marcar completed; re-dispatch ou corrigir antes de avançar.
6. **Conflito Phase 4:** 4-1 e 4-2 editam o mesmo arquivo — prompts exigem funções isoladas (`transcribe_vosk` / `transcribe_whisper`) e região `# === ENGINE: ... ===`; parent resolve merge se ambos retornarem diffs conflitantes (preferir re-run sequencial 4-2 após 4-1).

## Integração parent

Após cada batch: ler outputs listados, confirmar critérios de aceitação, atualizar plan statuses, passar paths concretos no prompt do próximo batch. Após cada fase: checklist curto do success criteria da fase. Se artifact ausente ou critério falhar → não abrir próxima fase.

---

## Phase 1 — Descoberta e contrato

### Batch 1-A (paralelo)

#### Task 1-1
| Campo | Valor |
|-------|--------|
| todo_id | `1-1` |
| description | `1-1 Estrutura projeto` |
| subagent_type | `explore` |
| readonly | true |
| run_in_background | false |

**Prompt:**
```
Todo: 1-1
Repo: c:\Users\lucas\Desktop\AuditoParaTexto (greenfield, possivelmente vazio).

Objetivo: mapear estrutura mínima para script único de assistente de voz offline.
Convenção alvo: assistente_voz.py + requirements.txt + README.md + models/ (gitignore sugerido).

Retorne:
1) Lista de arquivos a criar e propósito de cada um.
2) Onde fica a seção CONFIG.
3) Escreva o resumo em .cursor/plans/artifacts/estrutura-projeto.md (se explore for readonly e não puder escrever, devolva o markdown completo no return para o parent gravar).

Critério de sucesso: estrutura alinhada a "script simples" com config no topo; sem over-engineering (sem package multi-módulo).
Stop: não implementar código de aplicação.
```

#### Task 1-2
| Campo | Valor |
|-------|--------|
| todo_id | `1-2` |
| description | `1-2 Contrato config` |
| subagent_type | `generalPurpose` |
| readonly | false |
| run_in_background | false |

**Prompt:**
```
Todo: 1-2
Repo: c:\Users\lucas\Desktop\AuditoParaTexto

Objetivo: definir contrato de configuração do assistente de voz.

Variáveis obrigatórias e valores válidos:
- ENGINE: vosk | whisper
- WHISPER_SIZE: tiny | base | small | medium | turbo
- WHISPER_COMPUTE: int8 | float16 | float32
- DEVICE: cpu | cuda | auto
- OUTPUT_MODE: type | clipboard
- HOTKEY: string pynput (default Ctrl+Shift+Space)
- VOSK_MODEL_PATH: path local vosk-model-small-pt
- SAMPLE_RATE: 16000

Defaults recomendados: ENGINE=vosk, WHISPER_SIZE=base, WHISPER_COMPUTE=int8, DEVICE=cpu, OUTPUT_MODE=type.

Escreva tabela completa em: .cursor/plans/artifacts/config-contrato.md
Inclua notas: CUDA opcional; fallback CPU; idioma pt.

Critério: cobre todos os valores acima. Stop: não escrever assistente_voz.py ainda.
```

**Handoff Phase 1:** parent grava/confirma `estrutura-projeto.md` + `config-contrato.md` → libera Phase 2.

---

## Phase 2 — Scaffold e dependências

### Batch 2-A (paralelo)

#### Task 2-1
| Campo | Valor |
|-------|--------|
| todo_id | `2-1` |
| description | `2-1 requirements.txt` |
| subagent_type | `generalPurpose` |
| readonly | false |

**Prompt:**
```
Todo: 2-1
Inputs: .cursor/plans/artifacts/estrutura-projeto.md, .cursor/plans/artifacts/config-contrato.md
Output: requirements.txt na raiz do repo.

Incluir: sounddevice, soundfile, numpy, pynput, pyperclip, vosk, faster-whisper.
Comentários no arquivo (linhas #) explicando:
- install CPU padrão: pip install -r requirements.txt
- GPU Nvidia: instalar torch/cuBLAS conforme docs faster-whisper/ctranslate2; DEVICE=cuda; WHISPER_COMPUTE=float16 ou int8.

Critério: dependências suficientes para CPU offline. Não instalar nada no ambiente (só o arquivo). Mensagens em PT nos comentários.
```

#### Task 2-2
| Campo | Valor |
|-------|--------|
| todo_id | `2-2` |
| description | `2-2 Esqueleto script` |
| subagent_type | `generalPurpose` |
| readonly | false |

**Prompt:**
```
Todo: 2-2
Inputs: artifacts estrutura + config-contrato.
Output: assistente_voz.py

Criar esqueleto com:
- Docstring PT no topo
- Seção CONFIG com todas variáveis do contrato e defaults
- logging em português
- main() que imprime status "Aguardando hotkey..." e entra em loop placeholder (sem crash)
- stubs: start_recording, stop_recording, transcribe, deliver_text
- try/except para falha ao listar dispositivos de áudio (mensagem clara)

Critério: arquivo existe; constantes batem com config-contrato.md. Não implementar engines completos ainda.
Documentação inline em português.
```

**Handoff Phase 2:** `requirements.txt` + esqueleto presentes → Phase 3.

---

## Phase 3 — Áudio e hotkeys

### Batch 3-A

#### Task 3-1
| Campo | Valor |
|-------|--------|
| todo_id | `3-1` |
| description | `3-1 Gravação áudio` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 3-1
Editar assistente_voz.py apenas.

Implementar gravação com sounddevice InputStream:
- 16kHz mono float32/int16 → buffer thread-safe
- start_recording() / stop_recording() → bytes/numpy ou WAV temp via soundfile
- Erro se microfone não encontrado / PortAudio error: mensagem PT acionável
- Não bloquear a thread principal além do necessário

Critério: API start/stop clara; áudio 16k mono. Stop: não hotkey ainda (próximo todo).
```

### Batch 3-B (após 3-1)

#### Task 3-2
| Campo | Valor |
|-------|--------|
| todo_id | `3-2` |
| description | `3-2 Hotkey toggle` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 3-2
Editar assistente_voz.py. Depende de 3-1 concluído.

Implementar hotkey global com pynput:
- Mesmo atalho (HOTKEY) toggle: idle→recording→(stop+flag transcribe)
- Estados: idle | recording | transcribing (flags thread-safe)
- Feedback console: [REC] / [STOP] / [TRANSCRAVENDO]
- main() registra listener e mantém processo vivo
- Notas OS: Windows pode pedir elevação; macOS Accessibility; Linux uinput/input group

Critério: Ctrl+Shift+Space (ou HOTKEY) inicia e para. Não implementar Vosk/Whisper ainda (chamar stub transcribe).
```

**Handoff Phase 3:** gravação + hotkey OK → Phase 4.

---

## Phase 4 — Motores de transcrição

### Batch 4-A (paralelo com regiões isoladas)

#### Task 4-1
| Campo | Valor |
|-------|--------|
| todo_id | `4-1` |
| description | `4-1 Backend Vosk` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 4-1
Editar assistente_voz.py — APENAS adicionar/completar função(ões) sob marcador:
# === ENGINE: VOSK ===
# === FIM ENGINE: VOSK ===

Implementar:
- load_vosk_model(VOSK_MODEL_PATH)
- transcribe_vosk(audio) → str
- idioma PT; sample rate 16k
- Se pasta modelo ausente: erro com URL/instrução download vosk-model-small-pt (~31MB)

Não alterar seção Faster-Whisper. Não reformatar arquivo inteiro.
Critério: ENGINE=vosk usa este backend.
```

#### Task 4-2
| Campo | Valor |
|-------|--------|
| todo_id | `4-2` |
| description | `4-2 Backend Whisper` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 4-2
Editar assistente_voz.py — APENAS adicionar/completar função(ões) sob marcador:
# === ENGINE: WHISPER ===
# === FIM ENGINE: WHISPER ===

Implementar com faster_whisper.WhisperModel:
- sizes: tiny, base, small, medium, turbo
- device DEVICE (cpu/cuda/auto); se cuda falhar → fallback cpu + log
- compute_type WHISPER_COMPUTE (int8/float16/float32)
- language="pt"
- transcribe_whisper(audio) → str

Não alterar bloco Vosk. Não reformatar arquivo inteiro.
Critério: ENGINE=whisper + WHISPER_SIZE trocam só por config; default CPU funciona.
```

**Handoff Phase 4:** parent verifica ambos marcadores presentes e `transcribe()` despacha por ENGINE → Phase 5.

---

## Phase 5 — Saída e orquestração

### Batch 5-A

#### Task 5-1
| Campo | Valor |
|-------|--------|
| todo_id | `5-1` |
| description | `5-1 Type/clipboard` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 5-1
Editar assistente_voz.py.

Implementar deliver_text(texto):
- OUTPUT_MODE=clipboard → pyperclip.copy
- OUTPUT_MODE=type → digitar no foco (pynput); se Unicode problemático, fallback copy+Ctrl+V
- Tratar texto vazio
- Erros de permissão: mensagem PT

Critério: ambos modos cobertos.
```

### Batch 5-B (após 5-1)

#### Task 5-2
| Campo | Valor |
|-------|--------|
| todo_id | `5-2` |
| description | `5-2 Pipeline E2E` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 5-2
Editar assistente_voz.py.

Orquestrar: hotkey stop → transcribe(ENGINE) → deliver_text(OUTPUT_MODE).
Estados idle/recording/transcribing consistentes.
Tratar: mic ausente, modelo ausente, CUDA fail, áudio muito curto, texto vazio.
Lazy-load do modelo na primeira transcrição (ou no startup com log).
main() documentado e pronto para python assistente_voz.py.

Critério: fluxo único estável; comentários PT nas partes críticas.
```

**Handoff Phase 5:** pipeline completo → Phase 6.

---

## Phase 6 — Docs e verificação

### Batch 6-A

#### Task 6-1
| Campo | Valor |
|-------|--------|
| todo_id | `6-1` |
| description | `6-1 README PT` |
| subagent_type | `generalPurpose` |

**Prompt:**
```
Todo: 6-1
Inputs: assistente_voz.py, requirements.txt
Output: README.md em português.

Incluir:
1) pip install -r requirements.txt (comando completo)
2) GPU Nvidia / CUDA (opcional) — passos e aviso que default é CPU
3) Download modelo Vosk small PT e VOSK_MODEL_PATH
4) Como alternar ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, OUTPUT_MODE, HOTKEY
5) Permissões Windows / Linux / Mac
6) Como rodar: python assistente_voz.py

Critério: usuário novo instala e configura só com o README. Tom direto.
```

### Batch 6-B (após 6-1)

#### Task 6-2
| Campo | Valor |
|-------|--------|
| todo_id | `6-2` |
| description | `6-2 Verificar compile` |
| subagent_type | `shell` |
| readonly | true (sem editar app; pode escrever artifact de verificação) |

**Prompt:**
```
Todo: 6-2
Repo: c:\Users\lucas\Desktop\AuditoParaTexto

Rodar: python -m py_compile assistente_voz.py
Conferir que README menciona as mesmas variáveis do topo de assistente_voz.py.
Escrever checklist pass/fail em .cursor/plans/artifacts/verificacao.md

Critério: compile exit 0; inconsistências documentadas. Não alterar código a menos que compile falhe por sintaxe trivial — nesse caso reporte ao parent sem patch grande.
```

**Handoff final:** parent valida success criteria do plano fonte; marca todos completed; entrega resumo ao usuário com paths e como rodar.

---

## Ordem de lançamento (resumo)

| Ordem | Fase | Batch | Todos | Concorrência |
|------:|------|-------|-------|--------------|
| 1 | 1 | A | 1-1, 1-2 | 2 Tasks |
| 2 | 2 | A | 2-1, 2-2 | 2 Tasks |
| 3 | 3 | A | 3-1 | 1 Task |
| 4 | 3 | B | 3-2 | 1 Task |
| 5 | 4 | A | 4-1, 4-2 | 2 Tasks* |
| 6 | 5 | A | 5-1 | 1 Task |
| 7 | 5 | B | 5-2 | 1 Task |
| 8 | 6 | A | 6-1 | 1 Task |
| 9 | 6 | B | 6-2 | 1 Task |

\*Se merge conflict no mesmo arquivo, parent reexecuta 4-2 sequencialmente após 4-1.

**Total:** 12 Tasks · **picos de paralelismo:** 2

## Comando mental do parent (pseudo)

```
for phase in 1..6:
  for group in A, B, ...:
    spawn Tasks(group) in parallel
    await all
    synthesize + update statuses
    abort phase advance on failure
```
