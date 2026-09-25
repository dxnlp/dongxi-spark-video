const $ = (id) => document.getElementById(id);
const GIB = 1024 ** 3;
const activeStates = new Set(["starting", "running", "validating"]);
const examples = {
  alpine:
    "A wide cinematic shot of an alpine meadow at sunrise, pale pink mountain peaks above a blue valley filled with thin morning mist.",
  lanterns:
    "A cinematic tracking shot slowly moving along a quiet cobblestone street at blue hour. Warm paper lanterns sway gently above the street, their amber light reflecting in small puddles. Soft mist, realistic lighting, no people, no text.",
  ocean:
    "A cinematic wide shot of gentle ocean waves washing onto a dark sand beach at dusk. A pale lavender sky reflects in the water. The camera slowly glides forward, peaceful atmosphere, realistic movement and soft natural light.",
};
let state = null,
  selected = null,
  mediaKey = null,
  historyKey = null,
  presetKey = null,
  submitting = false,
  offline = false;
const samples = [];
const seconds = (value) => {
  const n = Math.max(0, Math.floor(value || 0));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
};
const gib = (n) => (n / GIB).toFixed(1);
function notice(message) {
  $("notice").textContent = message || "";
  $("notice").hidden = !message;
}
async function request(path, body) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed");
    return data;
  } finally {
    clearTimeout(timer);
  }
}
function count() {
  $("char-count").textContent = `${$("prompt").value.length} / 1200`;
}
$("prompt").addEventListener("input", count);
$("length").addEventListener("change", () => paint());
document.querySelectorAll("[data-example]").forEach((button) =>
  button.addEventListener("click", () => {
    $("prompt").value = examples[button.dataset.example];
    count();
    $("prompt").focus();
  }),
);
$("generate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting || state?.active_id) return;
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    $("prompt").focus();
    return;
  }
  const seed = $("seed").value === "" ? null : Number($("seed").value);
  if (
    seed !== null &&
    (!Number.isInteger(seed) || seed < 0 || seed > 2147483647)
  ) {
    notice("Seed must be a whole number from 0 to 2147483647.");
    return;
  }
  submitting = true;
  $("generate").disabled = true;
  notice("");
  try {
    const job = await request("/api/jobs", {
      prompt,
      seed,
      frames: Number($("length").value),
    });
    selected = job.id;
    historyKey = null;
    state.jobs.unshift(job);
    state.active_id = job.id;
    paint();
  } catch (error) {
    notice(error.message);
  } finally {
    submitting = false;
    paint();
  }
});
$("cancel").addEventListener("click", async () => {
  if (!state?.active_id) return;
  $("cancel").disabled = true;
  try {
    await request(`/api/jobs/${state.active_id}/cancel`, {});
    $("stage").textContent = "Stopping safely…";
  } catch (error) {
    notice(error.message);
    $("cancel").disabled = false;
  }
});
function chart(mem) {
  const now = Date.now();
  samples.push({ time: now, used: mem.used });
  while (samples.length && samples[0].time < now - 90000) samples.shift();
  const points = samples.map(
    (p) =>
      `${Math.max(0, (500 * (p.time - (now - 90000))) / 90000).toFixed(1)},${(97 - (90 * p.used) / mem.total).toFixed(1)}`,
  );
  if (points.length) {
    $("chart-line").setAttribute("d", "M" + points.join(" L"));
    $("chart-area").setAttribute(
      "d",
      `M${points[0].split(",")[0]},100 L${points.join(" L")} L500,100 Z`,
    );
  }
}
function preview(job) {
  const ready = job?.state === "complete";
  $("video").hidden = !ready;
  $("preview-placeholder").hidden = ready;
  $("download").hidden = !ready;
  $("preview-tag").textContent = ready
    ? "READY TO PLAY"
    : job && activeStates.has(job.state)
      ? "RENDERING"
      : "PREVIEW";
  if (ready) {
    const key = job.id;
    if (mediaKey !== key) {
      $("video").src = job.video_url;
      $("video").poster = job.poster_url || "";
      $("video").load();
      mediaKey = key;
    }
    $("download").href = job.video_url + "?download=1";
    $("video-title").textContent = job.label || "Your generated scene";
    $("video-detail").textContent =
      `${(job.duration || 124 / 24).toFixed(2)}s · 832 × 480 · ${job.render_seconds?.toFixed(1) || "—"}s to render`;
  } else {
    if (mediaKey) {
      $("video").pause();
      $("video").removeAttribute("src");
      $("video").load();
      mediaKey = null;
    }
    const running = job && activeStates.has(job.state);
    $("preview-placeholder").classList.toggle("rendering", !!running);
    $("placeholder-title").textContent = running
      ? "Your scene is taking shape"
      : job?.state === "failed"
        ? "This render stopped"
        : job?.state === "cancelled"
          ? "Render stopped"
          : "Room for your imagination";
    $("placeholder-copy").textContent = running
      ? "The finished video will appear here automatically."
      : job?.error || "Describe a scene to create a new video.";
    $("video-title").textContent = running
      ? "Rendering on your Spark"
      : "Ready when you are";
    $("video-detail").textContent = job
      ? `Seed ${job.seed} · ${(job.duration || 124 / 24).toFixed(2)}-second clip`
      : "A short scene, made locally.";
  }
}
function history(jobs) {
  const key =
    jobs.map((j) => `${j.id}:${j.state}:${j.poster_url || ""}`).join("|") +
    selected;
  if (key === historyKey) return;
  historyKey = key;
  $("history").replaceChildren();
  $("history-count").textContent = String(jobs.length);
  if (!jobs.length) {
    const p = document.createElement("p");
    p.className = "empty-history";
    p.textContent = "Your scenes will collect here.";
    $("history").append(p);
    return;
  }
  jobs.slice(0, 8).forEach((job) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "history-card" + (job.id === selected ? " selected" : "");
    card.setAttribute("aria-label", `View render: ${job.prompt}`);
    const image = document.createElement("div");
    image.className = "history-image";
    image.textContent = "✳";
    if (job.poster_url) {
      const img = document.createElement("img");
      img.src = job.poster_url;
      img.alt = "";
      img.loading = "lazy";
      image.append(img);
    }
    const badge = document.createElement("span");
    badge.className = "history-status";
    badge.textContent =
      job.state === "complete"
        ? `${(job.duration || 124 / 24).toFixed(2)}s · READY`
        : job.state.toUpperCase();
    image.append(badge);
    const copy = document.createElement("div");
    copy.className = "history-copy";
    const p = document.createElement("p");
    p.textContent = job.prompt;
    const meta = document.createElement("div"),
      date = document.createElement("span"),
      duration = document.createElement("span");
    date.textContent = new Date(job.created_at * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    duration.textContent = job.render_seconds
      ? `${job.render_seconds.toFixed(1)}s render`
      : "Local render";
    meta.append(date, duration);
    copy.append(p, meta);
    card.append(image, copy);
    card.addEventListener("click", () => {
      selected = job.id;
      historyKey = null;
      paint();
    });
    $("history").append(card);
  });
}
function paint() {
  if (!state) return;
  const active = state.jobs.find((j) => j.id === state.active_id);
  if (!selected || !state.jobs.some((j) => j.id === selected))
    selected = active?.id || state.jobs[0]?.id;
  const job = state.jobs.find((j) => j.id === selected);
  const monitor = active || job;
  const presets = state.settings.duration_presets || [
    {
      frames: 124,
      seconds: 124 / 24,
      render_seconds: 139,
      label: "5.2 seconds",
    },
  ];
  const key = JSON.stringify(presets);
  if (key !== presetKey) {
    const old = $("length").value;
    $("length").replaceChildren();
    presets.forEach((p) => {
      const option = document.createElement("option");
      option.value = String(p.frames);
      option.textContent = p.label || `${p.seconds.toFixed(1)} seconds`;
      $("length").append(option);
    });
    if (presets.some((p) => String(p.frames) === old)) $("length").value = old;
    presetKey = key;
  }
  $("length").disabled = !!active || submitting || offline;
  const chosen =
    presets.find((p) => String(p.frames) === $("length").value) || presets[0];
  $("expectation").textContent =
    `Measured: ${Math.round(chosen.render_seconds)} seconds · one render at a time`;
  $("generate").disabled = submitting || !!active || offline;
  $("generate").firstElementChild.textContent = submitting
    ? "Starting…"
    : active
      ? "Rendering on Spark…"
      : "Generate video";
  $("cancel").hidden = !active;
  $("cancel").disabled = !!active?.cancel_requested || offline;
  $("job-state").textContent = active
    ? "RENDERING"
    : monitor?.state === "complete"
      ? "COMPLETE"
      : monitor?.state === "failed"
        ? "STOPPED"
        : "IDLE";
  $("stage").textContent = offline
    ? "Connection to Spark interrupted"
    : monitor?.stage || "Ready for your next idea";
  $("stage-detail").textContent = offline
    ? "The independent render guard remains active on Spark."
    : active?.cancel_requested
      ? "Stopping this render and releasing memory."
      : active
        ? "Model loading, four diffusion steps, then video export."
        : monitor?.state === "complete"
          ? "Video decoded and checked. Ready for playback."
          : "Progress follows the renderer’s actual stages.";
  if (monitor?.error && !offline) $("stage-detail").textContent = monitor.error;
  const percent = monitor?.percent || 0;
  $("percent").textContent = String(Math.floor(percent));
  $("progress-fill").style.width = percent + "%";
  $("progress").setAttribute("aria-valuenow", String(percent));
  $("preview-progress").hidden = !active;
  $("preview-stage").textContent = offline
    ? "Connection interrupted"
    : active?.stage || "";
  $("preview-percent").textContent = Math.floor(active?.percent || 0) + "%";
  $("preview-progress-fill").style.width = (active?.percent || 0) + "%";
  $("preview-memory").textContent =
    `Memory ${gib(state.memory.used)} / ${gib(state.memory.total)} GiB`;
  $("preview-elapsed").textContent = seconds(active?.elapsed);
  $("elapsed").textContent = seconds(monitor?.elapsed) + " elapsed";
  $("elapsed-large").textContent = seconds(monitor?.elapsed);
  const mem = state.memory;
  const reserve = active?.host_reserve ?? mem.reserve;
  $("memory-used").textContent = gib(mem.used);
  $("memory-total").textContent = `/ ${gib(mem.total)} GiB`;
  $("memory-available").textContent =
    `${gib(mem.available)} GiB available · ${gib(reserve)} GiB guard reserve`;
  $("memory-fill").style.width = (100 * mem.used) / mem.total + "%";
  $("reserve-marker").title = `${gib(reserve)} GiB host reserve`;
  $("reserve-marker").style.left =
    (100 * (mem.total - reserve)) / mem.total + "%";
  $("cuda-memory").textContent = active
    ? active.cuda_allocated === null
      ? "Measuring…"
      : gib(active.cuda_allocated) + " GiB"
    : "0.0 GiB";
  $("memory-live").textContent = offline ? "STALE" : "LIVE";
  preview(job);
  history(state.jobs);
}
async function poll() {
  try {
    const next = await request("/api/state");
    if (next.app !== "spark-video") throw new Error("Unexpected server");
    const wasOffline = offline;
    offline = false;
    state = next;
    $("connection-dot").classList.remove("offline");
    $("connection-text").textContent = "DGX Spark connected";
    if (wasOffline) notice("");
    chart(state.memory);
    paint();
  } catch (error) {
    offline = true;
    $("connection-dot").classList.add("offline");
    $("connection-text").textContent = "Spark disconnected";
    if (!state)
      notice("Cannot reach Spark. Reopen the portal launcher to reconnect.");
    paint();
  } finally {
    setTimeout(poll, document.hidden ? 2500 : 1000);
  }
}
poll();
