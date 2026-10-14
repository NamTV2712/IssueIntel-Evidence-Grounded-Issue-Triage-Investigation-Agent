'use strict';
(() => {
  const storageKey = 'issueintel.preferences.v1';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const defaults = {language: navigator.language?.startsWith('vi') ? 'vi' : 'en', theme: 'system'};
  let saved;
  try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) { /* Storage is optional. */ }
  const preferences = {
    language: ['vi','en'].includes(saved?.language) ? saved.language : defaults.language,
    theme: ['system','light','dark'].includes(saved?.theme) ? saved.theme : defaults.theme
  };
  function t(key, values={}) {
    const message = window.IssueIntelMessages[key]?.[preferences.language] ?? key;
    return message.replace(/\{([a-z_]+)\}/g, (slot, name) => String(values[name] ?? slot));
  }
  function theme() {
    document.documentElement.dataset.theme = preferences.theme === 'system'
      ? (system.matches ? 'dark' : 'light') : preferences.theme;
    document.documentElement.dataset.themePreference = preferences.theme;
  }
  function language() {
    document.documentElement.lang = preferences.language;
    document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => el.placeholder = t(el.dataset.i18nPlaceholder));
    document.querySelectorAll('[data-i18n-aria]').forEach(el => el.setAttribute('aria-label', t(el.dataset.i18nAria)));
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch (_) { /* Works without persistence. */ }
  }
  theme();
  document.documentElement.lang = preferences.language;
  system.addEventListener('change', () => { if (preferences.theme === 'system') theme(); });
  window.IssueIntelUI = Object.freeze({t,
    sourceKey: text => Object.keys(window.IssueIntelMessages).find(key => Object.values(window.IssueIntelMessages[key]).includes(text)) ?? text,
    locale: () => preferences.language === 'vi' ? 'vi-VN' : 'en-US'});
  document.addEventListener('DOMContentLoaded', () => {
    language();
    document.getElementById('language').value = preferences.language;
    document.getElementById('theme').value = preferences.theme;
    document.getElementById('language').addEventListener('change', event => {
      if (!['vi','en'].includes(event.target.value)) return;
      preferences.language = event.target.value; save(); language();
      document.dispatchEvent(new Event('issueintel:languagechange'));
    });
    document.getElementById('theme').addEventListener('change', event => {
      if (!['system','light','dark'].includes(event.target.value)) return;
      preferences.theme = event.target.value; save(); theme();
    });
  });
})();
