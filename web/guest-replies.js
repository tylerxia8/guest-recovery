const guestReplyDrafts = new Map();
function guestReplyPanel(issue, message) {
  const base = issue.guest_replies.find(reply => reply.message_id === message.id);
  const key = issue.id + ':' + message.id;
  const draft = guestReplyDrafts.get(key) || {...base, source_fingerprint: base.draft_source_fingerprint};
  const section = node('details', '', 'guest-reply-panel');
  section.open = guestReplyDrafts.has(key);
  section.append(node('summary', 'Guest reply · draft only'));
  section.append(node('p', base.practice ? 'Practice draft for a historical or unverified stay. Nothing is sent.' : 'Review and save a reply. Sending to Hospitable is disabled.', 'help-text'));
  if (base.safety) section.append(node('p', 'Urgent safety issue: contact the manager immediately. A saved draft does not notify anyone.', 'warning'));
  if (base.stale || draft.source_fingerprint !== base.source_fingerprint) section.append(node('p', 'The conversation changed since this draft was prepared. Review the full conversation before saving.', 'warning'));
  const form = node('form');
  const label = node('label', 'Reply draft');
  const body = document.createElement('textarea');
  body.rows = 5;
  body.maxLength = 3000;
  body.required = true;
  body.value = draft.body;
  body.oninput = () => guestReplyDrafts.set(key, {...draft, body: body.value});
  label.append(body);
  const save = node('button', 'Save draft');
  const reset = node('button', 'Use suggested wording', 'secondary');
  reset.type = 'button';
  reset.onclick = () => { body.value = base.suggested; guestReplyDrafts.set(key, {...base, body: body.value}); draft.body = body.value; draft.version = base.version; draft.source_fingerprint = base.source_fingerprint; };
  const feedback = node('p', base.updated_at ? 'Saved · not sent' : 'Not saved · not sent', 'save-status');
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
  section.append(form);
  return section;
}
