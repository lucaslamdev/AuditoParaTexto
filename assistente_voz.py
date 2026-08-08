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

        # Snapshot do modo na entrega (evita corrida se a UI mudar no meio).
        modo_entrega = (OUTPUT_MODE or "type").strip().lower()

        # Vosk em streaming: usa o texto já reconhecido ao vivo (evita reprocessar).
        if vosk_stream_ativo():
            texto = vosk_stream_finish()
        else:
            if whisper_stream_ativo():
                whisper_stream_finish()
            if (ENGINE or "").strip().lower() == "whisper":
                # type: pode reutilizar warm paralelo (rápido).
                # clipboard: SEMPRE aguarda a transcrição final completa antes de copiar.
                if modo_entrega == "clipboard":
                    texto = transcribe_whisper(audio, rapido=False)
                else:
                    texto = whisper_final_rapido(audio)
            else:
                texto = transcribe(audio)
        if not texto or not str(texto).strip():
            log.info("Transcrição vazia — nada a entregar.")
            return

        log.info(
            "Entregando texto via OUTPUT_MODE=%s (%d chars).",
            modo_entrega,
            len(texto),
        )
        # Mostra o texto final no overlay ANTES da entrega (clipboard/type).
        set_ui_state("transcribing", last_text=texto)
        push_live(texto)
        deliver_text(texto, modo=modo_entrega)
        # UI: entregou o texto → idle com o último reconhecido no overlay
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
        # Só captura a janela-alvo no modo type (clipboard nunca cola/foca).
        if (OUTPUT_MODE or "").strip().lower() != "clipboard":
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

    # Recaptura o alvo no STOP apenas no modo type.
    if (OUTPUT_MODE or "").strip().lower() != "clipboard":
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
_whisper_load_lock = threading.Lock()  # serializa o CARREGAMENTO (evita download triplo)
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

    # Serializa o CARREGAMENTO: sem este lock, preload + preview + warm podem
    # disparar download/carga do mesmo modelo em paralelo (triplicando a espera
    # na primeira gravação e o uso de RAM).
    with _whisper_load_lock:
        # Re-checa o cache DENTRO do lock: quem esperava aqui não precisa carregar.
        if _whisper_model is not None and not force_cpu:
            log.debug("Modelo Whisper já carregado por outra thread (após lock).")
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

        log.info(
            "Modelo Whisper carregado e em cache (size=%s, device=%s).",
            size,
            _whisper_device,
        )
        return _whisper_model


def transcribe_whisper(audio: np.ndarray, *, rapido: bool = False) -> str:
    """
    Transcreve áudio mono float32 com o modelo configurado (WHISPER_SIZE).

    ``rapido=True``: beam_size=1 (overlay / passes paralelos leves).
    ``rapido=False``: qualidade normal (entrega final autoritativa).
    """
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""

    global _whisper_model, _whisper_force_cpu

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    opts: dict = {"language": "pt"}
    if rapido:
        opts.update(
            beam_size=1,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )

    with _whisper_lock:
        model = load_whisper_model()
        try:
            segments, _info = model.transcribe(samples, **opts)
            texto = "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            if _whisper_device != "cpu":
                log.warning(
                    "Falha na inferência Whisper em '%s' (%s). Recarregando em CPU.",
                    _whisper_device,
                    exc,
                )
                _whisper_model = None
                _whisper_force_cpu = True
                model = load_whisper_model(force_cpu=True)
                segments, _info = model.transcribe(samples, **opts)
                texto = "".join(seg.text for seg in segments).strip()
            else:
                raise
    if not rapido:
        log.info("Whisper: %d amostras → %d chars.", samples.shape[0], len(texto))
    else:
        log.debug("Whisper rápido: %d amostras → %d chars.", samples.shape[0], len(texto))
    return texto


# --- Whisper streaming / paralelo --------------------------------------------
# - tiny/base: o MESMO modelo serve overlay + final (sem duplicar pesos).
# - small/medium/turbo: overlay usa modelo leve (base/cpu); em paralelo o modelo
#   escolhido já vai transcrevendo o buffer (warm) para o STOP ser quase instantâneo.

