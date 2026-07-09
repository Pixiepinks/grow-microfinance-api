(function () {
  const apiBase = window.GROW_API_BASE || '';
  const token = window.localStorage && window.localStorage.getItem('access_token');

  async function api(path, options = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${apiBase}${path}`, Object.assign({}, options, { headers }));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || 'Request failed');
    return data;
  }

  function money(value) {
    return Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function ledgerRow(loanId, entry) {
    return `<tr>
      <td>${entry.installment_no}</td><td>${entry.period_start_date}</td><td>${entry.due_date}</td>
      <td>${entry.period_days}</td><td>${money(entry.opening_balance)}</td><td>${money(entry.principal_amount)}</td>
      <td>${money(entry.interest_amount)}</td><td>${money(entry.installment_amount)}</td><td>${money(entry.paid_amount)}</td>
      <td>${money(entry.delay_interest)}</td><td>${entry.status}</td>
      <td><button class="record-ledger-payment" data-loan-id="${loanId}" data-entry-id="${entry.id}">Record Payment</button></td>
    </tr>`;
  }

  async function loadLedger(loanId, target) {
    target.innerHTML = '<p>Loading ledger...</p>';
    try {
      const result = await api(`/admin/loans/${loanId}/ledger`);
      const totals = result.totals || {};
      target.innerHTML = `<div class="loan-tabs"><button data-tab="details">Details</button><button data-tab="ledger">Ledger</button></div>
        <section class="ledger-totals">
          <strong>Total Principal:</strong> ${money(totals.total_principal)}
          <strong>Total Interest:</strong> ${money(totals.total_interest)}
          <strong>Total Payable:</strong> ${money(totals.total_payable)}
          <strong>Total Paid:</strong> ${money(totals.total_paid)}
          <strong>Outstanding:</strong> ${money(totals.outstanding)}
          <strong>Delay Interest:</strong> ${money(totals.delay_interest)}
        </section>
        <table class="loan-ledger-table"><thead><tr><th>#</th><th>Start</th><th>Due</th><th>Days</th><th>Opening</th><th>Principal</th><th>Interest</th><th>Installment</th><th>Paid</th><th>Delay Interest</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>${(result.items || []).map((entry) => ledgerRow(loanId, entry)).join('')}</tbody></table>`;
    } catch (error) {
      target.innerHTML = `<p class="error">${error.message}</p>`;
    }
  }

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('.record-ledger-payment');
    if (!button) return;
    const paidAmount = window.prompt('Paid amount');
    if (!paidAmount) return;
    const paidDate = window.prompt('Paid date (YYYY-MM-DD)', new Date().toISOString().slice(0, 10));
    try {
      await api(`/admin/loans/${button.dataset.loanId}/ledger/${button.dataset.entryId}/payment`, {
        method: 'POST',
        body: JSON.stringify({ paid_amount: paidAmount, paid_date: paidDate }),
      });
      const container = button.closest('[data-loan-ledger]') || document.querySelector('[data-loan-ledger]');
      if (container) loadLedger(button.dataset.loanId, container);
    } catch (error) {
      window.alert(error.message);
    }
  });

  window.GrowMicrofinanceAdmin = Object.assign(window.GrowMicrofinanceAdmin || {}, { loadLedger });
})();
