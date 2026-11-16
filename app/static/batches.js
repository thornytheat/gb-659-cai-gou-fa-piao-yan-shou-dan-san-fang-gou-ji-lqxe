const out = document.querySelector("#result");
const feedback = document.querySelector("#feedback");
const createButton = document.querySelector("#create-batch");
const compareButton = document.querySelector("#compare-batches");
const versionInput = document.querySelector("#batch-version");
const leftInput = document.querySelector("#left-batch");
const rightInput = document.querySelector("#right-batch");
const diffBody = document.querySelector("#differences tbody");
const diffMeta = document.querySelector("#diff-meta");

async function request(url, options){
  const response = await fetch(url, options);
  const data = await response.json();
  if(!response.ok) throw new Error(data.detail?.message || "请求失败");
  return data;
}

createButton?.addEventListener("click", async () => {
  createButton.disabled = true; feedback.textContent = "建批中";
  try{
    const version = versionInput.value ? Number(versionInput.value) : null;
    const data = await request("/api/batches", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({mapping_version: version}),
    });
    out.textContent = JSON.stringify({batch_id: data.batch_id, mapping_version: data.mapping_version,
      created: data.created, amount: data.amount, groups: data.groups}, null, 2);
    feedback.textContent = data.created ? "已新建批次" : "批次已存在，复用";
    window.setTimeout(() => window.location.reload(), 400);
  }catch(error){ out.textContent = error.message; feedback.textContent = "建批失败，可重试"; }
  finally{ createButton.disabled = false; }
});

compareButton?.addEventListener("click", async () => {
  const left = leftInput.value.trim(), right = rightInput.value.trim();
  if(!left || !right){ out.textContent = "请填写左右两个批次"; return; }
  compareButton.disabled = true; feedback.textContent = "比较中";
  try{
    const data = await request(`/api/batches/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
    out.textContent = JSON.stringify(data, null, 2);
    diffBody.innerHTML = "";
    for(const item of data.line_differences){
      const row = document.createElement("tr");
      row.innerHTML = `<td class="num">${item.line_no}</td><td>${item.old_canonical ?? "—"}</td><td>${item.new_canonical ?? "—"}</td><td class="num"></td>`;
      diffBody.appendChild(row);
    }
    const moved = data.group_differences;
    for(const item of moved){
      const row = document.createElement("tr");
      row.innerHTML = `<td>合计</td><td>${item.old_amount}</td><td>${item.new_amount}</td><td class="num">${item.delta}</td>`;
      diffBody.appendChild(row);
    }
    diffMeta.textContent = data.identical_attribution ? "归属一致" : `${data.line_differences.length} 行发生变化`;
    feedback.textContent = "比较完成";
  }catch(error){ out.textContent = error.message; diffMeta.textContent = "比较失败"; feedback.textContent = "失败，可重试"; }
  finally{ compareButton.disabled = false; }
});