WHISPER_PREVIEW_FALLBACK_SIZE = "base"
WHISPER_PREVIEW_WINDOW_SEC = 6.0
WHISPER_STREAM_INTERVAL = 1.0
WHISPER_STREAM_MIN_SEG = 0.7
WHISPER_WARM_MAX_GAP_SEC = 2.0  # reutiliza warm se faltou ≤2s de áudio no fim

_whisper_preview_model = None
_whisper_preview_lock = threading.Lock()
_whisper_stream_active = False
_whisper_stream_thread: Optional[threading.Thread] = None
_whisper_stream_stop: Optional[threading.Event] = None
_whisper_warm_thread: Optional[threading.Thread] = None
_whisper_warm_text = ""
_whisper_warm_n = 0
_whisper_warm_lock = threading.Lock()


def _whisper_compartilha_modelo_preview() -> bool:
    """True se o modelo escolhido já é leve o bastante para o overlay (tiny/base)."""
    return (WHISPER_SIZE or "").strip().lower() in ("tiny", "base")


def _whisper_warm_interval() -> float:
    """Intervalo entre passes do modelo final em paralelo (por tamanho)."""
    size = (WHISPER_SIZE or "base").strip().lower()
    return {
        "tiny": 1.2,
        "base": 1.5,
        "small": 2.5,
        "medium": 3.5,
        "turbo": 4.0,
    }.get(size, 2.5)


def load_whisper_preview_model():
    """Modelo leve só para overlay quando WHISPER_SIZE não é tiny/base."""
    global _whisper_preview_model
    if _whisper_compartilha_modelo_preview():
        return load_whisper_model()
    if _whisper_preview_model is not None:
        return _whisper_preview_model
    from faster_whisper import WhisperModel

    log.info(
        "Carregando Whisper PREVIEW (%s/cpu/int8) — separado de WHISPER_SIZE=%s...",
        WHISPER_PREVIEW_FALLBACK_SIZE,
        WHISPER_SIZE,
    )
    _whisper_preview_model = WhisperModel(
        WHISPER_PREVIEW_FALLBACK_SIZE, device="cpu", compute_type="int8"
    )
    log.info("Whisper PREVIEW pronto.")
    return _whisper_preview_model


def _audio_janela(samples: np.ndarray, janela_s: float) -> np.ndarray:
    max_n = int(janela_s * SAMPLE_RATE)
    if samples.shape[0] > max_n:
        return samples[-max_n:]
    return samples


