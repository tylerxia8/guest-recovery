function propertyInstructionPanel(issue, message) {
  const panel = node('section', '', 'manager-purchase');
  const types = problemTypes(issue, message);
  const subtitle = document.querySelector('#category').value === 'Check-in & checkout' || types.length === 1 && types[0] === 'Check-in & checkout' ? 'Check-in & checkout' : 'Property instructions';
  panel.append(node('h2', 'Solution'), node('p', subtitle, 'eyebrow'));
  const solution = issue.instruction_solutions.find(item => item.message_id === message.id);
  if (!solution) { panel.append(node('p', 'Review the question and confirm the property-specific instructions.', 'help-text')); return panel; }
  const base = issue.guest_replies.find(reply => reply.message_id === message.id);
  const locked = !base || base.send && !['blocked', 'failed'].includes(base.send.state);
  for (const answer of solution.answers) {
    panel.append(node('blockquote', answer.body));
    const source = node('details');
    source.append(node('summary', `${answer.source} · ${date(answer.source_created_at)}`));
    if (answer.previous_question) source.append(node('p', 'Previous guest question', 'help-text'), node('blockquote', answer.previous_question));
    panel.append(source);
  }
  if (solution.stay_requests?.length) {
    const states = solution.stay_requests.map(item => item.reason === 'policy_information' ? 'Stay policy' : item.decision === 'approved' ? item.reason === 'already_covered' ? 'Existing checkout time preserved' : 'Cleaning schedule updated' : item.decision === 'denied' ? 'Request denied under the stay policy' : 'Waiting for cleaning readiness or schedule confirmation');
    panel.append(node('p', [...new Set(states)].join(' · '), 'help-text'));
  }
  const savedReply = base?.updated_at && !base.stale;
  if (savedReply && !solution.body) panel.append(node('p', 'Manager answer saved · ready for reply review.', 'help-text'));
  else for (const missing of solution.missing) panel.append(node('p', missing, 'warning'));
  for (const library of solution.library) {
    const details = node('details');
    details.open = false;
    details.append(node('summary', library.approved ? 'Edit the saved answer' : 'Edit reusable answer · ' + library.question));
    const form = node('form');
    const label = node('label', 'Reusable answer for this property');
    const body = node('textarea');
    body.value = library.approved?.body || '';
    body.placeholder = 'Write the general instructions, without guest names or one-stay exceptions.';
    body.required = true;
    body.maxLength = 2000;
    label.append(body);
    if (library.candidates.length) {
      details.append(node('p', 'Past manager replies at this exact property. Review that the details still apply.', 'help-text'));
      for (const candidate of library.candidates) {
        const choose = node('button', 'Use this example', 'secondary');
        choose.type = 'button';
        choose.onclick = () => { body.value = candidate.body; approved.checked = false; body.focus(); };
        details.append(node('blockquote', candidate.body), node('p', `${candidate.support_count} matching host replies · latest ${date(candidate.latest_at)}`, 'help-text'), choose);
        if (solution.library.length === 1) {
          const review = node('button', 'Review this reply', 'secondary');
          review.type = 'button';
          review.disabled = Boolean(locked);
          review.onclick = () => {
            guestReplyDrafts.set(issue.id + ':' + message.id, {...base, body: candidate.body, source_fingerprint: base.source_fingerprint});
            render();
            const replies = document.querySelector('#issues .guest-reply-panel');
            replies.closest('.manager-advanced').open = true;
            replies.open = true;
            replies.scrollIntoView({behavior: 'smooth', block: 'center'});
          };
          details.append(review);
        }
      }
    }
    const consent = node('label', '', 'checkbox-label');
    const approved = node('input');
    approved.type = 'checkbox';
    approved.required = true;
    consent.append(approved, node('span', 'These instructions are current and apply to similar questions at this property.'));
    body.oninput = () => { approved.checked = false; };
    const save = node('button', 'Save answer for this property');
    form.append(label, consent, save);
    form.onsubmit = async event => {
      event.preventDefault();
      save.disabled = true;
      try {
        await submitAction('/api/property-instructions', {issue_id: issue.id, message_id: message.id, intent: library.intent, body: body.value, approved: approved.checked, version: library.approved?.version || 0, source_fingerprint: issue.instruction_fingerprint});
        document.activeElement?.blur();
        await load();
        notice.textContent = 'Answer saved for similar questions at this property. Nothing was sent.';
      } catch (error) { notice.textContent = error.message; }
      finally { save.disabled = false; }
    };
    details.append(form);
    panel.append(details);
  }
  const reply = node('button', savedReply ? 'Review saved reply' : solution.body ? solution.missing.length ? 'Complete reply' : 'Review reply' : 'Write the answer', 'secondary');
  reply.type = 'button';
  reply.disabled = Boolean(locked);
  reply.onclick = () => {
    if (solution.body && !savedReply) guestReplyDrafts.set(issue.id + ':' + message.id, {...base, body: solution.body, source_fingerprint: base.source_fingerprint});
    render();
    const details = document.querySelector('#issues .guest-reply-panel');
    details.closest('.manager-advanced').open = true;
    details.open = true;
    details.scrollIntoView({behavior: 'smooth', block: 'center'});
  };
  panel.append(reply, node('p', locked ? 'A reply is already queued or sent. Continue the conversation in Hospitable.' : 'Review this answer before sending. Rule-confirmed stay changes may be sent automatically.', 'help-text'));
  return panel;
}
