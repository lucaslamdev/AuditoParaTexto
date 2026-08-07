"""
Assistente de voz offline.

Captura áudio via hotkey global, transcreve (Vosk ou Whisper) e entrega
o texto por digitação ou área de transferência. Configuração no topo.

Uso: python assistente_voz.py
Atalho padrão: Ctrl+Shift+Space (toggle gravar / parar + transcrever).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal, Optional, Union

import numpy as np
import pyperclip
import sounddevice as sd
import soundfile as sf
from pynput import keyboard

# === CONFIG ===
# Contrato: .cursor/plans/artifacts/config-contrato.md
# Constantes UPPER_SNAKE_CASE; sem .env/.yaml na v1.

ENGINE = "vosk"  # vosk | whisper
WHISPER_SIZE = "base"  # tiny | base | small | medium | turbo
WHISPER_COMPUTE = "int8"  # int8 | float16 | float32
DEVICE = "cpu"  # cpu | cuda | auto
OUTPUT_MODE = "type"  # type | clipboard
HOTKEY = "Ctrl+Shift+Space"  # formato pynput
VOSK_MODEL_PATH = str(Path(__file__).resolve().parent / "models" / "vosk-model-small-pt")
SAMPLE_RATE = 16000  # Hz — valor fixo contratual
MIN_AUDIO_SECONDS = 0.3  # gravações abaixo disso são descartadas (ruído de toque)

# === LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("assistente_voz")


# === ÁUDIO ===
# Gravação via sounddevice.InputStream: 16 kHz mono float32.
# Buffer em lista + lock (callback PortAudio em outra thread).
# start_recording() / stop_recording() — API pública; hotkey chama essa API.

_audio_chunks: list[np.ndarray] = []
_audio_lock = threading.Lock()
_input_stream: Optional[sd.InputStream] = None
_gravando = False


def _audio_callback(
    indata: np.ndarray,
    frames: int,
    time_info: object,
    status: sd.CallbackFlags,
) -> None:
    """Callback do InputStream: acumula blocos mono float32 de forma thread-safe."""
    if status:
        log.warning("Aviso PortAudio na captura: %s", status)
    # Cópia: indata é reutilizado pelo PortAudio após o retorno do callback
    with _audio_lock:
        _audio_chunks.append(indata.copy())


def _mensagem_erro_audio(exc: BaseException) -> str:
    """Monta mensagem em português com ação sugerida para falhas de microfone/PortAudio."""
    texto = str(exc).strip() or type(exc).__name__
    return (
        f"Não foi possível usar o microfone ({texto}). "
        "Verifique se há um microfone conectado e definido como padrão no sistema, "
        "se nenhum outro app está monopolizando o dispositivo e se o PortAudio/"
        "drivers de áudio estão instalados. Em seguida tente novamente."
    )


def listar_dispositivos_audio() -> None:
    """Lista dispositivos de entrada disponíveis via sounddevice."""
    try:
        dispositivos = sd.query_devices()
        padrao = sd.default.device
        log.info("Dispositivos de áudio (entrada):")
        for i, dev in enumerate(dispositivos):
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            marca = " [padrão]" if (isinstance(padrao, (list, tuple)) and padrao[0] == i) or padrao == i else ""
            log.info(
                "  [%d] %s — %d canais in, %.0f Hz%s",
                i,
                dev["name"],
                int(dev["max_input_channels"]),
                float(dev["default_samplerate"]),
                marca,
            )
        tem_entrada = any(int(d.get("max_input_channels", 0)) > 0 for d in dispositivos)
        if not tem_entrada:
            log.warning(
                "Nenhum microfone/dispositivo de entrada encontrado. "
                "A gravação falhará até haver um dispositivo disponível."
            )
    except sd.PortAudioError as exc:
        log.error("%s", _mensagem_erro_audio(exc))
    except Exception as exc:
        log.error("%s", _mensagem_erro_audio(exc))


def start_recording() -> None:
    """
    Inicia captura 16 kHz mono float32 em stream não bloqueante.

    O callback preenche um buffer thread-safe; a thread principal só abre o stream.
    Chame stop_recording() para obter o áudio. Não lança hotkey — só gravação.
    """
    global _input_stream, _gravando

    if _gravando:
        log.warning("Gravação já em andamento; ignore start_recording duplicado.")
        return

    with _audio_lock:
        _audio_chunks.clear()

    try:
        # Garante que existe pelo menos um dispositivo de entrada
        dispositivos = sd.query_devices()
        tem_entrada = any(int(d.get("max_input_channels", 0)) > 0 for d in dispositivos)
        if not tem_entrada:
            raise sd.PortAudioError("nenhum dispositivo de entrada encontrado")

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=_audio_callback,
        )
        stream.start()
    except sd.PortAudioError as exc:
        msg = _mensagem_erro_audio(exc)
        log.error("%s", msg)
        raise RuntimeError(msg) from exc
    except OSError as exc:
        msg = _mensagem_erro_audio(exc)
        log.error("%s", msg)
        raise RuntimeError(msg) from exc

    _input_stream = stream
    _gravando = True
    log.info("Gravação iniciada (%d Hz, mono, float32).", SAMPLE_RATE)


def stop_recording(*, as_wav: bool = False) -> Optional[Union[np.ndarray, Path]]:
    """
    Para a captura e devolve o áudio gravado.

    Por padrão retorna ndarray float32 shape (N,) a SAMPLE_RATE Hz.
    Com as_wav=True, grava WAV temporário via soundfile e retorna o Path.
    Se não houver gravação ativa ou áudio vazio, retorna None.
    """
    global _input_stream, _gravando

    if not _gravando and _input_stream is None:
        log.info("stop_recording: nenhuma gravação ativa.")
        return None

    stream = _input_stream
    _input_stream = None
    _gravando = False

    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except sd.PortAudioError as exc:
            log.error("%s", _mensagem_erro_audio(exc))
        except Exception as exc:
            log.warning("Falha ao fechar o stream de áudio: %s", exc)

    with _audio_lock:
        chunks = list(_audio_chunks)
        _audio_chunks.clear()

    if not chunks:
        log.warning("stop_recording: buffer vazio — nada gravado.")
        return None

    audio = np.concatenate(chunks, axis=0)
    # Garante vetor 1-D mono (N,) mesmo se o stream entregou (N, 1)
    if audio.ndim > 1:
        audio = audio.reshape(-1)
    audio = np.asarray(audio, dtype=np.float32)

    duracao = audio.shape[0] / float(SAMPLE_RATE)
    log.info("Gravação encerrada: %.2fs, %d amostras.", duracao, audio.shape[0])

    if not as_wav:
        return audio

    # WAV temp para pipelines que preferem arquivo em disco
    fd, nome = tempfile.mkstemp(prefix="assistente_voz_", suffix=".wav")
    path = Path(nome)
    try:
        # Fecha o fd do mkstemp antes de o soundfile abrir o path (Windows)
        os.close(fd)
        sf.write(str(path), audio, SAMPLE_RATE, subtype="FLOAT")
    except Exception as exc:
        log.error("Falha ao gravar WAV temporário: %s", exc)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return audio

    log.info("Áudio salvo em WAV temporário: %s", path)
    return path


# === HOTKEY ===
# Hotkey global via pynput.keyboard.GlobalHotKeys.
# Permissões por SO (comentário obrigatório):
# - Windows: captura global pode exigir execução elevada (admin) em alguns ambientes.
# - macOS: conceder Acessibilidade (Accessibility) ao terminal/Python em Preferências do Sistema.
# - Linux: usuário no grupo `input` (e às vezes permissões uinput); X11/Wayland variam.

EstadoHotkey = Literal["idle", "recording", "transcribing"]

_estado: EstadoHotkey = "idle"
_estado_lock = threading.Lock()

# Mapa de modificadores legíveis (HOTKEY) → tokens pynput GlobalHotKeys
_HOTKEY_MODS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "cmd": "cmd",
    "command": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "super": "cmd",
}


def hotkey_para_pynput(hotkey: str) -> str:
    """
    Converte string tipo 'Ctrl+Shift+Space' no formato GlobalHotKeys do pynput.

    Exemplo: 'Ctrl+Shift+Space' → '<ctrl>+<shift>+<space>'
    """
    partes = [p.strip() for p in hotkey.split("+") if p.strip()]
    if not partes:
        raise ValueError(f"HOTKEY inválida (vazia): {hotkey!r}")

    tokens: list[str] = []
    for parte in partes:
        chave = parte.lower()
        nome = _HOTKEY_MODS.get(chave, chave)
        tokens.append(f"<{nome}>")
    return "+".join(tokens)


def _set_estado(novo: EstadoHotkey) -> None:
    global _estado
    with _estado_lock:
        _estado = novo


def _get_estado() -> EstadoHotkey:
    with _estado_lock:
        return _estado


def _pipeline_apos_parar() -> None:
    """
    Pipeline único pós-[STOP]: stop_recording → transcribe(ENGINE) → deliver_text.

    Roda em thread dedicada para não bloquear o callback do pynput.
    Sempre devolve o estado para idle no finally.
    """
    print("[TRANSCRAVENDO]", flush=True)
    log.info("[TRANSCRAVENDO] processando áudio (ENGINE=%s)...", ENGINE)
    try:
        audio = stop_recording()
        if audio is None:
            log.warning("Nenhum áudio capturado — nada a transcrever.")
            return
        if not isinstance(audio, np.ndarray):
            # Pipeline usa ndarray; WAV temp (as_wav) não entra neste fluxo
            log.warning("Áudio inesperado (%s) — esperado ndarray float32.", type(audio).__name__)
            return

        duracao = audio.shape[0] / float(SAMPLE_RATE)
        if duracao < MIN_AUDIO_SECONDS:
            log.warning(
                "Áudio muito curto (%.2fs < %.2fs) — ignorado.",
                duracao,
                MIN_AUDIO_SECONDS,
            )
            return

        texto = transcribe(audio)
        if not texto or not str(texto).strip():
            log.info("Transcrição vazia — nada a entregar.")
            return

        log.info(
            "Entregando texto via OUTPUT_MODE=%s (%d chars).",
            OUTPUT_MODE,
            len(texto),
        )
        deliver_text(texto)
    except RuntimeError as exc:
        # modelo ausente, ENGINE inválido, CUDA/CPU falhou, etc.
        log.error("%s", exc)
    except Exception as exc:
        log.error("Falha no pipeline pós-gravação: %s", exc)
    finally:
        _set_estado("idle")
        log.info("Estado: idle (pronto para nova gravação).")


def _on_hotkey() -> None:
    """
    Toggle no mesmo atalho: idle→recording→(stop + transcribe + deliver_text).

    Ignora pressão enquanto já está em transcribing (thread-safe).
    Mic ausente: start_recording falha → volta a idle com log em PT.
    """
    global _estado
    with _estado_lock:
        atual = _estado
        if atual == "transcribing":
            log.debug("Hotkey ignorada: transcrição em andamento.")
            return
        if atual == "idle":
            # Reserva o estado antes de sair do lock (evita double-start)
            _estado = "recording"
            iniciar = True
        else:
            # recording → transcribing (pipeline roda em background)
            _estado = "transcribing"
            iniciar = False

    if iniciar:
        try:
            start_recording()
        except Exception as exc:
            _set_estado("idle")
            log.error("Não foi possível iniciar gravação: %s", exc)
            return
        print("[REC]", flush=True)
        log.info("[REC] gravação ativa — pressione %s de novo para parar.", HOTKEY)
        return

    print("[STOP]", flush=True)
    log.info("[STOP] encerrando gravação e disparando transcrição...")
    threading.Thread(
        target=_pipeline_apos_parar,
        name="transcribe-pipeline",
        daemon=True,
    ).start()


def configurar_hotkey() -> keyboard.GlobalHotKeys:
    """
    Registra a hotkey global (pynput) e inicia o listener em thread daemon.

    Retorna a instância GlobalHotKeys para o main() manter vivo / parar no exit.
    """
    combo = hotkey_para_pynput(HOTKEY)
    listener = keyboard.GlobalHotKeys({combo: _on_hotkey})
    listener.start()
    log.info("Hotkey global ativa: %s → %s", HOTKEY, combo)
    return listener


# === ENGINE: VOSK ===
# Implementação do motor Vosk (modelo PT em VOSK_MODEL_PATH).

_vosk_model = None  # cache do vosk.Model (carregado sob demanda)


def load_vosk_model(path: str = VOSK_MODEL_PATH):
    """
    Carrega o modelo Vosk a partir de ``path``, com cache em variável de módulo.

    Raises:
        RuntimeError: se a pasta do modelo não existir (com instrução de download).
    """
    global _vosk_model

    if _vosk_model is not None:
        log.debug("Modelo Vosk já em memória (cache).")
        return _vosk_model

    model_dir = Path(path)
    if not model_dir.is_dir():
        raise RuntimeError(
            f"Modelo Vosk não encontrado em '{model_dir}'. "
            "Baixe o vosk-model-small-pt (~31MB) em "
            "https://alphacephei.com/vosk/models , extraia a pasta "
            f"vosk-model-small-pt e coloque-a em '{model_dir}' "
            "(ou ajuste VOSK_MODEL_PATH)."
        )

    from vosk import Model

    log.info("Lazy-load: carregando modelo Vosk pela primeira vez: %s", model_dir)
    _vosk_model = Model(str(model_dir))
    log.info("Modelo Vosk carregado e em cache.")
    return _vosk_model


def transcribe_vosk(audio: np.ndarray) -> str:
    """
    Transcreve áudio mono float32 em [-1, 1] com Vosk (KaldiRecognizer).

    Converte para PCM int16 e reconhece a SAMPLE_RATE Hz.
    Retorna string vazia se o áudio for nulo/vazio.
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""

    import json

    from vosk import KaldiRecognizer

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    # float32 [-1, 1] → PCM linear 16-bit (little-endian) exigido pelo Vosk
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)

    model = load_vosk_model(VOSK_MODEL_PATH)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    # Alimenta em blocos (~0,25 s) para não estourar buffers internos
    bloco = SAMPLE_RATE // 4
    for i in range(0, pcm.shape[0], bloco):
        recognizer.AcceptWaveform(pcm[i : i + bloco].tobytes())

    resultado = json.loads(recognizer.FinalResult())
    texto = str(resultado.get("text") or "").strip()
    log.info("Vosk: %d amostras → %d chars.", pcm.shape[0], len(texto))
    return texto


