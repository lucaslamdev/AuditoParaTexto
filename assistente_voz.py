"""
Assistente de voz offline.

Captura áudio via hotkey global, transcreve (Vosk ou Whisper) e entrega
o texto por digitação ou área de transferência. Configuração no topo.

Uso: python assistente_voz.py
Atalho padrão: Ctrl+Shift+Space (toggle gravar / parar + transcrever).
"""

from __future__ import annotations

import json
import logging
import os
import queue
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
DEVICE = "auto"  # auto | cpu | cuda — auto tenta GPU e cai para CPU se faltar CUDA/cuBLAS
OUTPUT_MODE = "type"  # type | clipboard
HOTKEY = "Ctrl+Shift+Space"  # formato pynput
VOSK_MODEL_PATH = str(Path(__file__).resolve().parent / "models" / "vosk-model-small-pt")
SAMPLE_RATE = 16000  # Hz — valor fixo contratual
MIN_AUDIO_SECONDS = 0.3  # gravações abaixo disso são descartadas (ruído de toque)
INPUT_DEVICE = None  # índice do microfone (int) ou None = padrão do sistema

# === LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("assistente_voz")


# === CONFIG (arquivo config.json) ===
# Persistência das constantes da seção CONFIG em config.json na raiz do projeto.
# load_config lê o arquivo; save_config grava validado; apply_config_to_globals
# aplica os valores às variáveis de módulo. Sem UI/PyWebView nesta etapa.

# Caminho do arquivo de configuração (raiz do projeto, ao lado deste script)
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Valores válidos por campo (usados na validação; None = sem restrição de conjunto)
_CONFIG_CHOICES: dict[str, Optional[frozenset[str]]] = {
    "ENGINE": frozenset({"vosk", "whisper"}),
    "WHISPER_SIZE": frozenset({"tiny", "base", "small", "medium", "turbo"}),
    "WHISPER_COMPUTE": frozenset({"int8", "float16", "float32"}),
    "DEVICE": frozenset({"cpu", "cuda", "auto"}),
    "OUTPUT_MODE": frozenset({"type", "clipboard"}),
    "HOTKEY": None,
    "VOSK_MODEL_PATH": None,
    "SAMPLE_RATE": None,
    "MIN_AUDIO_SECONDS": None,
    "INPUT_DEVICE": None,
}


def _config_defaults() -> dict:
    """
    Retorna os defaults derivados das constantes atuais da seção CONFIG.

    VOSK_MODEL_PATH é devolvido como caminho relativo ("models/vosk-model-small-pt")
    para não gravar caminho absoluto no config.json.
    """
    return {
        "ENGINE": ENGINE,
        "WHISPER_SIZE": WHISPER_SIZE,
        "WHISPER_COMPUTE": WHISPER_COMPUTE,
        "DEVICE": DEVICE,
        "OUTPUT_MODE": OUTPUT_MODE,
        "HOTKEY": HOTKEY,
        "VOSK_MODEL_PATH": "models/vosk-model-small-pt",
        "SAMPLE_RATE": SAMPLE_RATE,
        "MIN_AUDIO_SECONDS": MIN_AUDIO_SECONDS,
        "INPUT_DEVICE": INPUT_DEVICE,
    }


def _validar_config(cfg: dict) -> dict:
    """
    Valida um dicionário de configuração campo a campo.

    Chaves ausentes recebem o default. Valores inválidos são registrados em
    português e substituídos pelo default daquele campo. Retorna um novo dict
    contendo exatamente as chaves conhecidas.
    """
    defaults = _config_defaults()
    validado: dict = {}

    for chave, padrao in defaults.items():
        if chave not in cfg:
            log.warning("Config: chave ausente '%s'; usando default %r.", chave, padrao)
            validado[chave] = padrao
            continue

        valor = cfg[chave]
        opcoes = _CONFIG_CHOICES.get(chave)

        # Campos com conjunto fechado de opções (ENGINE, WHISPER_SIZE, etc.)
        if opcoes is not None:
            texto = str(valor).strip().lower()
            if texto not in opcoes:
                log.warning(
                    "Config: valor inválido para '%s' (%r); usando default %r. Válidos: %s.",
                    chave,
                    valor,
                    padrao,
                    ", ".join(sorted(opcoes)),
                )
                validado[chave] = padrao
            else:
                validado[chave] = texto
            continue

        # SAMPLE_RATE: inteiro positivo
        if chave == "SAMPLE_RATE":
            try:
                inteiro = int(valor)
                if inteiro <= 0:
                    raise ValueError("deve ser positivo")
                validado[chave] = inteiro
            except (TypeError, ValueError):
                log.warning(
                    "Config: valor inválido para 'SAMPLE_RATE' (%r); usando default %r.",
                    valor,
                    padrao,
                )
                validado[chave] = padrao
            continue

        # MIN_AUDIO_SECONDS: número positivo
        if chave == "MIN_AUDIO_SECONDS":
            try:
                numero = float(valor)
                if numero <= 0:
                    raise ValueError("deve ser positivo")
                validado[chave] = numero
            except (TypeError, ValueError):
                log.warning(
                    "Config: valor inválido para 'MIN_AUDIO_SECONDS' (%r); usando default %r.",
                    valor,
                    padrao,
                )
                validado[chave] = padrao
            continue

        # INPUT_DEVICE: índice inteiro do microfone ou None (padrão do sistema)
        if chave == "INPUT_DEVICE":
            if valor is None or valor == "" or str(valor).strip().lower() in ("none", "padrao", "padrão"):
                validado[chave] = None
            else:
                try:
                    validado[chave] = int(valor)
                except (TypeError, ValueError):
                    log.warning(
                        "Config: valor inválido para 'INPUT_DEVICE' (%r); usando padrão do sistema.",
                        valor,
                    )
                    validado[chave] = None
            continue

        # HOTKEY / VOSK_MODEL_PATH: string não vazia
        texto = str(valor).strip()
        if not texto:
            log.warning(
                "Config: valor vazio para '%s'; usando default %r.", chave, padrao
            )
            validado[chave] = padrao
        else:
            validado[chave] = texto

    return validado


