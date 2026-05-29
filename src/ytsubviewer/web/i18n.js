const LANGUAGES = {
  zh: { label: "中文", dir: "ltr" },
  en: { label: "English", dir: "ltr" },
};

const translations = {};

async function loadLanguage(lang) {
  if (translations[lang]) return translations[lang];
  try {
    const resp = await fetch(`/static/i18n/${lang}.json`);
    translations[lang] = await resp.json();
  } catch (e) {
    translations[lang] = {};
  }
  return translations[lang];
}

function t(key, vars = {}) {
  const lang = appState.language || "zh";
  const dict = translations[lang] || {};
  let text = dict[key] || (translations.zh && translations.zh[key]) || key;
  for (const [k, v] of Object.entries(vars)) {
    text = text.replace(`{${k}}`, v);
  }
  return text;
}

async function setLanguage(lang) {
  appState.language = lang;
  await loadLanguage(lang);
  await loadLanguage("zh");
  document.documentElement.lang = lang;
  localStorage.setItem("ytsubviewer-lang", lang);
  applyTranslations();
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (el.tagName === "INPUT" && el.type !== "button" && el.type !== "submit") {
      el.placeholder = t(key);
    } else {
      el.textContent = t(key);
    }
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.getAttribute("data-i18n-title"));
  });
}
