const cleanerReturnDrafts = new Map();

function cleanerReturnPanel(issue, message) {
  const panel = node('section', '', 'manager-purchase');
  panel.append(node('h2', 'Solution'), node('p', 'Cleaner return', 'eyebrow'));
  const scheduler = dataset.cleaner_scheduler || {};
  const visit = issue.cleaner_return;
  if (scheduler.dashboard_url) {
    const link = node('a', 'Open cleaner scheduling ↗', 'amazon-link');
    link.href = scheduler.dashboard_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    panel.append(link);
  }
  if (issue.cleaner_return_escalate) {
    panel.append(node('p', 'Manager inspection needed', 'purchase-status'), node('p', 'This message includes a pest, suspected mold, or other issue requiring review before arranging a routine cleaning visit.', 'help-text'));
    return panel;
  }
  const another = cleanerReturnDrafts.get(issue.id)?.new_visit === true;
  if (visit && visit.state !== 'draft' && !another) {
    const statuses = {queued: 'Sending to scheduler', uncertain: 'Checking whether the scheduler received this request', blocked: 'Scheduling needs attention', submitted: 'Awaiting schedule', completed: 'Cleaner marked the visit complete', cancelled: 'Visit cancelled'};
    const remote = visit.scheduler;
    panel.append(node('p', visit.plan.request.scope, 'purchase-summary'), node('p', visit.plan.manager_declined ? 'Return declined' : statuses[visit.state] || visit.state, 'purchase-status'));
    const labels = {awaiting_schedule: 'Finding a feasible visit', awaiting_approval: 'Proposed visit · your approval is needed', scheduled: 'Scheduled · awaiting cleaner acceptance', accepted: 'Cleaner accepted the visit', completed: 'Cleaner marked the visit complete', cancelled: 'Visit cancelled', needs_manager: 'Manager action needed'};
    if (remote) panel.append(node('p', labels[remote.status] || 'Check scheduling status', 'help-text'));
    const approvalPending = ['queued', 'uncertain'].includes(visit.plan.approval?.state);
    if (approvalPending) panel.append(node('p', visit.plan.approval.state === 'uncertain' ? 'Checking whether the schedule approval was recorded. Do not approve again yet.' : 'Checking and approving this schedule…', 'help-text'));
    if (remote?.proposal && remote.status === 'awaiting_approval') {
      const proposal = remote.proposal;
      panel.append(node('strong', proposal.cleaner_name || 'Proposed cleaner'), node('p', `${date(proposal.start_at)} – ${date(proposal.end_at)}`, 'purchase-summary'));
    }
    if (remote?.proposal_failure) panel.append(node('p', remote.proposal_failure, 'warning'));
    if (!scheduler.internal && visit.state === 'submitted' && ['awaiting_schedule', 'awaiting_approval', 'needs_manager'].includes(remote?.status) && !remote?.assignment) {
      const actions = node('div', '', 'primary-actions');
      const proposal = remote.proposal;
      const choices = [...(proposal ? [['approve-schedule', 'Approve proposed visit', {run_id: proposal.run_id, expected_version: proposal.version, expected_fingerprint: proposal.fingerprint, expected_day_revision: proposal.day_revision}]] : []), ['propose', 'Refresh proposal', {}]];
      for (const [action, label, data] of choices) {
        const button = node('button', label, action === 'propose' ? 'secondary' : '');
        button.type = 'button';
        button.disabled = approvalPending;
        button.onclick = async () => {
          for (const control of actions.querySelectorAll('button')) control.disabled = true;
          try { await submitAction(`/api/cleaner-returns/${action}`, {id: visit.id, version: visit.version, source_fingerprint: issue.cleaner_return_fingerprint, ...data}); await load(); }
          catch (error) { notice.textContent = error.message; for (const control of actions.querySelectorAll('button')) control.disabled = approvalPending; }
        };
        actions.append(button);
      }
      panel.append(actions);
    }
    if (remote?.assignment) {
      const assignment = remote.assignment;
      panel.append(node('strong', assignment.cleaner_name || 'Assigned cleaner'));
      if (assignment.start_at && assignment.end_at) panel.append(node('p', `${date(assignment.start_at)} – ${date(assignment.end_at)}`, 'help-text'));
    }
    if (remote?.assignment && remote.notice) {
      const notices = {none: 'Cleaner notification has not been queued.', disabled: 'Cleaner notifications are disabled. Contact the cleaner directly until connected.', queued: 'Cleaner notification queued · acceptance is still needed.', sending: 'Sending the cleaner notification · acceptance is still needed.', sent: 'Cleaner notification sent.', failed: 'Cleaner notification failed · manager follow-up needed.', unknown: 'Notification outcome unknown · check before resending.'};
      panel.append(node('p', notices[remote.notice.state] || 'Check cleaner notification status.', 'help-text'));
      if (remote.notice.acceptance_overdue) panel.append(node('p', 'The cleaner has not accepted in time. Follow up or review another option; no automatic reassignment has occurred.', 'warning'));
      else if (remote.notice.manager_action_required) panel.append(node('p', 'This visit needs manager follow-up.', 'warning'));
      if (remote.status === 'scheduled' && !scheduler.internal && ['none', 'disabled', 'failed'].includes(remote.notice.state)) {
        const notify = node('button', 'Retry cleaner notice', 'secondary');
        notify.type = 'button';
        notify.disabled = ['queued', 'uncertain'].includes(visit.plan.notice_retry?.state);
        notify.onclick = async () => {
          notify.disabled = true;
          try { await submitAction('/api/cleaner-returns/notify', {id: visit.id, version: visit.version, source_fingerprint: issue.cleaner_return_fingerprint}); await load(); }
          catch (error) { notice.textContent = error.message; notify.disabled = false; }
        };
        panel.append(notify);
      }
    }
    if (visit.error) panel.append(node('p', visit.error, 'warning'));
    if (visit.guest_context_changed && visit.plan.confirmation_fingerprint !== issue.cleaner_return_fingerprint) panel.append(node('p', 'The guest has sent another message since this request was prepared. Review their latest needs and entry permission in the scheduling bot.', 'warning'));
    if (visit.state === 'blocked') {
      const retry = node('button', 'Retry same scheduling request', 'secondary');
      retry.type = 'button';
      retry.onclick = async () => {
        retry.disabled = true;
        try { await submitAction('/api/cleaner-returns/retry', {id: visit.id, version: visit.version}); await load(); }
        catch (error) { notice.textContent = error.message; retry.disabled = false; }
      };
      panel.append(retry);
    }
    panel.append(node('p', 'Guest messages are not sent by this flow. Confirm the cleaner’s acceptance before promising an arrival time.', 'help-text'));
    if (visit.state === 'completed') {
      const button = node('button', 'Guest confirmed it is fixed', 'secondary');
      button.type = 'button';
      button.onclick = async () => {
        button.disabled = true;
        try { await submitAction('/api/cleaner-returns/confirm', {id: visit.id, version: visit.version}); await load(); }
        catch (error) { notice.textContent = error.message; button.disabled = false; }
      };
      if (visit.plan.guest_confirmed_at) panel.append(node('p', 'Guest confirmation recorded.', 'help-text'));
      else panel.append(button);
    }
    if (['completed', 'cancelled'].includes(visit.state)) {
      const anotherVisit = node('button', visit.plan.manager_declined ? 'Reconsider' : 'Arrange another visit', 'secondary');
      anotherVisit.type = 'button';
      anotherVisit.onclick = () => { cleanerReturnDrafts.set(issue.id, {...issue.cleaner_return_proposal, new_visit: true}); render(); };
      panel.append(anotherVisit);
    }
    return panel;
  }
  const inferred = issue.cleaner_return_proposal;
  if (inferred.status === 'not_needed' && !another) {
    panel.append(node('strong', 'No cleaner return needed'), node('p', 'The guest’s follow-up says it is already handled or they do not want a visit.', 'help-text'));
    const followup = issue.messages.find(item => item.id === inferred.evidence[0]);
    if (followup) panel.append(node('blockquote', followup.body));
    return panel;
  }
  if (visit?.error) panel.append(node('p', visit.error, 'warning'));
  if (!scheduler.configured) panel.append(node('p', 'Scheduling is not connected. Confirming saves a plan only.', 'help-text'));
  const summary = node('p', '', 'purchase-summary');
  const estimate = node('p', '', 'help-text');
  const timing = node('p', '', 'help-text');
  panel.append(summary, estimate, timing, node('p', 'Cleaner: scheduler will assign around existing jobs.', 'help-text'));
  const missing = node('section');
  const question = node('blockquote');
  const ask = node('button', 'Review guest question', 'secondary');
  ask.type = 'button';
  const base = issue.guest_replies.find(reply => reply.message_id === message.id);
  ask.disabled = !base || Boolean(base.send && !['blocked', 'failed'].includes(base.send.state));
  ask.onclick = () => {
    guestReplyDrafts.set(issue.id + ':' + message.id, {...base, body: question.textContent, source_fingerprint: base.source_fingerprint});
    render();
    const replies = document.querySelector('#issues .guest-reply-panel');
    replies.closest('.manager-advanced').open = true;
    replies.open = true;
    replies.scrollIntoView({behavior: 'smooth', block: 'center'});
  };
  missing.append(node('p', 'Ask the guest', 'eyebrow'), question, ask);
  if (ask.disabled) missing.append(node('p', 'An earlier reply is already queued or sent. Continue this conversation in Hospitable.', 'help-text'));
  panel.append(missing);
  const actions = node('div', '', 'primary-actions');
  const confirm = node('button', scheduler.configured && issue.lane === 'needs_attention' ? 'Confirm return' : 'Confirm plan');
  confirm.type = 'button';
  const deny = node('button', 'Deny', 'secondary');
  deny.type = 'button';
  deny.onclick = async () => {
    deny.disabled = true;
    try { await submitAction('/api/cleaner-returns/decline', {issue_id: issue.id, version: visit?.version || 0, source_fingerprint: issue.cleaner_return_fingerprint}); cleanerReturnDrafts.delete(issue.id); await load(); }
    catch (error) { notice.textContent = error.message; deny.disabled = false; }
  };
  actions.append(confirm, deny);
  panel.append(actions);
  const edit = node('details');
  edit.append(node('summary', 'Edit'));
  const form = node('form', '', 'purchase-form');
  const saved = cleanerReturnDrafts.get(issue.id) || (visit?.state === 'draft' && !visit.guest_context_changed ? {kind: visit.plan.kind, ...visit.plan.request} : inferred);
  const typeLabel = node('label', 'What needs fixing?');
  const type = node('select');
  type.name = 'kind';
  for (const [value, text] of [['linens', 'Fresh linens and bed remake'], ['targeted', 'Targeted cleaning'], ['turnover', 'Missed turnover']]) {
    const option = node('option', text);
    option.value = value;
    type.append(option);
  }
  type.value = saved.kind;
  typeLabel.append(type);
  form.append(typeLabel);
  const scopeLabel = node('label', 'Areas and tasks');
  const scope = node('textarea');
  scope.name = 'scope';
  scope.required = true;
  scope.maxLength = 1000;
  scope.placeholder = 'For example: clean the bathroom floor and tub; replace used towels.';
  scope.value = saved.scope;
  scopeLabel.append(scope);
  form.append(scopeLabel);
  for (const [name, title, inputType] of [['duration_minutes', 'Estimated cleaning time (minutes)', 'number'], ['release_at', 'Guest permits entry from', 'datetime-local'], ['deadline_at', 'Finish by', 'datetime-local']]) {
    const label = node('label', title);
    const input = node('input');
    input.name = name;
    input.type = inputType;
    input.required = true;
    if (inputType === 'number') { input.min = 15; input.max = 240; input.step = 5; input.value = saved[name]; }
    else if (saved[name]) {
      const value = new Date(saved[name]);
      input.value = new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    label.append(input);
    form.append(label);
  }
  form.append(node('p', `Times use your device’s timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}.`, 'help-text'));
  const consentLabel = node('label', '', 'checkbox-label');
  const consent = node('input');
  consent.type = 'checkbox';
  consent.name = 'guest_access_confirmed';
  consent.checked = saved.guest_access_confirmed;
  consentLabel.append(consent, node('span', 'The guest agreed to entry during this window.'));
  const save = node('button', 'Save plan', 'secondary');
  save.type = 'submit';
  save.value = 'draft';
  const submit = node('button', 'Send to scheduling bot');
  submit.type = 'submit';
  submit.value = 'send';
  submit.disabled = !scheduler.configured || issue.lane !== 'needs_attention';
  form.append(consentLabel, save, submit);
  const update = () => {
    const start = form.elements.release_at.value;
    const end = form.elements.deadline_at.value;
    const hasWindow = Boolean(start && end && new Date(start).getTime() > Date.now() && new Date(end) > new Date(start) && new Date(end).getTime() <= Date.now() + 48 * 3600000 && new Date(end) - new Date(start) >= Number(form.elements.duration_minutes.value) * 60000);
    const ready = hasWindow && consent.checked;
    summary.textContent = scope.value;
    estimate.textContent = `Estimated visit: ${form.elements.duration_minutes.value} minutes`;
    const displayTimezone = inferred.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const format = value => new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short', timeZone: displayTimezone}).format(new Date(value));
    timing.textContent = hasWindow ? `Guest window: ${format(start)} – ${format(end)} (${displayTimezone})` : 'Visit window: waiting for the guest';
    confirm.disabled = !ready;
    missing.hidden = ready;
    question.textContent = !hasWindow ? (inferred.question || (consent.checked ? 'What day and time window would work for the cleaner to return?' : "I'm sorry about the cleaning issue. May we arrange for a cleaner to return? What day and time window would work for you, and may they enter during that window?")) : 'May the cleaner enter during the time window you mentioned?';
  };
  confirm.onclick = () => form.requestSubmit(scheduler.configured && issue.lane === 'needs_attention' ? submit : save);
  form.oninput = event => {
    if (['release_at', 'deadline_at'].includes(event?.target.name)) consent.checked = false;
    const values = Object.fromEntries(new FormData(form));
    cleanerReturnDrafts.set(issue.id, {...values, new_visit: another, guest_access_confirmed: consent.checked});
    update();
  };
  type.onchange = () => { form.elements.duration_minutes.value = {linens: 30, targeted: 45, turnover: 120}[type.value]; form.oninput(); };
  form.onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const data = {...values, issue_id: issue.id, new_visit: another, duration_minutes: Number(values.duration_minutes), release_at: new Date(values.release_at).toISOString(), deadline_at: new Date(values.deadline_at).toISOString(), guest_access_confirmed: consent.checked, submit: event.submitter?.value === 'send', version: visit?.version || 0, source_fingerprint: issue.cleaner_return_fingerprint};
    for (const control of form.elements) control.disabled = true;
    try { await submitAction('/api/cleaner-returns', data); cleanerReturnDrafts.delete(issue.id); await load(); }
    catch (error) { notice.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; submit.disabled = !scheduler.configured || issue.lane !== 'needs_attention'; }
  };
  edit.append(form);
  panel.append(edit);
  update();
  return panel;
}