def load_config() -> dict:
    """
    Lê config.json e retorna um dicionário validado.

    Se o arquivo não existir, retorna os defaults derivados das constantes atuais.
    Erros de leitura/JSON são registrados em português e recaem nos defaults.
    """
    if not CONFIG_PATH.is_file():
        log.info("config.json ausente; usando defaults derivados das constantes.")
        return _config_defaults()

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError) as exc:
        log.error(
            "Falha ao ler config.json (%s); usando defaults derivados das constantes.",
            exc,
        )
        return _config_defaults()

    if not isinstance(dados, dict):
        log.error("config.json não contém um objeto JSON; usando defaults.")
        return _config_defaults()

    return _validar_config(dados)


def save_config(cfg: dict) -> None:
    """
    Valida ``cfg`` e grava em config.json (JSON indentado, UTF-8).

    Chaves/valores são validados antes de gravar (via _validar_config), de modo
    que o arquivo resultante contém apenas chaves conhecidas e valores válidos.
    """
    validado = _validar_config(cfg)
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as arquivo:
            json.dump(validado, arquivo, indent=4, ensure_ascii=False)
            arquivo.write("\n")
        log.info("Configuração gravada em %s", CONFIG_PATH)
    except OSError as exc:
        log.error("Falha ao gravar config.json (%s).", exc)


def apply_config_to_globals(cfg: dict) -> None:
    """
    Aplica os valores de ``cfg`` às variáveis de módulo da seção CONFIG.

    Atualiza ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, DEVICE, OUTPUT_MODE, HOTKEY,
    VOSK_MODEL_PATH, SAMPLE_RATE e MIN_AUDIO_SECONDS. Caminhos relativos de
    VOSK_MODEL_PATH são resolvidos em relação à pasta do script.
    """
    global ENGINE, WHISPER_SIZE, WHISPER_COMPUTE, DEVICE, OUTPUT_MODE
    global HOTKEY, VOSK_MODEL_PATH, SAMPLE_RATE, MIN_AUDIO_SECONDS, INPUT_DEVICE

    validado = _validar_config(cfg)

    ENGINE = validado["ENGINE"]
    WHISPER_SIZE = validado["WHISPER_SIZE"]
    WHISPER_COMPUTE = validado["WHISPER_COMPUTE"]
    DEVICE = validado["DEVICE"]
    OUTPUT_MODE = validado["OUTPUT_MODE"]
    HOTKEY = validado["HOTKEY"]
    SAMPLE_RATE = int(validado["SAMPLE_RATE"])
    MIN_AUDIO_SECONDS = float(validado["MIN_AUDIO_SECONDS"])
    INPUT_DEVICE = validado["INPUT_DEVICE"]

    # Resolve caminho relativo do modelo Vosk contra a pasta do script
    caminho_modelo = Path(validado["VOSK_MODEL_PATH"])
    if not caminho_modelo.is_absolute():
        caminho_modelo = Path(__file__).resolve().parent / caminho_modelo
    VOSK_MODEL_PATH = str(caminho_modelo)

    log.info(
        "Config aplicada | ENGINE=%s DEVICE=%s OUTPUT_MODE=%s HOTKEY=%s SAMPLE_RATE=%s",
        ENGINE,
        DEVICE,
        OUTPUT_MODE,
        HOTKEY,
        SAMPLE_RATE,
    )


def _inicializar_config() -> None:
    """
    Prepara a configuração no startup, antes de áudio/hotkey.

    Se config.json não existir, cria-o com os defaults. Caso exista, carrega e
    aplica os valores às variáveis de módulo via apply_config_to_globals.
    """
    if not CONFIG_PATH.is_file():
        log.info("config.json não encontrado; criando com defaults.")
        save_config(_config_defaults())
        apply_config_to_globals(_config_defaults())
    else:
        apply_config_to_globals(load_config())


# === ÁUDIO ===
# Gravação via sounddevice.InputStream: 16 kHz mono float32.
# Buffer em lista + lock (callback PortAudio em outra thread).
# start_recording() / stop_recording() — API pública; hotkey chama essa API.

_audio_chunks: list[np.ndarray] = []
_audio_lock = threading.Lock()
_input_stream: Optional[sd.InputStream] = None
_gravando = False

_mic_pico_max = 0.0          # maior pico visto na gravação atual (diagnóstico)
_nivel_ultimo_envio = 0.0    # timestamp do último push de nível (throttle)


