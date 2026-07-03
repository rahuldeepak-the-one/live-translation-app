/* ============================================================
   Live Translation App — Church Display
   Application Logic
   ============================================================ */

// ============================================================
// CONFIGURATION
// ============================================================
const CONFIG = {
  // Language definitions
  languages: {
    en: { code: 'en', bcp47: 'en-IN', name: 'English', native: 'English' },
    ml: { code: 'ml', bcp47: 'ml-IN', name: 'Malayalam', native: 'മലയാളം' },
    hi: { code: 'hi', bcp47: 'hi-IN', name: 'Hindi', native: 'हिन्दी' },
    te: { code: 'te', bcp47: 'te-IN', name: 'Telugu', native: 'తెలుగు' },
  },

  // Translation providers (tried in order)
  translationProviders: [
    {
      name: 'Lingva-1',
      type: 'lingva',
      baseUrl: 'https://lingva.ml',
    },
    {
      name: 'Lingva-2',
      type: 'lingva',
      baseUrl: 'https://lingva.thedaviddelta.com',
    },
    {
      name: 'Lingva-3',
      type: 'lingva',
      baseUrl: 'https://translate.plausibility.cloud',
    },
    {
      name: 'MyMemory',
      type: 'mymemory',
      baseUrl: 'https://api.mymemory.translated.net',
    },
  ],

  // Debounce delay for translation requests (ms)
  translationDebounce: 400,

  // Max retries per provider before moving to next
  maxRetriesPerProvider: 1,

  // Translation request timeout (ms)
  requestTimeout: 8000,

  // Auto-restart speech recognition delay (ms)
  restartDelay: 300,

  // Toast display duration (ms)
  toastDuration: 4000,
};


// ============================================================
// SPEECH ENGINE — Wraps Web Speech API
// ============================================================
class SpeechEngine {
  constructor() {
    this.recognition = null;
    this.isRunning = false;
    this.lang = CONFIG.languages.en.bcp47;
    this._shouldRestart = false;
    this._restartTimer = null;

    // Event callbacks
    this.onInterim = null;    // (text) => {}
    this.onFinal = null;      // (text) => {}
    this.onStatusChange = null; // (status) => {}  — 'idle' | 'listening' | 'error'
    this.onError = null;      // (error) => {}

    this._initRecognition();
  }

  _initRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.error('Web Speech API not supported');
      return;
    }

    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.maxAlternatives = 1;
    this.recognition.lang = this.lang;

    this.recognition.onresult = (event) => {
      let interimTranscript = '';
      let finalTranscript = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += transcript;
        } else {
          interimTranscript += transcript;
        }
      }

      if (finalTranscript && this.onFinal) {
        this.onFinal(finalTranscript.trim());
      }
      if (interimTranscript && this.onInterim) {
        this.onInterim(interimTranscript.trim());
      }
    };

    this.recognition.onerror = (event) => {
      console.warn('Speech recognition error:', event.error);

      // "no-speech" and "aborted" are recoverable
      if (event.error === 'no-speech' || event.error === 'aborted') {
        return; // onend will fire and auto-restart
      }

      if (event.error === 'not-allowed') {
        this._shouldRestart = false;
        this.isRunning = false;
        if (this.onError) this.onError('Microphone access denied. Please allow microphone permissions.');
        if (this.onStatusChange) this.onStatusChange('error');
        return;
      }

      if (event.error === 'network') {
        if (this.onError) this.onError('Network error. Check your internet connection.');
        // Still try to restart
        return;
      }

      if (this.onError) this.onError(`Speech recognition error: ${event.error}`);
    };

    this.recognition.onend = () => {
      if (this._shouldRestart && this.isRunning) {
        // Auto-restart after Chrome's periodic stops
        clearTimeout(this._restartTimer);
        this._restartTimer = setTimeout(() => {
          try {
            this.recognition.lang = this.lang;
            this.recognition.start();
          } catch (e) {
            console.warn('Restart failed:', e);
            // Try again after a longer delay
            setTimeout(() => {
              try {
                this.recognition.lang = this.lang;
                this.recognition.start();
              } catch (e2) {
                console.error('Restart failed permanently:', e2);
                this.isRunning = false;
                this._shouldRestart = false;
                if (this.onStatusChange) this.onStatusChange('idle');
              }
            }, 1000);
          }
        }, CONFIG.restartDelay);
      } else {
        this.isRunning = false;
        if (this.onStatusChange) this.onStatusChange('idle');
      }
    };

    this.recognition.onstart = () => {
      if (this.onStatusChange) this.onStatusChange('listening');
    };
  }

  setLanguage(langCode) {
    const lang = CONFIG.languages[langCode];
    if (lang) {
      this.lang = lang.bcp47;
      if (this.recognition) {
        this.recognition.lang = this.lang;
      }
      // If currently running, restart with new language
      if (this.isRunning) {
        this._shouldRestart = true;
        try {
          this.recognition.stop();
        } catch (e) { /* ignore */ }
      }
    }
  }

  start() {
    if (!this.recognition) {
      if (this.onError) this.onError('Speech recognition not supported in this browser. Use Chrome.');
      return;
    }

    this._shouldRestart = true;
    this.isRunning = true;
    this.recognition.lang = this.lang;

    try {
      this.recognition.start();
    } catch (e) {
      console.warn('Start failed (may already be running):', e);
    }
  }

  stop() {
    this._shouldRestart = false;
    this.isRunning = false;
    clearTimeout(this._restartTimer);

    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch (e) { /* ignore */ }
    }

    if (this.onStatusChange) this.onStatusChange('idle');
  }
}