def transcribe_whisper_preview(audio: np.ndarray) -> str:
    """Texto rápido para o overlay (não entrega; não substitui o final)."""
    if audio is None or getattr(audio, "size", 0) == 0:
        return ""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = _audio_janela(samples, WHISPER_PREVIEW_WINDOW_SEC)

    if _whisper_compartilha_modelo_preview():
        # Mesmo modelo configurado (tiny/base) — evita duplicar em memória.
        return transcribe_whisper(samples, rapido=True)

    with _whisper_preview_lock:
        model = load_whisper_preview_model()
        segments, _info = model.transcribe(
            samples,
            language="pt",
            beam_size=1,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return "".join(seg.text for seg in segments).strip()


def _whisper_set_warm(texto: str, n_amostras: int) -> None:
    global _whisper_warm_text, _whisper_warm_n
    with _whisper_warm_lock:
        _whisper_warm_text = texto or ""
        _whisper_warm_n = int(n_amostras)


def _whisper_get_warm() -> tuple[str, int]:
    with _whisper_warm_lock:
        return _whisper_warm_text, _whisper_warm_n


def _whisper_clear_warm() -> None:
    _whisper_set_warm("", 0)


def _whisper_stream_worker() -> None:
    """Overlay ao vivo: re-transcreve a janela recente."""
    ultimo_n = 0
    primeiro = True
    push_live("Carregando modelo…")
    try:
        if _whisper_compartilha_modelo_preview():
            load_whisper_model()
        else:
            load_whisper_preview_model()
    except Exception as exc:
        log.warning("Preview Whisper indisponível: %s", exc)
        push_live("Preview indisponível")
        return

    while _whisper_stream_active:
        espera = 0.35 if primeiro else WHISPER_STREAM_INTERVAL
        primeiro = False
        if _whisper_stream_stop is not None and _whisper_stream_stop.wait(espera):
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
        if n == ultimo_n:
            continue
        ultimo_n = n
        try:
            if _whisper_compartilha_modelo_preview():
                # tiny/base: buffer completo no mesmo modelo (overlay = warm)
                texto = transcribe_whisper(
                    np.asarray(audio, dtype=np.float32), rapido=True
                )
                if texto:
                    push_live(texto)
                    _whisper_set_warm(texto, n)
            else:
                texto = transcribe_whisper_preview(
                    np.asarray(audio, dtype=np.float32)
                )
                if texto:
                    push_live(texto)
        except Exception as exc:
            log.debug("Preview Whisper: %s", exc)


def _whisper_warm_worker() -> None:
    """
    Passes em paralelo com o modelo SELECIONADO no buffer completo.

    Enquanto grava, já vai preparando um resultado quase-final. No STOP,
    se o warm cobriu quase todo o áudio, reutiliza e evita outra inferência longa.
    (Só sobe esta thread quando o modelo NÃO é tiny/base — nesses casos o
    próprio preview já atualiza o warm.)
    """
    ultimo_n = 0
    try:
        load_whisper_model()
    except Exception as exc:
        log.warning("Warm Whisper indisponível: %s", exc)
        return

    while _whisper_stream_active:
        if _whisper_stream_stop is not None and _whisper_stream_stop.wait(
            _whisper_warm_interval()
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
        if n / float(SAMPLE_RATE) < max(WHISPER_STREAM_MIN_SEG, 1.5):
            continue
        if n == ultimo_n:
            continue
        # Se o modelo ainda está ocupado (outro warm), pula o ciclo
        if not _whisper_lock.acquire(blocking=False):
            continue
        _whisper_lock.release()
        try:
            texto = transcribe_whisper(np.asarray(audio, dtype=np.float32), rapido=True)
            if texto:
                ultimo_n = n
                _whisper_set_warm(texto, n)
                log.debug("Warm Whisper: %d amostras → %d chars.", n, len(texto))
        except Exception as exc:
            log.debug("Warm Whisper: %s", exc)


def whisper_final_rapido(audio: np.ndarray) -> str:
    """
    Resultado no STOP: reutiliza warm paralelo se estiver fresco; senão transcreve.

    Evita reprocessar do zero quando o modelo escolhido já rodou em paralelo.
    """
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = int(samples.shape[0])
    warm_texto, warm_n = _whisper_get_warm()
    gap_s = (n - warm_n) / float(SAMPLE_RATE) if n >= warm_n else 999.0

    if warm_texto and gap_s <= WHISPER_WARM_MAX_GAP_SEC:
        log.info(
            "Whisper: reutilizando pass paralelo (warm) — gap=%.2fs, %d chars.",
            gap_s,
            len(warm_texto),
        )
        if gap_s > 0.45:
            try:
                texto = transcribe_whisper(samples, rapido=False)
                if texto:
                    return texto
            except Exception as exc:
                log.debug("Refine pós-warm falhou (%s); usando warm.", exc)
        return warm_texto

    return transcribe_whisper(samples, rapido=False)


def whisper_stream_start() -> bool:
    """Inicia preview ao vivo (+ warm paralelo se o modelo for maior que base)."""
    global _whisper_stream_active, _whisper_stream_thread, _whisper_stream_stop
    global _whisper_warm_thread, _vosk_live_text
    if _whisper_stream_active:
        return True
    _vosk_live_text = ""
    _whisper_clear_warm()
    push_live("Ouvindo…")
    _whisper_stream_stop = threading.Event()
    _whisper_stream_active = True
    _whisper_stream_thread = threading.Thread(
        target=_whisper_stream_worker,
        name="whisper-preview",
        daemon=True,
    )
    _whisper_stream_thread.start()

    _whisper_warm_thread = None
    if not _whisper_compartilha_modelo_preview():
        _whisper_warm_thread = threading.Thread(
            target=_whisper_warm_worker,
            name="whisper-warm",
            daemon=True,
        )
        _whisper_warm_thread.start()
        log.info(
            "Preview Whisper: overlay=%s | paralelo=%s (warm).",
            WHISPER_PREVIEW_FALLBACK_SIZE,
            WHISPER_SIZE,
        )
    else:
        log.info(
            "Preview Whisper: mesmo modelo %s no overlay e no final (sem duplicar).",
            WHISPER_SIZE,
        )
    return True


def whisper_stream_finish() -> None:
    """Encerra preview/warm (não bloqueia a entrega final)."""
    global _whisper_stream_active, _whisper_stream_thread, _whisper_warm_thread
    if not _whisper_stream_active and _whisper_stream_thread is None:
        return
    _whisper_stream_active = False
    if _whisper_stream_stop is not None:
        _whisper_stream_stop.set()
    for thread in (_whisper_stream_thread, _whisper_warm_thread):
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.35)
    _whisper_stream_thread = None
    _whisper_warm_thread = None


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


# === INSTÂNCIA ÚNICA ===
# Duas cópias do assistente escutando a MESMA hotkey é a causa clássica de
# "está digitando no modo clipboard": a cópia antiga (com type em memória)
# continua viva e cola, enquanto a nova (clipboard) só copia. Um lockfile
# (arquivo travado) garante que só uma instância rode por vez no mesmo usuário.
_LOCKFILE_PATH = Path(tempfile.gettempdir()) / "assistente_voz_instancia.lock"
_arquivo_lock = None  # handle do arquivo aberto (mantém o lock vivo)


def _adquirir_instancia_unica() -> None:
    """
    Adquire um lock exclusivo de instância única (Windows/Linux/macOS).

    Cria/abre o lockfile com 'w' e tenta flock (Windows usa msvcrt). Se outra
    instância já o detém, exibe aviso em PT e encerra o processo sem abrir UI.
    O lock é liberado automaticamente quando o processo termina (arquivo fecha).
    """
    global _arquivo_lock
    try:
        _arquivo_lock = open(_LOCKFILE_PATH, "w")
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(_arquivo_lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                _arquivo_lock.close()
                _arquivo_lock = None
                log.error(
                    "Outra instância do Assistente de Voz já está em execução. "
                    "Feche-a antes de abrir uma nova (evita colagem fantasma na hotkey)."
                )
                sys.exit(1)
        else:
            import fcntl

            try:
                fcntl.flock(_arquivo_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                _arquivo_lock.close()
                _arquivo_lock = None
                log.error(
                    "Outra instância do Assistente de Voz já está em execução. "
                    "Feche-a antes de abrir uma nova (evita colagem fantasma na hotkey)."
                )
                sys.exit(1)
    except OSError as exc:
        # Lock indisponível (pasta temporária sem escrita?): segue sem trava,
        # mas avisa — o fluxo não deve quebrar por causa do lockfile.
        log.warning("Não foi possível criar lock de instância única (%s).", exc)
        _arquivo_lock = None


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
    """Simula Ctrl+V (Cmd+V no Darwin/macOS) no campo em foco.

    Proteção: no modo clipboard nunca deve ser chamado.
    """
    if (OUTPUT_MODE or "").strip().lower() == "clipboard":
        log.error("Bloqueado: tentativa de colar com OUTPUT_MODE=clipboard.")
        return
    controlador = keyboard.Controller()
    modificador = keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl
    with controlador.pressed(modificador):
        controlador.press("v")
        controlador.release("v")


def _copiar_clipboard(texto: str) -> None:
    """
    Copia texto para a área de transferência de forma confiável.

    No Windows usa a API nativa (CF_UNICODETEXT) com retries. Nunca simula
    teclas / Ctrl+V — só grava no clipboard.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002

            data = texto.encode("utf-16-le") + b"\x00\x00"
            for _ in range(8):
                if not user32.OpenClipboard(None):
                    time.sleep(0.05)
                    continue
                try:
                    user32.EmptyClipboard()
                    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    if not h_mem:
                        raise OSError("GlobalAlloc falhou")
                    ptr = kernel32.GlobalLock(h_mem)
                    if not ptr:
                        kernel32.GlobalFree(h_mem)
                        raise OSError("GlobalLock falhou")
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h_mem)
                    if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                        kernel32.GlobalFree(h_mem)
                        raise OSError("SetClipboardData falhou")
                    return
                finally:
                    user32.CloseClipboard()
        except Exception as exc:
            log.debug("Clipboard Win32 falhou (%s); tentando pyperclip.", exc)

    pyperclip.copy(texto)
    try:
        if pyperclip.paste() != texto:
            time.sleep(0.05)
            pyperclip.copy(texto)
    except Exception:
        pass


def _toast_ui(mensagem: str) -> None:
    """Mostra um toast curto no overlay (best-effort)."""
    if _ui_window is None:
        return
    try:
        _ui_window.evaluate_js(f"showToast({json.dumps(mensagem, ensure_ascii=False)})")
    except Exception:
        pass


def deliver_text(texto: str, modo: Optional[str] = None) -> None:
    """
    Entrega o texto reconhecido conforme ``OUTPUT_MODE`` (ou ``modo`` explícito).

    - ``clipboard``: SOMENTE copia após a transcrição final. Não foca janela,
      não digita, não cola (Ctrl+V). Overlay mostra o texto; usuário cola à mão.
    - ``type``: restaura a janela alvo e cola via clipboard + Ctrl+V/Cmd+V.
    """
    if texto is None or not str(texto).strip():
        log.info("deliver_text: texto vazio — nada a entregar.")
        return

    conteudo = str(texto)
    modo_efetivo = (modo or OUTPUT_MODE or "type").strip().lower()

    try:
        if modo_efetivo == "clipboard":
            # Caminho isolado: zero SendInput / zero SetForegroundWindow.
            _copiar_clipboard(conteudo)
            log.info(
                "Clipboard: copiado (%d chars) — SEM digitar/colar. Use Ctrl+V.",
                len(conteudo),
            )
            _toast_ui("Copiado — Ctrl+V para colar")
            return

        if modo_efetivo != "type":
            log.warning(
                "OUTPUT_MODE=%r desconhecido; usando 'type'. Válidos: type, clipboard.",
                modo_efetivo,
            )

        _copiar_clipboard(conteudo)
        _focar_janela_alvo()
        time.sleep(0.08)
        if (OUTPUT_MODE or "").strip().lower() == "clipboard":
            log.error("Abortado Ctrl+V: OUTPUT_MODE virou clipboard no meio da entrega.")
            return
        _colar_via_atalho()
        log.info("Type: texto colado no foco (%d chars).", len(conteudo))

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
        compartilhado = (WHISPER_SIZE or "").strip().lower() in ("tiny", "base")
        log.info(
            "Whisper: size=%s device=%s compute=%s — "
            "overlay=%s; warm paralelo=%s.",
            WHISPER_SIZE,
            DEVICE,
            WHISPER_COMPUTE,
            WHISPER_SIZE if compartilhado else "base(cpu)",
            "mesmo modelo" if compartilhado else WHISPER_SIZE,
        )
        # Pré-carga em background: preview (se separado) + modelo escolhido.
        threading.Thread(
            target=_preload_whisper_modelos,
            name="whisper-preload",
            daemon=True,
        ).start()


def _preload_whisper_modelos() -> None:
    """Pré-carrega preview (se preciso) e o modelo selecionado sem bloquear a UI."""
    try:
        if _whisper_compartilha_modelo_preview():
            load_whisper_model()
        else:
            load_whisper_preview_model()
            load_whisper_model()
    except Exception as exc:
        log.warning("Pré-carga Whisper falhou: %s", exc)


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
_UI_OVERLAY_SIZE = (380, 124)


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
                # Inclui live no push para o updateStatus não apagar o preview
                # ao vivo com last_text vazio durante a gravação.
                "live": _vosk_live_text
                if _ui_state in ("recording", "transcribing")
                else "",
                "level": _ui_level if _ui_state == "recording" else 0.0,
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
        global _vosk_model, _whisper_model, _whisper_force_cpu, _whisper_preview_model

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
                if _whisper_preview_model is not None:
                    _whisper_preview_model = None
                    log.info("Cache do Whisper PREVIEW invalidado (config alterada).")
                _whisper_clear_warm()
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
            "live": _vosk_live_text
            if _ui_state in ("recording", "transcribing")
            else "",
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

    def resize_overlay(self, width: int, height: int) -> dict:
        """
        Expande/reduz o overlay conforme o texto da transcrição (auto-fit).

        Chamado pelo JS conforme o conteúdo cresce; clamped pelo backend.
        """
        try:
            if _ui_window is None:
                return {"ok": False, "error": "Janela indisponível."}
            w = max(360, min(560, int(width)))
            h = max(112, min(360, int(height)))
            _ui_window.resize(w, h)
            return {"ok": True, "w": w, "h": h}
        except Exception as exc:
            log.debug("resize_overlay(%sx%s) falhou: %s", width, height, exc)
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
    Aplica glass translúcido nativo no Windows sem usar transparent=True do
    WebView2 (que no Windows costuma cair para fundo branco).

    Estratégia estável:
      1) Acrylic/Mica DWM (blur do desktop atrás da janela)
      2) WS_EX_LAYERED + alpha da janela inteira (translucidez real)
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import Structure, byref, c_byte, c_int, c_void_p, sizeof
    except Exception:
        return

    # Pequena espera: o HWND só existe de forma estável após o show.
    time.sleep(0.35)
    hwnd = _hwnd_da_janela_ui(window)
    if not hwnd:
        log.debug("Glass nativo: HWND da UI não encontrado.")
        return

    user32 = ctypes.windll.user32
    aplicado = False

    # Cantos arredondados (Win11) + tema escuro + Acrylic
    try:
        dwm = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMWCP_ROUND = 2  # ~12px — deve bater com --radius no CSS
        DWMSBT_TRANSIENT_WINDOW = 3  # Acrylic

        dark = c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(dark), sizeof(dark))
        # Clip nativo dos cantos (evita “cantos quadrados” por cima do glass CSS)
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
            aplicado = True
    except Exception as exc:
        log.debug("Glass nativo DWM indisponível: %s", exc)

    # Fallback Windows 10: AccentPolicy Acrylic blur behind
    if not aplicado:
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

            # 0xAABBGGRR — alpha ~0xB8 + tom preto
            accent = ACCENTPOLICY(4, 2, 0xB80C0C0E, 0)
            data = WINCOMPATTRDATA(
                19,
                ctypes.cast(ctypes.pointer(accent), c_void_p),
                sizeof(accent),
            )
            ok = user32.SetWindowCompositionAttribute(hwnd, byref(data))
            if ok:
                log.info("Glass nativo: Acrylic (AccentPolicy) aplicado.")
                aplicado = True
        except Exception as exc:
            log.debug("Glass nativo AccentPolicy falhou: %s", exc)

    # Translucidez da janela inteira (não depende do transparent= do WebView2).
    # Assim o desktop aparece por trás do vidro escuro, sem fundo branco.
    try:
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x80000
        LWA_ALPHA = 0x2
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        # 238/255 ≈ 93% opaco — menos translúcido, ainda com leve vidro
        user32.SetLayeredWindowAttributes(hwnd, 0, 238, LWA_ALPHA)
        log.info("Glass nativo: alpha da janela aplicado (translucidez).")
    except Exception as exc:
        log.debug("Glass nativo alpha falhou: %s", exc)


def run_ui() -> bool:
    """
    Abre a janela glass do PyWebView apontando para ui/index.html.

    Janela frameless/on-top com bridge js_api=Api(). No Windows, o fundo branco
    do WebView2 transparente é evitado: usamos background escuro + Acrylic/alpha
    nativos para o efeito de vidro. webview.start() bloqueia até fechar.
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

    # NÃO usar transparent=True no Windows/WebView2: cai para fundo branco.
    # O glass vem do Acrylic DWM + alpha da janela + CSS escuro translúcido.
    _ui_window = webview.create_window(
        "Assistente de Voz",
        url=str(html_path),
        js_api=Api(),
        frameless=True,
        on_top=True,
        easy_drag=True,
        transparent=False,
        # shadow do PyWebView é retangular e “vaza” nos cantos arredondados
        shadow=False,
        background_color="#0C0C0E",
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
    # Garante que só uma cópia do assistente escute a hotkey global. Se outra
    # instância estiver ativa, o aviso é exibido e o processo é encerrado.
    _adquirir_instancia_unica()

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
