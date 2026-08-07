# Estrutura do Projeto — Assistente de Voz Offline

**Repo:** `c:\Users\lucas\Desktop\AuditoParaTexto`  
**Convenção:** script único, config no topo, sem package multi-módulo  

## Árvore alvo

```
AuditoParaTexto/
├── assistente_voz.py
├── requirements.txt
├── README.md
├── .gitignore
├── models/
│   └── .gitkeep
└── .cursor/plans/
```

## Arquivos

| Arquivo | Propósito |
|---------|-----------|
| `assistente_voz.py` | Script único: CONFIG, áudio, hotkey, Vosk/Whisper, saída, main() |
| `requirements.txt` | Dependências pip + notas CUDA |
| `README.md` | Instalação e uso em PT |
| `.gitignore` | models/*, venv, __pycache__, wavs |
| `models/.gitkeep` | Pasta vazia versionada |

## CONFIG

Topo de `assistente_voz.py`, após imports, região `# === CONFIG ===`. Constantes UPPER_SNAKE_CASE. Sem .env/.yaml na v1.

Ordem do arquivo: CONFIG → LOGGING → ÁUDIO → HOTKEY → ENGINE VOSK → ENGINE WHISPER → SAÍDA → ORQUESTRAÇÃO → main()
