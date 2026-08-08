// UI do Assistente de Voz — estado visual, drawer de configurações e ponte com o Python.
// A ponte usa window.pywebview.api (get_config / save_config / get_status).
// Sem a API (aberto no navegador), cai em modo mock com localStorage.

"use strict";

// Rótulos amigáveis por estado.
const ROTULOS_ESTADO = {
  idle: "Ocioso",
  recording: "Gravando",
  transcribing: "Transcrevendo",
};

// Campos do formulário (name no HTML == chave da config).
const CAMPOS_CONFIG = [
  "ENGINE",
  "INPUT_DEVICE",
  "WHISPER_SIZE",
  "WHISPER_COMPUTE",
  "DEVICE",
  "OUTPUT_MODE",
  "HOTKEY",
  "VOSK_MODEL_PATH",
];

const MOCK_KEY = "voz_config";
let _pollTimer = null;
let _settingsOpen = false;
let _lastOverlaySize = { w: 380, h: 120 };
let _fitTimer = null;

// ---- Estado visual -----------------------------------------------------

// Atalho: define apenas o estado.
function setStatus(state) {
  updateStatus({ state });
}

// Expande a janela conforme o texto da transcrição (só no modo overlay).
function fitOverlayToTranscript() {
  if (_settingsOpen) return;
  const texto = (document.getElementById("last-text") || {}).textContent || "";
  const chars = texto.trim().length;
  // Largura: cresce com linhas longas; altura: com quebras / volume de texto.
  const approxCols = 36;
  const lines = chars === 0 ? 1 : Math.max(1, Math.ceil(chars / approxCols));
  const lineH = 19;
  const chromeH = 52; // status inferior + paddings + chrome
  const w = Math.min(560, Math.max(360, 300 + Math.min(chars, 120) * 1.6));
  const h = Math.min(360, Math.max(112, chromeH + lines * lineH + (chars > 0 ? 12 : 0)));
  const wi = Math.round(w);
  const hi = Math.round(h);
  if (Math.abs(wi - _lastOverlaySize.w) < 8 && Math.abs(hi - _lastOverlaySize.h) < 6) {
    return;
  }
  _lastOverlaySize = { w: wi, h: hi };
  if (!(temApi() && window.pywebview.api.resize_overlay)) return;
  try {
    window.pywebview.api.resize_overlay(wi, hi);
  } catch (e) {}
}

function scheduleFitOverlay() {
  clearTimeout(_fitTimer);
  _fitTimer = setTimeout(fitOverlayToTranscript, 60);
}

// Atualiza a UI a partir do payload do motor: {state, last_text, engine}.
function updateStatus(payload) {
  payload = payload || {};
  const overlay = document.getElementById("overlay");
  const label = document.getElementById("status-label");
  const engine = document.getElementById("engine");
  const lastText = document.getElementById("last-text");

  const state = payload.state || "idle";

  if (overlay) {
    overlay.classList.remove("state-idle", "state-recording", "state-transcribing");
    overlay.classList.add("state-" + state);
  }
  if (label) label.textContent = ROTULOS_ESTADO[state] || "Ocioso";
  if (engine && typeof payload.engine === "string" && payload.engine) {
    engine.textContent = payload.engine;
  }
  // recording/transcribing: mostra 'live' (parcial). idle: mostra last_text.
  // Nunca apagar o parcial com last_text vazio no meio da fala.
  if (lastText) {
    if (state === "recording" || state === "transcribing") {
      if (typeof payload.live === "string" && payload.live.length) {
        lastText.textContent = payload.live;
      } else if (payload.last_text) {
        lastText.textContent = payload.last_text;
      }
    } else if (payload.last_text !== undefined) {
      lastText.textContent = payload.last_text || "";
    }
  }
  if (payload.level !== undefined) updateLevel(payload.level);
  scheduleFitOverlay();
}

// Mostra o texto parcial ao vivo (streaming) no overlay enquanto grava.
function updateLive(texto) {
  const lastText = document.getElementById("last-text");
  if (lastText) lastText.textContent = texto || "";
  scheduleFitOverlay();
}

// Atualiza o medidor de nível do microfone (pico 0..1).
function updateLevel(pico) {
  const bar = document.getElementById("level-bar");
  if (!bar) return;
  // Escala não-linear para dar destaque a níveis baixos de fala.
  const pct = Math.max(0, Math.min(100, Math.sqrt(pico) * 140));
  bar.style.width = pct + "%";
}

// Expõe globais para o Python chamar via evaluate_js.
window.updateStatus = updateStatus;
window.setStatus = setStatus;
window.updateLive = updateLive;
window.updateLevel = updateLevel;

// ---- Ponte de configuração --------------------------------------------

function temApi() {
  return !!(window.pywebview && window.pywebview.api);
}

// Lê a config (API do Python, ou localStorage como mock).
async function loadConfig() {
  if (temApi() && window.pywebview.api.get_config) {
    try {
      return (await window.pywebview.api.get_config()) || {};
    } catch (e) {
      console.error("Falha ao obter config via API:", e);
    }
  }
  try {
    return JSON.parse(localStorage.getItem(MOCK_KEY)) || {};
  } catch (e) {
    return {};
  }
}

// Preenche o formulário com os valores da config.
function preencherForm(cfg) {
  CAMPOS_CONFIG.forEach((chave) => {
    const el = document.querySelector('[name="' + chave + '"]');
    if (el && cfg[chave] !== undefined && cfg[chave] !== null) {
      el.value = String(cfg[chave]);
    }
  });
}