// ============================================================
// TRANSLATION SERVICE — Multi-provider with failover
// ============================================================
class TranslationService {
  constructor() {
    this._currentProviderIndex = 0;
    this._cache = new Map();
    this._pendingRequest = null;
    this._debounceTimer = null;
    this._activeProviderName = CONFIG.translationProviders[0].name;

    // Callbacks
    this.onProviderChange = null; // (providerName) => {}
  }

  get activeProvider() {
    return this._activeProviderName;
  }

  /**
   * Translate text with debouncing and multi-provider failover
   */
  async translate(text, sourceLang, targetLang) {
    if (!text || sourceLang === targetLang) return text;

    // Check cache
    const cacheKey = `${sourceLang}|${targetLang}|${text}`;
    if (this._cache.has(cacheKey)) {
      return this._cache.get(cacheKey);
    }

    // Try providers in order
    let lastError = null;
    const startIndex = this._currentProviderIndex;

    for (let attempt = 0; attempt < CONFIG.translationProviders.length; attempt++) {
      const providerIndex = (startIndex + attempt) % CONFIG.translationProviders.length;
      const provider = CONFIG.translationProviders[providerIndex];

      try {
        const result = await this._translateWithProvider(provider, text, sourceLang, targetLang);
        if (result) {
          // Cache the result
          this._cache.set(cacheKey, result);

          // If we switched providers, update
          if (providerIndex !== this._currentProviderIndex) {
            this._currentProviderIndex = providerIndex;
            this._activeProviderName = provider.name;
            if (this.onProviderChange) this.onProviderChange(provider.name);
          }

          return result;
        }
      } catch (e) {
        lastError = e;
        console.warn(`Translation provider ${provider.name} failed:`, e.message);
      }
    }

    console.error('All translation providers failed:', lastError);
    return `[Translation failed] ${text}`;
  }

  async _translateWithProvider(provider, text, sourceLang, targetLang) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), CONFIG.requestTimeout);

    try {
      let url;
      if (provider.type === 'lingva') {
        url = `${provider.baseUrl}/api/v1/${sourceLang}/${targetLang}/${encodeURIComponent(text)}`;
      } else if (provider.type === 'mymemory') {
        url = `${provider.baseUrl}/get?q=${encodeURIComponent(text)}&langpair=${sourceLang}|${targetLang}`;
      }

      const response = await fetch(url, { signal: controller.signal });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      if (provider.type === 'lingva') {
        return data.translation || null;
      } else if (provider.type === 'mymemory') {
        if (data.responseStatus === 200 && data.responseData) {
          return data.responseData.translatedText || null;
        }
        return null;
      }
    } finally {
      clearTimeout(timeoutId);
    }
  }

  clearCache() {
    this._cache.clear();
  }
}