def _atualizar_nivel_mic(pico: float) -> None:
    """Registra o pico do microfone e envia o nível ao overlay (~10x/s)."""
    global _mic_pico_max, _nivel_ultimo_envio
    if pico > _mic_pico_max:
        _mic_pico_max = pico
    agora = time.time()
    if agora - _nivel_ultimo_envio >= 0.1:
        _nivel_ultimo_envio = agora
        push_level(pico)


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
    bloco = indata.copy()
    with _audio_lock:
        _audio_chunks.append(bloco)
    # Medidor de nível (pico do bloco) enviado ao overlay de forma throttled.
    try:
        pico = float(np.max(np.abs(bloco))) if bloco.size else 0.0
        _atualizar_nivel_mic(pico)
    except Exception:
        pass
    # Se o streaming Vosk estiver ativo, alimenta o reconhecedor ao vivo.
    if vosk_stream_ativo():
        vosk_stream_feed(bloco)


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

    global _mic_pico_max
    _mic_pico_max = 0.0
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
            device=INPUT_DEVICE,  # None = dispositivo padrão do sistema
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
    log.info(
        "Gravação encerrada: %.2fs, %d amostras. Pico do mic=%.4f%s",
        duracao,
        audio.shape[0],
        _mic_pico_max,
        "  (ATENÇÃO: nível muito baixo — verifique o microfone selecionado/ganho)"
        if _mic_pico_max < 0.02 else "",
    )

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
    # Estado da UI: começou a transcrever (parou de gravar)
    set_ui_state("transcribing")
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
            # Descarta o streaming acumulado para não vazar para a próxima gravação
            if vosk_stream_ativo():
                vosk_stream_finish()
            if whisper_stream_ativo():
                whisper_stream_finish()
            return

        # Vosk em streaming: usa o texto já reconhecido ao vivo (evita reprocessar).
        if vosk_stream_ativo():
            texto = vosk_stream_finish()
        else:
            # Whisper: para o preview antes da transcrição final (evita uso
            # concorrente do modelo) e transcreve o áudio completo.
            if whisper_stream_ativo():
                whisper_stream_finish()
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
        # UI: entregou o texto → volta para idle exibindo o último reconhecido
        set_ui_state("idle", last_text=texto)
    except RuntimeError as exc:
        # modelo ausente, ENGINE inválido, CUDA/CPU falhou, etc.
        log.error("%s", exc)
    except Exception as exc:
        log.error("Falha no pipeline pós-gravação: %s", exc)
    finally:
        # Se algo interrompeu antes de finalizar o streaming, encerra para não
        # vazar estado do reconhecedor para a próxima gravação.
        if vosk_stream_ativo():
            try:
                vosk_stream_finish()
            except Exception as exc:
                log.debug("Falha ao encerrar streaming Vosk no finally: %s", exc)
        if whisper_stream_ativo():
            try:
                whisper_stream_finish()
            except Exception as exc:
                log.debug("Falha ao encerrar preview Whisper no finally: %s", exc)
        _set_estado("idle")
        # Só força idle na UI se ainda não voltamos (sucesso já setou com last_text).
        if _ui_state != "idle":
            set_ui_state("idle")
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
        # Guarda a janela em foco (onde o usuário quer o texto) antes do overlay
        # ou de qualquer outra coisa roubar o foreground.
        _capturar_janela_alvo()
        # Ativa a transcrição ao vivo conforme o ENGINE (best-effort). Vosk usa
        # streaming nativo; Whisper usa re-transcrição periódica (pseudo-stream).
        engine_atual = (ENGINE or "").strip().lower()
        if engine_atual == "vosk":
            try:
                vosk_stream_start()
            except Exception as exc:
                log.debug("Não foi possível ativar o streaming Vosk: %s", exc)
        elif engine_atual == "whisper":
            try:
                whisper_stream_start()
            except Exception as exc:
                log.debug("Não foi possível ativar o preview Whisper: %s", exc)
        try:
            start_recording()
        except Exception as exc:
            _set_estado("idle")
            # Cancela um eventual streaming iniciado se o mic falhar
            if vosk_stream_ativo():
                vosk_stream_finish()
            if whisper_stream_ativo():
                whisper_stream_finish()
            # UI: falha ao iniciar mic → mantém idle
            set_ui_state("idle")
            log.error("Não foi possível iniciar gravação: %s", exc)
            return
        # UI: gravação iniciada
        set_ui_state("recording", last_text="")
        print("[REC]", flush=True)
        log.info("[REC] gravação ativa — pressione %s de novo para parar.", HOTKEY)
        return

    # Recaptura o alvo no STOP (usuário ainda deve estar no editor).
    _capturar_janela_alvo()
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


# --- Vosk em streaming (transcrição ao vivo) ---------------------------------
# Durante a gravação, o áudio é alimentado ao KaldiRecognizer em tempo real e os
# resultados parciais são exibidos no overlay. Ao parar, o texto final é montado
# a partir dos segmentos já confirmados + o resultado final.

_vosk_stream_rec = None            # KaldiRecognizer ativo (ou None)
_vosk_stream_queue: Optional["queue.Queue"] = None
_vosk_stream_thread: Optional[threading.Thread] = None
_vosk_stream_active = False        # True enquanto o consumidor deve processar
_vosk_stream_segmentos: list[str] = []  # segmentos confirmados (Result)
_vosk_stream_lock = threading.Lock()
_vosk_live_text = ""               # texto parcial atual (lido pela UI via polling)


def vosk_live_text() -> str:
    """Texto parcial atual do streaming (para o overlay ler via get_status)."""
    return _vosk_live_text


