const state = {
  token: localStorage.getItem('mt5PortalToken') || '',
  user: null,
  client: null,
  clients: [],
  groups: [],
  mt5AccountsByGroup: {},
  importClassifications: [],
  clientProfile: null,
  clientDashboard: null,
  storage: null,
};

const $ = (id) => document.getElementById(id);
const today = () => new Date().toISOString().slice(0, 10);
const nowLocalDateTime = () => {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};

function formJson(form) {
  const data = new FormData(form);
  const payload = {};
  for (const [key, value] of data.entries()) {
    if (value !== '') payload[key] = value;
  }
  for (const input of form.querySelectorAll('input[type="checkbox"]')) {
    payload[input.name] = input.checked;
  }
  return payload;
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { headers, ...options });
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === 'object' ? data.detail || JSON.stringify(data) : data;
    throw new Error(detail || `Request failed with ${response.status}`);
  }
  return data;
}

function show(el, value) {
  if (!el) return;
  const rendered = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  el.textContent = rendered;
  if (rendered.startsWith('Error:')) toast(rendered, 'error');
  else if (rendered && rendered !== '{}') toast('Saved.', 'success');
}

function optionList(items, valueKey, labelKey) {
  return items.map((item) => `<option value="${item[valueKey]}">${item[labelKey]}</option>`).join('');
}

function fillSelect(id, html) {
  const select = $(id);
  if (select) select.innerHTML = html || '<option value="">No records yet</option>';
}

