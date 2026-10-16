// deweloperuch-mcp landing page: copy buttons and the demo conversation.
(() => {
  'use strict';

  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Enhance the always-visible HTML navigation only after its controls are wired.
  function initNavigation(navigation) {
    const toggle = navigation.querySelector('.nav-toggle');
    const mobile = matchMedia('(max-width: 760px)');
    const setExpanded = (open) => {
      toggle.setAttribute('aria-expanded', String(open));
      navigation.toggleAttribute('data-nav-open', open);
    };
    toggle.addEventListener('click', () => setExpanded(!navigation.hasAttribute('data-nav-open')));
    navigation.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && navigation.hasAttribute('data-nav-open')) {
        setExpanded(false);
        toggle.focus();
      }
    });
    navigation.addEventListener('click', (event) => {
      const link = event.target.closest('a');
      if (!link || !mobile.matches) return;
      setExpanded(false);
      if (link.closest('#site-navigation') || link.id === 'project-link') toggle.focus({ preventScroll: true });
    });
    mobile.addEventListener('change', () => {
      setExpanded(false);
      if (navigation.contains(document.activeElement)) {
        (mobile.matches ? toggle : navigation.querySelector('.brand')).focus({ preventScroll: true });
      }
    });
    toggle.hidden = false;
    navigation.setAttribute('data-nav-ready', '');
  }
  document.querySelectorAll('[data-navigation]').forEach(initNavigation);

  // Copy buttons ship hidden so a page without JS never shows a dead button.
  // writeText runs synchronously inside the click handler (Safari requires it).
  function initCopy(button) {
    const label = button.querySelector('[aria-live]');
    const source = button.parentElement.querySelector('.cmd-text');
    let timer;
    const copied = () => {
      label.textContent = 'Skopiowano';
      button.classList.add('is-copied');
      clearTimeout(timer);
      timer = setTimeout(() => {
        label.textContent = 'Kopiuj';
        button.classList.remove('is-copied');
      }, 1800);
    };
    const failed = () => {
      clearTimeout(timer);
      if (source) {
        const range = document.createRange();
        range.selectNodeContents(source);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      button.classList.remove('is-copied');
      label.textContent = 'Zaznaczono, skopiuj ręcznie';
    };
    button.hidden = false;
    button.addEventListener('click', () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(button.dataset.command).then(copied, failed);
        } else {
          failed();
        }
      } catch (error) {
        failed();
      }
    });
  }
  document.querySelectorAll('button.copy').forEach(initCopy);

  // Demo: the HTML holds the final state; animate only with motion allowed.
  const demo = document.getElementById('demo');
  if (!demo || reduceMotion || !('IntersectionObserver' in window)) return;

  // Behavior hooks are documented in README.md; visual classes are free to change.
  const roles = ['composer', 'chip-state', 'chip-dot'];
  const hooks = roles.map((role) => demo.querySelector(`[data-demo-role="${role}"]`));
  const [composer, chipState, chipDot] = hooks;
  const placeholder = composer?.textContent;
  const question = composer?.dataset.question;
  const completedState = chipState?.textContent;
  const phases = ['question', 'chip', 'arg', 'result', 'answer', 'table', 'row', 'source'];
  const groups = Object.fromEntries(phases.map((name) => [name,
    Array.from(demo.querySelectorAll(`[data-step="${name}"]`))]));
  const show = (el) => el.setAttribute('data-shown', '');
  let observer;
  let playing = false;
  let skip = false;

  function validate() {
    const validHooks = roles.every((role, index) => {
      const found = demo.querySelectorAll(`[data-demo-role="${role}"]`);
      return found.length === 1 && found[0] === hooks[index];
    });
    const validSteps = phases.every((name) => groups[name].length > 0
      && groups[name].every((el) => demo.contains(el) && el.dataset.step === name));
    if (!validHooks || !validSteps || !question?.trim() || !placeholder?.trim()
        || !completedState?.trim() || composer.dataset.question !== question) {
      throw new Error('The demo animation contract is incomplete or changed');
    }
  }

  // Reveal via the root state first: recovery never depends on the failed reveal step.
  function finish(state) {
    demo.dataset.demo = state;
    playing = false;
    if (composer) {
      composer.textContent = placeholder;
      composer.classList.remove('is-typing');
    }
    if (chipState) chipState.textContent = completedState;
    if (chipDot) chipDot.classList.remove('is-busy');
    observer?.disconnect();
    document.removeEventListener('visibilitychange', onVisibilityChange);
  }

  // Hidden tabs throttle timers. Finish the active sequence without further delays.
  function onVisibilityChange() { if (document.hidden && playing) skip = true; }
  async function wait(ms) {
    if (!skip) await new Promise((resolve) => setTimeout(resolve, ms));
    validate();
  }

  async function play() {
    try {
      observer.disconnect();
      validate();
      playing = true;
      demo.dataset.demo = 'playing';
      composer.classList.add('is-typing');
      for (let i = 1; i <= question.length && !skip; i += 1) {
        composer.textContent = question.slice(0, i);
        await wait(1000 / 30);
      }
      await wait(300);
      composer.classList.remove('is-typing');
      composer.textContent = '';
      groups.question.forEach(show);
      await wait(400);
      chipState.textContent = 'w toku';
      chipDot.classList.add('is-busy');
      groups.chip.forEach(show);
      for (const el of groups.arg) { await wait(80); show(el); }
      await wait(900);
      chipState.textContent = completedState;
      chipDot.classList.remove('is-busy');
      for (const el of groups.result) { await wait(80); show(el); }
      await wait(300);
      groups.answer.forEach(show);
      groups.table.forEach(show);
      for (const el of groups.row) { await wait(120); show(el); }
      await wait(200);
      groups.source.forEach(show);
      finish('done');
    } catch {
      finish('final');
    }
  }

  try {
    validate();
    observer = new IntersectionObserver((entries) => {
      const seen = entries.some((entry) => entry.isIntersecting
        && (entry.intersectionRatio >= 0.4 || entry.intersectionRect.height >= window.innerHeight * 0.5));
      if (seen && !playing) play();
    }, { threshold: [0, 0.1, 0.2, 0.3, 0.4] });
    document.addEventListener('visibilitychange', onVisibilityChange);
    composer.textContent = '';
    demo.dataset.demo = 'ready';
    observer.observe(demo);
  } catch {
    finish('final');
  }
})();