def _vosk_stream_consumidor() -> None:
    """Thread consumidora: puxa PCM da fila e alimenta o reconhecedor Vosk.

    Emite os parciais para o overlay via push_live(). Encerra ao receber o
    sentinela None na fila.
    """
    global _vosk_stream_segmentos
    while True:
        try:
            data = _vosk_stream_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if data is None:  # sentinela de término
            break
        try:
            if _vosk_stream_rec.AcceptWaveform(data):
                seg = str(json.loads(_vosk_stream_rec.Result()).get("text") or "").strip()
                if seg:
                    with _vosk_stream_lock:
                        _vosk_stream_segmentos.append(seg)
                    push_live(_vosk_texto_ao_vivo(""))
            else:
                parcial = str(
                    json.loads(_vosk_stream_rec.PartialResult()).get("partial") or ""
                ).strip()
                push_live(_vosk_texto_ao_vivo(parcial))
        except Exception as exc:  # não deixa a thread morrer silenciosamente
            log.debug("Consumidor Vosk stream: %s", exc)


def _vosk_texto_ao_vivo(parcial: str) -> str:
    """Junta os segmentos já confirmados com o parcial atual (para preview)."""
    with _vosk_stream_lock:
        partes = list(_vosk_stream_segmentos)
    if parcial:
        partes.append(parcial)
    return " ".join(partes).strip()


def vosk_stream_start() -> bool:
    """Inicia a transcrição em streaming do Vosk.

    Carrega o modelo (sob demanda), cria o reconhecedor e sobe a thread
    consumidora. Retorna True se ativou; False se o modelo estiver ausente
    (nesse caso o pipeline cai no modo por bloco, que reporta o erro).
    """
    global _vosk_stream_rec, _vosk_stream_queue, _vosk_stream_thread
    global _vosk_stream_active, _vosk_stream_segmentos, _vosk_live_text

    from vosk import KaldiRecognizer

    try:
        model = load_vosk_model(VOSK_MODEL_PATH)
    except RuntimeError as exc:
        log.warning("Streaming Vosk indisponível: %s", exc)
        return False

    _vosk_stream_rec = KaldiRecognizer(model, SAMPLE_RATE)
    _vosk_stream_queue = queue.Queue()
    with _vosk_stream_lock:
        _vosk_stream_segmentos = []
    _vosk_live_text = ""
    _vosk_stream_active = True
    _vosk_stream_thread = threading.Thread(
        target=_vosk_stream_consumidor,
        name="vosk-stream",
        daemon=True,
    )
    _vosk_stream_thread.start()
    log.info("Streaming Vosk ativo — transcrição ao vivo.")
    return True


def vosk_stream_feed(audio_float32: np.ndarray) -> None:
    """Enfileira um bloco de áudio (float32) convertido para PCM int16."""
    if not _vosk_stream_active or _vosk_stream_queue is None:
        return
    samples = np.asarray(audio_float32, dtype=np.float32).reshape(-1)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    _vosk_stream_queue.put(pcm.tobytes())


def vosk_stream_finish() -> str:
    """Encerra o streaming e retorna o texto final acumulado."""
    global _vosk_stream_active
    if not _vosk_stream_active:
        return ""

    _vosk_stream_active = False
    if _vosk_stream_queue is not None:
        _vosk_stream_queue.put(None)  # sentinela
    if _vosk_stream_thread is not None:
        _vosk_stream_thread.join(timeout=5.0)

    final = ""
    if _vosk_stream_rec is not None:
        try:
            final = str(json.loads(_vosk_stream_rec.FinalResult()).get("text") or "").strip()
        except Exception as exc:
            log.debug("FinalResult Vosk stream: %s", exc)

    with _vosk_stream_lock:
        partes = list(_vosk_stream_segmentos)
    if final:
        partes.append(final)
    texto = " ".join(partes).strip()
    log.info("Streaming Vosk finalizado → %d chars.", len(texto))
    return texto


def vosk_stream_ativo() -> bool:
    """Indica se o streaming Vosk está ativo no momento."""
    return _vosk_stream_active


# === FIM ENGINE: VOSK ===


# === ENGINE: WHISPER ===
# Implementação do motor Whisper (tamanho, compute e DEVICE).

_whisper_model = None  # cache do faster_whisper.WhisperModel (carregado sob demanda)
_whisper_device = None  # dispositivo efetivamente usado ("cpu"/"cuda") p/ fallback
_whisper_lock = threading.Lock()  # serializa load/transcribe (preview + final)
_whisper_force_cpu = False  # grudento: após falha de CUDA/cuBLAS, fica em CPU

_WHISPER_SIZES = frozenset({"tiny", "base", "small", "medium", "turbo"})
_WHISPER_COMPUTE_TYPES = frozenset({"int8", "float16", "float32"})
_WHISPER_DEVICES = frozenset({"cpu", "cuda", "auto"})