// ============================================================
// DISPLAY MANAGER — Handles the output area
// ============================================================
class DisplayManager {
  constructor(containerEl, interimEl, emptyStateEl) {
    this.container = containerEl;
    this.interimEl = interimEl;
    this.emptyStateEl = emptyStateEl;
    this.segments = [];
  }

  addSegment(originalText, translatedText) {
    // Hide empty state
    if (this.emptyStateEl) {
      this.emptyStateEl.style.display = 'none';
    }

    const segment = document.createElement('div');
    segment.className = 'segment';
    segment.innerHTML = `
      <div class="segment__translated">${this._escapeHtml(translatedText)}</div>
      <div class="segment__original">${this._escapeHtml(originalText)}</div>
    `;

    // Insert before interim block
    if (this.interimEl) {
      this.container.insertBefore(segment, this.interimEl);
    } else {
      this.container.appendChild(segment);
    }

    this.segments.push(segment);
    this._scrollToBottom();
  }

  updateInterim(text) {
    if (!this.interimEl) return;

    if (text) {
      this.interimEl.style.display = 'block';
      this.interimEl.querySelector('.interim-block__text').textContent = text;

      // Hide empty state
      if (this.emptyStateEl) {
        this.emptyStateEl.style.display = 'none';
      }
    } else {
      this.interimEl.style.display = 'none';
    }

    this._scrollToBottom();
  }

  clear() {
    this.segments.forEach(s => s.remove());
    this.segments = [];
    this.updateInterim('');

    if (this.emptyStateEl) {
      this.emptyStateEl.style.display = '';
    }
  }

  _scrollToBottom() {
    requestAnimationFrame(() => {
      this.container.scrollTop = this.container.scrollHeight;
    });
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}


// ============================================================
// TOAST — Error/info notifications
// ============================================================
class Toast {
  constructor(el) {
    this.el = el;
    this._timer = null;
  }

  show(message, duration = CONFIG.toastDuration) {
    clearTimeout(this._timer);
    this.el.textContent = message;
    this.el.classList.add('visible');

    this._timer = setTimeout(() => {
      this.el.classList.remove('visible');
    }, duration);
  }

  hide() {
    clearTimeout(this._timer);
    this.el.classList.remove('visible');
  }
}


// ============================================================
// APP CONTROLLER — Wires everything together
// ============================================================
class App {
  constructor() {
    // State
    this.sourceLang = 'en';
    this.targetLang = 'ml';
    this.isRunning = false;

    // Modules
    this.speech = new SpeechEngine();
    this.translator = new TranslationService();
    this.display = null;
    this.toast = null;

    // DOM refs (populated in init)
    this.els = {};

    // Translation queue
    this._translationQueue = [];
    this._isTranslating = false;
  }

  init() {
    this._bindDOM();
    this._setupDisplay();
    this._setupSpeechCallbacks();
    this._setupEventListeners();
    this._updateUI();

    console.log('🎤 Live Translation App initialized');
  }

  _bindDOM() {
    this.els = {
      statusBadge: document.getElementById('status-badge'),
      statusLabel: document.getElementById('status-label'),
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

  _setupDisplay() {
    this.display = new DisplayManager(
      this.els.displayArea,
      this.els.interimBlock,
      this.els.emptyState
    );
    this.toast = new Toast(this.els.toastEl);
  }

  _setupSpeechCallbacks() {
    this.speech.onInterim = (text) => {
      this.display.updateInterim(text);
    };

    this.speech.onFinal = (text) => {
      this.display.updateInterim('');
      this._enqueueTranslation(text);
    };

    this.speech.onStatusChange = (status) => {
      this._setStatus(status);
    };

    this.speech.onError = (errorMsg) => {
      this.toast.show(errorMsg);
    };
  }

  _setupEventListeners() {
    // Start button
    this.els.btnStart.addEventListener('click', () => this.start());

    // Stop button
    this.els.btnStop.addEventListener('click', () => this.stop());

    // Clear button
    this.els.btnClear.addEventListener('click', () => {
      this.display.clear();
      this.translator.clearCache();
    });

    // Target language buttons
    this.els.targetLangBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        this.targetLang = btn.dataset.targetLang;
        this._updateTargetLangUI();
        this.translator.clearCache();
      });
    });

