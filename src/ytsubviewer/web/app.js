const appState = {
  currentWorkDir: "",
  currentTaskId: "",
  pollingHandle: null,
  pollTick: 0,
  stylePresets: [],
  performanceModes: [],
  providers: [],
  currentEditor: null,
  sessionToken: "",
  language: localStorage.getItem("ytsubviewer-lang") || "zh",
};

const elements = {
  appVersion: document.getElementById("app-version"),
  presetChips: document.getElementById("preset-chips"),
  providerUrlInput: document.getElementById("provider-url-input"),
  modelInput: document.getElementById("model-input"),
  modelSuggestions: document.getElementById("model-suggestions"),
  urlInput: document.getElementById("url-input"),
  analyzeButton: document.getElementById("analyze-button"),
  generateButton: document.getElementById("generate-button"),
  queueBatchButton: document.getElementById("queue-batch-button"),
  styleSelect: document.getElementById("style-select"),
  performanceSelect: document.getElementById("performance-select"),
  creatorDefaultsToggle: document.getElementById("creator-defaults-toggle"),
  styleDescription: document.getElementById("style-description"),
  glossaryInput: document.getElementById("glossary-input"),
  protectedTermsInput: document.getElementById("protected-terms-input"),
  saveProfileButton: document.getElementById("save-profile-button"),
  profileStatus: document.getElementById("profile-status"),
  videoTitle: document.getElementById("video-title"),
  videoDuration: document.getElementById("video-duration"),
  videoStrategy: document.getElementById("video-strategy"),
  videoChannel: document.getElementById("video-channel"),
  videoThumbnail: document.getElementById("video-thumbnail"),
  jobStatusTitle: document.getElementById("job-status-title"),
  jobPercent: document.getElementById("job-percent"),
  jobStage: document.getElementById("job-stage"),
  jobEta: document.getElementById("job-eta"),
  jobProgressBar: document.getElementById("job-progress-bar"),
  jobLogs: document.getElementById("job-logs"),
  jobHint: document.getElementById("job-hint"),
  cancelJobButton: document.getElementById("cancel-job-button"),
  retryJobButton: document.getElementById("retry-job-button"),
  refreshJobButton: document.getElementById("refresh-job-button"),
  openPlayerButton: document.getElementById("open-player-button"),
  bilingualToggle: document.getElementById("bilingual-toggle"),
  exportChineseButton: document.getElementById("export-chinese-button"),
  exportBilingualButton: document.getElementById("export-bilingual-button"),
  previewChineseButton: document.getElementById("preview-chinese-button"),
  previewBilingualButton: document.getElementById("preview-bilingual-button"),
  playerMessage: document.getElementById("player-message"),
  downloads: document.getElementById("downloads"),
  editorSearchInput: document.getElementById("editor-search-input"),
  editorIssuesOnly: document.getElementById("editor-issues-only"),
  loadEditorButton: document.getElementById("load-editor-button"),
  bulkSourceInput: document.getElementById("bulk-source-input"),
  bulkTargetInput: document.getElementById("bulk-target-input"),
  bulkReplaceButton: document.getElementById("bulk-replace-button"),
  qualitySummary: document.getElementById("quality-summary"),
  qualityIssues: document.getElementById("quality-issues"),
  editorList: document.getElementById("editor-list"),
  providerSelect: document.getElementById("provider-select"),
  modelSelect: document.getElementById("model-select"),
  apiKeyInput: document.getElementById("api-key-input"),
  saveSettingsButton: document.getElementById("save-settings-button"),
  testProviderButton: document.getElementById("test-provider-button"),
  refreshButton: document.getElementById("refresh-button"),
  settingsStatus: document.getElementById("settings-status"),
  testResult: document.getElementById("test-result"),
  dataRoot: document.getElementById("data-root"),
  configPath: document.getElementById("config-path"),
  environmentStatus: document.getElementById("environment-status"),
  environmentChecks: document.getElementById("environment-checks"),
  licenseStatus: document.getElementById("license-status"),
  licenseInput: document.getElementById("license-input"),
  activateLicenseButton: document.getElementById("activate-license-button"),
  deactivateLicenseButton: document.getElementById("deactivate-license-button"),
  updateStatus: document.getElementById("update-status"),
  historyList: document.getElementById("history-list"),
  languageSelect: document.getElementById("language-select"),
  licenseOverlay: document.getElementById("license-overlay"),
  licenseOverlayInput: document.getElementById("license-overlay-input"),
  licenseOverlaySubmit: document.getElementById("license-overlay-submit"),
  licenseOverlayMessage: document.getElementById("license-overlay-message"),
};

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (appState.sessionToken) {
    headers["Authorization"] = `Bearer ${appState.sessionToken}`;
  }
  const response = await fetch(path, {
    headers,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail)
      ? detail.map((e) => e.msg || JSON.stringify(e)).join("; ")
      : typeof detail === "string"
        ? detail
        : JSON.stringify(detail || data);
    throw new Error(message || t("status.failed"));
  }
  return data;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function refreshIcons() {
  if (window.lucide) {
    lucide.createIcons();
  }
}

// ── Toast notifications ──
let toastTimeout = null;
function showToast(message, type = "info") {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `toast ${type}`;
  clearTimeout(toastTimeout);
  requestAnimationFrame(() => {
    toast.classList.add("show");
  });
  toastTimeout = setTimeout(() => {
    toast.classList.remove("show");
  }, 3000);
}