def load_whisper_model(force_cpu: bool = False):
    """
    Carrega ``faster_whisper.WhisperModel`` com cache em variável de módulo.

    Usa ``WHISPER_SIZE``, ``DEVICE`` e ``WHISPER_COMPUTE`` da configuração.
    Se ``DEVICE`` for ``cuda``/``auto`` e a carga falhar, faz fallback para CPU
    e registra o aviso em português. Com ``force_cpu=True`` ignora o DEVICE e
    carrega direto em CPU (usado no fallback em tempo de transcrição).
    """
    global _whisper_model, _whisper_device, _whisper_force_cpu

    if _whisper_model is not None and not force_cpu:
        log.debug("Modelo Whisper já em memória (cache).")
        return _whisper_model

    # Grudento: se o cuBLAS/CUDA já falhou nesta sessão, sempre usa CPU (evita
    # reabrir a mesma falha e o thrashing entre auto↔cpu).
    if _whisper_force_cpu:
        force_cpu = True

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

    device = (DEVICE or "auto").strip().lower()
    if device not in _WHISPER_DEVICES:
        log.warning(
            "DEVICE=%r inválido; usando 'auto'. Válidos: %s.",
            DEVICE,
            ", ".join(sorted(_WHISPER_DEVICES)),
        )
        device = "auto"

    # float16 não é suportado em CPU pelo ctranslate2 → cai para int8
    if force_cpu:
        device = "cpu"
    if device == "cpu" and compute == "float16":
        log.info("float16 não é suportado em CPU; usando int8.")
        compute = "int8"

    log.info(
        "Lazy-load: carregando modelo Whisper pela primeira vez "
        "(size=%s device=%s compute_type=%s)",
        size,
        device,
        compute,
    )
    try:
        _whisper_model = WhisperModel(size, device=device, compute_type=compute)
        _whisper_device = device
    except Exception as exc:
        if device in ("cuda", "auto"):
            log.warning(
                "Falha ao usar dispositivo '%s' para Whisper (%s). "
                "Alternando para CPU.",
                device,
                exc,
            )
            compute_cpu = "int8" if compute == "float16" else compute
            try:
                _whisper_model = WhisperModel(size, device="cpu", compute_type=compute_cpu)
                _whisper_device = "cpu"
                _whisper_force_cpu = True  # não tenta CUDA de novo nesta sessão
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

    log.info("Modelo Whisper carregado e em cache (size=%s, device=%s).", size, _whisper_device)
    return _whisper_model