// Mostra/oculta campos específicos de cada motor conforme o ENGINE escolhido.
function atualizarCamposPorEngine() {
  const sel = document.querySelector('[name="ENGINE"]');
  const engine = sel ? sel.value : "vosk";
  document.querySelectorAll(".engine-whisper").forEach((el) => {
    el.hidden = engine !== "whisper";
  });
  document.querySelectorAll(".engine-vosk").forEach((el) => {
    el.hidden = engine !== "vosk";
  });
}

// Coleta os valores do formulário.
function coletarForm() {
  const cfg = {};
  CAMPOS_CONFIG.forEach((chave) => {
    const el = document.querySelector('[name="' + chave + '"]');
    if (el) cfg[chave] = el.value;
  });
  return cfg;
}

// Salva a config (API do Python, ou localStorage como mock).
async function saveConfig() {
  const cfg = coletarForm();
  if (temApi() && window.pywebview.api.save_config) {
    try {
      const res = await window.pywebview.api.save_config(cfg);
      if (res && res.ok === false) {
        showToast(res.error || "Erro ao salvar");
        return;
      }
      showToast("Salvo");
    } catch (e) {
      console.error("Falha ao salvar via API:", e);
      showToast("Erro ao salvar");
    }
  } else {
    localStorage.setItem(MOCK_KEY, JSON.stringify(cfg));
    showToast("Salvo (local)");
  }
}

// ---- Drawer de configurações ------------------------------------------

// Popula o seletor de microfones a partir da API do Python.
async function popularMicrofones(valorAtual) {
  const sel = document.getElementById("field-input-device");
  if (!sel || !(temApi() && window.pywebview.api.list_input_devices)) return;
  try {
    const devs = (await window.pywebview.api.list_input_devices()) || [];
    sel.innerHTML = '<option value="">Padrão do sistema</option>';
    devs.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = String(d.index);
      opt.textContent = "[" + d.index + "] " + d.name;
      sel.appendChild(opt);
    });
    if (valorAtual !== undefined && valorAtual !== null && valorAtual !== "") {
      sel.value = String(valorAtual);
    }
  } catch (e) {
    console.error("Falha ao listar microfones:", e);
  }
}

async function openSettings() {
  const cfg = await loadConfig();
  await popularMicrofones(cfg.INPUT_DEVICE);
  preencherForm(cfg);
  atualizarCamposPorEngine();
  _settingsOpen = true;
  // Expande a janela para caber o painel (transparência não é confiável no Windows).
  if (temApi() && window.pywebview.api.resize_window) {
    try {
      await window.pywebview.api.resize_window("settings");
    } catch (e) {}
  }
  const el = document.getElementById("settings");
  if (el) el.hidden = false;
}

function closeSettings() {
  const el = document.getElementById("settings");
  if (el) el.hidden = true;
  _settingsOpen = false;
  _lastOverlaySize = { w: 0, h: 0 };
  // Volta ao tamanho compacto do overlay.
  if (temApi() && window.pywebview.api.resize_window) {
    try {
      window.pywebview.api.resize_window("overlay");
    } catch (e) {}
  }
  scheduleFitOverlay();
}

// ---- Toast -------------------------------------------------------------

function showToast(mensagem) {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.style.cssText =
      "position:fixed;left:50%;bottom:20px;transform:translateX(-50%);" +
      "background:rgba(20,20,22,0.92);color:rgba(255,255,255,0.92);" +
      "padding:8px 14px;border-radius:10px;font:13px system-ui,'Segoe UI',sans-serif;" +
      "border:1px solid rgba(255,255,255,0.08);box-shadow:0 6px 24px rgba(0,0,0,0.5);" +
      "z-index:100;opacity:0;transition:opacity 160ms ease;pointer-events:none;";
    document.body.appendChild(toast);
  }
  toast.textContent = mensagem;
  requestAnimationFrame(() => (toast.style.opacity = "1"));
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toast.style.opacity = "0"), 1400);
}
window.showToast = showToast;

// ---- Polling de status (fallback quando eventos não chegam) ------------

function startStatusPolling() {
  if (!(temApi() && window.pywebview.api.get_status)) return;
  if (_pollTimer) return;
  _pollTimer = setInterval(async () => {
    try {
      const st = await window.pywebview.api.get_status();
      if (st) updateStatus(st);
    } catch (e) {
      // UI/bridge indisponível: ignora silenciosamente.
    }
  }, 150);
}

// Fecha o programa (pede ao Python destruir a janela PyWebView).
async function quitApp() {
  if (temApi() && window.pywebview.api.quit) {
    try {
      await window.pywebview.api.quit();
      return;
    } catch (e) {
      console.error("Falha ao fechar via API:", e);
    }
  }
  window.close();
}

// ---- Inicialização -----------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const btnSettings = document.getElementById("btn-settings");
  const btnClose = document.getElementById("btn-close");
  const btnQuit = document.getElementById("btn-quit");
  const btnQuitSettings = document.getElementById("btn-quit-settings");
  const form = document.getElementById("settings-form");

  if (btnSettings) btnSettings.addEventListener("click", openSettings);
  if (btnClose) btnClose.addEventListener("click", closeSettings);
  if (btnQuit) btnQuit.addEventListener("click", quitApp);
  if (btnQuitSettings) btnQuitSettings.addEventListener("click", quitApp);

  const selEngine = document.querySelector('[name="ENGINE"]');
  if (selEngine) selEngine.addEventListener("change", atualizarCamposPorEngine);

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      saveConfig();
    });
  }

  // Estado inicial e polling (se a API existir).
  updateStatus({ state: "idle" });
  loadConfig().then((cfg) => {
    const engine = document.getElementById("engine");
    if (engine && cfg.ENGINE) engine.textContent = cfg.ENGINE;
  });
  startStatusPolling();
});