function setButtonLoading(button, loading) {
  if (!button) return;
  if (loading) {
    button.classList.add("loading");
    button.disabled = true;
  } else {
    button.classList.remove("loading");
    button.disabled = false;
  }
}

function setActionBusy(isBusy) {
  [
    elements.analyzeButton,
    elements.generateButton,
    elements.queueBatchButton,
    elements.saveSettingsButton,
    elements.testProviderButton,
    elements.refreshButton,
    elements.saveProfileButton,
    elements.activateLicenseButton,
    elements.deactivateLicenseButton,
  ].forEach((button) => {
    if (button) {
      button.disabled = isBusy;
      if (isBusy) {
        button.classList.add("loading");
      } else {
        button.classList.remove("loading");
      }
    }
  });
}

function collectTaskPayload() {
  return {
    url: firstUrlFromInput(),
    style_preset: elements.styleSelect.value || "default",
    glossary_text: elements.glossaryInput.value.trim(),
    protected_terms_text: elements.protectedTermsInput.value.trim(),
    performance_mode: elements.performanceSelect.value || "balanced",
    use_creator_defaults: elements.creatorDefaultsToggle.checked,
  };
}

function collectBatchPayload() {
  return {
    urls_text: elements.urlInput.value.trim(),
    style_preset: elements.styleSelect.value || "default",
    glossary_text: elements.glossaryInput.value.trim(),
    protected_terms_text: elements.protectedTermsInput.value.trim(),
    performance_mode: elements.performanceSelect.value || "balanced",
    use_creator_defaults: elements.creatorDefaultsToggle.checked,
  };
}

function firstUrlFromInput() {
  const urls = elements.urlInput.value
    .replaceAll("\r", "\n")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  return urls[0] || "";
}

function renderStylePresets(presets) {
  appState.stylePresets = presets || [];
  elements.styleSelect.innerHTML = appState.stylePresets
    .map((preset) => `<option value="${escapeHtml(preset.name)}">${escapeHtml(preset.label)}</option>`)
    .join("");
  updateStyleDescription();
}

function renderPerformanceModes(modes) {
  appState.performanceModes = modes || [];
  elements.performanceSelect.innerHTML = appState.performanceModes
    .map((mode) => `<option value="${escapeHtml(mode.name)}">${escapeHtml(mode.label)}</option>`)
    .join("");
  if (appState.performanceModes.some((item) => item.name === "balanced")) {
    elements.performanceSelect.value = "balanced";
  }
}

function renderProviders(providers, settings) {
  appState.providers = providers || [];
  const currentProvider = (settings && settings.provider_name) || "deepseek";

  // Render preset chips
  renderPresetChips(currentProvider);

  // Fill fields from current provider
  const provider = appState.providers.find((p) => p.name === currentProvider);
  const customBaseUrl = (settings && settings.custom_base_url) || "";
  if (provider) {
    elements.providerUrlInput.value = customBaseUrl || provider.base_url || "";
    if (provider.has_key) {
      elements.apiKeyInput.placeholder = "已保存 (留空则保留原值)";
    } else if (provider.name === "ollama") {
      elements.apiKeyInput.placeholder = "本地模型无需 Key";
    } else {
      elements.apiKeyInput.placeholder = `输入 ${provider.label} 的 API Key`;
    }
  } else if (customBaseUrl) {
    elements.providerUrlInput.value = customBaseUrl;
  }

  // Set model
  const currentModel = (settings && settings.model_name) || (provider && provider.models[0]) || "";
  elements.modelInput.value = currentModel;

  // Build model list: current model first (if custom), then provider defaults
  const providerModels = provider ? [...provider.models] : [];
  if (currentModel && !providerModels.includes(currentModel)) {
    providerModels.unshift(currentModel);
  }

  // Update hidden selects for backward compatibility
  elements.providerSelect.innerHTML = appState.providers
    .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.label)}</option>`)
    .join("");
  elements.providerSelect.value = currentProvider;
  elements.modelSelect.innerHTML = providerModels
    .map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`)
    .join("");
  elements.modelSelect.value = currentModel;

  // Show model suggestions
  renderModelSuggestions(providerModels);
}

function renderPresetChips(activeName) {
  const chips = appState.providers.map((p) => {
    const active = p.name === activeName ? " active" : "";
    const keyDot = p.has_key || p.name === "ollama" ? "background:var(--success)" : "background:var(--muted-soft)";
    return `<button type="button" class="preset-chip${active}" data-provider="${escapeHtml(p.name)}">
      <span class="chip-dot" style="${keyDot}"></span>
      ${escapeHtml(p.label)}
    </button>`;
  });
  elements.presetChips.innerHTML = chips.join("");

  // Bind click events
  elements.presetChips.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const name = chip.dataset.provider;
      selectProvider(name);
    });
  });
}

function selectProvider(name) {
  const provider = appState.providers.find((p) => p.name === name);
  if (!provider) return;

  // Update active chip
  elements.presetChips.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.provider === name);
  });

  // Update fields
  elements.providerUrlInput.value = provider.base_url || "";
  elements.providerSelect.value = name;

  if (provider.name === "ollama") {
    elements.apiKeyInput.placeholder = "本地模型无需 Key";
    elements.apiKeyInput.value = "";
  } else if (provider.has_key) {
    elements.apiKeyInput.placeholder = "已保存 (留空则保留原值)";
  } else {
    elements.apiKeyInput.placeholder = `输入 ${provider.label} 的 API Key`;
  }

  // Set first model
  if (provider.models.length > 0) {
    elements.modelInput.value = provider.models[0];
    elements.modelSelect.value = provider.models[0];
  }

  renderModelSuggestions(provider.models);
}