def transcribe_whisper(audio: np.ndarray) -> str:
    """
    Transcreve áudio mono float32 em [-1, 1] com Faster-Whisper (idioma ``pt``).

    Retorna string vazia se o áudio for nulo/vazio.
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""

    global _whisper_model, _whisper_force_cpu

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    # Serializa acesso ao modelo: o preview ao vivo e a transcrição final nunca
    # rodam concorrentes (o WhisperModel não é seguro para uso simultâneo).
    with _whisper_lock:
        model = load_whisper_model()
        try:
            segments, _info = model.transcribe(samples, language="pt")
            texto = "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            # Ex.: cublas64_12.dll ausente quando DEVICE=cuda/auto sem CUDA.
            if _whisper_device != "cpu":
                log.warning(
                    "Falha na inferência Whisper em '%s' (%s). Recarregando em CPU.",
                    _whisper_device,
                    exc,
                )
                _whisper_model = None
                _whisper_force_cpu = True  # não tenta CUDA de novo nesta sessão
                model = load_whisper_model(force_cpu=True)
                segments, _info = model.transcribe(samples, language="pt")
                texto = "".join(seg.text for seg in segments).strip()
            else:
                raise
    log.info("Whisper: %d amostras → %d chars.", samples.shape[0], len(texto))
    return texto


# --- Whisper em pseudo-streaming (transcrição ao vivo) -----------------------
# O Faster-Whisper não transcreve em fluxo contínuo como o Vosk. Para dar um
# preview ao vivo, uma thread re-transcreve periodicamente TODO o áudio já
# capturado e envia o resultado ao overlay. Ao parar, o pipeline faz a
# transcrição final do áudio completo (autoritativa) — este preview é
# best-effort e não substitui o resultado final.

WHISPER_STREAM_INTERVAL = 2.0   # segundos entre re-transcrições do preview
WHISPER_STREAM_MIN_SEG = 1.0    # só começa a transcrever após ~1s de áudio

_whisper_stream_active = False
_whisper_stream_thread: Optional[threading.Thread] = None
_whisper_stream_stop: Optional[threading.Event] = None


def _whisper_stream_worker() -> None:
    """Thread do preview: re-transcreve o buffer acumulado a cada intervalo."""
    ultimo_n = 0
    while _whisper_stream_active:
        # Espera o intervalo (interrompível pelo stop) antes de transcrever.
        if _whisper_stream_stop is not None and _whisper_stream_stop.wait(
            WHISPER_STREAM_INTERVAL
        ):
            break
        if not _whisper_stream_active:
            break
        with _audio_lock:
            if not _audio_chunks:
                continue
            audio = np.concatenate(_audio_chunks, axis=0)
        if audio.ndim > 1:
            audio = audio.reshape(-1)
        n = int(audio.shape[0])
        if n / float(SAMPLE_RATE) < WHISPER_STREAM_MIN_SEG:
            continue
        if n == ultimo_n:  # nada de novo desde a última passada
            continue
        ultimo_n = n
        try:
            texto = transcribe_whisper(np.asarray(audio, dtype=np.float32))
            if _whisper_stream_active and texto:
                push_live(texto)
        except Exception as exc:
            log.debug("Preview Whisper: %s", exc)


def whisper_stream_start() -> bool:
    """Inicia o preview ao vivo do Whisper (thread de re-transcrição)."""
    global _whisper_stream_active, _whisper_stream_thread, _whisper_stream_stop
    global _vosk_live_text
    if _whisper_stream_active:
        return True
    _vosk_live_text = ""
    _whisper_stream_stop = threading.Event()
    _whisper_stream_active = True
    _whisper_stream_thread = threading.Thread(
        target=_whisper_stream_worker,
        name="whisper-stream",
        daemon=True,
    )
    _whisper_stream_thread.start()
    log.info("Preview Whisper ativo — transcrição ao vivo (best-effort).")
    return True


def whisper_stream_finish() -> None:
    """Encerra o preview ao vivo do Whisper e aguarda a thread finalizar."""
    global _whisper_stream_active, _whisper_stream_thread
    if not _whisper_stream_active:
        return
    _whisper_stream_active = False
    if _whisper_stream_stop is not None:
        _whisper_stream_stop.set()
    thread = _whisper_stream_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)
    _whisper_stream_thread = None


def whisper_stream_ativo() -> bool:
    """True enquanto o preview ao vivo do Whisper estiver rodando."""
    return _whisper_stream_active


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


# Handle da janela em foco quando a gravação começa/para (Windows).
# Usado para restaurar o foco antes de colar o texto — sem isso o type
# costuma cair no overlay do PyWebView ou em lugar nenhum.
_target_hwnd = None


def _capturar_janela_alvo() -> None:
    """Guarda a janela em primeiro plano (Windows) para restaurar no deliver."""
    global _target_hwnd
    if sys.platform != "win32":
        _target_hwnd = None
        return
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        _target_hwnd = int(hwnd) if hwnd else None
    except Exception as exc:
        log.debug("Não foi possível capturar janela alvo: %s", exc)
        _target_hwnd = None


def _focar_janela_alvo() -> bool:
    """Restaura o foco na janela capturada. Retorna True se tentou com sucesso."""
    if sys.platform != "win32" or not _target_hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.IsWindow(_target_hwnd):
            return False
        user32.SetForegroundWindow(_target_hwnd)
        time.sleep(0.08)
        return True
    except Exception as exc:
        log.debug("Não foi possível focar janela alvo: %s", exc)
        return False


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
    - ``type``: restaura a janela alvo e cola via clipboard + Ctrl+V/Cmd+V
      (mais confiável que ``Controller.type()`` com acentos PT e WebView).

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

        # type: clipboard + colar. Digitação direta do pynput falha com acentos
        # PT e costuma ir para o overlay; restaurar foco + Ctrl+V é o caminho
        # confiável no Windows (e funciona bem nos outros SOs também).
        pyperclip.copy(conteudo)
        _focar_janela_alvo()
        time.sleep(0.05)
        _colar_via_atalho()
        log.info("Texto colado no foco via atalho (%d chars).", len(conteudo))

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


# === UI (PyWebView) ===
# Janela "glass" (frameless/transparente) que fala com o JS via bridge js_api.
# A classe Api expõe get_config/save_config/get_status ao front-end (ui/app.js).
# O estado da UI (_ui_state/_ui_last_text) é mantido em variáveis de módulo; o
# envio de eventos para o JS (evaluate_js) fica na próxima task (3-2) — aqui só
# criamos o mecanismo e fazemos get_status refletir esse estado.

# Estados possíveis da UI (espelham os estados da hotkey)
_ui_state: EstadoHotkey = "idle"
_ui_last_text: str = ""
_ui_level: float = 0.0  # nível atual do mic (0..1), lido pela UI via get_status

# Referência à janela do PyWebView (para evaluate_js futuramente, na task 3-2)
_ui_window = None

# Tamanho do overlay compacto (largura, altura). O painel de configurações
# expande a janela via Api.resize_window("settings").
_UI_OVERLAY_SIZE = (380, 150)


def push_ui() -> None:
    """
    Empurra o estado atual para o front-end via evaluate_js (updateStatus).

    Só faz algo quando há janela PyWebView (_ui_window). Em modo console
    (_ui_window is None) é um no-op — sem dependência forte com a UI.

    O payload {state, last_text, engine} é serializado com json.dumps para
    escapar aspas/acentos e virar um objeto JS válido. Chamável de outra thread
    (pywebview suporta evaluate_js entre threads); exceções são toleradas
    (por exemplo, janela já fechada).
    """
    if _ui_window is None:
        return
    try:
        payload = json.dumps(
            {
                "state": _ui_state,
                "last_text": _ui_last_text,
                "engine": ENGINE,
            },
            ensure_ascii=False,
        )
        # json.dumps gera um literal de objeto JS válido para updateStatus(...)
        _ui_window.evaluate_js(f"updateStatus({payload})")
    except Exception as exc:
        # UI fechada / bridge indisponível: não deve quebrar o pipeline de STT
        log.debug("push_ui ignorado (UI indisponível): %s", exc)


def push_live(texto: str) -> None:
    """Empurra o texto parcial (ao vivo) para o overlay via updateLive(...).

    No-op em modo console (_ui_window is None). Tolera exceções (UI fechada).
    """
    # Guarda o parcial para o polling do JS (get_status). No Windows/WebView2,
    # evaluate_js a partir de thread de fundo nem sempre atualiza a página; o
    # polling (JS → Python) é o caminho confiável para o preview ao vivo.
    global _vosk_live_text
    _vosk_live_text = texto
    if _ui_window is None:
        return
    try:
        _ui_window.evaluate_js(f"updateLive({json.dumps(texto, ensure_ascii=False)})")
    except Exception as exc:
        log.debug("push_live ignorado (UI indisponível): %s", exc)


def push_level(pico: float) -> None:
    """Empurra o nível do microfone (0..1) para o overlay via updateLevel(...)."""
    global _ui_level
    _ui_level = float(pico)
    if _ui_window is None:
        return
    try:
        _ui_window.evaluate_js(f"updateLevel({float(pico):.4f})")
    except Exception as exc:
        log.debug("push_level ignorado (UI indisponível): %s", exc)


def listar_dispositivos_entrada() -> list:
    """Lista dispositivos de entrada como [{index, name}] para a UI."""
    itens = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_input_channels", 0)) > 0:
                itens.append({"index": i, "name": str(dev.get("name", f"dispositivo {i}"))})
    except Exception as exc:
        log.debug("Falha ao listar dispositivos de entrada: %s", exc)
    return itens


def set_ui_state(state: EstadoHotkey, last_text: Optional[str] = None) -> None:
    """
    Atualiza o estado de UI de módulo (_ui_state) e, opcionalmente, o último texto,
    e dispara push_ui() para refletir a mudança no front-end em tempo real.

    Em modo console (_ui_window is None) apenas atualiza as variáveis; push_ui()
    é no-op nesse caso.
    """
    global _ui_state, _ui_last_text
    _ui_state = state
    if last_text is not None:
        _ui_last_text = last_text
    # Reflete o novo estado na UI (no-op se estiver em modo console)
    push_ui()


class Api:
    """
    Ponte (bridge) exposta ao JavaScript pela janela PyWebView (js_api).

    Métodos:
        get_config()  → configuração efetiva atual (dict validado).
        save_config() → valida, grava, aplica e invalida caches de modelo.
        get_status()  → estado atual da UI, último texto e ENGINE.
    """

    def get_config(self) -> dict:
        """Retorna a configuração efetiva atual (lida/validada de config.json)."""
        return load_config()

    def save_config(self, data: dict) -> dict:
        """
        Valida e grava a nova configuração, aplica às globais e invalida caches.

        Se ENGINE/WHISPER_SIZE/WHISPER_COMPUTE/DEVICE mudarem, zera os caches de
        modelo (_vosk_model/_whisper_model) para forçar recarga sob demanda.
        Retorna {"ok": True} em sucesso ou {"ok": False, "error": "..."} (PT).
        """
        global _vosk_model, _whisper_model, _whisper_force_cpu

        if not isinstance(data, dict):
            return {"ok": False, "error": "Configuração inválida: esperado um objeto."}

        try:
            # A UI só envia os campos do formulário; mescla com a config atual
            # para não perder SAMPLE_RATE / MIN_AUDIO_SECONDS / etc.
            atual = load_config()
            atual.update(data)
            data = atual

            # Guarda os valores atuais para detectar mudanças que exigem recarga
            engine_antigo = ENGINE
            whisper_size_antigo = WHISPER_SIZE
            whisper_compute_antigo = WHISPER_COMPUTE
            device_antigo = DEVICE

            # Grava (validado) e aplica às variáveis de módulo
            save_config(data)
            apply_config_to_globals(data)

            # Invalida caches se algo que afeta o modelo mudou
            if (
                ENGINE != engine_antigo
                or WHISPER_SIZE != whisper_size_antigo
                or WHISPER_COMPUTE != whisper_compute_antigo
                or DEVICE != device_antigo
            ):
                if _vosk_model is not None:
                    _vosk_model = None
                    log.info("Cache do modelo Vosk invalidado (config alterada).")
                if _whisper_model is not None:
                    _whisper_model = None
                    log.info("Cache do modelo Whisper invalidado (config alterada).")
                # Troca explícita de DEVICE deve poder tentar CUDA de novo.
                if DEVICE != device_antigo:
                    _whisper_force_cpu = False

            return {"ok": True}
        except Exception as exc:
            log.error("Falha ao salvar configuração via UI: %s", exc)
            return {"ok": False, "error": f"Falha ao salvar configuração: {exc}"}

    def get_status(self) -> dict:
        """Retorna o estado atual da UI, o último texto reconhecido e o ENGINE.

        Inclui também 'live' (parcial do streaming Vosk) e 'level' (nível do
        mic), para o overlay atualizar via polling — mais confiável que
        evaluate_js entre threads no Windows/WebView2.
        """
        return {
            "state": _ui_state,
            "last_text": _ui_last_text,
            "engine": ENGINE,
            "live": _vosk_live_text if _ui_state == "recording" else "",
            "level": _ui_level if _ui_state == "recording" else 0.0,
        }

    def list_input_devices(self) -> list:
        """Lista microfones disponíveis ([{index, name}]) para o seletor da UI."""
        return listar_dispositivos_entrada()

    def resize_window(self, mode: str) -> dict:
        """
        Redimensiona a janela conforme o modo pedido pela UI.

        'overlay'  → pill compacto (padrão).
        'settings' → maior, para caber o painel de configurações.
        """
        try:
            if _ui_window is None:
                return {"ok": False, "error": "Janela indisponível."}
            if mode == "settings":
                _ui_window.resize(440, 560)
            else:
                _ui_window.resize(*_UI_OVERLAY_SIZE)
            return {"ok": True}
        except Exception as exc:  # janela fechada / backend sem suporte
            log.debug("resize_window(%s) falhou: %s", mode, exc)
            return {"ok": False, "error": str(exc)}

    def quit(self) -> dict:
        """Fecha a janela da UI e encerra o programa."""
        try:
            if _ui_window is not None:
                _ui_window.destroy()
            return {"ok": True}
        except Exception as exc:
            log.error("Falha ao fechar a interface: %s", exc)
            return {"ok": False, "error": str(exc)}


def _hwnd_da_janela_ui(window) -> int:
    """Obtém o HWND nativo da janela PyWebView (Windows)."""
    try:
        native = getattr(window, "native", None)
        if native is not None and hasattr(native, "Handle"):
            return int(native.Handle.ToInt32())
    except Exception:
        pass
    try:
        import ctypes

        return int(ctypes.windll.user32.FindWindowW(None, "Assistente de Voz") or 0)
    except Exception:
        return 0


def _aplicar_glass_nativo(window) -> None:
    """
    Aplica blur Acrylic/Mica nativo no Windows para o fundo translúcido real.

    No Windows 11 tenta DWMWA_SYSTEMBACKDROP_TYPE (Acrylic). No Windows 10
    (ou se o DWM falhar) usa SetWindowCompositionAttribute com Acrylic.
    Em outros SOs é no-op (CSS backdrop-filter + transparent cuidam do visual).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import POINTER, Structure, byref, c_int, c_void_p, sizeof
    except Exception:
        return

    # Pequena espera: o HWND só existe de forma estável após o show.
    time.sleep(0.25)
    hwnd = _hwnd_da_janela_ui(window)
    if not hwnd:
        log.debug("Glass nativo: HWND da UI não encontrado.")
        return

    # Cantos arredondados (Win11) + tema escuro
    try:
        dwm = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMWCP_ROUND = 2
        DWMSBT_TRANSIENT_WINDOW = 3  # Acrylic

        dark = c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(dark), sizeof(dark))
        corner = c_int(DWMWCP_ROUND)
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, byref(corner), sizeof(corner)
        )
        backdrop = c_int(DWMSBT_TRANSIENT_WINDOW)
        hr = dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, byref(backdrop), sizeof(backdrop)
        )
        if hr == 0:
            log.info("Glass nativo: Acrylic DWM (Windows 11) aplicado.")
            return
    except Exception as exc:
        log.debug("Glass nativo DWM indisponível: %s", exc)

    # Fallback Windows 10: AccentPolicy Acrylic blur behind
    try:

        class ACCENTPOLICY(Structure):
            _fields_ = [
                ("AccentState", c_int),
                ("AccentFlags", c_int),
                ("GradientColor", c_int),
                ("AnimationId", c_int),
            ]

        class WINCOMPATTRDATA(Structure):
            _fields_ = [
                ("Attribute", c_int),
                ("Data", c_void_p),
                ("SizeOfData", c_int),
            ]

        # 0xAABBGGRR — alpha ~0xA0 + tom preto azulado
        accent = ACCENTPOLICY(4, 2, 0xA00E0E12, 0)
        data = WINCOMPATTRDATA(
            19,
            ctypes.cast(ctypes.pointer(accent), c_void_p),
            sizeof(accent),
        )
        ok = ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, byref(data))
        if ok:
            log.info("Glass nativo: Acrylic (AccentPolicy) aplicado.")
        else:
            log.debug("Glass nativo: SetWindowCompositionAttribute retornou 0.")
    except Exception as exc:
        log.debug("Glass nativo AccentPolicy falhou: %s", exc)


