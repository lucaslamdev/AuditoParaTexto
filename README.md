# Assistente de voz offline

Captura áudio pelo atalho global, transcreve em português (Vosk ou Whisper) e entrega o texto digitando no foco ou copiando para a área de transferência.

Tudo é offline. A configuração fica em `config.json` (raiz do projeto), editável pela UI "vidro preto" ou direto no arquivo. Não há `.env`.

`python assistente_voz.py` agora abre a UI (overlay + painel de configurações). Se o PyWebView não estiver disponível, cai automaticamente no modo console (só hotkey).

## Requisitos

- Python 3.9+
- Microfone definido como dispositivo de entrada padrão
- Modelo Vosk PT (obrigatório se `ENGINE = "vosk"`, o default)

## 1. Instalação (CPU)

Na pasta do projeto:

```bash
pip install -r requirements.txt
```

O default é **CPU**. Whisper (faster-whisper) e Vosk funcionam sem GPU.

O `requirements.txt` já inclui o **`pywebview`** (usado pela UI). No **Windows**, a UI depende do runtime **Microsoft Edge WebView2** — ele costuma já vir instalado no Windows 10/11; se faltar, baixe em https://developer.microsoft.com/microsoft-edge/webview2/. Sem PyWebView/WebView2, o app roda em modo console.

## 2. GPU NVIDIA / CUDA (opcional)

Só precisa disso se quiser acelerar o **Whisper**. Vosk não usa CUDA.

1. Instale o driver NVIDIA e o runtime **CUDA 12 + cuDNN 9** (cuBLAS e cuDNN).  
   Documentação: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2).
2. No Linux, as libs também podem ir via pip:

   ```bash
   pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
   export LD_LIBRARY_PATH=$(python3 -c 'import os; import nvidia.cublas.lib; import nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))')
   ```

3. Em `assistente_voz.py`, na seção `CONFIG`:

   ```python
   ENGINE = "whisper"
   DEVICE = "cuda"          # ou "auto"
   WHISPER_COMPUTE = "float16"  # ou "int8"
   ```

Se CUDA falhar, o script cai para CPU sozinho.

Stacks antigos: CUDA 11 → `pip install --force-reinstall ctranslate2==3.24.0`. CUDA 12 + cuDNN 8 → `ctranslate2==4.4.0`.

## 3. Modelo Vosk (português)

Com `ENGINE = "vosk"` (default), baixe o modelo **vosk-model-small-pt** (~31 MB):

1. Abra https://alphacephei.com/vosk/models
2. Baixe **vosk-model-small-pt**
3. Extraia a pasta `vosk-model-small-pt` para `models/` na raiz do projeto:

   ```
   models/vosk-model-small-pt/
   ```

O caminho default em `CONFIG` é:

```python
VOSK_MODEL_PATH = str(Path(__file__).resolve().parent / "models" / "vosk-model-small-pt")
```

Se extrair em outro lugar, ajuste `VOSK_MODEL_PATH` para o caminho absoluto (ou relativo) dessa pasta.

Whisper baixa o modelo automaticamente na primeira execução (`WHISPER_SIZE`).

## 4. Configuração (`config.json`)

O `config.json` na raiz do projeto é a **fonte da verdade**. A UI lê, edita e persiste esse arquivo; se ele não existir, é criado automaticamente com os defaults na primeira execução. Editar direto o `config.json` também funciona — salve e rode de novo.

| Chave | Valores | Default | Função |
|---|---|---|---|
| `ENGINE` | `vosk` \| `whisper` | `vosk` | Motor de transcrição |
| `WHISPER_SIZE` | `tiny` \| `base` \| `small` \| `medium` \| `turbo` | `base` | Tamanho do Whisper |
| `WHISPER_COMPUTE` | `int8` \| `float16` \| `float32` | `int8` | Precisão do Whisper |
| `DEVICE` | `cpu` \| `cuda` \| `auto` | `cpu` | Dispositivo (Whisper) |
| `OUTPUT_MODE` | `type` \| `clipboard` | `type` | Digitar no foco ou só copiar |
| `HOTKEY` | string tipo pynput | `Ctrl+Shift+Space` | Atalho gravar / parar |
| `VOSK_MODEL_PATH` | caminho local | `models/vosk-model-small-pt` | Pasta do modelo Vosk |
| `SAMPLE_RATE` | `16000` | `16000` | Taxa de amostragem (Hz); valor fixo recomendado |
| `MIN_AUDIO_SECONDS` | número positivo | `0.3` | Descarta gravações mais curtas (ruído de toque) |

