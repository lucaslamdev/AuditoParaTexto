# Contrato de configuração — Assistente de voz

Documento de referência das variáveis de ambiente / config do assistente.  
**Stop:** não implementa `assistente_voz.py`.

## Tabela de variáveis

| Variável | Obrigatória | Valores válidos | Default recomendado | Descrição |
|---|---|---|---|---|
| `ENGINE` | sim | `vosk` \| `whisper` | `vosk` | Motor de reconhecimento de fala |
| `WHISPER_SIZE` | sim* | `tiny` \| `base` \| `small` \| `medium` \| `turbo` | `base` | Tamanho do modelo Whisper (*relevante quando `ENGINE=whisper`) |
| `WHISPER_COMPUTE` | sim* | `int8` \| `float16` \| `float32` | `int8` | Precisão de compute do Whisper (*relevante quando `ENGINE=whisper`) |
| `DEVICE` | sim | `cpu` \| `cuda` \| `auto` | `cpu` | Dispositivo de inferência |
| `OUTPUT_MODE` | sim | `type` \| `clipboard` | `type` | Destino do texto reconhecido (`type` = digitação; `clipboard` = área de transferência) |
| `HOTKEY` | sim | string no formato pynput | `Ctrl+Shift+Space` | Atalho global para ativar/desativar captura |
| `VOSK_MODEL_PATH` | sim* | path local absoluto ou relativo | path para `vosk-model-small-pt` | Caminho do modelo Vosk PT (*relevante quando `ENGINE=vosk`) |
| `SAMPLE_RATE` | sim | `16000` | `16000` | Taxa de amostragem do áudio (Hz); valor fixo contratual |

## Defaults recomendados (resumo)

```
ENGINE=vosk
WHISPER_SIZE=base
WHISPER_COMPUTE=int8
DEVICE=cpu
OUTPUT_MODE=type
HOTKEY=Ctrl+Shift+Space
VOSK_MODEL_PATH=<path-local>/vosk-model-small-pt
SAMPLE_RATE=16000
```

## Notas

1. **CUDA opcional** — `DEVICE=cuda` só é válido se houver GPU NVIDIA e runtime CUDA disponíveis; não é requisito de instalação.
2. **Fallback CPU** — com `DEVICE=auto`, preferir CUDA quando disponível; caso contrário (ou se CUDA falhar), usar `cpu`. Com `DEVICE=cuda` indisponível, a implementação deve degradar para CPU de forma segura.
3. **Idioma pt** — reconhecimento configurado para português (modelo Vosk `vosk-model-small-pt`; Whisper com idioma `pt`).
4. **HOTKEY** — string compatível com o formato de combinação do pynput (ex.: `Ctrl+Shift+Space`).
5. **SAMPLE_RATE** — contrato fixo em `16000`; outros valores estão fora do escopo deste contrato.
