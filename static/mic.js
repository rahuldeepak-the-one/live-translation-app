/* ============================================================
   Live Translation — WebSocket Client
   Captures mic audio, streams to local server, displays results
   ============================================================ */

// ============================================================
// CONFIGURATION
// ============================================================
const CLIENT_CONFIG = {
  // Audio capture
  targetSampleRate: 16000,
  chunkIntervalMs: 250,       // Send audio every 250ms

  // Languages
  languages: {
    en: { name: 'English', native: 'English' },
    ml: { name: 'Malayalam', native: 'മലയാളം' },
    hi: { name: 'Hindi', native: 'हिन्दी' },
    te: { name: 'Telugu', native: 'తెలుగు' },
  },

  // WebSocket
  reconnectDelayMs: 2000,
  maxReconnectAttempts: 50,
};


// ============================================================
// AUDIO CAPTURE — Mic → PCM Int16 chunks
// ============================================================
class AudioCapture {
  constructor(onChunk) {
    this.onChunk = onChunk;
    this.audioContext = null;
    this.stream = null;
    this.processor = null;
    this.source = null;
    this.isCapturing = false;
    this._buffer = [];
    this._sendTimer = null;
    this._actualSampleRate = 48000;
  }

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        }
      });

      this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      this._actualSampleRate = this.audioContext.sampleRate;

      this.source = this.audioContext.createMediaStreamSource(this.stream);

      // ScriptProcessorNode — deprecated but universally supported
      this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);

      this.processor.onaudioprocess = (e) => {
        if (!this.isCapturing) return;
        const float32 = e.inputBuffer.getChannelData(0);
        // Downsample to 16kHz and convert to Int16
        const downsampled = this._downsample(float32, this._actualSampleRate, CLIENT_CONFIG.targetSampleRate);
        const int16 = this._float32ToInt16(downsampled);
        this._buffer.push(int16);
      };

      this.source.connect(this.processor);
      this.processor.connect(this.audioContext.destination);

      // Send accumulated audio every chunkIntervalMs
      this._sendTimer = setInterval(() => {
        if (this._buffer.length > 0) {
          const merged = this._mergeBuffers(this._buffer);
          this._buffer = [];
          if (this.onChunk) this.onChunk(merged.buffer);
        }
      }, CLIENT_CONFIG.chunkIntervalMs);

      this.isCapturing = true;
      return this._actualSampleRate;

    } catch (err) {
      console.error('Mic access failed:', err);
      throw err;
    }
  }

  stop() {
    this.isCapturing = false;
    clearInterval(this._sendTimer);

    if (this.processor) {
      this.processor.disconnect();
      this.processor = null;
    }
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
    this._buffer = [];
  }

  _downsample(float32Array, fromRate, toRate) {
    if (fromRate === toRate) return float32Array;
    const ratio = fromRate / toRate;
    const newLength = Math.floor(float32Array.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
      result[i] = float32Array[Math.floor(i * ratio)];
    }
    return result;
  }

  _float32ToInt16(float32Array) {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return int16;
  }

  _mergeBuffers(buffers) {
    const totalLength = buffers.reduce((sum, b) => sum + b.length, 0);
    const merged = new Int16Array(totalLength);
    let offset = 0;
    for (const buf of buffers) {
      merged.set(buf, offset);
      offset += buf.length;
    }
    return merged;
  }
}


// ============================================================
// WEBSOCKET CONNECTION — with auto-reconnect
// ============================================================
class ServerConnection {
  constructor() {
    this.ws = null;
    this.isConnected = false;
    this._reconnectAttempts = 0;
    this._shouldReconnect = true;

    // Callbacks
    this.onTranslation = null;  // ({original, translated, timing}) => {}
    this.onStatus = null;       // (status) => {}
    this.onConnect = null;      // () => {}
    this.onDisconnect = null;   // () => {}
    this.onError = null;        // (msg) => {}
  }