const schedulerForm = document.querySelector('#cleaner-scheduler-settings');
managerSession.then(session => { document.querySelector('#cleaner-scheduler-panel').hidden = session.role !== 'admin' || Boolean(dataset.cleaner_scheduler?.internal); });
function renderInternalScheduler(scheduler) {
  if (!scheduler?.internal) return;
  document.querySelector('#cleaner-scheduler-panel').hidden = true;
}
document.querySelector('#cleaner-scheduler-panel').addEventListener('toggle', event => {
  if (!event.target.open) return;
  schedulerForm.elements.url.value = dataset.cleaner_scheduler?.url || '';
  schedulerForm.elements.dashboard_url.value = dataset.cleaner_scheduler?.dashboard_url || '';
  schedulerForm.elements.token.placeholder = dataset.cleaner_scheduler?.stay_configured || dataset.cleaner_scheduler?.configured ? 'Configured — leave blank to keep' : 'Create a connection token in scheduler account settings';
});
schedulerForm.onsubmit = async event => {
  event.preventDefault();
  const button = schedulerForm.querySelector('button');
  button.disabled = true;
  try {
    const result = await submitAction('/api/settings/cleaner-scheduler', Object.fromEntries(new FormData(schedulerForm)));
    schedulerForm.elements.token.value = '';
    document.querySelector('#cleaner-scheduler-feedback').textContent = result.stay_configured ? 'Connected. Cleaning readiness and checkout requests can now be checked. Guest replies still require approval.' : 'Add a Guest Messages connection token to connect.';
    await load();
  } catch (error) { document.querySelector('#cleaner-scheduler-feedback').textContent = error.message; }
  finally { button.disabled = false; }
};