# === FIM ENGINE: VOSK ===


# === ENGINE: WHISPER ===
# Implementação do motor Whisper (tamanho, compute e DEVICE).

_whisper_model = None  # cache do faster_whisper.WhisperModel (carregado sob demanda)

_WHISPER_SIZES = frozenset({"tiny", "base", "small", "medium", "turbo"})
_WHISPER_COMPUTE_TYPES = frozenset({"int8", "float16", "float32"})
_WHISPER_DEVICES = frozenset({"cpu", "cuda", "auto"})


def load_whisper_model():
    """
    Carrega ``faster_whisper.WhisperModel`` com cache em variável de módulo.

    Usa ``WHISPER_SIZE``, ``DEVICE`` e ``WHISPER_COMPUTE`` da configuração.
    Se ``DEVICE`` for ``cuda``/``auto`` e a carga falhar, faz fallback para CPU
    e registra o aviso em português.
    """
    global _whisper_model

    if _whisper_model is not None:
        log.debug("Modelo Whisper já em memória (cache).")
        return _whisper_model

    from faster_whisper import WhisperModel

    size = (WHISPER_SIZE or "base").strip().lower()
    if size not in _WHISPER_SIZES:
        log.warning(
            "WHISPER_SIZE=%r inválido; usando 'base'. Válidos: %s.",
            WHISPER_SIZE,
            ", ".join(sorted(_WHISPER_SIZES)),
        )
        size = "base"

    compute = (WHISPER_COMPUTE or "int8").strip().lower()
    if compute not in _WHISPER_COMPUTE_TYPES:
        log.warning(
            "WHISPER_COMPUTE=%r inválido; usando 'int8'. Válidos: %s.",
            WHISPER_COMPUTE,
            ", ".join(sorted(_WHISPER_COMPUTE_TYPES)),
        )
        compute = "int8"

    device = (DEVICE or "cpu").strip().lower()
    if device not in _WHISPER_DEVICES:
        log.warning(
            "DEVICE=%r inválido; usando 'cpu'. Válidos: %s.",
            DEVICE,
            ", ".join(sorted(_WHISPER_DEVICES)),
        )
        device = "cpu"

    log.info(
        "Lazy-load: carregando modelo Whisper pela primeira vez "
        "(size=%s device=%s compute_type=%s)",
        size,
        device,
        compute,
    )
    try:
        _whisper_model = WhisperModel(size, device=device, compute_type=compute)
    except Exception as exc:
        if device in ("cuda", "auto"):
            log.warning(
                "Falha ao usar dispositivo '%s' para Whisper (%s). "
                "Alternando para CPU.",
                device,
                exc,
            )
            try:
                _whisper_model = WhisperModel(size, device="cpu", compute_type=compute)
            except Exception as exc_cpu:
                raise RuntimeError(
                    f"Falha ao carregar Whisper mesmo em CPU ({exc_cpu}). "
                    "Verifique faster-whisper/ctranslate2 e o tamanho do modelo."
                ) from exc_cpu
        else:
            raise RuntimeError(
                f"Falha ao carregar Whisper em CPU ({exc}). "
                "Verifique faster-whisper/ctranslate2 e o tamanho do modelo."
            ) from exc

    log.info("Modelo Whisper carregado e em cache (size=%s).", size)
    return _whisper_model


