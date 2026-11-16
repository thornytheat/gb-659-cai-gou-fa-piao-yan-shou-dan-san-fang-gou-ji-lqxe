const out = document.querySelector("#result");
const feedback = document.querySelector("#feedback");
const publish = document.querySelector("#publish");
const aliasInput = document.querySelector("#alias-input");
const baseVersion = document.querySelector("#base-version");
const noteInput = document.querySelector("#publish-note");
if(publish && aliasInput && baseVersion){
  let inFlight = false;
  publish.addEventListener("click", async () => {
    if(inFlight) return;
    let aliases;
    try{ aliases = JSON.parse(aliasInput.value); }
    catch(error){ out.textContent = "别名表不是合法 JSON：" + error.message; feedback.textContent = "未发布"; return; }
    const base = Number(baseVersion.value);
    inFlight = true; publish.disabled = true; feedback.textContent = "发布中";
    try{
      const response = await fetch("/api/master/publish", {
        method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify({aliases, base_version: base, note: noteInput.value || null,
                              idempotency_key: `ui:v${base}:${JSON.stringify(aliases)}`}),
      });
      const data = await response.json();
      if(!response.ok) throw new Error(data.detail?.message || "发布失败");
      out.textContent = JSON.stringify(data, null, 2);
      baseVersion.value = data.version;
      feedback.textContent = data.created ? `已发布 v${data.version}` : `v${data.version} 已存在，复用`;
      window.setTimeout(() => window.location.reload(), 400);
    }catch(error){ out.textContent = error.message; feedback.textContent = "发布失败，可重试"; }
    finally{ inFlight = false; publish.disabled = false; }
  });
}