  connect() {
    this._shouldReconnect = true;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/mic`;

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      console.error('WebSocket creation failed:', e);
      this._scheduleReconnect();
      return;
    }

    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      this.isConnected = true;
      this._reconnectAttempts = 0;
      console.log('Connected to server');
      if (this.onConnect) this.onConnect();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case 'sentence':
            if (this.onTranslation) this.onTranslation(msg);
            break;
          case 'status':
            if (this.onStatus) this.onStatus(msg.state, msg.message);
            break;
          case 'config_ack':
            console.log('Config acknowledged:', msg.config);
            break;
          case 'cleared':
            console.log('Server buffer cleared');
            break;
          case 'error':
            if (this.onError) this.onError(msg.message);
            break;
        }
      } catch (e) {
        console.warn('Bad message from server:', e);
      }
    };

    this.ws.onclose = () => {
      this.isConnected = false;
      if (this.onDisconnect) this.onDisconnect();
      this._scheduleReconnect();
    };

    this.ws.onerror = (e) => {
      console.error('WebSocket error:', e);
    };
  }

  disconnect() {
    this._shouldReconnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.isConnected = false;
  }

  sendAudio(arrayBuffer) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(arrayBuffer);
    }
  }

  sendConfig(config) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'config', ...config }));
    }
  }

  sendClear() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'clear' }));
    }
  }

  _scheduleReconnect() {
    if (!this._shouldReconnect) return;
    if (this._reconnectAttempts >= CLIENT_CONFIG.maxReconnectAttempts) {
      if (this.onError) this.onError('Max reconnect attempts reached.');
      return;
    }
    this._reconnectAttempts++;
    const delay = CLIENT_CONFIG.reconnectDelayMs * Math.min(this._reconnectAttempts, 5);
    console.log(`Reconnecting in ${delay}ms (attempt ${this._reconnectAttempts})...`);
    setTimeout(() => this.connect(), delay);
  }
}


// ============================================================
// DISPLAY MANAGER (reused from app.js pattern)
// ============================================================
class DisplayManager {
  constructor(containerEl, interimEl, emptyStateEl) {
    this.container = containerEl;
    this.interimEl = interimEl;
    this.emptyStateEl = emptyStateEl;
    this.segments = [];
  }

  addSegment(originalText, translatedText, timing) {
    if (this.emptyStateEl) this.emptyStateEl.style.display = 'none';

    const segment = document.createElement('div');
    segment.className = 'segment';

    const timingStr = timing
      ? `<span class="segment__timing">STT: ${timing.stt}s · Translate: ${timing.translate}s</span>`
      : '';

    segment.innerHTML = `
      <div class="segment__translated">${this._esc(translatedText)}</div>
      <div class="segment__original">${this._esc(originalText)} ${timingStr}</div>
    `;

    if (this.interimEl) {
      this.container.insertBefore(segment, this.interimEl);
    } else {
      this.container.appendChild(segment);
    }

    this.segments.push(segment);
    this._scroll();
  }

  showInterim(text) {
    if (!this.interimEl) return;
    if (text) {
      this.interimEl.style.display = 'block';
      this.interimEl.querySelector('.interim-block__text').textContent = text;
      if (this.emptyStateEl) this.emptyStateEl.style.display = 'none';
    } else {
      this.interimEl.style.display = 'none';
    }
    this._scroll();
  }

  clear() {
    this.segments.forEach(s => s.remove());
    this.segments = [];
    this.showInterim('');
    if (this.emptyStateEl) this.emptyStateEl.style.display = '';
  }

  _scroll() {
    requestAnimationFrame(() => {
      this.container.scrollTop = this.container.scrollHeight;
    });
  }

  _esc(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }
}


// ============================================================
// TOAST
// ============================================================
class Toast {
  constructor(el) { this.el = el; this._t = null; }

  show(msg, ms = 4000) {
    clearTimeout(this._t);
    this.el.textContent = msg;
    this.el.classList.add('visible');
    this._t = setTimeout(() => this.el.classList.remove('visible'), ms);
  }
}


// ============================================================
// APP CONTROLLER
// ============================================================
class ClientApp {
  constructor() {
    this.sourceLang = 'en';
    this.targetLang = 'ml';
    this.isRunning = false;

    this.audio = new AudioCapture((chunk) => this.conn.sendAudio(chunk));
    this.conn = new ServerConnection();
    this.display = null;
    this.toast = null;
    this.els = {};
  }

  init() {
    this._bindDOM();
    this.display = new DisplayManager(
      this.els.displayArea, this.els.interimBlock, this.els.emptyState
    );
    this.toast = new Toast(this.els.toastEl);

    this._setupConnection();
    this._setupListeners();
    this._updateUI();

    // Connect to server
    this.conn.connect();
    console.log('🎤 Client initialized — connecting to server...');
  }

  _bindDOM() {
    this.els = {
      statusBadge: document.getElementById('status-badge'),
      statusLabel: document.getElementById('status-label'),
      connDot: document.getElementById('conn-dot'),
      connLabel: document.getElementById('conn-label'),
      settingsBtn: document.getElementById('btn-settings'),
      settingsPanel: document.getElementById('settings-panel'),
      displayArea: document.getElementById('display-area'),
      interimBlock: document.getElementById('interim-block'),
      emptyState: document.getElementById('empty-state'),
      toastEl: document.getElementById('toast'),
      btnStart: document.getElementById('btn-start'),
      btnStop: document.getElementById('btn-stop'),
      btnClear: document.getElementById('btn-clear'),
      targetLangBtns: document.querySelectorAll('.btn-lang[data-target-lang]'),
      sourceLangBtns: document.querySelectorAll('.btn-lang[data-source-lang]'),
      footerSourceLang: document.getElementById('footer-source-lang'),
      footerProvider: document.getElementById('footer-provider'),
    };
  }

  _setupConnection() {
    this.conn.onConnect = () => {
      this._setConnStatus(true);
      this.conn.sendConfig({
        sourceLang: this.sourceLang,
        targetLang: this.targetLang,
        sampleRate: CLIENT_CONFIG.targetSampleRate,
      });
      this.toast.show('Connected to server ✓', 2000);
    };

    this.conn.onDisconnect = () => {
      this._setConnStatus(false);
      if (this.isRunning) {
        this.stop();
        this.toast.show('Server disconnected. Stopped listening.');
      }
    };

    this.conn.onTranslation = (msg) => {
      this.display.showInterim('');
      this.display.addSegment(msg.en, 'heard ✓', null);
    };

    this.conn.onStatus = (status, message) => {
      if (status === 'processing') {
        this._setStatus('translating');
        this.display.showInterim('Processing speech...');
      } else if (status === 'listening') {
        this._setStatus('listening');
        this.display.showInterim('');
      } else if (status === 'ready') {
        this._setStatus('idle');
      }
    };

    this.conn.onError = (msg) => {
      this.toast.show(msg);
    };
  }

  _setupListeners() {
    this.els.btnStart.addEventListener('click', () => this.start());
    this.els.btnStop.addEventListener('click', () => this.stop());
    this.els.btnClear.addEventListener('click', () => {
      this.display.clear();
      this.conn.sendClear();
    });

    this.els.targetLangBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        this.targetLang = btn.dataset.targetLang;
        this._updateTargetLangUI();
        this.conn.sendConfig({ targetLang: this.targetLang });
      });
    });

    this.els.sourceLangBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        this.sourceLang = btn.dataset.sourceLang;
        this._updateSourceLangUI();
        this.conn.sendConfig({ sourceLang: this.sourceLang });
      });
    });

    this.els.settingsBtn.addEventListener('click', () => {
      this.els.settingsPanel.classList.toggle('open');
    });

    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.code === 'Space') { e.preventDefault(); this.isRunning ? this.stop() : this.start(); }
      else if (e.code === 'Escape') { this.display.clear(); this.conn.sendClear(); }
      else if (e.code === 'KeyF') { document.body.classList.toggle('fullscreen-mode'); }
    });
  }

  async start() {
    if (!this.conn.isConnected) {
      this.toast.show('Not connected to server. Waiting...');
      return;
    }

    try {
      await this.audio.start();
      this.isRunning = true;
      this._setStatus('listening');
      this._updateToggleUI();
    } catch (err) {
      this.toast.show('Microphone access denied. Please allow microphone.');
    }
  }

  stop() {
    this.audio.stop();
    this.isRunning = false;
    this._setStatus('idle');
    this._updateToggleUI();
    this.display.showInterim('');
  }

  // --- UI helpers ---

  _setStatus(status) {
    const badge = this.els.statusBadge;
    if (badge) badge.setAttribute('data-status', status);
    const labels = { idle: 'Idle', listening: 'Listening', translating: 'Translating...', error: 'Error' };
    if (this.els.statusLabel) this.els.statusLabel.textContent = labels[status] || status;
  }

  _setConnStatus(connected) {
    if (this.els.connDot) {
      this.els.connDot.style.background = connected ? '#22c55e' : '#ef4444';
    }
    if (this.els.connLabel) {
      this.els.connLabel.textContent = connected ? 'Server Connected' : 'Disconnected';
    }
  }

  _updateToggleUI() {
    this.els.btnStart.style.display = this.isRunning ? 'none' : 'flex';
    this.els.btnStop.style.display = this.isRunning ? 'flex' : 'none';
  }

  _updateTargetLangUI() {
    this.els.targetLangBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.targetLang === this.targetLang);
    });
  }

  _updateSourceLangUI() {
    this.els.sourceLangBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.sourceLang === this.sourceLang);
    });
    const lang = CLIENT_CONFIG.languages[this.sourceLang];
    if (this.els.footerSourceLang && lang) {
      this.els.footerSourceLang.textContent = lang.name;
    }
  }

  _updateUI() {
    this._updateToggleUI();
    this._updateTargetLangUI();
    this._updateSourceLangUI();
    this._setStatus('idle');
    this._setConnStatus(false);
    if (this.els.footerProvider) this.els.footerProvider.textContent = 'Local GPU (Whisper + NLLB)';
  }
}

// ============================================================
// BOOTSTRAP
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  const app = new ClientApp();
  app.init();
  window.__app = app;
});