function table(rows) {
  if (!rows || rows.length === 0) return '<p class="muted">No data yet.</p>';
  const keys = Object.keys(rows[0]);
  return `<table><thead><tr>${keys.map((key) => `<th>${key}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${keys.map((key) => `<td>${row[key] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : '';
}

function helpIcon(text) {
  return `<span class="info-dot" tabindex="0" aria-label="${escapeHtml(text)}" data-tooltip="${escapeHtml(text)}">!</span>`;
}


function money(value) {
  const number = Number(value || 0);
  if (Number.isNaN(number)) return '$0';
  return number.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}


function percent(value) {
  const number = Number(value || 0);
  if (Number.isNaN(number)) return '0.00%';
  return `${number.toFixed(2)}%`;
}

function classifyLabel(value) {
  const labels = {
    client_deposit: 'Client deposit',
    deposit_split_equally: 'Deposit split equally',
    deposit_split_by_percentage: 'Deposit split by ownership %',
    returned_pending_transfer: 'Returned pending transfer',
    broker_correction: 'Broker correction',
    client_withdrawal: 'Client withdrawal',
    shared_group_expense: 'Shared group expense',
    external_commission_withdrawal: 'External commission withdrawal',
    partner_commission_withdrawal: 'Partner commission withdrawal',
    mixed_commission_withdrawal: 'Mixed commission withdrawal',
    transfer_to_new_mt5_account: 'Transfer to new MT5 account',
    transfer_to_existing_mt5_account: 'Transfer to existing MT5 account',
    broker_fee: 'Broker fee',
    manual_adjustment: 'Manual adjustment',
    ignore: 'Ignore / already handled',
  };
  return labels[value] || String(value || '').replaceAll('_', ' ');
}

function classificationEffect(value) {
  const effects = {
    client_deposit: 'Adds an effective deposit to the selected client.',
    deposit_split_equally: 'Splits the deposit equally across active group members.',
    deposit_split_by_percentage: 'Splits the deposit using current ownership percentages.',
    client_withdrawal: 'Records an effective withdrawal for the selected client.',
    shared_group_expense: 'Splits the withdrawal as a shared group expense.',
    external_commission_withdrawal: 'Marks external commission as physically withdrawn from MT5.',
    partner_commission_withdrawal: 'Marks partner commission as withdrawn for the selected partner.',
    mixed_commission_withdrawal: 'Splits one withdrawal between external and partner commission amounts.',
    transfer_to_new_mt5_account: 'Creates a pending transfer for a new MT5 account. This is not a loss.',
    transfer_to_existing_mt5_account: 'Links the withdrawal to an existing MT5 account transfer.',
    broker_fee: 'Records a shared broker fee/expense.',
    broker_correction: 'Records a broker correction/manual adjustment.',
    manual_adjustment: 'Creates an adjustment entry. Use only with a clear reason.',
    ignore: 'No ledger entry will be created for this movement.',
  };
  return effects[value] || 'Creates ledger entries according to this classification.';
}

function lineIcon(name) {
  const icons = {
    dashboard: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13h7V4H4v9Zm9 7h7V4h-7v16ZM4 20h7v-5H4v5Z"/></svg>',
    clients: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 11a4 4 0 1 0-3.5-5.9A5 5 0 1 0 8 13c-3.3 0-6 2-6 4.5V20h12v-2.5c0-1.5-.8-2.8-2.1-3.6A6.5 6.5 0 0 1 16 11Zm0 2c-1.4 0-2.7.4-3.7 1.1 1.1.9 1.7 2.1 1.7 3.4V20h8v-2.5c0-2.5-2.7-4.5-6-4.5Z"/></svg>',
    deposits: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h18v10H3V7Zm2 2v6h14V9H5Zm2 8h10v2H7v-2Zm5-7 4 3h-3v2h-2v-2H8l4-3Z"/></svg>',
    mt5: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H4V5Zm2 2v7h12V7H6Zm3 12h6v-2H9v2Zm-3 1h12v-1H6v1Z"/></svg>',
    views: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5c5 0 8.7 4.5 9.7 6-.9 1.5-4.6 6-9.7 6s-8.7-4.5-9.7-6C3.3 9.5 7 5 12 5Zm0 2C8.5 7 5.7 9.7 4.8 11c.9 1.3 3.7 4 7.2 4s6.3-2.7 7.2-4C18.3 9.7 15.5 7 12 7Zm0 2a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z"/></svg>',
    import: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v5h-2V6H7v12h10v-3h2v5H5V4Zm8 4 5 4-5 4v-3H9v-2h4V8Z"/></svg>',
    workflows: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2Z"/></svg>',
  };
  return icons[name] || icons.dashboard;
}

function friendlyRole(role) {
  return role === 'partner' ? 'Partner' : 'Client';
}

function clientGroupCards(groups) {
  if (!groups || groups.length === 0) return '<p class="muted">You are not assigned to any group yet.</p>';
  return `<div class="client-group-grid">${groups.map((group) => `
    <article class="client-group-card">
      <div class="client-group-top">
        <div>
          <p class="eyebrow">${escapeHtml(friendlyRole(group.role))}</p>
          <h3>${escapeHtml(group.group_name || group.group_id)}</h3>
        </div>
        ${Number(group.membership_count || 1) > 1 ? '<span class="pill warning">Duplicate merged</span>' : '<span class="pill">Active</span>'}
      </div>
      <div class="metric-row">
        <div><span>Current balance</span><strong>${money(group.current_balance)}</strong></div>
        <div><span>Available</span><strong>${money(group.available_balance)}</strong></div>
      </div>
      <div class="mini-ledger">
        <span>Capital base: ${money(group.effective_capital)}</span>
        <span>Ledger finalized: ${money(group.finalized_ledger_balance)}</span>
      </div>
      <details>
        <summary>Technical details</summary>
        <p class="muted small-text">Group ID: ${escapeHtml(group.group_id)}</p>
      </details>
    </article>`).join('')}</div>`;
}

function renderClientDashboard(target, dashboard, options = {}) {
  const groups = dashboard.groups || [];
  const isAdminView = options.adminView === true;
  target.innerHTML = `
    <div class="client-dashboard-head">
      <div>
        <p class="eyebrow">${isAdminView ? 'Client account' : 'My account'}</p>
        <h2>${escapeHtml(dashboard.client.display_name)}</h2>
      </div>
      <div class="balance-capsules">
        <div><span>Total balance</span><strong>${money(dashboard.combined_balance)}</strong></div>
        <div><span>Available</span><strong>${money(dashboard.available_balance || dashboard.combined_balance)}</strong></div>
      </div>
    </div>
    ${clientGroupCards(groups)}
  `;
}

function renderClientBalanceDetails(target, dashboard) {
  const groups = dashboard.groups || [];
  target.innerHTML = table(groups.map((group) => ({
    group: group.group_name || group.group_id,
    role: friendlyRole(group.role),
    capital_base: money(group.effective_capital),
    current_balance: money(group.current_balance),
    finalized_balance: money(group.finalized_balance),
    available_balance: money(group.available_balance),
  })));
}

function renderClientProfile(profile) {
  if (!profile) return;
  const client = profile.client;
  const user = profile.user;
  if ($('clientSidebarName')) $('clientSidebarName').textContent = client.display_name || user.username;
  if ($('clientProfileSummary')) {
    $('clientProfileSummary').innerHTML = `
      <div class="profile-grid">
        <div><span>Name</span><strong>${escapeHtml(client.display_name)}</strong></div>
        <div><span>Username</span><strong>${escapeHtml(user.username)}</strong><small>Username cannot be changed</small></div>
        <div><span>Email</span><strong>${escapeHtml(client.email || 'No email added yet')}</strong><small>${profile.password_reset_available ? 'Password reset by email is available' : 'Add email to enable password reset'}</small></div>
        <div><span>Email reports</span><strong>${client.email_reports_opt_in ? 'On' : 'Off'}</strong></div>
        <div><span>2FA preference</span><strong>${user.two_factor_enabled ? 'Enabled' : 'Disabled'}</strong></div>
      </div>`;
  }
  const profileForm = $('clientProfileForm');
  if (profileForm) {
    profileForm.elements.email.value = client.email || '';
    profileForm.elements.email_reports_opt_in.checked = Boolean(client.email_reports_opt_in);
  }
  const twofaForm = $('client2faForm');
  if (twofaForm) twofaForm.elements.enabled.checked = Boolean(user.two_factor_enabled);
}

function setupClientTabs() {
  document.querySelectorAll('.client-tab').forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = 'true';
    button.addEventListener('click', () => {
      const tab = button.dataset.clientTab;
      document.querySelectorAll('.client-tab').forEach((item) => item.classList.toggle('active', item === button));
      document.querySelectorAll('.client-tab-page').forEach((page) => page.classList.toggle('active', page.dataset.clientPage === tab));
    });
  });
}

function workflowItem(title, item, actionsHtml) {
  const name = item.client_name || item.mt5_account_name || item.description || item.transaction_id;
  return `
    <div class="workflow-item">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(name)} · $${escapeHtml(item.absolute_amount || item.total_amount || item.amount || '0')}</p>
        <p class="muted">${escapeHtml(item.description || '')}</p>
        <p class="muted">Effective: ${escapeHtml(item.effective_date || 'not set')} · ID: ${escapeHtml(shortId(item.entry_id || item.transaction_id))}</p>
      </div>
      <div class="workflow-actions">${actionsHtml}</div>
    </div>`;
}

function workflowSection(title, items, renderActions) {
  if (!items || items.length === 0) {
    return `<div class="workflow-section"><h4>${escapeHtml(title)}</h4><p class="muted">Nothing pending.</p></div>`;
  }
  return `<div class="workflow-section"><h4>${escapeHtml(title)}</h4><div class="workflow-list">${items.map((item) => workflowItem(title, item, renderActions(item))).join('')}</div></div>`;
}

function transferAccountOptions() {
  const accounts = Object.values(state.mt5AccountsByGroup).flat();
  return accounts.map((account) => `<option value="${escapeHtml(account.account_id)}">${escapeHtml(account.nickname)}</option>`).join('');
}

function renderWorkflowInbox(data) {
  const transferOptions = transferAccountOptions();
  const html = `
    <div class="workflow-summary">External commission payable: <strong>$${escapeHtml(data.external_commission_payable || '0')}</strong></div>
    ${workflowSection('Pending deposits', data.pending_deposits, (item) => `<button type="button" data-action="deposit-effective" data-entry-id="${escapeHtml(item.entry_id)}">Make effective</button>${helpIcon('Adds this deposit to the client balance and starts using it in allocation calculations.')}`)}
    ${workflowSection('Withdrawal requests', data.withdrawal_requests, (item) => `
      <button type="button" data-action="withdrawal-approve" data-entry-id="${escapeHtml(item.entry_id)}">Approve</button>${helpIcon('Approves the request and sets the date when it will affect the client balance.')}
      <button type="button" class="secondary" data-action="withdrawal-reject" data-entry-id="${escapeHtml(item.entry_id)}">Reject</button>${helpIcon('Rejects the request. The client balance will not change.')}
    `)}
    ${workflowSection('Approved withdrawals', data.approved_withdrawals, (item) => `<button type="button" data-action="withdrawal-effective" data-entry-id="${escapeHtml(item.entry_id)}">Make effective</button>${helpIcon('Deducts the approved withdrawal from the client balance.')}`)}
    ${workflowSection('Effective withdrawals', data.effective_withdrawals, (item) => `<button type="button" data-action="withdrawal-paid" data-entry-id="${escapeHtml(item.entry_id)}">Mark paid</button>${helpIcon('Marks that you actually paid the client outside the portal.')}`)}
    ${workflowSection('Pending expenses', data.pending_expenses, (item) => `<button type="button" data-action="expense-effective" data-transaction-id="${escapeHtml(item.transaction_id)}">Make all effective</button>${helpIcon('Applies the shared expense equally to active group members.')}`)}
    ${workflowSection('Pending internal transfers', data.pending_transfers, (item) => `
      <select class="workflow-transfer-to" data-transfer-entry-id="${escapeHtml(item.entry_id)}">${transferOptions}</select>
      <button type="button" data-action="transfer-complete" data-entry-id="${escapeHtml(item.entry_id)}">Complete</button>${helpIcon('Completes a transfer between MT5 accounts without treating it as profit or loss.')}
    `)}
  `;
  $('workflowInbox').innerHTML = html;
}

async function loadWorkflowInbox() {
  const groupId = $('workflowGroupSelect')?.value;
  if (!groupId) return;
  const data = await api(`/api/admin/groups/${groupId}/workflow-items`);
  renderWorkflowInbox(data);
}


function toast(message, tone = 'info') {
  const toastBox = $('toastBox');
  if (!toastBox || !message) return;
  const item = document.createElement('div');
  item.className = `toast toast-${tone}`;
  item.textContent = String(message).slice(0, 220);
  toastBox.appendChild(item);
  window.setTimeout(() => item.classList.add('show'), 20);
  window.setTimeout(() => {
    item.classList.remove('show');
    window.setTimeout(() => item.remove(), 260);
  }, 3600);
}


function adminSectionMeta(title) {
  const meta = {
    'Admin dashboard': {
      icon: lineIcon('dashboard'),
      nav: 'Dashboard',
      desc: 'High-level overview and refresh controls.',
      help: 'Use this first to confirm you are logged in and to refresh portal data.'
    },
    'Admin setup': {
      icon: lineIcon('clients'),
      nav: 'Clients',
      desc: 'Create client logins and groups.',
      help: 'Create usernames/passwords, client profiles, and investment groups here.'
    },
    'Groups and deposits': {
      icon: lineIcon('deposits'),
      nav: 'Deposits',
      desc: 'Assign clients to groups and record deposits.',
      help: 'Use this to add a client into a group and record money received outside the portal.'
    },
    'MT5 accounts': {
      icon: lineIcon('mt5'),
      nav: 'MT5',
      desc: 'Add MT5 accounts and manual snapshots.',
      help: 'Create MT5 account records and test balances before enabling live sync.'
    },
    'Admin dashboards': {
      icon: lineIcon('views'),
      nav: 'Views',
      desc: 'View client dashboards, group balances, and audit events.',
      help: 'Use this to inspect what a client sees and review admin audit actions.'
    },
    'Existing group import wizard': {
      icon: lineIcon('import'),
      nav: 'Import',
      desc: 'Classify old MT5 deposits, withdrawals, transfers, and commission withdrawals.',
      help: 'For now this is a manual simulation of detected MT5 movements. You add one MT5 cash movement, classify it, review, then finalize. Live MT5 scanning comes later.'
    },
    'Admin financial workflows': {
      icon: lineIcon('workflows'),
      nav: 'Workflows',
      desc: 'Approve withdrawals, expenses, transfers, commissions, and daily close.',
      help: 'Operational inbox for pending money actions. Prefer this over manual ledger IDs.'
    },
  };
  return meta[title] || { icon: lineIcon('dashboard'), nav: title, desc: 'Admin section', help: 'Open this section to manage related admin actions.' };
}

function setupThemeToggle() {
  if ($('themeToggle')) return;
  const actions = document.querySelector('.top-actions');
  if (!actions) return;
  const saved = localStorage.getItem('mt5PortalTheme') || 'light';
  document.documentElement.dataset.theme = saved;
  const button = document.createElement('button');
  button.id = 'themeToggle';
  button.type = 'button';
  button.className = 'secondary theme-toggle';
  button.textContent = saved === 'dark' ? 'Light mode' : 'Dark mode';
  button.setAttribute('aria-label', 'Toggle light and dark mode');
  button.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('mt5PortalTheme', next);
    button.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
    toast(`${next === 'dark' ? 'Dark' : 'Light'} mode enabled.`, 'info');
  });
  actions.insertBefore(button, actions.firstChild);
}

function setupAdminCommandCenter() {
  const adminPanel = $('adminPanel');
  if (!adminPanel || $('adminWorkspace')) return;

  const cards = Array.from(adminPanel.querySelectorAll(':scope > .card'));
  if (!cards.length) return;

  const shell = document.createElement('div');
  shell.id = 'adminWorkspace';
  shell.className = 'admin-workspace';
  shell.innerHTML = `
    <aside class="admin-sidebar" aria-label="Admin navigation">
      <div class="admin-sidebar-brand">
        <strong>Admin Portal</strong>
        <span>Command center</span>
      </div>
      <label class="admin-search-wrap">
        <span>Search tools</span>
        <input id="adminToolSearch" type="search" placeholder="Search clients, deposits, MT5..." />
      </label>
      <nav id="adminSidebarNav" class="admin-sidebar-nav"></nav>
      <div class="admin-sidebar-note">
        <strong>Tip</strong>
        <p>Open one workspace at a time. Hover <span class="info-dot static-info" data-tooltip="Help icons explain what an input or action does.">!</span> for guidance.</p>
      </div>
    </aside>
    <section id="adminContentArea" class="admin-content-area"></section>
  `;

  adminPanel.classList.add('admin-app-shell');
  adminPanel.insertBefore(shell, cards[0]);
  const nav = $('adminSidebarNav');
  const content = $('adminContentArea');

  cards.forEach((card, index) => {
    const sectionHeadTitle = card.querySelector(':scope > .section-head h2');
    const plainTitle = card.querySelector(':scope > h2');
    const titleEl = sectionHeadTitle || plainTitle;
    const title = titleEl?.textContent?.trim() || `Section ${index + 1}`;
    const meta = adminSectionMeta(title);
    card.classList.add('admin-tab-page', 'collapsible-card');
    card.dataset.adminTitle = title;
    card.dataset.searchText = card.textContent.toLowerCase();
    card.classList.toggle('active', index === 0);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = `admin-nav-item${index === 0 ? ' active' : ''}`;
    button.dataset.adminTitle = title;
    button.dataset.searchText = `${title} ${meta.nav} ${meta.desc} ${card.textContent}`.toLowerCase();
    button.innerHTML = `
      <span class="admin-nav-icon">${meta.icon}</span>
      <span><strong>${escapeHtml(meta.nav)}</strong><small>${escapeHtml(meta.desc)}</small></span>
      ${helpIcon(meta.help)}
    `;
    nav.appendChild(button);
    content.appendChild(card);
  });

  nav.addEventListener('click', (event) => {
    const button = event.target.closest('.admin-nav-item');
    if (!button) return;
    activateAdminSection(button.dataset.adminTitle);
  });
}

function activateAdminSection(title) {
  document.querySelectorAll('.admin-nav-item').forEach((item) => item.classList.toggle('active', item.dataset.adminTitle === title));
  document.querySelectorAll('#adminContentArea > .admin-tab-page').forEach((card) => {
    const active = card.dataset.adminTitle === title;
    card.classList.toggle('active', active);
    if (active) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function setupAdminSearch() {
  const search = $('adminToolSearch');
  if (!search || search.dataset.bound) return;
  search.dataset.bound = 'true';
  search.addEventListener('input', (event) => {
    const q = event.target.value.trim().toLowerCase();
    const buttons = Array.from(document.querySelectorAll('.admin-nav-item'));
    let matches = 0;
    buttons.forEach((button) => {
      const match = !q || button.dataset.searchText.includes(q);
      button.classList.toggle('search-hidden', !match);
      if (match) matches += 1;
    });
    if (q && matches === 1) {
      const only = buttons.find((button) => !button.classList.contains('search-hidden'));
      if (only) activateAdminSection(only.dataset.adminTitle);
    }
  });
}

function setupCollapsibleAdminCards() {
  const adminPanel = $('adminPanel');
  if (!adminPanel) return;
  setupAdminCommandCenter();
  setupAdminSearch();
  setupHelpIcons();
}

function setupHelpIcons() {
  if (document.body.dataset.helpReady === 'true') return;
  document.body.dataset.helpReady = 'true';
  const hints = [
    ['#setupAdminForm h3', 'Create the first administrator account. This can only be done once.'],
    ['#loginForm h3', 'Log in as an admin or a client using the username and password created by the admin.'],
    ['#clientForm h3', 'Create a client login. The admin sets the username and password.'],
    ['#groupForm h3', 'Create a separate pool/group. Each group can have its own clients, MT5 accounts, and commission rules.'],
    ['#memberForm h3', 'Add a client to a group as a normal client or partner.'],
    ['#depositForm h3', 'Record money that a client deposited outside the portal. It becomes part of calculations when made effective.'],
    ['#mt5Form h3', 'Add MT5 account details for a group. For cent accounts, values are divided by 100.'],
    ['#snapshotForm h3', 'Manually enter MT5 balance/equity data for testing before live MT5 sync.'],
    ['#loadClientDashboard', 'Admin-only view of one client dashboard for checking balances.'],
    ['#loadGroupDashboard', 'Shows group balances and MT5 read-only details for the selected group.'],
    ['#loadWorkflowInboxButton', 'Loads all pending deposits, withdrawals, expenses, and transfers for the selected group.'],
    ['#adminWithdrawalActionForm h3', 'Advanced manual withdrawal actions. Prefer the Workflow inbox when possible.'],
    ['#expenseForm h3', 'Record a shared expense that is split equally across active group members.'],
    ['#internalTransferForm h3', 'Record money moved out of one MT5 account to open or fund another account.'],
    ['#completeTransferForm h3', 'Mark a pending transfer as completed once the destination MT5 account is ready.'],
    ['#dailyCloseForm h3', 'Locks a broker-server-day result and allocates trading profit/loss to clients.'],
    ['#importMovementForm h3', 'Classify old MT5 deposits/withdrawals/transfers before finalizing an existing group import.'],
    ['#reviewImportButton', 'Shows the ledger entries that will be created without saving them yet.'],
    ['#finalizeImportButton', 'Saves the reviewed import classifications as official ledger entries.'],
    ['#commissionWithdrawalForm h3', 'Record commission that was withdrawn externally or credited to a partner.'],
    ['#manualAdjustmentForm h3', 'Use only for corrections. A reason is required for audit history.'],
    ['#loadLedgerButton', 'Shows the financial ledger for the selected group.'],
    ['#downloadGroupLedgerButton', 'Downloads the group ledger as a CSV file.'],
    ['#clientWithdrawalForm h3', 'Request a withdrawal from your group balance. Admin approval is required.'],
    ['#loadClientLedgerButton', 'Shows your deposits, withdrawals, expenses, commissions, and daily allocations.'],
    ['#downloadClientLedgerButton', 'Downloads your personal transaction history as CSV.'],
  ];
  hints.forEach(([selector, text]) => {
    document.querySelectorAll(selector).forEach((el) => {
      if (el.querySelector?.('.info-dot') || el.nextElementSibling?.classList?.contains('info-dot')) return;
      if (el.tagName === 'BUTTON') {
        el.insertAdjacentHTML('afterend', helpIcon(text));
      } else {
        el.insertAdjacentHTML('beforeend', helpIcon(text));
      }
    });
  });

  const fieldHints = {
    'Username': 'Login name. It should be unique. Client usernames cannot be changed later.',
    'Password': 'Temporary or login password. Store it securely and share it privately.',
    'Display name': 'The name shown on dashboards and reports.',
    'Email optional': 'Optional at creation. Clients can add or update their own email later.',
    'Group name': 'A group is one shared wallet/pool with its own clients and MT5 accounts.',
    'Currency': 'Display currency for this group. Keep USD for now.',
    'Client': 'Choose which client this action applies to.',
    'Group': 'Choose the pool/group this action belongs to.',
    'Amount USD': 'Enter the real USD amount. Cent-account conversion is handled separately for MT5 balances.',
    'Effective date': 'The date this action starts affecting balances and percentages.',
    'Broker server day': 'The broker day used for daily close accounting.',
    'Raw balance': 'The value shown inside MT5 before cent-account conversion.',
    'Raw equity': 'MT5 equity before conversion. Final accounting currently uses closed balance logic.',
    'Classification': 'Tell the portal what this MT5 cash movement means. It will not guess for you.',
    'Import mode': 'Choose how an existing group should be onboarded into the portal.',
    'MT5 comment': 'Optional comment copied from MT5 history, such as Deposit or Withdrawal.',
    'Destination MT5 if needed': 'Use when a withdrawal was actually money moved to another MT5 account.',
    'Description': 'Human-readable note saved in the ledger/audit trail.',
  };

  document.querySelectorAll('label').forEach((label) => {
    if (label.querySelector('.label-help')) return;
    const raw = Array.from(label.childNodes).find((node) => node.nodeType === Node.TEXT_NODE)?.textContent?.trim();
    if (!raw) return;
    const key = Object.keys(fieldHints).find((item) => raw.toLowerCase().startsWith(item.toLowerCase()));
    const text = fieldHints[key];
    if (!text) return;
    const dot = document.createElement('span');
    dot.className = 'info-dot label-help';
    dot.tabIndex = 0;
    dot.textContent = '!';
    dot.dataset.tooltip = text;
    label.appendChild(dot);
  });

  const buttonHints = {
    'Create client': 'Creates a new client login and profile. You can reset their password later.',
    'Create group': 'Creates a separate pool/shared wallet.',
    'Add member': 'Adds the chosen client to the chosen group. Duplicate memberships are blocked.',
    'Record deposit': 'Creates a pending deposit. Make it effective when it should affect balances.',
    'Create MT5 account': 'Saves MT5 account details for this group. Live connection testing is a later step.',
    'Create snapshot': 'Adds a manual MT5 snapshot for testing calculations without live MT5 sync.',
    'Review import': 'Shows what ledger entries will be created. It does not save yet.',
    'Finalize import': 'Writes the reviewed import classifications into the ledger.',
    'Clear list': 'Clears the queued import movements without saving them.',
    'Load workflow inbox': 'Loads pending approvals and actions for the selected group.',
    'Finalize daily close': 'Locks a daily result and allocates profit/loss to clients.',
  };
  document.querySelectorAll('button').forEach((button) => {
    const text = button.textContent.trim();
    if (!buttonHints[text] || button.nextElementSibling?.classList?.contains('button-help')) return;
    const holder = document.createElement('span');
    holder.className = 'button-help';
    holder.innerHTML = helpIcon(buttonHints[text]);
    button.insertAdjacentElement('afterend', holder);
  });
}

function setupFloatingTooltips() {
  if (document.body.dataset.floatingTooltipsReady === 'true') return;
  document.body.dataset.floatingTooltipsReady = 'true';

  let tooltip = document.getElementById('floatingTooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'floatingTooltip';
    tooltip.className = 'floating-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    document.body.appendChild(tooltip);
  }

  const hideTooltip = () => {
    tooltip.classList.remove('visible');
    tooltip.textContent = '';
  };

  const positionTooltip = (target) => {
    const text = target.dataset.tooltip || target.getAttribute('aria-label');
    if (!text) return hideTooltip();
    tooltip.textContent = text;
    tooltip.classList.add('visible');

    const rect = target.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    const margin = 12;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    let top = rect.top - tipRect.height - 10;

    if (left < margin) left = margin;
    if (left + tipRect.width > viewportWidth - margin) left = viewportWidth - tipRect.width - margin;
    if (top < margin) top = rect.bottom + 10;
    if (top + tipRect.height > viewportHeight - margin) top = viewportHeight - tipRect.height - margin;

    tooltip.style.left = `${Math.max(margin, left)}px`;
    tooltip.style.top = `${Math.max(margin, top)}px`;
  };

  document.addEventListener('mouseover', (event) => {
    const target = event.target.closest?.('.info-dot');
    if (target) positionTooltip(target);
  });
  document.addEventListener('focusin', (event) => {
    const target = event.target.closest?.('.info-dot');
    if (target) positionTooltip(target);
  });
  document.addEventListener('mousemove', (event) => {
    const target = event.target.closest?.('.info-dot');
    if (target) positionTooltip(target);
  });
  document.addEventListener('mouseout', (event) => {
    if (event.target.closest?.('.info-dot')) hideTooltip();
  });
  document.addEventListener('focusout', (event) => {
    if (event.target.closest?.('.info-dot')) hideTooltip();
  });
  window.addEventListener('scroll', hideTooltip, true);
  window.addEventListener('resize', hideTooltip);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hideTooltip();
  });
}

function addPageOverlays() {
  if ($('toastBox')) return;
  const box = document.createElement('div');
  box.id = 'toastBox';
  box.className = 'toast-box';
  document.body.appendChild(box);
}


function setVisible(id, visible) {
  const el = $(id);
  if (el) el.classList.toggle('hidden', !visible);
}

function setSessionBadge() {
  const badge = $('sessionBadge');
  if (!state.user) {
    badge.textContent = 'Not logged in';
    return;
  }
  badge.textContent = `${state.user.username} · ${state.user.role}`;
}

function updatePanels() {
  const isAdmin = state.user?.role === 'admin';
  const isClient = state.user?.role === 'client';
  document.body.classList.toggle('state-login', !state.user);
  document.body.classList.toggle('state-admin', isAdmin);
  document.body.classList.toggle('state-client', isClient);
  setVisible('authPanel', !state.user);
  setVisible('adminPanel', isAdmin);
  setVisible('clientPanel', isClient);
  setVisible('logoutButton', Boolean(state.user));
  setSessionBadge();
  if (isAdmin) setupCollapsibleAdminCards();
  if (isClient) setupClientTabs();
}

async function loadSession() {
  if (!state.token) {
    state.user = null;
    state.client = null;
    updatePanels();
    return;
  }
  try {
    const session = await api('/api/session/me');
    state.user = session.user;
    state.client = session.client || null;
    updatePanels();
    if (state.user.role === 'admin') await refreshLists();
    if (state.user.role === 'client') await loadClientSelfDashboard();
  } catch (_err) {
    localStorage.removeItem('mt5PortalToken');
    state.token = '';
    state.user = null;
    state.client = null;
    updatePanels();
  }
}

async function refreshLists() {
  if (state.user?.role !== 'admin') return;
  state.clients = await api('/api/admin/clients');
  state.groups = await api('/api/admin/groups');
  try { state.storage = await api('/api/system/storage'); } catch (_err) { state.storage = null; }

  const clientOptions = optionList(state.clients, 'client_id', 'display_name');
  const groupOptions = optionList(state.groups, 'group_id', 'name');
  ['memberClientSelect', 'depositClientSelect', 'dashboardClientSelect', 'adjustmentClientSelect', 'securityClientSelect', 'importClientSelect'].forEach((id) => fillSelect(id, clientOptions));
  fillSelect('commissionClientSelect', '<option value="">External commission</option>' + clientOptions);
  ['memberGroupSelect', 'depositGroupSelect', 'mt5GroupSelect', 'dashboardGroupSelect', 'workflowGroupSelect', 'expenseGroupSelect', 'transferGroupSelect', 'dailyCloseGroupSelect', 'commissionGroupSelect', 'adjustmentGroupSelect', 'ledgerGroupSelect', 'importGroupSelect'].forEach((id) => fillSelect(id, groupOptions));

  state.mt5AccountsByGroup = {};
  for (const group of state.groups) {
    try {
      state.mt5AccountsByGroup[group.group_id] = await api(`/api/admin/groups/${group.group_id}/mt5-accounts`);
    } catch (_err) {
      state.mt5AccountsByGroup[group.group_id] = [];
    }
  }
  refreshSnapshotAccountSelect();
  renderAdminSummary();
}

function renderAdminSummary() {
  const accounts = Object.values(state.mt5AccountsByGroup).flat();
  const storageNote = state.storage?.uses_external_data_dir
    ? '<div class="persistence-note"><strong>Persistent data enabled</strong><span>Your users, groups, and ledger are stored outside this project folder, so future code upgrades should not reset local data.</span></div>'
    : '';
  $('adminSummary').innerHTML = `
    <div class="summary-tile">Clients<strong>${state.clients.length}</strong></div>
    <div class="summary-tile">Groups<strong>${state.groups.length}</strong></div>
    <div class="summary-tile">MT5 accounts<strong>${accounts.length}</strong></div>
    ${storageNote}
  `;
}

function refreshSnapshotAccountSelect() {
  const accounts = Object.values(state.mt5AccountsByGroup).flat();
  const accountOptions = optionList(accounts, 'account_id', 'nickname');
  ['snapshotAccountSelect', 'transferFromAccountSelect', 'transferToAccountSelect', 'importAccountSelect', 'importToAccountSelect'].forEach((id) => fillSelect(id, accountOptions));
}

async function loadClientSelfDashboard() {
  const profile = await api('/api/client/me/profile');
  const dashboard = await api('/api/client/me/dashboard');
  const accounts = await api('/api/client/me/mt5-accounts');
  state.clientProfile = profile;
  state.clientDashboard = dashboard;
  state.client = profile.client;
  const groups = dashboard.groups || [];
  renderClientProfile(profile);
  renderClientDashboard($('clientSelfDashboard'), dashboard);
  renderClientBalanceDetails($('clientBalanceDetails'), dashboard);
  $('clientMt5Accounts').innerHTML = table(accounts.map((account) => ({
    group: account.group_name || account.group_id,
    account: account.nickname,
    broker: account.broker_name,
    server: account.server,
    login: account.investor_login,
    password: account.investor_password,
    balance: account.latest_balance || '',
  })));
  fillSelect('clientWithdrawalGroupSelect', groups.map((group) => `<option value="${group.group_id}">${escapeHtml(group.group_name || group.group_id)} · available ${money(group.available_balance)}</option>`).join(''));
}

async function loadClientLedger() {
  const ledger = await api('/api/client/me/ledger');
  $('clientLedgerView').innerHTML = table(ledger);
}

async function downloadAuthedCsv(path, filename) {
  if (!state.token) return;
  const response = await fetch(path, { headers: { Authorization: `Bearer ${state.token}` } });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function submitForm(formId, resultId, handler) {
  const form = $(formId);
  const result = $(resultId);
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = formJson(form);
      const data = await handler(payload, form);
      show(result, data);
      if (state.user?.role === 'admin') await refreshLists();
      if (state.user?.role === 'client') await loadClientSelfDashboard();
    } catch (err) {
      show(result, `Error: ${err.message}`);
    }
  });
}


function importQueueRows() {
  return state.importClassifications.map((item, index) => {
    const client = state.clients.find((candidate) => candidate.client_id === item.client_id);
    const account = Object.values(state.mt5AccountsByGroup).flat().find((candidate) => candidate.account_id === item.movement.mt5_account_id);
    return {
      index,
      number: index + 1,
      amount: Number(item.movement.amount || 0),
      date: item.movement.occurred_on,
      classification: item.classification,
      client: client?.display_name || '',
      account: account?.nickname || '',
      comment: item.movement.comment || '',
      description: item.description || '',
    };
  });
}

function importQueueSummary() {
  const rows = importQueueRows();
  const total = rows.reduce((sum, row) => sum + row.amount, 0);
  const deposits = rows.filter((row) => row.amount > 0).reduce((sum, row) => sum + row.amount, 0);
  const withdrawals = rows.filter((row) => row.amount < 0).reduce((sum, row) => sum + Math.abs(row.amount), 0);
  const commissions = rows.filter((row) => row.classification.includes('commission')).reduce((sum, row) => sum + Math.abs(row.amount), 0);
  const transfers = rows.filter((row) => row.classification.includes('transfer')).reduce((sum, row) => sum + Math.abs(row.amount), 0);
  return { count: rows.length, total, deposits, withdrawals, commissions, transfers };
}

function renderImportQueue() {
  const target = $('importQueue');
  if (!target) return;
  const rows = importQueueRows();
  if (!rows.length) {
    target.innerHTML = `
      <div class="empty-state">
        <strong>No queued movements</strong>
        <p>Add an old MT5 deposit/withdrawal on the left, choose what it means, then review before finalizing.</p>
      </div>`;
    return;
  }
  const summary = importQueueSummary();
  target.innerHTML = `
    <div class="import-summary-grid">
      <div><span>Queued</span><strong>${summary.count}</strong></div>
      <div><span>Additions</span><strong>${money(summary.deposits)}</strong></div>
      <div><span>Withdrawals</span><strong>${money(summary.withdrawals)}</strong></div>
      <div><span>Commission</span><strong>${money(summary.commissions)}</strong></div>
      <div><span>Transfers</span><strong>${money(summary.transfers)}</strong></div>
    </div>
    <div class="import-queue-list">
      ${rows.map((row) => `
        <article class="import-queue-card ${row.amount < 0 ? 'negative' : 'positive'}">
          <div class="import-card-main">
            <span class="row-kicker">Movement ${row.number}</span>
            <strong>${row.amount < 0 ? '-' : '+'}${money(Math.abs(row.amount))}</strong>
            <small>${escapeHtml(row.date)}${row.comment ? ` · ${escapeHtml(row.comment)}` : ''}</small>
          </div>
          <div class="import-card-meta">
            <span class="classification-pill">${escapeHtml(classifyLabel(row.classification))}</span>
            <p>${escapeHtml(classificationEffect(row.classification))}</p>
            ${row.client ? `<small>Client: ${escapeHtml(row.client)}</small>` : ''}
            ${row.account ? `<small>MT5: ${escapeHtml(row.account)}</small>` : ''}
          </div>
          <div class="import-card-actions">
            <button type="button" class="secondary small-action" data-import-delete="${row.index}">Remove</button>
          </div>
        </article>`).join('')}
    </div>`;
}

function renderImportReviewResult(result) {
  const target = $('importWizardResult');
  if (!target) return;
  const lines = result.lines || [];
  target.classList.add('review-result-card');
  target.innerHTML = `
    <div class="review-header">
      <div>
        <span class="row-kicker">Import preview</span>
        <strong>${result.entry_count || 0} ledger entries will be created</strong>
        <p>This is a preview only. Nothing is saved until you finalize.</p>
      </div>
      <div class="review-total">${money(result.total_classified_amount || 0)}</div>
    </div>
    ${lines.length ? `<div class="review-line-list">${lines.map((line) => `
      <div class="review-line">
        <div><strong>${escapeHtml(classifyLabel(line.classification))}</strong><span>${escapeHtml(line.description || '')}</span></div>
        <div>${money(line.amount)}</div>
        <small>${line.generated_entry_count} entr${Number(line.generated_entry_count) === 1 ? 'y' : 'ies'}</small>
      </div>`).join('')}</div>` : '<p class="muted">No ledger entries will be created.</p>'}
    <details class="advanced-details">
      <summary>Advanced technical details</summary>
      <pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>
    </details>`;
}

function renderImportFinalizeResult(result) {
  renderImportReviewResult(result);
  const target = $('importWizardResult');
  if (!target) return;
  const header = target.querySelector('.review-header p');
  if (header) header.textContent = 'Import finalized. The ledger entries are now official.';
  toast('Import finalized.', 'success');
}

function renderAdminGroupDashboard(target, balances, closed, accounts) {
  if (!target) return;
  const members = balances.members || [];
  const totalCapital = members.reduce((sum, member) => sum + Number(member.effective_capital || 0), 0);
  const totalFinalized = members.reduce((sum, member) => sum + Number(member.finalized_balance ?? member.current_balance ?? 0), 0);
  const memberRows = members.map((member) => {
    const share = totalCapital > 0 ? (Number(member.effective_capital || 0) / totalCapital) * 100 : 0;
    return {
      name: member.display_name || shortId(member.client_id),
      role: friendlyRole(member.role),
      capital: Number(member.effective_capital || 0),
      share,
      finalized: Number(member.finalized_balance ?? member.current_balance ?? 0),
      available: Number(member.available_balance ?? member.current_balance ?? 0),
    };
  });
  target.innerHTML = `
    <div class="group-dashboard-premium">
      <div class="summary-grid compact-metrics">
        <div><span>MT5 closed balance</span><strong>${money(closed.closed_balance)}</strong></div>
        <div><span>Member capital base</span><strong>${money(totalCapital)}</strong></div>
        <div><span>Ledger finalized</span><strong>${money(totalFinalized)}</strong></div>
        <div><span>Members</span><strong>${members.length}</strong></div>
      </div>
      <section class="premium-subsection">
        <div class="subsection-title"><h4>Members</h4><span>Capital, share, and available balance</span></div>
        ${memberRows.length ? `<div class="premium-table-wrap"><table class="premium-table"><thead><tr><th>Name</th><th>Role</th><th>Capital base</th><th>Share</th><th>Finalized</th><th>Available</th></tr></thead><tbody>${memberRows.map((member) => `<tr><td><strong>${escapeHtml(member.name)}</strong></td><td>${escapeHtml(member.role)}</td><td>${money(member.capital)}</td><td>${percent(member.share)}</td><td>${money(member.finalized)}</td><td>${money(member.available)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state"><strong>No members yet</strong><p>Add clients to this group before reviewing balances.</p></div>'}
      </section>
      <section class="premium-subsection">
        <div class="subsection-title"><h4>Client-visible MT5 accounts</h4><span>Read-only information clients can see</span></div>
        ${accounts && accounts.length ? `<div class="mt5-account-list">${accounts.map((account) => `<article><strong>${escapeHtml(account.nickname || 'MT5 Account')}</strong><span>${escapeHtml(account.broker_name || '')} · ${escapeHtml(account.server || '')}</span><small>Login: ${escapeHtml(account.investor_login || 'Hidden')}</small></article>`).join('')}</div>` : '<div class="empty-state"><strong>No MT5 accounts connected yet</strong><p>Add an MT5 account from the MT5 section to start tracking this group.</p></div>'}
      </section>
      <details class="advanced-details">
        <summary>Advanced technical IDs</summary>
        ${table(members.map((member) => ({ membership_id: member.membership_id, group_id: member.group_id, client_id: member.client_id })))}
      </details>
    </div>`;
}

function importWizardPayload() {
  return {
    import_mode: $('importModeSelect')?.value || 'percentage_import',
    classifications: state.importClassifications,
  };
}

function normalizeOptional(value) {
  return value && String(value).trim() ? value : undefined;
}

async function runImportWizard(action) {
  const groupId = $('importGroupSelect')?.value;
  if (!groupId) throw new Error('Choose a group first.');
  if (state.importClassifications.length === 0) throw new Error('Add at least one detected movement.');
  return api(`/api/admin/groups/${groupId}/import-wizard/${action}`, {
    method: 'POST',
    body: JSON.stringify(importWizardPayload()),
  });
}


window.addEventListener('DOMContentLoaded', async () => {
  addPageOverlays();
  setupFloatingTooltips();
  setupThemeToggle();
  setupHelpIcons();
  setupCollapsibleAdminCards();
  $('depositForm').effective_date.value = today();
  $('expenseForm').effective_date.value = today();
  $('adminWithdrawalActionForm').effective_date.value = today();
  $('dailyCloseForm').broker_server_day.value = today();
  $('dailyCloseForm').previous_broker_server_day.value = today();
  $('snapshotForm').broker_server_time.value = nowLocalDateTime();
  $('snapshotForm').broker_server_day.value = today();
  if ($('importMovementForm')) $('importMovementForm').occurred_on.value = today();
  renderImportQueue();

  $('refreshButton').addEventListener('click', refreshLists);
  $('loadWorkflowInboxButton')?.addEventListener('click', async () => {
    try {
      await loadWorkflowInbox();
      show($('workflowResult'), 'Workflow inbox loaded.');
    } catch (err) {
      show($('workflowResult'), `Error: ${err.message}`);
    }
  });

  $('workflowInbox')?.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const groupId = $('workflowGroupSelect')?.value;
    if (!groupId) return;
    const action = button.dataset.action;
    const entryId = button.dataset.entryId;
    const transactionId = button.dataset.transactionId;
    try {
      let result;
      if (action === 'deposit-effective') {
        result = await api(`/api/admin/ledger/${entryId}/deposit/effective`, { method: 'POST', body: JSON.stringify({}) });
      } else if (action === 'withdrawal-approve') {
        const effectiveDate = prompt('Effective date for withdrawal?', today()) || today();
        result = await api(`/api/admin/ledger/${entryId}/withdrawal/approve`, { method: 'POST', body: JSON.stringify({ effective_date: effectiveDate }) });
      } else if (action === 'withdrawal-reject') {
        const reason = prompt('Reason for rejecting this withdrawal?', 'Rejected by admin');
        if (!reason) return;
        result = await api(`/api/admin/ledger/${entryId}/withdrawal/reject`, { method: 'POST', body: JSON.stringify({ reason }) });
      } else if (action === 'withdrawal-effective') {
        result = await api(`/api/admin/ledger/${entryId}/withdrawal/effective`, { method: 'POST', body: JSON.stringify({}) });
      } else if (action === 'withdrawal-paid') {
        result = await api(`/api/admin/ledger/${entryId}/withdrawal/paid`, { method: 'POST', body: JSON.stringify({}) });
      } else if (action === 'expense-effective') {
        result = await api(`/api/admin/groups/${groupId}/expenses/${transactionId}/effective`, { method: 'POST', body: JSON.stringify({}) });
      } else if (action === 'transfer-complete') {
        const select = $(`workflowInbox`).querySelector(`select[data-transfer-entry-id="${entryId}"]`);
        const toMt5AccountId = select?.value;
        if (!toMt5AccountId) throw new Error('Choose the destination MT5 account first.');
        result = await api(`/api/admin/ledger/${entryId}/internal-transfer/complete`, { method: 'POST', body: JSON.stringify({ to_mt5_account_id: toMt5AccountId }) });
      }
      show($('workflowResult'), result || 'Done');
      await refreshLists();
      await loadWorkflowInbox();
    } catch (err) {
      show($('workflowResult'), `Error: ${err.message}`);
    }
  });
  $('logoutButton').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) }); } catch (_err) {}
    localStorage.removeItem('mt5PortalToken');
    state.token = '';
    state.user = null;
    state.client = null;
    updatePanels();
  });

  await submitForm('setupAdminForm', 'setupAdminResult', (payload) => api('/api/setup/admin', { method: 'POST', body: JSON.stringify(payload) }));

  await submitForm('loginForm', 'loginResult', async (payload) => {
    const login = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) });
    state.token = login.access_token;
    localStorage.setItem('mt5PortalToken', state.token);
    state.user = login.user;
    show($('loginResult'), login);
    await loadSession();
    return login;
  });

  await submitForm('clientForm', 'clientResult', (payload) => api('/api/admin/clients', { method: 'POST', body: JSON.stringify(payload) }));
  await submitForm('groupForm', 'groupResult', (payload) => api('/api/admin/groups', { method: 'POST', body: JSON.stringify(payload) }));

  await submitForm('memberForm', 'memberResult', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    return api(`/api/admin/groups/${groupId}/members`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('depositForm', 'depositResult', async (payload) => {
    const groupId = payload.group_id;
    const makeEffective = payload.make_effective_now;
    delete payload.group_id;
    delete payload.make_effective_now;
    const pending = await api(`/api/admin/groups/${groupId}/deposits/pending`, { method: 'POST', body: JSON.stringify(payload) });
    if (makeEffective) {
      const effective = await api(`/api/admin/ledger/${pending.entry_id}/deposit/effective`, { method: 'POST', body: JSON.stringify({}) });
      return { pending, effective };
    }
    return pending;
  });

  await submitForm('importMovementForm', 'importMovementResult', async (payload) => {
    const movement = {
      movement_id: `manual-${Date.now()}-${state.importClassifications.length + 1}`,
      amount: payload.amount,
      occurred_on: payload.occurred_on,
      comment: payload.comment || '',
      mt5_account_id: normalizeOptional(payload.mt5_account_id),
      currency: 'USD',
    };
    const item = {
      movement,
      classification: payload.classification,
      client_id: normalizeOptional(payload.client_id),
      to_mt5_account_id: normalizeOptional(payload.to_mt5_account_id),
      description: normalizeOptional(payload.description),
    };
    state.importClassifications.push(item);
    renderImportQueue();
    return { queued: state.importClassifications.length, item };
  });


  await submitForm('adminWithdrawalActionForm', 'adminWithdrawalActionResult', (payload) => {
    const entryId = payload.entry_id;
    const action = payload.action;
    delete payload.entry_id;
    delete payload.action;
    if (action === 'approve') {
      return api(`/api/admin/ledger/${entryId}/withdrawal/approve`, { method: 'POST', body: JSON.stringify({ effective_date: payload.effective_date || today() }) });
    }
    if (action === 'reject') {
      return api(`/api/admin/ledger/${entryId}/withdrawal/reject`, { method: 'POST', body: JSON.stringify({ reason: payload.reason || 'Rejected by admin' }) });
    }
    if (action === 'effective') {
      return api(`/api/admin/ledger/${entryId}/withdrawal/effective`, { method: 'POST', body: JSON.stringify({}) });
    }
    return api(`/api/admin/ledger/${entryId}/withdrawal/paid`, { method: 'POST', body: JSON.stringify({}) });
  });

  await submitForm('expenseForm', 'expenseResult', async (payload) => {
    const groupId = payload.group_id;
    const makeEffective = payload.make_effective_now;
    delete payload.group_id;
    delete payload.make_effective_now;
    const pendingEntries = await api(`/api/admin/groups/${groupId}/expenses/equal/pending`, { method: 'POST', body: JSON.stringify(payload) });
    if (makeEffective) {
      const effectiveEntries = [];
      for (const entry of pendingEntries) {
        effectiveEntries.push(await api(`/api/admin/ledger/${entry.entry_id}/expense/effective`, { method: 'POST', body: JSON.stringify({}) }));
      }
      return { pending: pendingEntries, effective: effectiveEntries };
    }
    return pendingEntries;
  });

  await submitForm('internalTransferForm', 'internalTransferResult', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    return api(`/api/admin/groups/${groupId}/internal-transfers/pending`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('completeTransferForm', 'completeTransferResult', (payload) => {
    const entryId = payload.entry_id;
    delete payload.entry_id;
    return api(`/api/admin/ledger/${entryId}/internal-transfer/complete`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('dailyCloseForm', 'dailyCloseResult', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    if (!payload.manual_profit_loss) delete payload.manual_profit_loss;
    if (!payload.override_reason) delete payload.override_reason;
    return api(`/api/admin/groups/${groupId}/daily-close/finalize`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('commissionWithdrawalForm', 'commissionWithdrawalResult', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    if (!payload.client_id) delete payload.client_id;
    return api(`/api/admin/groups/${groupId}/commission/withdrawals`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('manualAdjustmentForm', 'manualAdjustmentResult', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    return api(`/api/admin/groups/${groupId}/manual-adjustments`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('mt5Form', 'mt5Result', (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    return api(`/api/admin/groups/${groupId}/mt5-accounts`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('snapshotForm', 'snapshotResult', (payload) => {
    const accountId = payload.account_id;
    delete payload.account_id;
    payload.broker_server_time = new Date(payload.broker_server_time).toISOString();
    payload.raw_margin = '0';
    payload.raw_free_margin = '0';
    return api(`/api/admin/mt5-accounts/${accountId}/snapshots`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('clientWithdrawalForm', 'clientWithdrawalResult', async (payload) => {
    const groupId = payload.group_id;
    delete payload.group_id;
    payload.client_id = state.client.client_id;
    return api(`/api/client/groups/${groupId}/withdrawals/request`, { method: 'POST', body: JSON.stringify(payload) });
  });

  await submitForm('clientProfileForm', 'clientProfileResult', async (payload) => {
    const profile = await api('/api/client/me/profile', { method: 'PATCH', body: JSON.stringify(payload) });
    state.clientProfile = profile;
    state.client = profile.client;
    renderClientProfile(profile);
    return profile;
  });

  await submitForm('clientPasswordForm', 'clientPasswordResult', (payload) => (
    api('/api/client/me/password', { method: 'POST', body: JSON.stringify(payload) })
  ));

  await submitForm('client2faForm', 'client2faResult', async (payload) => {
    const result = await api('/api/client/me/2fa', { method: 'POST', body: JSON.stringify(payload) });
    await loadClientSelfDashboard();
    return result;
  });

  await submitForm('passwordResetRequestForm', 'passwordResetRequestResult', (payload) => (
    api('/api/auth/password-reset/request', { method: 'POST', body: JSON.stringify(payload) })
  ));

  await submitForm('passwordResetConfirmForm', 'passwordResetConfirmResult', (payload) => (
    api('/api/auth/password-reset/confirm', { method: 'POST', body: JSON.stringify(payload) })
  ));

  await submitForm('adminClientPasswordResetForm', 'adminClientSecurityResult', async (payload) => {
    const clientId = payload.client_id;
    delete payload.client_id;
    return api(`/api/admin/clients/${clientId}/password-reset`, { method: 'POST', body: JSON.stringify(payload) });
  });

  $('adminResetClient2faButton')?.addEventListener('click', async () => {
    const clientId = $('securityClientSelect')?.value;
    if (!clientId) return;
    try {
      const result = await api(`/api/admin/clients/${clientId}/2fa/reset`, { method: 'POST', body: JSON.stringify({}) });
      show($('adminClientSecurityResult'), result);
    } catch (err) {
      show($('adminClientSecurityResult'), `Error: ${err.message}`);
    }
  });


  $('reviewImportButton')?.addEventListener('click', async () => {
    try {
      const result = await runImportWizard('review');
      renderImportReviewResult(result);
    } catch (err) {
      show($('importWizardResult'), `Error: ${err.message}`);
    }
  });

  $('finalizeImportButton')?.addEventListener('click', async () => {
    try {
      const result = await runImportWizard('finalize');
      state.importClassifications = [];
      renderImportQueue();
      renderImportFinalizeResult(result);
      await refreshLists();
    } catch (err) {
      show($('importWizardResult'), `Error: ${err.message}`);
    }
  });

  $('clearImportButton')?.addEventListener('click', () => {
    state.importClassifications = [];
    renderImportQueue();
    const result = $('importWizardResult');
    if (result) { result.classList.remove('review-result-card'); result.textContent = 'Import review list cleared.'; }
    toast('Import list cleared.', 'info');
  });

  $('importQueue')?.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-import-delete]');
    if (!removeButton) return;
    const index = Number(removeButton.dataset.importDelete);
    if (Number.isInteger(index)) {
      state.importClassifications.splice(index, 1);
      renderImportQueue();
      const result = $('importWizardResult');
      if (result) { result.classList.remove('review-result-card'); result.textContent = 'Movement removed. Review again before finalizing.'; }
      toast('Movement removed.', 'info');
    }
  });

  $('loadLedgerButton').addEventListener('click', async () => {
    const groupId = $('ledgerGroupSelect').value;
    if (!groupId) return;
    const ledger = await api(`/api/admin/groups/${groupId}/ledger`);
    $('ledgerView').innerHTML = table(ledger);
  });

  $('loadDailyClosesButton').addEventListener('click', async () => {
    const groupId = $('ledgerGroupSelect').value;
    if (!groupId) return;
    const closes = await api(`/api/admin/groups/${groupId}/daily-closes`);
    $('ledgerView').innerHTML = table(closes);
  });

  $('downloadGroupLedgerButton')?.addEventListener('click', async () => {
    const groupId = $('ledgerGroupSelect').value;
    if (!groupId) return;
    try { await downloadAuthedCsv(`/api/admin/groups/${groupId}/ledger/export.csv`, 'group_ledger.csv'); }
    catch (err) { $('ledgerView').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`; }
  });

  $('loadClientLedgerButton')?.addEventListener('click', async () => {
    try { await loadClientLedger(); } catch (err) { $('clientLedgerView').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`; }
  });

  $('downloadClientLedgerButton')?.addEventListener('click', async () => {
    try { await downloadAuthedCsv('/api/client/me/ledger/export.csv', 'my_transaction_history.csv'); }
    catch (err) { $('clientLedgerView').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`; }
  });

  $('loadExternalCommission').addEventListener('click', async () => {
    const groupId = $('commissionGroupSelect').value;
    if (!groupId) return;
    const payable = await api(`/api/admin/groups/${groupId}/commission/external/payable`);
    show($('commissionWithdrawalResult'), payable);
  });

  $('loadClientDashboard').addEventListener('click', async () => {
    const clientId = $('dashboardClientSelect').value;
    if (!clientId) return;
    const dashboard = await api(`/api/admin/clients/${clientId}/dashboard?reason=admin-dashboard-check`);
    renderClientDashboard($('clientDashboard'), dashboard, { adminView: true });
  });

  $('loadGroupDashboard').addEventListener('click', async () => {
    const groupId = $('dashboardGroupSelect').value;
    if (!groupId) return;
    const balances = await api(`/api/admin/groups/${groupId}/balances`);
    const closed = await api(`/api/admin/groups/${groupId}/mt5-closed-balance`);
    const accounts = await api(`/api/admin/groups/${groupId}/mt5-client-view`);
    $('groupDashboard').innerHTML = `<p><strong>MT5 closed balance:</strong> $${closed.closed_balance}</p><h4>Members</h4>${table(balances.members)}<h4>Client-visible MT5 accounts</h4>${table(accounts)}`;
  });

  $('loadAuditEventsButton')?.addEventListener('click', async () => {
    const clientId = $('securityClientSelect')?.value;
    const path = clientId ? `/api/admin/audit-events?target_client_id=${clientId}` : '/api/admin/audit-events';
    try {
      const events = await api(path);
      $('auditEventsView').innerHTML = table(events);
    } catch (err) {
      $('auditEventsView').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    }
  });

  await loadSession();
});