def transcribe_whisper(audio: np.ndarray) -> str:
    """
    Transcreve áudio mono float32 em [-1, 1] com Faster-Whisper (idioma ``pt``).

    Retorna string vazia se o áudio for nulo/vazio.
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    model = load_whisper_model()

    segments, _info = model.transcribe(samples, language="pt")
    texto = "".join(seg.text for seg in segments).strip()
    log.info("Whisper: %d amostras → %d chars.", samples.shape[0], len(texto))
    return texto


# === FIM ENGINE: WHISPER ===


# === SAÍDA ===
# Entrega do texto: digitar no foco (pynput) ou só área de transferência (pyperclip).
# Digitação pode falhar com Unicode PT em alguns layouts → fallback copy + Ctrl+V/Cmd+V.
# Permissões: macOS Acessibilidade; Linux xclip/xsel + input; Windows pode exigir elevação.


def _mensagem_erro_saida(exc: BaseException) -> str:
    """Monta mensagem em português com ação sugerida para falhas de clipboard/digitação."""
    detalhe = str(exc).strip() or type(exc).__name__
    if sys.platform == "darwin":
        dica = (
            "No macOS, conceda Acessibilidade (Accessibility) ao Terminal/Python "
            "em Preferências do Sistema → Privacidade e Segurança."
        )
    elif sys.platform.startswith("linux"):
        dica = (
            "No Linux, instale xclip ou xsel para a área de transferência e "
            "verifique permissões do grupo input/uinput."
        )
    else:
        dica = (
            "No Windows, confira se o app em foco aceita colar/digitar e, se "
            "necessário, execute o assistente com permissões adequadas."
        )
    return f"Não foi possível entregar o texto ({detalhe}). {dica}"


def _colar_via_atalho() -> None:
    """Simula Ctrl+V (Cmd+V no Darwin/macOS) no campo em foco."""
    controlador = keyboard.Controller()
    modificador = keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl
    with controlador.pressed(modificador):
        controlador.press("v")
        controlador.release("v")


def deliver_text(texto: str) -> None:
    """
    Entrega o texto reconhecido conforme ``OUTPUT_MODE``.

    - ``clipboard``: copia com ``pyperclip.copy`` (não digita).
    - ``type``: digita no foco com ``pynput.keyboard.Controller().type()``;
      se Unicode/layout falhar (TypeError etc.), copia e cola via Ctrl+V
      (Cmd+V no macOS).

    Texto vazio ou só espaços: registra e retorna sem ação.
    Erros de permissão/clipboard: mensagem em português no log.
    """
    if texto is None or not str(texto).strip():
        log.info("deliver_text: texto vazio — nada a entregar.")
        return

    conteudo = str(texto)
    modo = (OUTPUT_MODE or "type").strip().lower()

    try:
        if modo == "clipboard":
            pyperclip.copy(conteudo)
            log.info(
                "Texto copiado para a área de transferência (%d chars).",
                len(conteudo),
            )
            return

        if modo != "type":
            log.warning(
                "OUTPUT_MODE=%r desconhecido; usando 'type'. Válidos: type, clipboard.",
                OUTPUT_MODE,
            )

        # type: digitar no foco; fallback clipboard + colar se Unicode problemático
        controlador = keyboard.Controller()
        try:
            controlador.type(conteudo)
            log.info("Texto digitado no foco (%d chars).", len(conteudo))
        except (TypeError, ValueError, RuntimeError) as exc:
            log.warning(
                "Digitação direta falhou (%s); fallback clipboard + colar.",
                exc,
            )
            pyperclip.copy(conteudo)
            _colar_via_atalho()
            log.info("Texto colado via atalho (%d chars).", len(conteudo))

    except PermissionError as exc:
        log.error("%s", _mensagem_erro_saida(exc))
    except pyperclip.PyperclipException as exc:
        log.error("%s", _mensagem_erro_saida(exc))
    except OSError as exc:
        log.error("%s", _mensagem_erro_saida(exc))
    except Exception as exc:
        log.error("%s", _mensagem_erro_saida(exc))


# === ORQUESTRAÇÃO ===
# Fluxo único: hotkey stop → stop_recording → transcribe(ENGINE) → deliver_text(OUTPUT_MODE)
# Estados: idle → recording → transcribing → idle
# Modelo: lazy-load na primeira transcrição (load_vosk_model / load_whisper_model).
# Áudio < MIN_AUDIO_SECONDS ou texto vazio: não chama deliver_text.


def transcribe(audio: Optional[np.ndarray]) -> str:
    """
    Transcreve o áudio mono float32 com o ENGINE configurado (vosk | whisper).

    Lazy-load: o modelo só é carregado na primeira chamada via load_*_model.
    Áudio nulo/vazio → string vazia. ENGINE inválido → RuntimeError em PT.
    CUDA: fallback para CPU já ocorre dentro de load_whisper_model.
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        log.info("transcribe: áudio ausente ou vazio — engine=%s", ENGINE)
        return ""

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    engine = (ENGINE or "vosk").strip().lower()

    try:
        if engine == "vosk":
            log.info("Transcrevendo com Vosk (carga do modelo sob demanda, se ainda não carregado)...")
            return transcribe_vosk(samples)
        if engine == "whisper":
            log.info(
                "Transcrevendo com Whisper (carga do modelo sob demanda, se ainda não carregado)..."
            )
            return transcribe_whisper(samples)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Falha ao transcrever com ENGINE={engine}: {exc}") from exc

    raise RuntimeError(
        f"ENGINE={ENGINE!r} inválido. Use 'vosk' ou 'whisper' na seção CONFIG."
    )