function renderModelSuggestions(models) {
  elements.modelSuggestions.innerHTML = models
    .map((m) => `<button type="button" class="model-suggestion">${escapeHtml(m)}</button>`)
    .join("");

  elements.modelSuggestions.querySelectorAll(".model-suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
      elements.modelInput.value = btn.textContent;
      elements.modelSelect.value = btn.textContent;
      elements.modelSuggestions.classList.remove("open");
    });
  });
}

async function testProvider() {
  const providerName = elements.providerSelect.value;
  const modelName = elements.modelInput.value.trim() || elements.modelSelect.value;
  const apiKey = elements.apiKeyInput.value.trim();
  const baseUrl = elements.providerUrlInput.value.trim();

  setButtonLoading(elements.testProviderButton, true);
  elements.testResult.style.display = "block";
  elements.testResult.textContent = "测试中...";
  elements.testResult.style.color = "inherit";

  try {
    const payload = await request("/api/settings/test", {
      method: "POST",
      body: JSON.stringify({
        provider_name: providerName,
        model_name: modelName,
        api_key: apiKey,
        base_url: baseUrl,
      }),
    });
    elements.testResult.textContent = payload.message;
    showToast(payload.success ? "连接成功" : "连接失败", payload.success ? "success" : "error");
    elements.testResult.style.color = payload.success ? "#22c55e" : "#ef4444";
  } catch (error) {
    elements.testResult.textContent = `测试失败：${error.message}`;
    elements.testResult.style.color = "#ef4444";
  }
}

function updateStyleDescription() {
  const selected = appState.stylePresets.find((item) => item.name === elements.styleSelect.value) || appState.stylePresets[0];
  if (selected) {
    elements.styleSelect.value = selected.name;
    elements.styleDescription.textContent = selected.description;
  }
}

function renderEnvironment(payload) {
  elements.appVersion.textContent = `v${payload.version}`;
  elements.dataRoot.textContent = `${t("env.api_key_ready").includes("数据") ? "数据目录" : "Data dir"}：${payload.settings.data_root}`;
  elements.configPath.textContent = `${t("env.api_key_ready").includes("配置") ? "配置文件" : "Config"}：${payload.settings.config_path}`;
  elements.environmentStatus.textContent = `${t("env.api_key_ready").includes("应用") ? "应用状态" : "Status"}：${payload.environment.overall_status}`;
  elements.settingsStatus.textContent = payload.settings.api_key_ready
    ? t("env.api_key_ready")
    : t("env.api_key_missing");
  elements.environmentChecks.innerHTML = payload.environment.checks
    .map((item) => `<div class="check-item"><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.message)}</p></div>`)
    .join("");
}

function renderLicense(license) {
  if (!license) {
    elements.licenseStatus.textContent = t("auth.load_failed");
    return;
  }
  const expiry = license.expires_at ? new Date(license.expires_at * 1000).toLocaleString() : "长期有效";
  const grace = license.offline_grace_until ? new Date(license.offline_grace_until * 1000).toLocaleString() : "未启用";
  elements.licenseStatus.innerHTML = `
    <strong>${t("auth.status_label")}</strong>
    <p>${t("auth.mode", { mode: license.mode, status: license.status })}</p>
    <p>${t("auth.version", { version: license.version })}</p>
    <p>${t("auth.licensee", { value: license.licensee || "-" })}</p>
    <p>${t("auth.plan", { value: license.plan || "-" })}</p>
    <p>${t("auth.expires", { value: escapeHtml(expiry) })}</p>
    <p>${t("auth.grace", { value: escapeHtml(grace) })}</p>
  `;
}

function renderUpdate(update) {
  if (!update) {
    elements.updateStatus.textContent = t("update.load_failed");
    return;
  }
  elements.updateStatus.innerHTML = `
    <strong>${t("update.status_label")}</strong>
    <p>${t("update.current", { version: escapeHtml(update.current_version) })}</p>
    <p>${t("update.latest", { version: escapeHtml(update.latest_version) })}</p>
    <p>${escapeHtml(update.message)}</p>
  `;
}

function renderMetadata(metadata, strategyText) {
  if (!metadata) {
    elements.videoTitle.textContent = t("video.no_analysis");
    elements.videoDuration.textContent = t("video.duration", { value: "-" });
    elements.videoStrategy.textContent = t("video.strategy", { value: "-" });
    elements.videoChannel.textContent = t("video.channel", { value: "-" });
    elements.videoThumbnail.removeAttribute("src");
    return;
  }
  elements.videoTitle.textContent = metadata.title || "未命名视频";
  elements.videoDuration.textContent = t("video.duration", { value: metadata.duration_text || "-" });
  elements.videoStrategy.textContent = t("video.strategy", { value: strategyText || "-" });
  elements.videoChannel.textContent = t("video.channel", { value: metadata.channel_name || metadata.uploader || "-" });
  if (metadata.thumbnail_url) {
    elements.videoThumbnail.src = metadata.thumbnail_url;
  } else {
    elements.videoThumbnail.removeAttribute("src");
  }
}

function renderLogs(lines, error) {
  const logLines = Array.isArray(lines) && lines.length ? lines : [t("job.no_logs")];
  elements.jobLogs.innerHTML = logLines
    .map((line) => `<div class="log-line">${escapeHtml(line)}</div>`)
    .join("");
  if (error) {
    elements.jobLogs.insertAdjacentHTML("beforeend", `<div class="log-line error">${escapeHtml(error)}</div>`);
  }
}