Exemplo de `config.json`:

```json
{
    "ENGINE": "whisper",
    "WHISPER_SIZE": "small",
    "WHISPER_COMPUTE": "int8",
    "DEVICE": "cpu",
    "OUTPUT_MODE": "clipboard",
    "HOTKEY": "Ctrl+Alt+V",
    "VOSK_MODEL_PATH": "models/vosk-model-small-pt",
    "SAMPLE_RATE": 16000,
    "MIN_AUDIO_SECONDS": 0.3
}
```

`HOTKEY` usa o formato `Ctrl+Shift+Space` (modificadores: Ctrl, Alt, Shift, Cmd/Win).

## 5. Permissões (hotkey e digitação)

O atalho é global (`pynput`). `OUTPUT_MODE = "type"` simula teclado; `"clipboard"` só copia.

**Windows**

- A captura global e a digitação podem exigir executar o terminal/Python **como administrador**.
- Confira se o app em foco aceita digitar/colar.
- Libere o microfone em Configurações → Privacidade → Microfone.

**Linux**

- Coloque o usuário no grupo `input` (e, se necessário, permissões de `uinput`):
  ```bash
  sudo usermod -aG input $USER
  ```
  Depois faça logout/login.
- X11 e Wayland se comportam de forma diferente; se o atalho não disparar, teste em sessão X11.
- Para a área de transferência: instale `xclip` ou `xsel`.

**macOS**

- Em Ajustes do Sistema → Privacidade e Segurança, conceda ao Terminal (ou ao Python):
  - **Acessibilidade** (digitação / simular teclado)
  - **Monitoramento de entrada** (hotkey global)
  - **Microfone**
- Reabra o terminal depois de autorizar.

## 6. Como rodar

```bash
python assistente_voz.py
```

Isso abre a UI "vidro preto" (overlay + painel de configurações). Se o PyWebView/WebView2 não estiver disponível, cai no modo console. Deixe o processo aberto; fechar a janela (ou Ctrl+C no console) encerra.

## 7. Interface

A UI tem duas partes:

- **Overlay** (pill compacta, sempre no topo): mostra o **estado** atual — **Ocioso**, **Gravando** ou **Transcrevendo** —, a **engine** ativa (Vosk/Whisper) e o **último texto** transcrito. O estado acompanha a hotkey em tempo real.
- **Painel de configurações**: o botão **⚙** no overlay abre as configurações. Alterar os campos e **Salvar** grava tudo em `config.json` e aplica na hora (trocar `ENGINE`/tamanho/precisão/dispositivo recarrega o modelo sob demanda). **Fechar** volta ao overlay.

## 8. Uso

1. Foque o campo onde o texto deve ir (se `OUTPUT_MODE = "type"`).
2. Pressione a hotkey (`Ctrl+Shift+Space` por padrão) → gravação começa.
3. Fale.
4. Pressione a **mesma** hotkey de novo → para, transcreve e entrega o texto.

Gravações muito curtas (&lt; 0,3 s) são ignoradas (ruído do toque).

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Microfone não encontrado | Defina um dispositivo de entrada padrão; feche apps que monopolizam o mic. |
| Modelo Vosk ausente | Extraia `vosk-model-small-pt` em `models/` ou ajuste `VOSK_MODEL_PATH`. |
| Hotkey não funciona | Revise as permissões da seção 5. |
| Texto não é digitado | Use `OUTPUT_MODE = "clipboard"` e cole manualmente, ou conceda Acessibilidade / rode como admin. |
| CUDA falhou | Normal: o Whisper volta para CPU. Confira driver + cuBLAS/cuDNN se quiser GPU. |