    // Source language buttons
    this.els.sourceLangBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        this.sourceLang = btn.dataset.sourceLang;
        this.speech.setLanguage(this.sourceLang);
        this._updateSourceLangUI();
        this.translator.clearCache();
      });
    });

    // Settings panel toggle
    this.els.settingsBtn.addEventListener('click', () => {
      this.els.settingsPanel.classList.toggle('open');
    });

    // Translation provider change
    this.translator.onProviderChange = (name) => {
      if (this.els.footerProvider) {
        this.els.footerProvider.textContent = `via ${name}`;
      }
    };

    // Keyboard shortcut: Space to toggle, Escape to clear
    document.addEventListener('keydown', (e) => {
      // Don't capture if user is in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      if (e.code === 'Space') {
        e.preventDefault();
        this.isRunning ? this.stop() : this.start();
      } else if (e.code === 'Escape') {
        this.display.clear();
      } else if (e.code === 'KeyF') {
        document.body.classList.toggle('fullscreen-mode');
      }
    });
  }

  // --- Actions ---

  start() {
    this.isRunning = true;
    this.speech.start();
    this._updateToggleUI();
  }

  stop() {
    this.isRunning = false;
    this.speech.stop();
    this.display.updateInterim('');
    this._updateToggleUI();
  }

  // --- Translation Queue ---

  _enqueueTranslation(text) {
    this._translationQueue.push(text);
    this._processQueue();
  }

  async _processQueue() {
    if (this._isTranslating || this._translationQueue.length === 0) return;

    this._isTranslating = true;
    this._setStatus('translating');

    while (this._translationQueue.length > 0) {
      const text = this._translationQueue.shift();

      try {
        const translated = await this.translator.translate(text, this.sourceLang, this.targetLang);
        this.display.addSegment(text, translated);
      } catch (e) {
        console.error('Translation error:', e);
        this.display.addSegment(text, `⚠ ${text}`);
        this.toast.show('Translation failed. Showing original text.');
      }
    }

    this._isTranslating = false;

    // Restore status
    if (this.isRunning) {
      this._setStatus('listening');
    } else {
      this._setStatus('idle');
    }
  }

  // --- UI Updates ---

  _setStatus(status) {
    if (this.els.statusBadge) {
      this.els.statusBadge.setAttribute('data-status', status);
    }

    const labels = {
      idle: 'Idle',
      listening: 'Listening',
      translating: 'Translating...',
      error: 'Error',
    };

    if (this.els.statusLabel) {
      this.els.statusLabel.textContent = labels[status] || status;
    }
  }

  _updateToggleUI() {
    if (this.isRunning) {
      this.els.btnStart.style.display = 'none';
      this.els.btnStop.style.display = 'flex';
    } else {
      this.els.btnStart.style.display = 'flex';
      this.els.btnStop.style.display = 'none';
    }
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

    const lang = CONFIG.languages[this.sourceLang];
    if (this.els.footerSourceLang && lang) {
      this.els.footerSourceLang.textContent = lang.name;
    }
  }

  _updateUI() {
    this._updateToggleUI();
    this._updateTargetLangUI();
    this._updateSourceLangUI();
    this._setStatus('idle');

    if (this.els.footerProvider) {
      this.els.footerProvider.textContent = `via ${this.translator.activeProvider}`;
    }
  }
}


// ============================================================
// BOOTSTRAP
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  const app = new App();
  app.init();

  // Expose for debugging
  window.__app = app;
});
