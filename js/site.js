// deweloperuch-mcp landing page: copy buttons and the demo conversation.
(() => {
  'use strict';

  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

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

  const composer = demo.querySelector('.composer-text');
  const chipState = demo.querySelector('.chip-state');
  const chipDot = demo.querySelector('.chip-dot');
  const placeholder = composer.textContent;
  const question = composer.dataset.question;
  const steps = (name) => Array.from(demo.querySelectorAll(`[data-step="${name}"]`));
  const show = (el) => el.setAttribute('data-shown', '');
  // A hidden tab throttles timers: when the tab is hidden mid-animation, finish at once
  // instead of stalling half-way. Hiding the tab before the demo starts changes nothing.
  let playing = false;
  let skip = false;
  document.addEventListener('visibilitychange', () => { if (document.hidden && playing) skip = true; });
  const wait = (ms) => (skip ? Promise.resolve() : new Promise((resolve) => setTimeout(resolve, ms)));

  demo.dataset.demo = 'ready';
  composer.textContent = '';

  async function play() {
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
    steps('question').forEach(show);
    await wait(400);
    chipState.textContent = 'w toku';
    chipDot.classList.add('is-busy');
    steps('chip').forEach(show);
    for (const el of steps('arg')) { await wait(80); show(el); }
    await wait(900);
    chipState.textContent = 'gotowe';
    chipDot.classList.remove('is-busy');
    for (const el of steps('result')) { await wait(80); show(el); }
    await wait(300);
    steps('answer').forEach(show);
    steps('table').forEach(show);
    for (const el of steps('row')) { await wait(120); show(el); }
    await wait(200);
    steps('source').forEach(show);
    composer.textContent = placeholder;
    demo.dataset.demo = 'done';
    playing = false;
  }

  const observer = new IntersectionObserver((entries) => {
    const seen = entries.some((entry) => entry.isIntersecting
      && (entry.intersectionRatio >= 0.4 || entry.intersectionRect.height >= window.innerHeight * 0.5));
    if (seen) {
      observer.disconnect();
      play();
    }
  }, { threshold: [0, 0.1, 0.2, 0.3, 0.4] });
  observer.observe(demo);
})();