function renderDownloads(state) {
  appState.currentWorkDir = state?.work_dir || "";
  const downloads = state?.downloads || {};
  const labels = {
    video: t("downloads.video"),
    subtitle: t("downloads.subtitle"),
    chinese_ass: t("downloads.chinese_ass"),
    bilingual_ass: t("downloads.bilingual_ass"),
    quality_report: t("downloads.quality_report"),
    burned_chinese_video: t("downloads.burned_chinese"),
    burned_bilingual_video: t("downloads.burned_bilingual"),
  };
  const cards = Object.entries(downloads).map(([key, file]) => `
    <div class="download-card">
      <div>
        <p>${escapeHtml(labels[key] || key)}</p>
        <p>${escapeHtml(file.name)}</p>
      </div>
      <a href="${escapeHtml(file.url)}">${t("button.export_chinese").includes("导出") ? "下载" : "Download"}</a>
    </div>
  `);
  elements.downloads.innerHTML = cards.length
    ? cards.join("")
    : `<div class="empty-state">${t("download.empty")}</div>`;

  const hasWorkDir = Boolean(appState.currentWorkDir);
  elements.openPlayerButton.disabled = !hasWorkDir;
  elements.exportChineseButton.disabled = !hasWorkDir;
  elements.exportBilingualButton.disabled = !hasWorkDir;
  elements.previewChineseButton.disabled = !hasWorkDir;
  elements.previewBilingualButton.disabled = !hasWorkDir;
  elements.loadEditorButton.disabled = !hasWorkDir || !appState.currentTaskId;
  elements.bulkReplaceButton.disabled = !hasWorkDir || !appState.currentTaskId;
  refreshIcons();
}

function statusTextForTask(job) {
  return {
    running: t("status.running"),
    completed: t("status.completed"),
    failed: t("status.failed"),
    pending: t("status.pending"),
    cancelled: t("status.cancelled"),
  }[job.status] || t("status.running");
}

function renderTask(job) {
  if (!job) {
    appState.currentTaskId = "";
    appState.currentWorkDir = "";
    elements.jobStatusTitle.textContent = t("status.ready");
    elements.jobPercent.textContent = "0%";
    elements.jobStage.textContent = t("job.stage", { value: "-" });
    elements.jobEta.textContent = t("job.eta", { value: "-" });
    elements.jobProgressBar.style.width = "0%";
    elements.jobHint.textContent = t("job.hint_idle");
    renderLogs([t("job.no_logs")]);
    renderDownloads(null);
    renderQuality(null);
    elements.cancelJobButton.disabled = true;
    elements.retryJobButton.disabled = true;
    return;
  }

  appState.currentTaskId = job.job_id || "";
  const progress = Math.max(0, Math.min(100, job.progress_percent || 0));
  const kindLabel = job.kind === "export"
    ? (t("button.export_chinese").includes("导出") ? "导出任务" : "Export Task")
    : (t("button.generate").includes("生成") ? "生成任务" : "Generation Task");
  elements.jobStatusTitle.textContent = `${statusTextForTask(job)} / ${kindLabel}`;
  elements.jobPercent.textContent = `${progress}%`;
  elements.jobStage.textContent = t("job.stage", { value: job.stage || "-" });
  elements.jobEta.textContent = t("job.eta", { value: job.eta_text || "-" });
  elements.jobProgressBar.style.width = `${progress}%`;
  elements.jobHint.textContent = job.status === "running"
    ? t("job.hint_running")
    : job.status === "completed"
      ? t("job.hint_completed")
      : job.status === "failed"
        ? t("job.hint_failed")
        : job.status === "pending"
          ? t("job.hint_pending")
          : t("job.hint_default");
  renderLogs(job.logs, job.error);
  renderDownloads(job.state);
  renderQuality(job.state);

  if (job.title) {
    renderMetadata(
      {
        title: job.title,
        duration_text: job.duration_text,
        thumbnail_url: job.thumbnail_url,
        channel_name: job.state?.channel_name,
      },
      job.strategy_text,
    );
  }

  elements.cancelJobButton.disabled = !job.can_cancel;
  elements.retryJobButton.disabled = !job.can_retry;

  if (job.status === "running" || job.status === "pending") {
    startPolling();
  }
  refreshIcons();
}

function renderHistory(jobs) {
  const cards = (jobs || []).map((job) => {
    const statusClass = job.status || "pending";
    const statusText = {
      completed: "完成",
      failed: "失败",
      running: "进行中",
      pending: "等待中",
      cancelled: "已取消",
    }[statusClass] || statusClass;
    return `
    <div class="history-item" data-task-id="${escapeHtml(job.job_id)}">
      <div class="history-item-info">
        <div class="history-item-title">${escapeHtml(job.title || "(未命名任务)")}</div>
        <div class="history-item-meta">${escapeHtml(job.duration_text || "-")} · ${escapeHtml(job.kind === "export" ? "导出" : "翻译")}</div>
      </div>
      <span class="history-item-status ${statusClass}">${statusText}</span>
    </div>
  `;
  });
  elements.historyList.innerHTML = cards.length
    ? cards.join("")
    : `<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:0.88rem;">暂无历史任务</div>`;
  refreshIcons();
}