def _avisar_modelo_no_startup() -> None:
    """Valida disco/config sem carregar pesos — lazy-load fica na 1ª transcrição."""
    engine = (ENGINE or "vosk").strip().lower()
    if engine == "vosk":
        model_dir = Path(VOSK_MODEL_PATH)
        if model_dir.is_dir():
            log.info(
                "Modelo Vosk presente em disco (será carregado na 1ª transcrição): %s",
                model_dir,
            )
        else:
            log.warning(
                "Modelo Vosk ainda não encontrado em '%s'. "
                "Será exigido na primeira transcrição. Baixe vosk-model-small-pt "
                "(~31MB) em https://alphacephei.com/vosk/models e extraia nessa pasta.",
                model_dir,
            )
        return
    if engine == "whisper":
        log.info(
            "Whisper: size=%s device=%s compute=%s — modelo carrega na 1ª transcrição "
            "(CUDA falha → fallback CPU).",
            WHISPER_SIZE,
            DEVICE,
            WHISPER_COMPUTE,
        )


def main() -> None:
    """
    Ponto de entrada: ``python assistente_voz.py``

    1. Valida ENGINE e registra a configuração.
    2. Lista dispositivos de áudio (mic ausente → log, sem crash).
    3. Avisa se o modelo Vosk ainda não está no disco (sem carregar pesos).
    4. Registra a hotkey global e permanece vivo até Ctrl+C.

    Fluxo em runtime (mesmo atalho HOTKEY):
        idle → [REC] gravação → [STOP] → [TRANSCRAVENDO]
        → transcribe(ENGINE) → deliver_text(OUTPUT_MODE) → idle
    """
    engine = (ENGINE or "").strip().lower()
    if engine not in ("vosk", "whisper"):
        log.error(
            "ENGINE=%r inválido. Use 'vosk' ou 'whisper' no topo de assistente_voz.py.",
            ENGINE,
        )
        sys.exit(1)

    log.info(
        "Assistente de voz | ENGINE=%s DEVICE=%s OUTPUT_MODE=%s HOTKEY=%s SAMPLE_RATE=%s",
        ENGINE,
        DEVICE,
        OUTPUT_MODE,
        HOTKEY,
        SAMPLE_RATE,
    )
    if engine == "whisper":
        log.info(
            "Whisper config | SIZE=%s COMPUTE=%s",
            WHISPER_SIZE,
            WHISPER_COMPUTE,
        )

    listar_dispositivos_audio()
    _avisar_modelo_no_startup()
    listener = configurar_hotkey()

    print(f"Aguardando hotkey ({HOTKEY})... [idle]", flush=True)
    log.info("Aguardando hotkey... (%s). Ctrl+C para sair.", HOTKEY)

    try:
        # Mantém o processo vivo enquanto o listener pynput roda em background
        while listener.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Encerrado pelo usuário (Ctrl+C).")
    finally:
        try:
            if _get_estado() == "recording":
                stop_recording()
        except Exception:
            pass
        try:
            listener.stop()
        except Exception:
            pass
        _set_estado("idle")
        sys.exit(0)


if __name__ == "__main__":
    main()