def run_ui() -> bool:
    """
    Abre a janela glass do PyWebView apontando para ui/index.html.

    Janela frameless, transparente e on-top com bridge js_api=Api(). No Windows
    aplica Acrylic nativo para o fundo translúcido real. webview.start() bloqueia
    até fechar. Retorna True se a UI rodou; False se PyWebView indisponível.
    """
    global _ui_window

    try:
        import webview
    except ImportError:
        log.warning(
            "PyWebView não está instalado — usando o modo console. "
            "Para a interface gráfica, instale as dependências "
            "(pip install pywebview) e, no Windows, o runtime Microsoft Edge "
            "WebView2 (https://developer.microsoft.com/microsoft-edge/webview2/)."
        )
        return False

    # Caminho absoluto para o HTML da interface (ao lado deste script)
    html_path = Path(__file__).resolve().parent / "ui" / "index.html"

    # transparent=True: WebView2 com fundo transparente para o glass CSS
    # (backdrop-filter) e o Acrylic nativo aparecerem. shadow=False evita
    # artefatos opacos ao redor do pill no Windows.
    _ui_window = webview.create_window(
        "Assistente de Voz",
        url=str(html_path),
        js_api=Api(),
        frameless=True,
        on_top=True,
        easy_drag=True,
        transparent=True,
        shadow=False,
        vibrancy=True,
        background_color="#000000",
        width=_UI_OVERLAY_SIZE[0],
        height=_UI_OVERLAY_SIZE[1],
        resizable=False,
    )

    webview.start(
        func=lambda w: threading.Thread(
            target=_aplicar_glass_nativo, args=(w,), name="glass-native", daemon=True
        ).start(),
        args=_ui_window,
    )
    return True


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
    # Prepara a configuração ANTES de listar dispositivos/registrar hotkey.
    _inicializar_config()

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
    # A hotkey global roda em thread daemon do pynput e continua ativa
    # enquanto a janela da UI estiver aberta (run_ui bloqueia a thread principal).
    listener = configurar_hotkey()

    try:
        # Tenta abrir a interface gráfica; run_ui() bloqueia até a janela fechar.
        if run_ui():
            log.info("Interface gráfica encerrada.")
        else:
            # PyWebView indisponível: mantém o comportamento antigo (console).
            print(f"Aguardando hotkey ({HOTKEY})... [idle]", flush=True)
            log.info("Aguardando hotkey... (%s). Ctrl+C para sair.", HOTKEY)
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