function renderQuality(state) {
  const quality = state?.quality_report || null;
  if (!quality) {
    elements.qualitySummary.textContent = t("quality.loading");
    elements.qualityIssues.innerHTML = "";
    return;
  }
  elements.qualitySummary.innerHTML = `
    <strong>${t("quality.summary")}</strong>
    <p>${t("quality.total", { total: escapeHtml(quality.total_cues), issues: escapeHtml(quality.issue_count) })}</p>
    <p>${t("quality.errors", { errors: escapeHtml(quality.error_count), warnings: escapeHtml(quality.warning_count) })}</p>
    <p>${t("quality.leftover", { leftover: escapeHtml(quality.leftover_english_count), long: escapeHtml(quality.long_line_count) })}</p>
  `;
  const issues = quality.issues || [];
  elements.qualityIssues.innerHTML = issues.length
    ? issues.map((issue) => `
        <div class="check-item">
          <strong>${escapeHtml(issue.severity)} / ${escapeHtml(issue.code)}</strong>
          <p>${escapeHtml(issue.message)}</p>
          <p>${escapeHtml((issue.cue_ids || []).join(", ") || "-")}</p>
        </div>
      `).join("")
    : `<div class="empty-state">${t("quality.empty")}</div>`;
}

function renderEditor(payload) {
  appState.currentEditor = payload;
  if (!payload) {
    elements.editorList.innerHTML = `<div class="empty-state">${t("editor.empty")}</div>`;
    return;
  }
  const rows = payload.rows || [];
  elements.editorList.innerHTML = rows.length
    ? rows.map((row) => `
        <div class="editor-row" data-cue-id="${escapeHtml(row.cue_id)}">
          <div class="editor-meta">
            <span>#${escapeHtml(row.index)}</span>
            <span>${escapeHtml(secondsToClock(row.start))} - ${escapeHtml(secondsToClock(row.end))}</span>
            ${row.has_issue ? '<span class="editor-tag">问题句</span>' : ""}
            ${row.edited ? '<span class="editor-tag">已编辑</span>' : ""}
            ${row.locked ? '<span class="editor-tag">已锁定</span>' : ""}
          </div>
          <div class="editor-source">${escapeHtml(row.source_text)}</div>
          <textarea class="input textarea editor-target">${escapeHtml(row.target_text)}</textarea>
          <div class="editor-context">${escapeHtml(row.previous_source_text || "")}${row.previous_source_text ? " / " : ""}${escapeHtml(row.next_source_text || "")}</div>
          <div class="button-row compact-row">
            <button class="button secondary" type="button" data-action="save-cue">${t("editor.save")}</button>
            <button class="button secondary" type="button" data-action="retranslate-cue">${t("editor.retranslate")}</button>
            <button class="button secondary" type="button" data-action="${row.locked ? "unlock-cue" : "lock-cue"}">${row.locked ? t("editor.unlock") : t("editor.lock")}</button>
          </div>
        </div>
      `).join("")
    : `<div class="empty-state">${t("editor.empty")}</div>`;
}

