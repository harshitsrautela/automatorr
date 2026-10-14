// Decision form behaviour: show only the fields that apply to the chosen decision, and ask for confirmation before saving.
(function () {
  const form = document.getElementById("decision-form");
  if (!form) return;
  const status = document.getElementById("status");
  const reason = document.getElementById("reason");
  const date = document.getElementById("payment_date");
  const note = document.getElementById("note");
  const fDate = document.getElementById("f-date");
  const fReason = document.getElementById("f-reason");
  const fNote = document.getElementById("f-note");
  const dialog = document.getElementById("confirm");
  const summary = document.getElementById("summary");
  const REASONS = { hold: ["Short receipt", "Price variance"], reject: ["Vendor blocked", "No open PO"] };
  let confirmed = false;

  status.addEventListener("change", () => {
    const v = status.value;
    fDate.hidden = v !== "approve";
    fReason.hidden = !(v === "hold" || v === "reject");
    fNote.hidden = v === "" || v === "approve";
    reason.innerHTML = '<option value="">Choose…</option>' + (REASONS[v] || []).map((r) => `<option value="${r}">${r}</option>`).join("");
    date.required = v === "approve";
    reason.required = v === "hold" || v === "reject";
    note.required = v === "hold";
  });

  form.addEventListener("submit", (e) => {
    if (confirmed) return;
    e.preventDefault();
    const label = status.options[status.selectedIndex].text;
    const bits = [label];
    if (status.value === "approve" && date.value) bits.push("pay on " + date.value);
    if (reason.value) bits.push(reason.value);
    if (note.value.trim()) bits.push("note: " + note.value.trim());
    summary.textContent = bits.join(" · ");
    dialog.showModal();
  });

  document.getElementById("confirm-no").addEventListener("click", () => dialog.close());
  document.getElementById("confirm-yes").addEventListener("click", () => {
    confirmed = true;
    dialog.close();
    form.requestSubmit();
  });
})();
