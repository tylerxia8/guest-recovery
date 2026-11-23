const guestReplyDrafts = new Map();
function guestReplyPanel(issue, message) {
  const base = issue.guest_replies.find(reply => reply.message_id === message.id);
  const key = issue.id + ':' + message.id;
  const draft = guestReplyDrafts.get(key) || {...base, source_fingerprint: base.draft_source_fingerprint};
  const section = node('details', '', 'guest-reply-panel');
  section.open = guestReplyDrafts.has(key);
  section.append(node('summary', 'Guest reply'));
  section.append(node('p', base.practice ? 'Practice draft for a historical or unverified stay. Nothing is sent.' : 'Save your draft, then approve this exact reply to send it through Hospitable.', 'help-text'));
  if (base.safety) section.append(node('p', 'Urgent safety issue. New live reports trigger a manager email and emergency instructions. Check the Solution card for sending status.', 'warning'));
  if (base.needs_manager) section.append(node('p', base.suggested ? 'Add the missing details before sending this draft.' : 'No matching manager answer found. Write your reply below.', 'warning'));
  if (base.stale || draft.source_fingerprint !== base.source_fingerprint) section.append(node('p', 'The conversation changed since this draft was prepared. Review the full conversation before saving.', 'warning'));
  const form = node('form');
  const label = node('label', 'Reply draft');
  const body = document.createElement('textarea');
  body.rows = 5;
  body.maxLength = 3000;
  body.required = true;
  body.placeholder = 'Write the property manager’s answer here.';
  body.value = draft.body;
  body.oninput = () => { guestReplyDrafts.set(key, {...draft, body: body.value}); send.disabled = true; approved.checked = false; };
  label.append(body);
  const save = node('button', 'Save draft');
  const reset = node('button', 'Use suggested wording', 'secondary');
  reset.type = 'button';
  reset.onclick = () => { send.disabled = true; approved.checked = false; body.value = base.suggested; guestReplyDrafts.set(key, {...base, body: body.value}); draft.body = body.value; draft.version = base.version; draft.source_fingerprint = base.source_fingerprint; };
  const feedback = node('p', base.send ? 'Draft saved · see send status below' : base.updated_at ? 'Saved · not sent' : 'Not saved · not sent', 'save-status');
  feedback.setAttribute('role', 'status');
  const actions = node('div', '', 'primary-actions');
  actions.append(save, reset);
  form.append(label, actions, feedback);
  form.onsubmit = async event => {
    event.preventDefault();
    save.disabled = true;
    reset.disabled = true;
    body.disabled = true;
    try {
      await submitAction('/api/guest-replies', {issue_id: issue.id, message_id: message.id, body: body.value, version: draft.version, source_fingerprint: draft.source_fingerprint});
      guestReplyDrafts.delete(key);
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Guest reply draft saved. Nothing was sent.';
    } catch (error) { feedback.textContent = error.message; }
    finally { save.disabled = false; reset.disabled = false; body.disabled = false; }
  };
  const approval = node('label', '', 'checkbox-label');
  const approved = document.createElement('input');
  approved.type = 'checkbox';
  approval.append(approved, node('span', 'I approve this exact saved reply for this guest.'));
  const send = node('button', 'Approve & send', 'secondary');
  send.type = 'button';
  const locked = base.send && !['blocked', 'failed'].includes(base.send.state);
  const ready = base.sending_enabled && base.updated_at && !base.stale && draft.version === base.version && draft.source_fingerprint === base.source_fingerprint && body.value === base.body && !locked;
  send.disabled = true;
  approved.disabled = !ready;
  approved.onchange = () => { send.disabled = !ready || !approved.checked || body.value !== base.body; };
  send.onclick = async () => {
    send.disabled = true;
    approved.disabled = true;
    body.disabled = true;
    save.disabled = true;
    reset.disabled = true;
    try {
      await submitAction('/api/guest-replies/send', {issue_id: issue.id, message_id: message.id, body: body.value, version: draft.version, source_fingerprint: draft.source_fingerprint, approved: approved.checked});
      guestReplyDrafts.delete(key);
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Reply approved. Checking the live Hospitable conversation before sending.';
    } catch (error) { feedback.textContent = error.message; }
    finally { approved.disabled = !ready; body.disabled = locked; save.disabled = locked; reset.disabled = locked; }
  };
  if (locked) { body.disabled = true; save.disabled = true; reset.disabled = true; }
  section.append(form);
  if (base.sending_enabled && !locked) section.append(approval, send);
  if (!base.sending_enabled && !base.practice) section.append(node('p', 'Live sending needs a fresh Hospitable import, one matched property, and a configured access token.', 'help-text'));
  if (base.send) {
    const labels = {queued: 'Approved · waiting for live conversation check', attempting: 'Sending · do not send again', accepted: 'Hospitable accepted the message · guest delivery is not verified', blocked: 'Not sent · review and approve again', failed: 'Hospitable rejected the send · review before approving again', unknown: 'Send outcome unknown · check Hospitable before replying again'};
    section.append(node('p', labels[base.send.state], base.send.state === 'unknown' ? 'warning' : 'help-text'));
    if (base.send.error) section.append(node('p', base.send.error, 'help-text'));
    const approvedBody = node('blockquote', base.send.body);
    section.append(node('p', 'Approved wording', 'help-text'), approvedBody);
  }
  return section;
}