function secondsToClock(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

async function bootstrap() {
  setActionBusy(true);
  try {
    const savedLang = localStorage.getItem("ytsubviewer-lang") || "zh";
    await setLanguage(savedLang);
    if (elements.languageSelect) {
      elements.languageSelect.value = savedLang;
    }

    const payload = await request("/api/bootstrap", { method: "GET" });
    appState.sessionToken = payload.session_token || "";

    // 检查激活状态，未激活时显示全屏遮罩
    const license = payload.license;
    if (license && !license.active) {
      showLicenseOverlay();
    } else {
      hideLicenseOverlay();
    }

    renderStylePresets(payload.style_presets);
    renderPerformanceModes(payload.performance_modes);
    renderProviders(payload.providers || [], payload.settings);
    renderEnvironment(payload);
    renderLicense(payload.license);
    renderUpdate(payload.update);
    renderHistory(payload.history);
    renderTask(payload.job);
    refreshIcons();
  } catch (error) {
    elements.settingsStatus.textContent = error.message;
  } finally {
    setActionBusy(false);
  }
}

function showLicenseOverlay() {
  if (elements.licenseOverlay) {
    elements.licenseOverlay.style.display = "flex";
  }
}

function hideLicenseOverlay() {
  if (elements.licenseOverlay) {
    elements.licenseOverlay.style.display = "none";
  }
}

async function submitLicenseOverlay() {
  const key = (elements.licenseOverlayInput?.value || "").trim();
  if (!key) {
    elements.licenseOverlayMessage.textContent = "请输入激活码。";
    return;
  }
  elements.licenseOverlayMessage.textContent = "正在验证...";
  elements.licenseOverlayMessage.style.color = "var(--muted)";
  elements.licenseOverlaySubmit.disabled = true;

  try {
    // 优先尝试远程验证
    let payload;
    try {
      payload = await request("/api/license/verify", {
        method: "POST",
        body: JSON.stringify({ license_key: key }),
      });
    } catch {
      // 远程验证不可用时回退到本地激活
      payload = await request("/api/license/activate", {
        method: "POST",
        body: JSON.stringify({ license_key: key }),
      });
    }

    if (payload && payload.active) {
      hideLicenseOverlay();
      renderLicense(payload);
      elements.licenseOverlayMessage.textContent = "";
    } else {
      elements.licenseOverlayMessage.textContent = "激活码无效或已过期。";
      elements.licenseOverlayMessage.style.color = "var(--danger)";
    }
  } catch (error) {
    elements.licenseOverlayMessage.textContent = error.message;
    elements.licenseOverlayMessage.style.color = "var(--danger)";
  } finally {
    elements.licenseOverlaySubmit.disabled = false;
  }
}

async function analyzeVideo() {
  setButtonLoading(elements.analyzeButton, true);
  showToast("正在分析视频...", "info");
  try {
    const payload = await request("/api/analyze", {
      method: "POST",
      body: JSON.stringify(collectTaskPayload()),
    });
    renderMetadata(payload.metadata, payload.strategy_text);
    elements.styleSelect.value = payload.resolved_controls.style_preset || "default";
    elements.glossaryInput.value = payload.resolved_controls.glossary_text || "";
    elements.protectedTermsInput.value = payload.resolved_controls.protected_terms_text || "";
    updateStyleDescription();
    if (payload.profile) {
      elements.profileStatus.textContent = t("profile.identified", { name: payload.profile.channel_name || payload.profile.uploader || payload.profile.profile_id });
    } else {
      elements.profileStatus.textContent = t("profile.not_found");
    }
    if (payload.state) {
      renderDownloads(payload.state);
      renderQuality(payload.state);
      elements.jobStatusTitle.textContent = payload.controls_match
        ? (t("button.generate").includes("生成") ? "本地结果可直接使用" : "Local result ready")
        : (t("button.generate").includes("生成") ? "检测到旧结果" : "Old result detected");
      renderLogs([
        payload.controls_match
          ? (t("button.generate").includes("生成") ? "当前翻译配置与本地结果一致，可以直接播放、下载或继续编辑。" : "Translation config matches local result. Ready to play, download or edit.")
          : (t("button.generate").includes("生成") ? "检测到已有结果，但翻译配置变化后建议重新生成。" : "Old result detected. Regeneration recommended after config change."),
      ]);
      elements.jobPercent.textContent = "100%";
      elements.jobProgressBar.style.width = "100%";
    } else {
      renderTask(null);
      renderLogs([t("button.generate").includes("生成") ? "分析完成，可以开始生成中文字幕。" : "Analysis complete. Ready to generate subtitles."]);
      showToast("分析完成", "success");
    }
  } catch (error) {
    if (error.message && error.message.includes("YouTube") && error.message.includes("登录")) {
      renderLogs([], error.message);
      showYoutubeLoginPrompt();
      showToast("YouTube 需要登录", "error");
    } else {
      renderLogs([], error.message);
      showToast(error.message, "error");
    }
    elements.jobStatusTitle.textContent = t("status.failed");
  } finally {
    setActionBusy(false);
  }
}

function showYoutubeLoginPrompt() {
  if (!elements.jobLogs) return;

  const container = document.createElement("div");
  container.style.cssText = "margin-top:16px;padding:16px;border-radius:12px;background:rgba(0,0,0,0.05);";

  const msg = document.createElement("p");
  msg.style.cssText = "margin:0 0 12px;font-size:14px;line-height:1.6;";
  msg.textContent = "YouTube 需要从你的 Chrome 浏览器获取登录信息。";
  container.appendChild(msg);

  const btn = document.createElement("button");
  btn.textContent = "获取 YouTube Cookies";
  btn.style.cssText = "padding:10px 24px;border-radius:10px;cursor:pointer;font-size:14px;border:none;background:#e76f51;color:white;font-weight:500;";
  container.appendChild(btn);

  const status = document.createElement("p");
  status.style.cssText = "margin:8px 0 0;font-size:13px;color:#666;";
  container.appendChild(status);

  btn.onclick = async () => {
    btn.disabled = true;
    btn.style.opacity = "0.6";
    status.textContent = "正在获取...";

    try {
      const result = await request("/api/youtube-login", { method: "POST" });
      if (result.success) {
        status.textContent = result.message;
        btn.textContent = "成功";
        btn.style.background = "#4ade80";
        return;
      }

      status.textContent = result.message || "获取失败，请重试。";
      status.style.color = "#e76f51";
      btn.disabled = false;
      btn.style.opacity = "1";
    } catch (e) {
      status.textContent = e.message;
      btn.disabled = false;
      btn.style.opacity = "1";
    }
  };

  elements.jobLogs.appendChild(container);
}

async function generateSubtitle() {
  setButtonLoading(elements.generateButton, true);
  showToast("正在提交翻译任务...", "info");
  try {
    const payload = await request("/api/generate", {
      method: "POST",
      body: JSON.stringify(collectTaskPayload()),
    });
    renderTask(payload.job);
    renderHistory((await request("/api/job/history", { method: "GET" })).jobs);
    showToast("任务已提交，开始翻译", "success");
  } catch (error) {
    if (error.message && error.message.includes("YouTube") && error.message.includes("登录")) {
      renderLogs([], error.message);
      showYoutubeLoginPrompt();
      showToast("YouTube 需要登录", "error");
    } else {
      renderLogs([], error.message);
      showToast(error.message, "error");
    }
    elements.jobStatusTitle.textContent = t("status.failed");
  } finally {
    setActionBusy(false);
  }
}

async function queueBatch() {
  setActionBusy(true);
  try {
    const payload = await request("/api/batch", {
      method: "POST",
      body: JSON.stringify(collectBatchPayload()),
    });
    renderHistory(payload.history || payload.jobs || []);
    if (payload.jobs?.length) {
      renderTask(payload.jobs[0]);
      renderLogs([t("batch.queued", { count: payload.jobs.length })]);
    }
  } catch (error) {
    renderLogs([], error.message);
  } finally {
    setActionBusy(false);
  }
}

async function saveSettings() {
  setButtonLoading(elements.saveSettingsButton, true);
  showToast("正在保存设置...", "info");
  try {
    const providerName = elements.providerSelect.value;
    const modelName = elements.modelInput.value.trim() || elements.modelSelect.value;
    const apiKey = elements.apiKeyInput.value.trim();
    const baseUrl = elements.providerUrlInput.value.trim();

    // Build provider_api_keys map
    const providerApiKeys = {};
    if (providerName && apiKey) {
      providerApiKeys[providerName] = apiKey;
    }

    const payload = await request("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        api_key: apiKey,
        provider_name: providerName,
        model_name: modelName,
        base_url: baseUrl,
        provider_api_keys: providerApiKeys,
      }),
    });
    renderStylePresets(payload.style_presets);
    renderPerformanceModes(payload.performance_modes);
    if (payload.providers) {
      renderProviders(payload.providers, payload.settings);
    }
    renderEnvironment(payload);
    renderLicense(payload.license);
    renderUpdate(payload.update);
    renderHistory(payload.history);
    renderTask(payload.job);
    elements.settingsStatus.textContent = "设置已保存。";
    elements.settingsStatus.style.color = "var(--success)";
    showToast("设置已保存", "success");
  } catch (error) {
    elements.settingsStatus.textContent = error.message;
    elements.settingsStatus.style.color = "var(--danger)";
    showToast(error.message, "error");
  } finally {
    setActionBusy(false);
  }
}

