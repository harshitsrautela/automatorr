// Decision form: shows the fields that apply to the chosen decision, a custom calendar for the pick-up date, and a confirm dialog.
(function () {
  const form = document.getElementById("decision-form");
  if (!form) return;
  const $ = (id) => document.getElementById(id);
  const disp = $("disposition"), amount = $("refund_amount"), sku = $("replacement_sku"), reason = $("reason"), pickup = $("pickup_date");
  const fAmount = $("f-amount"), fSku = $("f-sku"), fReason = $("f-reason"), fPickup = $("f-pickup"), fComment = $("f-comment");
  const dialog = $("confirm"), summary = $("summary");
  let confirmed = false;

  disp.addEventListener("change", () => {
    const v = disp.value;
    fAmount.hidden = v !== "refund";
    fSku.hidden = v !== "replace";
    fReason.hidden = v !== "reject";
    fPickup.hidden = !(v === "refund" || v === "replace");
    fComment.hidden = v === "";
    amount.required = v === "refund";
    sku.required = v === "replace";
    reason.required = v === "reject";
  });

  // The calendar: a button opens a month grid; the day buttons carry only the day number.
  const open = $("pickup-open"), cal = $("cal"), title = $("cal-title"), grid = $("cal-grid"), shown = $("pickup-shown");
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  let view = new Date(Date.UTC(2026, 9, 1));
  function render() {
    title.textContent = `${MONTHS[view.getUTCMonth()]} ${view.getUTCFullYear()}`;
    grid.innerHTML = "";
    for (const d of ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) {
      const h = document.createElement("span");
      h.className = "dow";
      h.textContent = d;
      grid.appendChild(h);
    }
    const first = (view.getUTCDay() + 6) % 7;
    for (let i = 0; i < first; i++) grid.appendChild(document.createElement("span"));
    const days = new Date(Date.UTC(view.getUTCFullYear(), view.getUTCMonth() + 1, 0)).getUTCDate();
    for (let d = 1; d <= days; d++) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "day";
      b.textContent = String(d);
      const iso = `${view.getUTCFullYear()}-${String(view.getUTCMonth() + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      if (pickup.value === iso) b.classList.add("chosen");
      b.addEventListener("click", () => {
        pickup.value = iso;
        shown.textContent = `${String(d).padStart(2, "0")} ${MONTHS[view.getUTCMonth()].slice(0, 3)} ${view.getUTCFullYear()}`;
        cal.hidden = true;
      });
      grid.appendChild(b);
    }
  }
  open.addEventListener("click", () => { cal.hidden = !cal.hidden; if (!cal.hidden) render(); });
  $("cal-prev").addEventListener("click", () => { view = new Date(Date.UTC(view.getUTCFullYear(), view.getUTCMonth() - 1, 1)); render(); });
  $("cal-next").addEventListener("click", () => { view = new Date(Date.UTC(view.getUTCFullYear(), view.getUTCMonth() + 1, 1)); render(); });

  form.addEventListener("submit", (e) => {
    if (confirmed) return;
    e.preventDefault();
    const bits = [disp.options[disp.selectedIndex].text];
    if (disp.value === "refund" && amount.value) bits.push("₹" + amount.value);
    if (disp.value === "replace" && sku.value) bits.push(sku.value);
    if (disp.value === "reject" && reason.value) bits.push(reason.value);
    if (pickup.value) bits.push("pick-up " + pickup.value);
    summary.textContent = bits.join(" · ");
    dialog.showModal();
  });
  $("confirm-no").addEventListener("click", () => dialog.close());
  $("confirm-yes").addEventListener("click", () => { confirmed = true; dialog.close(); form.requestSubmit(); });
})();