async function saveCreatorProfile() {
  const url = firstUrlFromInput();
  if (!url) {
    elements.profileStatus.textContent = t("profile.prompt_input");
    return;
  }
  try {
    const payload = await request("/api/creator-profile/save", {
      method: "POST",
      body: JSON.stringify({
        url,
        style_preset: elements.styleSelect.value || "default",
        glossary_text: elements.glossaryInput.value.trim(),
        protected_terms_text: elements.protectedTermsInput.value.trim(),
      }),
    });
    const profile = payload.profile;
    elements.profileStatus.textContent = t("profile.saved", { name: profile.channel_name || profile.uploader || profile.profile_id });
  } catch (error) {
    elements.profileStatus.textContent = error.message;
  }
}

async function openPlayer() {
  if (!appState.currentWorkDir) {
    elements.playerMessage.textContent = t("player.no_result");
    return;
  }
  try {
    const payload = await request("/api/open-player", {
      method: "POST",
      body: JSON.stringify({
        work_dir: appState.currentWorkDir,
        bilingual: elements.bilingualToggle.checked,
      }),
    });
    elements.playerMessage.textContent = payload.message;
  } catch (error) {
    elements.playerMessage.textContent = error.message;
  }
}

async function startExport({ bilingual, preview }) {
  if (!appState.currentWorkDir) {
    elements.playerMessage.textContent = t("player.gen_first");
    return;
  }
  try {
    const payload = await request("/api/export", {
      method: "POST",
      body: JSON.stringify({
        work_dir: appState.currentWorkDir,
        bilingual,
        preview,
        performance_mode: elements.performanceSelect.value || "balanced",
      }),
    });
    renderTask(payload.job);
    renderHistory((await request("/api/job/history", { method: "GET" })).jobs);
  } catch (error) {
    elements.playerMessage.textContent = error.message;
  }
}

async function cancelCurrentTask() {
  if (!appState.currentTaskId) {
    return;
  }
  try {
    const payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/cancel`, { method: "POST" });
    renderTask(payload.job);
    renderHistory(payload.history);
  } catch (error) {
    renderLogs([], error.message);
  }
}

async function retryCurrentTask() {
  if (!appState.currentTaskId) {
    return;
  }
  try {
    const payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/retry`, { method: "POST" });
    renderTask(payload.job);
    renderHistory(payload.history);
  } catch (error) {
    renderLogs([], error.message);
  }
}

async function loadEditor() {
  if (!appState.currentTaskId) {
    return;
  }
  try {
    const params = new URLSearchParams();
    if (elements.editorIssuesOnly.checked) {
      params.set("issues_only", "true");
    }
    if (elements.editorSearchInput.value.trim()) {
      params.set("query", elements.editorSearchInput.value.trim());
    }
    const payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/editor?${params.toString()}`, { method: "GET" });
    renderEditor(payload);
  } catch (error) {
    elements.editorList.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function runBulkReplace() {
  if (!appState.currentTaskId) {
    return;
  }
  try {
    const payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/cue/bulk-replace`, {
      method: "POST",
      body: JSON.stringify({
        source_text: elements.bulkSourceInput.value.trim(),
        target_text: elements.bulkTargetInput.value.trim(),
      }),
    });
    renderQuality(payload.state);
    renderDownloads(payload.state);
    renderEditor(payload.editor);
  } catch (error) {
    elements.editorList.insertAdjacentHTML("afterbegin", `<div class="log-line error">${escapeHtml(error.message)}</div>`);
  }
}

async function activateLicense() {
  try {
    const payload = await request("/api/license/activate", {
      method: "POST",
      body: JSON.stringify({ license_key: elements.licenseInput.value.trim() }),
    });
    renderLicense(payload);
  } catch (error) {
    elements.licenseStatus.textContent = error.message;
  }
}

async function deactivateLicense() {
  try {
    const payload = await request("/api/license/deactivate", { method: "POST" });
    renderLicense(payload);
  } catch (error) {
    elements.licenseStatus.textContent = error.message;
  }
}

async function openHistoryTask(taskId) {
  const payload = await request(`/api/job/${encodeURIComponent(taskId)}`, { method: "GET" });
  renderTask(payload.job);
}

async function refreshCurrentState() {
  const current = await request("/api/job/current", { method: "GET" });
  renderTask(current.job);
  if (appState.pollTick % 2 === 0) {
    const history = await request("/api/job/history", { method: "GET" });
    renderHistory(history.jobs);
  }
}

function startPolling() {
  if (appState.pollingHandle) {
    return;
  }
  appState.pollingHandle = window.setInterval(async () => {
    appState.pollTick += 1;
    try {
      await refreshCurrentState();
    } catch (error) {
      console.error(error);
    }
  }, 2500);
}

function stopPolling() {
  if (!appState.pollingHandle) {
    return;
  }
  window.clearInterval(appState.pollingHandle);
  appState.pollingHandle = null;
}

elements.styleSelect.addEventListener("change", updateStyleDescription);
elements.providerSelect.addEventListener("change", () => {
  selectProvider(elements.providerSelect.value);
});
elements.testProviderButton.addEventListener("click", testProvider);

// Model input suggestions
elements.modelInput.addEventListener("focus", () => {
  elements.modelSuggestions.classList.add("open");
});
elements.modelInput.addEventListener("blur", () => {
  setTimeout(() => elements.modelSuggestions.classList.remove("open"), 200);
});
elements.modelInput.addEventListener("input", () => {
  const query = elements.modelInput.value.toLowerCase();
  const provider = appState.providers.find((p) => p.name === elements.providerSelect.value);
  if (!provider) return;
  const filtered = provider.models.filter((m) => m.toLowerCase().includes(query));
  renderModelSuggestions(filtered.length > 0 ? filtered : provider.models);
  elements.modelSuggestions.classList.add("open");
  // Sync hidden select
  if (provider.models.includes(elements.modelInput.value)) {
    elements.modelSelect.value = elements.modelInput.value;
  }
});
elements.analyzeButton.addEventListener("click", analyzeVideo);
elements.generateButton.addEventListener("click", generateSubtitle);
elements.queueBatchButton.addEventListener("click", queueBatch);
elements.saveSettingsButton.addEventListener("click", saveSettings);
elements.refreshButton.addEventListener("click", bootstrap);
elements.saveProfileButton.addEventListener("click", saveCreatorProfile);
elements.openPlayerButton.addEventListener("click", openPlayer);
elements.exportChineseButton.addEventListener("click", () => startExport({ bilingual: false, preview: false }));
elements.exportBilingualButton.addEventListener("click", () => startExport({ bilingual: true, preview: false }));
elements.previewChineseButton.addEventListener("click", () => startExport({ bilingual: false, preview: true }));
elements.previewBilingualButton.addEventListener("click", () => startExport({ bilingual: true, preview: true }));
elements.cancelJobButton.addEventListener("click", cancelCurrentTask);
elements.retryJobButton.addEventListener("click", retryCurrentTask);
elements.refreshJobButton.addEventListener("click", refreshCurrentState);
elements.loadEditorButton.addEventListener("click", loadEditor);
elements.bulkReplaceButton.addEventListener("click", runBulkReplace);
elements.activateLicenseButton.addEventListener("click", activateLicense);
elements.deactivateLicenseButton.addEventListener("click", deactivateLicense);

// License overlay
if (elements.licenseOverlaySubmit) {
  elements.licenseOverlaySubmit.addEventListener("click", submitLicenseOverlay);
}
if (elements.licenseOverlayInput) {
  elements.licenseOverlayInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      submitLicenseOverlay();
    }
  });
}

if (elements.languageSelect) {
  elements.languageSelect.addEventListener("change", (e) => setLanguage(e.target.value));
}

elements.historyList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-task-id]");
  if (!button) {
    return;
  }
  await openHistoryTask(button.dataset.taskId);
});

elements.editorList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) {
    return;
  }
  const row = button.closest("[data-cue-id]");
  if (!row || !appState.currentTaskId) {
    return;
  }
  const cueId = Number(row.dataset.cueId);
  const textarea = row.querySelector(".editor-target");
  try {
    let payload;
    if (button.dataset.action === "save-cue") {
      payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/cue/update`, {
        method: "POST",
        body: JSON.stringify({ cue_id: cueId, target_text: textarea.value }),
      });
    } else if (button.dataset.action === "retranslate-cue") {
      payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/cue/retranslate`, {
        method: "POST",
        body: JSON.stringify({ cue_id: cueId }),
      });
    } else if (button.dataset.action === "lock-cue" || button.dataset.action === "unlock-cue") {
      payload = await request(`/api/job/${encodeURIComponent(appState.currentTaskId)}/cue/lock`, {
        method: "POST",
        body: JSON.stringify({ cue_id: cueId, locked: button.dataset.action === "lock-cue" }),
      });
    }
    if (payload) {
      renderQuality(payload.state);
      renderDownloads(payload.state);
      renderEditor(payload.editor);
    }
  } catch (error) {
    row.insertAdjacentHTML("afterbegin", `<div class="log-line error">${escapeHtml(error.message)}</div>`);
  }
});

// Sidebar navigation
document.querySelectorAll(".nav-item").forEach((item) => {
  item.addEventListener("click", (e) => {
    e.preventDefault();
    const section = item.dataset.section;
    if (!section) return;

    // Update active nav item
    document.querySelectorAll(".nav-item").forEach((nav) => nav.classList.remove("active"));
    item.classList.add("active");

    // Show target section
    document.querySelectorAll(".page-section").forEach((sec) => {
      sec.classList.toggle("active", sec.id === section);
    });

    // Refresh icons
    if (window.lucide) {
      lucide.createIcons();
    }
  });
});

bootstrap();
startPolling();
